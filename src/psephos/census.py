"""Pinned Census polygons for source discovery, not legal applicability or zoning."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pyogrio
from lxml import html
from pyogrio.raw import read as read_gis
from pyproj import CRS, Transformer
from shapely import from_wkb
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

from .acquire import Acquirer, Receipt
from .geography import property_value
from .retrieve import cutoff_date, cutoff_observation
from .store import Feature, Provision, Store, json_text

COLLECTION = "census-geography-2025"
BASE = "https://www2.census.gov/geo/tiger/TIGER2025/"
DOCS = "https://www2.census.gov/geo/pdfs/maps-data/data/tiger/tgrshp2025/"
RELEASE = "https://www.census.gov/geographies/mapping-files/2025/geo/tiger-line-file.html"
VINTAGE = "2025-01-01"
PUBLISHED = "2025-09-23"
CAP = 512 * 1024**2
STATES = set(
    "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 60 66 69 72 78".split()
)
# Ch4 §4.8: 29 MCD states, DC equivalent, Puerto Rico and Island Areas.
# Entire archives are retained, including their statistical/nonfunctioning subdivisions.
MCD_STATES = set(
    "05 09 11 17 18 19 20 22 23 24 25 26 27 28 29 31 33 34 36 37 38 39 42 44 46 47 50 51 54 55 60 66 69 72 78".split()
)
CITY_IDENTITIES = {
    "3651000": ("New York", "02395220", "us-ny-nyc"),
    "4159000": ("Portland", "02411471", "us-or-portland"),
}
WARNING = (
    "Census geographic source discovery, not applicable law, zoning, parcel boundaries or "
    "jurisdictional authority. Legal and statistical entities overlap independently; a place "
    "can cross counties. Missing retained collections does not mean no law. Tribal and "
    "special-purpose boundaries are not covered. Census disclaims legal boundary determinations."
)


def inventory(raw: bytes, layer: str) -> list[str]:
    names = html.fromstring(raw).xpath('//a[contains(@href,".zip")]/@href')
    expected = (
        {f"tl_2025_us_{layer.lower()}.zip"}
        if layer in {"STATE", "COUNTY"}
        else {f"tl_2025_{code}_{layer.lower()}.zip" for code in STATES}
    )
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError(f"Census {layer} inventory differs from pinned 2025 scope")
    return sorted(names)


def archive_members(raw: bytes, name: str) -> list[dict[str, Any]]:
    """Works on a complete ZIP or its retained EOCD/central-directory suffix; no extraction."""
    stem = name.removesuffix(".zip")
    expected = {
        stem + ext
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".shp.ea.iso.xml", ".shp.iso.xml")
    }
    with ZipFile(BytesIO(raw)) as z:
        entries = z.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != expected:
            raise ValueError(f"Unexpected Census archive members: {name}")
        if any(e.flag_bits & 1 or e.file_size > 256 * 1024**2 for e in entries):
            raise ValueError("Encrypted/oversized Census archive member")
        return [
            {
                "name": e.filename,
                "bytes": e.file_size,
                "compressed_bytes": e.compress_size,
                "crc32": e.CRC,
            }
            for e in entries
        ]


def census_bytes(s: Store) -> int:
    return int(
        s.db.execute(
            "SELECT coalesce(sum(CAST(coalesce(json_extract(a.headers,'$.psephos_downloaded_bytes'),"
            "CASE WHEN a.status IN (200,206) THEN b.bytes ELSE 0 END) AS INTEGER)),0) "
            "FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 "
            "WHERE a.url LIKE 'https://%census.gov/%'"
        ).fetchone()[0]
    )


def preflight(s: Store, a: Acquirer) -> dict[str, Any]:
    evidence = []
    for url in [
        RELEASE,
        *(DOCS + f"TGRSHP2025_TechDoc_{part}.pdf" for part in ("Ch1", "Ch3", "Ch4", "F-S")),
    ]:
        r = a.fetch(url, immutable=True, max_file_bytes=8 * 1024**2)
        if url == RELEASE:
            text = " ".join(html.fromstring(s.artifact(r.sha256)).text_content().split())
            if "January 1, 2025" not in text or "September 23, 2025" not in text:
                raise ValueError("Census release evidence no longer confirms the pinned clocks")
        evidence.append({"url": url, "acquisition_id": r.id, "sha256": r.sha256, "bytes": r.size})
    archives = []
    for layer in ("STATE", "COUNTY", "PLACE", "COUSUB"):
        url = BASE + layer + "/"
        index = a.fetch(url, immutable=True, max_file_bytes=1024**2)
        evidence.append(
            {"url": url, "acquisition_id": index.id, "sha256": index.sha256, "bytes": index.size}
        )
        for name in inventory(s.artifact(index.sha256), layer):
            tail = a.fetch(url + name, immutable=True, suffix_bytes=65557, max_file_bytes=65557)
            row = s.db.execute(
                "SELECT status,headers FROM acquisitions WHERE id=?", (tail.id,)
            ).fetchone()
            size = (
                tail.size
                if row["status"] == 200
                else int(json.loads(row["headers"])["content-range"].split("/")[-1])
            )
            members = archive_members(s.artifact(tail.sha256), name)
            archives.append(
                {
                    "name": name,
                    "url": url + name,
                    "layer": layer,
                    "statefp": name.split("_")[2],
                    "bytes": size,
                    "uncompressed_bytes": sum(m["bytes"] for m in members),
                    "members": members,
                    "preflight_acquisition_id": tail.id,
                    "selected": layer != "COUSUB" or name.split("_")[2] in MCD_STATES,
                }
            )
    retained = census_bytes(s)
    missing = sum(r["bytes"] for r in archives if r["selected"] and a._cached(r["url"]) is None)
    if missing + retained > CAP:
        raise ValueError("Pinned geographic scope exceeds cumulative 512 MiB acquisition cap")
    return {
        "collection": COLLECTION,
        "vintage": VINTAGE,
        "published_on": PUBLISHED,
        "source_evidence": evidence,
        "archives": archives,
        "all_archive_bytes": sum(r["bytes"] for r in archives),
        "selected_archive_bytes": sum(r["bytes"] for r in archives if r["selected"]),
        "selected_uncompressed_bytes": sum(
            r["uncompressed_bytes"] for r in archives if r["selected"]
        ),
        "scope": "All 56 STATE/equivalent and PLACE scopes, national COUNTY; COUSUB in 29 MCD states plus DC/PR/Island Areas. CCD states and Alaska census-subarea archives omitted.",
        "warning": WARNING,
    }


def validate_identity(p: dict[str, Any], layer: str, state: str) -> str:
    fields = {
        "STATE": ("STATEFP",),
        "COUNTY": ("STATEFP", "COUNTYFP"),
        "PLACE": ("STATEFP", "PLACEFP"),
        "COUSUB": ("STATEFP", "COUNTYFP", "COUSUBFP"),
    }[layer]
    widths = {"STATEFP": 2, "COUNTYFP": 3, "PLACEFP": 5, "COUSUBFP": 5}
    if any(
        not isinstance(p.get(k), str) or not re.fullmatch(r"\d{" + str(widths[k]) + "}", p[k])
        for k in fields
    ):
        raise ValueError("Census ID fields must retain leading-zero strings")
    identity = "".join(p[k] for k in fields)
    if (
        p.get("GEOID") != identity
        or p["STATEFP"] not in STATES
        or (state != "us" and p["STATEFP"] != state)
    ):
        raise ValueError("Census GEOID/state composition mismatch")
    expected_mtfcc = {
        "STATE": {"G4000"},
        "COUNTY": {"G4020"},
        "PLACE": {"G4110", "G4210"},
        "COUSUB": {"G4040"},
    }
    if p.get("MTFCC") not in expected_mtfcc[layer] or not all(
        p.get(k) for k in ("NAME", "LSAD", "FUNCSTAT", "GEOIDFQ")
    ):
        raise ValueError("Census feature class or required source attributes missing")
    if layer != "STATE" and not p.get("CLASSFP"):
        raise ValueError("Census CLASSFP missing")
    if layer == "STATE" and not re.fullmatch("[A-Z]{2}", str(p.get("STUSPS", ""))):
        raise ValueError("Census state postal identity missing")
    return identity


def shape_source(path: Path, name: str) -> str:
    return f"/vsizip/{{{path}}}/{name.removesuffix('.zip')}.shp"


def census_features(path: Path, item: dict[str, Any]) -> Iterator[Feature]:
    source = shape_source(path, item["name"])
    meta, _, geometries, columns = read_gis(source)
    if not meta["crs"] or CRS.from_user_input(meta["crs"]) != CRS.from_epsg(4269):
        raise ValueError("Expected the publisher's NAD83 CRS, not guessed coordinates")
    transformer = Transformer.from_crs(meta["crs"], "EPSG:4326", always_xy=True)
    seen = set()
    for index, raw in enumerate(geometries):
        p = {
            str(name): property_value(column[index])
            for name, column in zip(meta["fields"], columns, strict=True)
        }
        identity = validate_identity(p, item["layer"], item["statefp"])
        if identity in seen:
            raise ValueError("Duplicate Census GEOID within layer")
        seen.add(identity)
        geometry = transform(transformer.transform, from_wkb(raw))
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Empty or nonpolygon Census geometry")
        minx, miny, maxx, maxy = geometry.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise ValueError("Census geometry outside requested WGS84 coordinate bounds")
        # Invalid source topology is retained and warned at lookup, never silently repaired.
        yield Feature(
            item["layer"] + "/" + identity, mapping(geometry), p, geometry.bounds, item["layer"]
        )
    if len(seen) != int(pyogrio.read_info(source)["features"]):
        raise ValueError("Census parsed/declared feature counts differ")


def publish(s: Store, receipt: Receipt, item: dict[str, Any]) -> tuple[str, int, bool]:
    raw = s.artifact(receipt.sha256)
    if len(raw) != item["bytes"] or archive_members(raw, item["name"]) != item["members"]:
        raise ValueError("Census full archive differs from retained size/member preflight")
    with ZipFile(BytesIO(raw)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Census archive CRC failure")
    info = pyogrio.read_info(shape_source(s.object_path(receipt.sha256), item["name"]))
    metadata = {
        "features": int(info["features"]),
        "layer": item["layer"],
        "statefp": item["statefp"],
        "source_crs": info["crs"],
        "output_crs": "EPSG:4326",
        "always_xy": True,
        "fields": list(info["fields"]),
        "preflight_acquisition_id": item["preflight_acquisition_id"],
        "uncompressed_bytes": item["uncompressed_bytes"],
        "warning": WARNING,
    }
    key = "census:2025/" + item["name"]
    version, _, new = s.ingest(
        collection=COLLECTION,
        document=key,
        title=item["name"],
        url=receipt.url,
        acquisition=receipt.id,
        member=item["name"].removesuffix(".zip") + ".shp",
        snapshot_date=VINTAGE,
        snapshot_basis="Census 2025 boundary/name vintage; not legal applicability or current boundaries",
        published_on=PUBLISHED,
        parser="census-1",
        metadata=metadata,
        provisions=[
            Provision(
                key,
                item["name"],
                item["layer"],
                json_text(metadata),
                "",
                receipt.url,
                unit_kind="dataset_metadata",
            )
        ],
        features=census_features(s.object_path(receipt.sha256), item),
    )
    return version, metadata["features"], new


def sync_census(s: Store, a: Acquirer, limit: int | None, as_of: str | None) -> None:
    if as_of or limit or a.refresh:
        raise ValueError(
            "Census sync is the fixed bounded 2025 scope; no limit, refresh or invented historical edition"
        )
    a.delay = max(1.1, a.delay)
    a.max_bytes = min(a.max_bytes, a.downloaded + CAP - census_bytes(s))
    plan = preflight(s, a)
    directory = s.root / "census2025"
    directory.mkdir(exist_ok=True)
    (directory / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    s.collection(
        COLLECTION,
        ("us", "United States", "federal", None),
        name="Census 2025 source-discovery geography",
        authority="United States Census Bureau",
        kind="source_geography",
        homepage=BASE,
        source_status="Official Census statistical geography; not legal boundary adjudication",
        access="US government materials reproducible with requested Census attribution; TIGER/Line trademark conditions, no accuracy warranty. See technical documentation Ch1.",
        metadata={k: v for k, v in plan.items() if k not in {"archives", "collection"}},
    )
    for item in plan["archives"]:
        if not item["selected"]:
            s.inventory(
                COLLECTION,
                item["name"],
                item["url"],
                "omitted_scope",
                "CCD state or Alaska census subareas; not part of the MCD archive subset",
            )
            continue
        try:
            s.inventory(COLLECTION, item["name"], item["url"], "pending")
            r = a.fetch(item["url"], immutable=True, max_file_bytes=item["bytes"])
            _, count, new = publish(s, r, item)
            s.inventory(COLLECTION, item["name"], item["url"], "indexed")
            from .sources import progress

            progress(COLLECTION, item["name"], count, new)
        except (ValueError, RuntimeError, OSError) as exc:
            s.inventory(COLLECTION, item["name"], item["url"], "failed", str(exc))
            raise


def entity_type(layer: str, p: dict[str, Any]) -> str:
    if layer == "PLACE":
        return "census_designated_place" if p["MTFCC"] == "G4210" else "incorporated_place"
    if layer == "COUSUB":
        if p["FUNCSTAT"] == "F":
            return "county_subdivision_equivalent"
        return (
            "statistical_county_subdivision" if p["FUNCSTAT"] == "S" else "legal_county_subdivision"
        )
    return "state_or_equivalent" if layer == "STATE" else "county_or_equivalent"


def route(s: Store, jurisdiction: str, observed: str | None) -> dict[str, Any]:
    registered = s.db.execute(
        "SELECT name FROM jurisdictions WHERE id=?", (jurisdiction,)
    ).fetchone()
    result: dict[str, Any] = {
        "jurisdiction": jurisdiction,
        "status": "unregistered_jurisdiction",
        "collections": [],
    }
    if registered is None:
        return result
    result.update(name=registered[0], status="registered_no_retained_collections")
    rows = s.db.execute(
        "SELECT id,name,kind,homepage FROM collections WHERE jurisdiction_id=? AND kind!='source_geography' ORDER BY id LIMIT 21",
        (jurisdiction,),
    ).fetchall()
    for row in rows[:20]:
        count = s.db.execute(
            "SELECT count(DISTINCT d.id),min(v.snapshot_date),max(v.snapshot_date) "
            "FROM documents d JOIN versions v ON v.document_id=d.id WHERE d.collection_id=? "
            "AND (? IS NULL OR v.available_at<=?)",
            (row["id"], observed, observed),
        ).fetchone()
        result["collections"].append(
            {
                **dict(row),
                "retained_documents": count[0],
                "snapshot_range": list(count[1:]),
                "status": "retained" if count[0] else "registered_unacquired_at_cutoff",
            }
        )
    if any(c["retained_documents"] for c in result["collections"]):
        result["status"] = "retained_collections"
    result["next_collection_offset"] = 20 if len(rows) > 20 else None
    result["continue_with"] = {"tool": "legal_coverage", "jurisdiction": jurisdiction}
    return result


def legal_sources_at(
    s: Store,
    longitude: float,
    latitude: float,
    *,
    geometry_as_of: str | None = None,
    observation_cutoff: str | None = None,
    offset: int = 0,
    limit: int = 10,
) -> dict[str, Any]:
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    ):
        raise ValueError(
            "Provide finite WGS84 longitude/latitude in degrees; coordinates are never swapped"
        )
    if not 0 <= offset <= 100000 or not 1 <= limit <= 20:
        raise ValueError("Use offset 0..100000 and limit 1..20")
    day, clock = cutoff_date(geometry_as_of), cutoff_observation(observation_cutoff)
    versions = s.db.execute(
        "SELECT v.*,d.title,d.url FROM documents d JOIN versions v ON v.id=(SELECT id FROM versions "
        "WHERE document_id=d.id AND (? IS NULL OR snapshot_date<=?) AND (? IS NULL OR available_at<=?) "
        "ORDER BY snapshot_date DESC,available_at DESC,rowid DESC LIMIT 1) WHERE d.collection_id=?",
        (day, day, clock, clock, COLLECTION),
    ).fetchall()
    eligible_ids = {v["id"]: v for v in versions}
    point = Point(longitude, latitude)
    matches, warnings, state_codes = [], [], set()
    rows = s.db.execute(
        "SELECT f.* FROM feature_bounds b JOIN features f ON f.rowid=b.rowid "
        "JOIN versions v ON v.id=f.version_id JOIN documents d ON d.id=v.document_id "
        "WHERE d.collection_id=? AND b.minx<=? AND b.maxx>=? AND b.miny<=? AND b.maxy>=? ORDER BY f.layer,f.source_key",
        (COLLECTION, longitude, longitude, latitude, latitude),
    )
    for row in rows:
        if row["version_id"] not in eligible_ids:
            continue
        geometry = shape(json.loads(row["geometry"]))
        if not geometry.is_valid:
            warnings.append(
                {
                    "id": row["id"],
                    "reason": "Invalid source polygon near point; no topological conclusion",
                }
            )
            continue
        if not geometry.covers(point):
            continue
        p = json.loads(row["properties"])
        layer, v = row["layer"], eligible_ids[row["version_id"]]
        state_codes.add(p["STATEFP"])
        linked = None
        basis = "No reviewed legal-collection identity crosswalk; not evidence of absent law"
        if layer == "STATE":
            linked = "us-" + p["STUSPS"].lower()
            basis = "Exact Census STATEFP/STUSPS state/equivalent identity; local registry ID convention"
        elif layer == "PLACE" and p["GEOID"] in CITY_IDENTITIES:
            name, gnis, jurisdiction = CITY_IDENTITIES[p["GEOID"]]
            if (
                p.get("NAME"),
                p.get("PLACENS"),
                p.get("MTFCC"),
                p.get("CLASSFP"),
                p.get("FUNCSTAT"),
            ) == (name, gnis, "G4110", "C1", "A"):
                linked = jurisdiction
                basis = "Reviewed exact Census GEOID + GNIS + native name/class identity paired with retained municipal publisher scope; no fuzzy join"
        matches.append(
            {
                "id": row["id"],
                "layer": layer,
                "entity_type": entity_type(layer, p),
                "properties": p,
                "on_boundary": geometry.boundary.covers(point),
                "geometry_source": {
                    "url": v["url"],
                    "artifact_sha": v["artifact_sha"],
                    "acquisition_id": row["acquisition_id"],
                    "vintage": v["snapshot_date"],
                    "published_on": v["published_on"],
                    "available_at": v["available_at"],
                },
                "identity_basis": basis,
                "legal_sources": route(s, linked, clock)
                if linked
                else {"status": "not_crosswalked", "collections": []},
            }
        )
    acquired_names = {v["title"] for v in versions}
    expected = {f"tl_2025_us_{layer}.zip" for layer in ("state", "county")}
    expected |= {f"tl_2025_{code}_place.zip" for code in state_codes}
    expected |= {f"tl_2025_{code}_cousub.zip" for code in state_codes & MCD_STATES}
    result: dict[str, Any] = {
        "point": {"longitude": longitude, "latitude": latitude, "crs": "EPSG:4326"},
        "status": "matches" if matches else "no_intersection_in_eligible_acquired_geometry",
        "matches": [],
        "total": len(matches),
        "offset": offset,
        "next_offset": None,
        "geometry_coverage": {
            "collection": COLLECTION,
            "eligible_archives": len(versions),
            "expected_selected_archives": 93,
            "missing_point_scope_archives": sorted(expected - acquired_names),
            "omitted_cousub_statefp_at_point": sorted(state_codes - MCD_STATES),
            "geometry_warning_count": len(warnings),
            "geometry_warnings": warnings[:10],
        },
        "geometry_as_of": day,
        "observation_cutoff": clock,
        "federal_reference_sources": route(s, "us", clock) if matches else None,
        "clocks": "geometry_as_of selects Census snapshots only, not legal-text dates. Collection snapshot ranges are independent; use legal_read/search cutoffs. Unknown history/effectiveness remains unknown.",
        "warning": WARNING,
        "next_step": "Use legal_coverage(collection=...) → legal_read/search. Call zoning_at separately for retained NYC/Portland zoning; this tool does not return zoning districts.",
    }
    for item in matches[offset : offset + limit]:
        result["matches"].append(item)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 24000:
            result["matches"].pop()
            break
    advanced = len(result["matches"])
    if offset < len(matches) and not advanced:
        result.update(status="oversized_entity", skipped_entity_offset=offset)
        advanced = 1
    result["next_offset"] = offset + advanced if offset + advanced < len(matches) else None
    return result
