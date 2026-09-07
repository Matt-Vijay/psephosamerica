"""Actual publisher polygons, not centroid guesses or inferred legal applicability."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pyogrio
from pyogrio.raw import read as read_gis
from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

from .acquire import Acquirer, Receipt
from .retrieve import eligible
from .sources import checked_zip, progress
from .store import Feature, Provision, Store, digest, json_text, utc_now

NYC_META = "https://data.cityofnewyork.us/api/views/mm69-vrje.json"
NYC_ZIP = "https://data.cityofnewyork.us/download/mm69-vrje/application/zip"
PDX_LAYER = "https://www.portlandmaps.com/arcgis/rest/services/Public/Zoning/MapServer/3"

GEO_WARNING = (
    "All intersecting acquired polygons, including boundaries. A point is not a parcel; "
    "GIS/text vintages may differ. District, overlay, plan, pending-map and agency-area fields "
    "retain their source meanings. This does not certify buildability, applicability, or absence "
    "of restrictions. Empty results are a coverage/result statement, not permission."
)


def property_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def gdb_features(path: Path, layer: str, member: str) -> Iterator[Feature]:
    source = f"/vsizip/{{{path}}}/{member}"
    meta, ids, geometries, columns = read_gis(source, layer=layer, return_fids=True)
    if not meta["crs"]:
        raise ValueError("Publisher GIS CRS missing")
    transformer = Transformer.from_crs(meta["crs"], "EPSG:4326", always_xy=True)
    for index, raw in enumerate(geometries):
        geometry = transform(transformer.transform, from_wkb(raw))
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Unexpected empty/nonpolygon zoning feature")
        properties = {
            str(name): property_value(column[index])
            for name, column in zip(meta["fields"], columns, strict=True)
        }
        yield Feature(
            layer + "/" + str(ids[index]), mapping(geometry), properties, geometry.bounds, layer
        )


def dataset_unit(key: str, title: str, metadata: dict[str, Any], url: str) -> Provision:
    return Provision(
        key=key,
        citation=title,
        heading=title,
        text=json.dumps(metadata, indent=2),
        markup="",
        url=url,
        unit_kind="dataset_metadata",
        metadata={"warning": GEO_WARNING},
    )


def sync_nyc_geo(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of or limit:
        raise ValueError(
            "NYC geometry sync uses the complete publisher bulk archive, with its stated vintage"
        )
    metadata_receipt, meta = a.json(NYC_META)
    terms = a.fetch("https://opendata.cityofnewyork.us/overview/")
    raw = a.fetch(NYC_ZIP)
    if meta.get("blobFileSize") != raw.size:
        raise ValueError(
            "NYC bulk bytes do not match portal inventory size; refresh metadata before retrying"
        )
    with checked_zip(s.object_path(raw.sha256)) as archive:
        candidates = {
            name.split(".gdb/")[0] + ".gdb" for name in archive.namelist() if ".gdb/" in name
        }
        if len(candidates) != 1:
            raise ValueError("NYC archive has no unique geodatabase")
        member = candidates.pop()
    s.collection(
        "nyc-zoning-gis",
        ("us-ny-nyc", "New York City", "municipality", "us-ny"),
        parents=(("us-ny", "New York", "state", "us"),),
        name="NYC Zoning GIS Features",
        authority="NYC Department of City Planning",
        kind="zoning_geography",
        homepage=NYC_META,
        source_status="Publisher informational GIS, not individual-tax-lot legal determination",
        access="Official bulk GIS download; NYC Open Data and agency-specific terms apply, not a generic CC0 claim",
        metadata={
            "portal": meta,
            "metadata_artifact": metadata_receipt.sha256,
            "terms_artifact": terms.sha256,
            "warning": "Description vintage is independent of portal viewLastModified and text currency.",
        },
    )
    source = f"/vsizip/{{{s.object_path(raw.sha256)}}}/{member}"
    layers = {
        str(layer): pyogrio.read_info(source, layer=str(layer))
        for layer, _ in pyogrio.list_layers(source)
    }

    def features() -> Iterator[Feature]:
        for layer, info in layers.items():
            count = 0
            for feature in gdb_features(s.object_path(raw.sha256), layer, member):
                yield feature
                count += 1
            if count != int(info["features"]):
                raise ValueError(f"GIS feature count mismatch in {layer}")

    # The six layers are published together in one immutable archive and one local transaction.
    expected = sum(int(info["features"]) for info in layers.values())
    metadata = {
        "output_crs": "EPSG:4326",
        "expected_features": expected,
        "layers": {
            layer: {"features": int(info["features"]), "source_crs": info["crs"]}
            for layer, info in layers.items()
        },
        "publisher_description": meta["description"],
        "portal_metadata_artifact": metadata_receipt.sha256,
    }
    s.inventory("nyc-zoning-gis", "zoning.gdb", raw.url, "pending")
    _, _, new = s.ingest(
        collection="nyc-zoning-gis",
        document="nyc-gis:zoning",
        title="NYC Zoning GIS Features",
        url=NYC_META,
        acquisition=raw.id,
        member=member,
        snapshot_date=None,
        snapshot_basis="Publisher monthly vintage in description; no invented exact-day snapshot",
        parser="gdb-2",
        metadata=metadata,
        provisions=[dataset_unit("nyc-gis:zoning", "NYC Zoning GIS Features", metadata, NYC_META)],
        features=features(),
    )
    s.inventory("nyc-zoning-gis", "zoning.gdb", raw.url, "indexed")
    progress("nyc-zoning-gis", "zoning.gdb", expected, new)


def geojson_features(
    collection: dict[str, Any], id_field: str, acquisition: int | None = None
) -> Iterator[Feature]:
    if collection.get("type") != "FeatureCollection":
        raise ValueError("Publisher response is not a GeoJSON FeatureCollection")
    seen = set()
    for feature in collection["features"]:
        identity = str(feature["properties"][id_field])
        if identity in seen:
            raise ValueError("Duplicate publisher feature ID")
        seen.add(identity)
        geometry = shape(feature["geometry"])
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Unexpected zoning geometry")
        minx, miny, maxx, maxy = geometry.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise ValueError("GeoJSON response not in requested EPSG:4326 bounds")
        yield Feature(
            identity, feature["geometry"], feature["properties"], geometry.bounds, "3", acquisition
        )


def publish_arcgis_layer(
    s: Store,
    inventory: Receipt,
    pages: list[Receipt],
    id_field: str,
    expected_ids: list[int],
    metadata: dict[str, Any],
) -> tuple[str, int, bool]:
    """One whole-layer version. Failed parsing, missing IDs, or an interrupted transaction exposes nothing new."""
    manifest = [{"artifact_sha": receipt.sha256, "acquisition_id": receipt.id} for receipt in pages]
    manifest_hash = digest(json_text([receipt.sha256 for receipt in pages]).encode())

    def features() -> Iterator[Feature]:
        obtained = set()
        for receipt in pages:
            payload = json.loads(s.artifact(receipt.sha256))
            for feature in geojson_features(payload, id_field, receipt.id):
                if feature.key in obtained:
                    raise ValueError("Duplicate feature across acquired pages")
                obtained.add(feature.key)
                yield feature
        if obtained != {str(identity) for identity in expected_ids}:
            raise ValueError("Acquired layer does not equal the publisher's enumerated membership")

    metadata = {
        **metadata,
        "page_receipts": manifest,
        "expected_features": len(expected_ids),
        "inventory_artifact": inventory.sha256,
        "page_manifest_sha256": manifest_hash,
    }
    return s.ingest(
        collection="portland-zoning-gis",
        document="portland-gis:layer-3",
        title="Portland Detailed Zoning Designations",
        url=PDX_LAYER,
        acquisition=inventory.id,
        member="page-manifest:" + manifest_hash,
        snapshot_date=None,
        snapshot_basis="Live export observation interval; no server snapshot isolation claimed",
        parser="arcgis-layer-1",
        metadata=metadata,
        available_at=metadata["accepted_at"],
        provisions=[
            dataset_unit("portland-gis:layer-3", "Portland Zoning GIS", metadata, PDX_LAYER)
        ],
        features=features(),
    )


def sync_portland_geo(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of or limit:
        raise ValueError("Portland GIS sync uses a complete current paginated publisher export")
    meta_receipt, meta = a.json(PDX_LAYER + "?f=pjson")
    terms = a.fetch("https://www.portlandmaps.com/bps/arpa/tos.pdf")
    inventory_receipt, inventory = a.json(
        PDX_LAYER + "/query?where=1%3D1&returnIdsOnly=true&f=json"
    )
    ids = sorted(inventory["objectIds"])
    id_field = inventory["objectIdFieldName"]
    s.collection(
        "portland-zoning-gis",
        ("us-or-portland", "Portland, Oregon", "municipality", "us-or"),
        parents=(("us-or", "Oregon", "state", "us"),),
        name="Portland Detailed Zoning Designations",
        authority="City of Portland",
        kind="zoning_geography",
        homepage=PDX_LAYER,
        source_status="City informational GIS; includes separately marked unincorporated administered areas",
        access="Publisher ArcGIS read/export API; retained terms describe default PDDL but PDF is watermarked DRAFT; verify terms before redistribution",
        metadata={
            "layer_metadata": meta,
            "layer_metadata_artifact": meta_receipt.sha256,
            "inventory_artifact": inventory_receipt.sha256,
            "expected_features": len(ids),
            "terms_artifact": terms.sha256,
            "warning": GEO_WARNING,
        },
    )
    pages = []
    s.inventory("portland-zoning-gis", "layer-3", PDX_LAYER, "pending")
    for offset in range(0, len(ids), 500):
        subset = ids[offset : offset + 500]
        query = urlencode(
            {
                "objectIds": ",".join(map(str, subset)),
                "outFields": "*",
                "outSR": 4326,
                "returnGeometry": "true",
                "f": "geojson",
            }
        )
        url = PDX_LAYER + "/query?" + query
        raw, payload = a.json(url)
        obtained = sorted(
            feature["properties"][id_field] for feature in payload.get("features", [])
        )
        if obtained != subset:
            raise ValueError(
                "ArcGIS omitted/duplicated requested object IDs; no completeness claim"
            )
        pages.append(raw)
    previous = s.db.execute("SELECT 1 FROM documents WHERE id='portland-gis:layer-3'").fetchone()
    if a.refresh or not previous:
        check = a.fetch(
            PDX_LAYER + "/query?where=1%3D1&returnIdsOnly=true&f=json", max_age_seconds=0
        )
        final = json.loads(s.artifact(check.sha256))
        if sorted(final["objectIds"]) != ids:
            raise ValueError(
                "Publisher layer membership changed during export; previous accepted layer retained"
            )
    _, _, new = publish_arcgis_layer(
        s,
        inventory_receipt,
        pages,
        id_field,
        ids,
        {
            "output_crs": "EPSG:4326",
            "source_crs": meta["extent"]["spatialReference"],
            "observation_started": min(r.observed_at for r in pages),
            "accepted_at": utc_now(),
            "consistency": "Initial/final object-ID membership agrees. Attribute changes during live export are not isolated.",
        },
    )
    s.inventory("portland-zoning-gis", "layer-3", PDX_LAYER, "indexed")
    progress("portland-zoning-gis", "layer-3", len(ids), new)


def zoning_at(
    s: Store,
    longitude: float,
    latitude: float,
    *,
    collection: str | None = None,
    as_of: str | None = None,
    observation_cutoff: str | None = None,
) -> dict[str, Any]:
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    ):
        raise ValueError("Provide finite WGS84 longitude/latitude in degrees")
    point = Point(longitude, latitude)
    prefix, params = eligible(as_of, observation_cutoff)
    where = " AND d.collection_id=?" if collection else ""
    if collection:
        params.append(collection)
    rows = s.db.execute(
        prefix
        + "SELECT f.id,f.source_key,f.layer,f.geometry,f.properties,d.collection_id,d.title,d.url AS source_url,v.snapshot_date,"
        "v.observed_at,fa.sha256 AS artifact_sha,fa.id AS acquisition_id,v.metadata FROM feature_bounds b "
        "JOIN features f ON f.rowid=b.rowid JOIN chosen v ON v.id=f.version_id "
        "JOIN acquisitions fa ON fa.id=f.acquisition_id "
        "JOIN documents d ON d.id=v.document_id WHERE 1=1"
        + where
        + " AND b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=? ORDER BY f.id",
        (*params, longitude, longitude, latitude, latitude),
    )
    matches, warnings = [], []
    for row in rows:
        geometry = shape(json.loads(row["geometry"]))
        if not geometry.is_valid:
            warnings.append(
                {
                    "id": row["id"],
                    "reason": "Invalid acquired geometry near query; no topological conclusion",
                }
            )
            continue
        if geometry.covers(point):
            item = dict(row)
            item.pop("geometry")
            item["properties"] = json.loads(item["properties"])
            item["metadata"] = json.loads(item["metadata"])
            item["on_boundary"] = geometry.boundary.covers(point)
            matches.append(item)
    return {
        "point": {"longitude": longitude, "latitude": latitude, "crs": "EPSG:4326"},
        "matches": matches,
        "geometry_warnings": warnings,
        "warning": GEO_WARNING,
        "as_of": as_of,
        "observation_cutoff": observation_cutoff,
        "temporal_warning": "An as-of date excludes undated GIS snapshots; no historical geometry is invented.",
    }
