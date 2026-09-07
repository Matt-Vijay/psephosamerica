import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import retain
from shapely.geometry import Polygon, box, mapping

from psephos import census
from psephos.geography import zoning_at
from psephos.store import Feature, Provision


def register(store, collection=census.COLLECTION, jurisdiction="us", kind="source_geography"):
    store.collection(
        collection,
        (jurisdiction, jurisdiction, "fixture", None),
        name=collection,
        authority="Fixture",
        kind=kind,
        homepage="https://example.test/",
        source_status="Fixture",
        access="Fixture",
    )


def properties(layer="PLACE", code="3651000", **extra):
    p = dict(
        STATEFP=code[:2],
        GEOID=code,
        GEOIDFQ="1600000US" + code,
        NAME="New York",
        NAMELSAD="New York city",
        LSAD="25",
        CLASSFP="C1",
        MTFCC="G4110",
        FUNCSTAT="A",
        PLACEFP=code[2:],
        PLACENS="02395220",
    )
    if layer == "STATE":
        p.update(MTFCC="G4000", STUSPS="NY", LSAD="00", NAME="New York")
        p.pop("CLASSFP")
    elif layer == "COUNTY":
        p.update(COUNTYFP=code[2:], MTFCC="G4020", CLASSFP="H1", NAME="County")
    elif layer == "COUSUB":
        p.update(COUNTYFP=code[2:5], COUSUBFP=code[5:], MTFCC="G4040")
    p.update(extra)
    return p


def install(store, layer, props, geometry=None, suffix="", collection=census.COLLECTION):
    geometry = box(0, 0, 2, 2) if geometry is None else geometry
    receipt = retain(store, (layer + props["GEOID"] + suffix).encode())
    name = (
        f"tl_2025_{'us' if layer in {'STATE', 'COUNTY'} else props['STATEFP']}_{layer.lower()}.zip"
    )
    return store.ingest(
        collection=collection,
        document=layer + props["GEOID"] + suffix,
        title=name,
        url=receipt.url,
        acquisition=receipt.id,
        snapshot_date=census.VINTAGE,
        published_on=census.PUBLISHED,
        snapshot_basis="fixture",
        parser="test",
        provisions=[Provision(name, name, name, "Metadata", "", receipt.url)],
        features=[
            Feature(layer + "/" + props["GEOID"], mapping(geometry), props, geometry.bounds, layer)
        ],
    )


def test_exact_inventory_and_zip_members_not_guessed():
    assert census.inventory(b'<a href="tl_2025_us_state.zip">state</a>', "STATE") == [
        "tl_2025_us_state.zip"
    ]
    with pytest.raises(ValueError, match="inventory"):
        census.inventory(b'<a href="tl_2025_us_state.zip"></a>' * 2, "STATE")
    raw = BytesIO()
    with ZipFile(raw, "w") as z:
        z.writestr("../bad.shp", b"x")
    with pytest.raises(ValueError, match="members"):
        census.archive_members(raw.getvalue(), "tl_2025_us_state.zip")


def test_parser_ids_crs_membership_and_native_properties(monkeypatch):
    p = properties(code="0151000", NAME="Fixture")
    info = {"features": 1}
    meta = {"crs": "EPSG:4269", "fields": list(p)}
    monkeypatch.setattr(
        census, "read_gis", lambda _: (meta, None, [box(0, 0, 1, 1).wkb], [[v] for v in p.values()])
    )
    monkeypatch.setattr(census.pyogrio, "read_info", lambda _: info)
    item = {"name": "fixture.zip", "layer": "PLACE", "statefp": "01"}
    f = list(census.census_features(Path("not-read"), item))[0]
    assert f.key == "PLACE/0151000" and f.properties == p
    info["features"] = 2
    with pytest.raises(ValueError, match="counts"):
        list(census.census_features(Path("not-read"), item))
    meta["crs"] = None
    with pytest.raises(ValueError, match="NAD83"):
        list(census.census_features(Path("not-read"), item))
    for bad in ({"GEOID": "151000"}, {"STATEFP": 1}, {"CLASSFP": ""}):
        with pytest.raises(ValueError):
            census.validate_identity({**p, **bad}, "PLACE", "01")


def test_intersections_types_exact_routes_and_separate_zoning(store):
    register(store)
    register(store, "nyc-law", "us-ny-nyc", "code")
    install(store, "STATE", properties("STATE", "36"))
    install(store, "PLACE", properties())
    install(store, "COUNTY", properties("COUNTY", "36001"), box(0, 0, 1, 2))
    install(store, "COUNTY", properties("COUNTY", "36003"), box(1, 0, 2, 2))
    install(
        store,
        "PLACE",
        properties(code="3612345", MTFCC="G4210", FUNCSTAT="S", NAME="CDP", CLASSFP="U1"),
    )
    r = census.legal_sources_at(store, 1, 1)
    assert len(r["matches"]) == 5
    assert sum(m["on_boundary"] for m in r["matches"]) == 2
    city = next(m for m in r["matches"] if m["properties"]["GEOID"] == "3651000")
    assert city["legal_sources"]["jurisdiction"] == "us-ny-nyc"
    assert city["legal_sources"]["collections"][0]["status"] == "registered_unacquired_at_cutoff"
    state = next(m for m in r["matches"] if m["layer"] == "STATE")
    assert state["legal_sources"]["status"] == "unregistered_jurisdiction"
    cdp = next(m for m in r["matches"] if m["properties"]["MTFCC"] == "G4210")
    assert (
        cdp["entity_type"] == "census_designated_place"
        and cdp["legal_sources"]["status"] == "not_crosswalked"
    )
    assert not zoning_at(store, 1, 1)["matches"]
    assert not zoning_at(store, 1, 1, collection=census.COLLECTION)["matches"]
    assert not census.legal_sources_at(store, 5, 5)["matches"]
    assert len(json.dumps(r).encode()) < 24576


def test_clock_holes_invalid_topology_no_nearest_or_coordinate_swap(store):
    register(store)
    hole = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (3, 1), (3, 3), (1, 3)]])
    install(store, "PLACE", properties(), hole)
    assert not census.legal_sources_at(store, 2, 2)["matches"]
    assert census.legal_sources_at(store, 1, 2)["matches"][0]["on_boundary"]
    for kwargs in (
        {"geometry_as_of": "2024-12-31"},
        {"observation_cutoff": "2025-12-31T23:59:59Z"},
    ):
        assert not census.legal_sources_at(store, 0.5, 0.5, **kwargs)["matches"]
    assert census.legal_sources_at(store, 0.5, 0.5, geometry_as_of="2025-01-01")["matches"]
    with pytest.raises(ValueError, match="timezone"):
        census.legal_sources_at(store, 1, 1, observation_cutoff="2026-01-01")
    for lon, lat in [(float("nan"), 1), (45, -122), (-181, 0)]:
        with pytest.raises(ValueError):
            census.legal_sources_at(store, lon, lat)
    invalid = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    install(store, "PLACE", properties(code="3699999"), invalid)
    assert census.legal_sources_at(store, 2, 2)["geometry_coverage"]["geometry_warning_count"] == 1


def test_source_identity_not_fuzzy_and_pagination(store):
    register(store)
    for n in range(25):
        install(store, "PLACE", properties(code=f"36{n:05d}"))
    first = census.legal_sources_at(store, 1, 1, limit=3)
    assert first["total"] == 25 and len(first["matches"]) == 3 and first["next_offset"] == 3
    assert all(m["legal_sources"]["status"] == "not_crosswalked" for m in first["matches"])
    second = census.legal_sources_at(store, 1, 1, offset=3, limit=3)
    assert not {m["id"] for m in first["matches"]} & {m["id"] for m in second["matches"]}
    assert not census.legal_sources_at(store, 1, 1, offset=25)["matches"]
