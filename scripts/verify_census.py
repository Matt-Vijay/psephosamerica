"""Offline, geographic-source-only truth checks and real read-only MCP navigation."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pyogrio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pyogrio.raw import read
from shapely.geometry import MultiPolygon, Polygon, shape

from psephos import census
from psephos.acquire import Acquirer, Receipt
from psephos.geography import property_value
from psephos.store import Feature, Store, digest, json_text, utc_now


def failure_resume(s: Store, item: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """One real archive, fresh tiny catalog; never copy the national database."""
    with tempfile.TemporaryDirectory(prefix="psephos-census-resume-") as temp:
        target = Store(Path(temp))
        raw = s.artifact(source["sha256"])
        path = target.object_path(source["sha256"])
        path.parent.mkdir(parents=True)
        path.hardlink_to(s.object_path(source["sha256"]))
        with target.db:
            target.db.execute("INSERT INTO artifacts VALUES (?,?)", (source["sha256"], len(raw)))
            acquired = target.db.execute(
                "INSERT INTO acquisitions(url,final_url,observed_at,status,sha256,headers) VALUES (?,?,?,200,?,'{}')",
                (source["url"], source["url"], source["observed_at"], source["sha256"]),
            ).lastrowid
        assert acquired is not None
        r = Receipt(acquired, source["url"], source["sha256"], source["observed_at"], len(raw))
        target.collection(
            census.COLLECTION,
            ("us", "United States", "federal", None),
            name="Real-archive replay",
            authority="Census",
            kind="source_geography",
            homepage=census.BASE,
            source_status="Replay",
            access="Local proof",
        )
        original = census.census_features

        def interrupted(path: Path, spec: dict[str, Any]) -> Iterator[Feature]:
            for feature in original(path, spec):
                yield feature
                raise ValueError("injected interruption after first real feature")

        a = Acquirer(target)
        a.client.close()
        requests = []

        def no_network(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            raise AssertionError("Offline replay tried the network")

        a.client = httpx.Client(transport=httpx.MockTransport(no_network))
        try:
            with patch.object(census, "census_features", interrupted):
                try:
                    census.publish(target, r, item)
                    raise AssertionError("Failure injection did not execute")
                except ValueError as exc:
                    assert "injected interruption" in str(exc)
            assert target.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0
            assert target.db.execute("SELECT count(*) FROM features").fetchone()[0] == 0
            assert a.fetch(r.url, immutable=True) == r
            version, count, new = census.publish(target, r, item)
            assert new and count > 0
            repeated, repeat_count, new = census.publish(target, a.fetch(r.url), item)
            assert not new and (repeated, repeat_count) == (version, count)
            assert target.db.execute("SELECT count(*) FROM features").fetchone()[0] == count
            assert not requests and a.downloaded == 0
            return {
                "archive": item["name"],
                "features": count,
                "artifact_sha": r.sha256,
                "failure": "Injected after one real feature; version/features rolled back",
                "resumed_same_artifact": True,
                "idempotent_version": version,
                "external_requests": len(requests),
                "downloaded_bytes": a.downloaded,
                "whole_catalog_copied": False,
            }
        finally:
            a.close()
            target.close()


def verify(data: Path) -> dict[str, Any]:
    s = Store(data, readonly=True)
    try:
        plan = json.loads((data / "census2025/plan.json").read_bytes())
        acquisition_rows = [
            dict(r)
            for r in s.db.execute(
                "SELECT a.*,b.bytes FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 "
                "WHERE a.url LIKE 'https://%census.gov/%' ORDER BY a.id"
            )
        ]
        objects = {r["sha256"]: r["bytes"] for r in acquisition_rows if r["sha256"]}
        for sha, size in objects.items():
            assert len(s.artifact(sha)) == size
        assert census.census_bytes(s) <= census.CAP
        statuses = Counter(r["status"] for r in acquisition_rows)
        accepted = [r for r in plan["archives"] if r["selected"]]
        assert len(accepted) == 93
        assert sum(r["bytes"] for r in accepted) == plan["selected_archive_bytes"]
        versions = [
            dict(r)
            for r in s.db.execute(
                "SELECT v.*,d.title,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=?",
                (census.COLLECTION,),
            )
        ]
        assert len(versions) == len(accepted)
        by_name = {v["title"]: v for v in versions}
        assert len(by_name) == len(versions)
        declared: Counter[str] = Counter()
        actual: Counter[str] = Counter()
        classes: Counter[str] = Counter()
        functions: Counter[str] = Counter()
        types: Counter[str] = Counter()
        archive_results = []
        all_keys = set()
        invalid = []
        for item in accepted:
            v = by_name[item["name"]]
            assert v["snapshot_date"] == census.VINTAGE and v["published_on"] == census.PUBLISHED
            assert v["effective_on"] is None
            assert v["url"] == item["url"]
            assert (
                census.archive_members(s.artifact(v["artifact_sha"]), item["name"])
                == item["members"]
            )
            source = census.shape_source(s.object_path(v["artifact_sha"]), item["name"])
            info = pyogrio.read_info(source)
            assert info["crs"] == "EPSG:4269"
            declared[item["layer"]] += int(info["features"])
            meta, _, _, columns = read(source, read_geometry=False)
            native = {}
            for index in range(int(info["features"])):
                p = {
                    str(k): property_value(col[index])
                    for k, col in zip(meta["fields"], columns, strict=True)
                }
                key = census.validate_identity(p, item["layer"], item["statefp"])
                assert key not in native
                native[key] = p
            count = 0
            for row in s.db.execute(
                "SELECT f.*,b.minx,b.maxx,b.miny,b.maxy FROM features f JOIN feature_bounds b ON b.rowid=f.rowid WHERE f.version_id=?",
                (v["id"],),
            ):
                p = json.loads(row["properties"])
                assert native.pop(p["GEOID"]) == p
                assert row["source_key"] == row["layer"] + "/" + p["GEOID"]
                assert row["source_key"] not in all_keys
                all_keys.add(row["source_key"])
                geometry = shape(json.loads(row["geometry"]))
                minx, miny, maxx, maxy = geometry.bounds
                assert row["minx"] <= minx <= maxx <= row["maxx"]
                assert row["miny"] <= miny <= maxy <= row["maxy"]
                if not geometry.is_valid:
                    invalid.append(row["source_key"])
                actual[row["layer"]] += 1
                classes[item["layer"] + "/" + p.get("CLASSFP", "not_in_state_layout")] += 1
                functions[item["layer"] + "/" + p["FUNCSTAT"]] += 1
                types[census.entity_type(row["layer"], p)] += 1
                count += 1
            assert not native and count == info["features"]
            archive_results.append(
                {
                    "name": item["name"],
                    "url": item["url"],
                    "features": count,
                    "bytes": item["bytes"],
                    "uncompressed_bytes": item["uncompressed_bytes"],
                    "sha256": v["artifact_sha"],
                    "acquisition_id": v["acquisition_id"],
                }
            )
        assert actual == declared
        inv = Counter(
            {
                r["status"]: r["n"]
                for r in s.db.execute(
                    "SELECT status,count(*) n FROM inventories WHERE collection_id=? GROUP BY status",
                    (census.COLLECTION,),
                )
            }
        )
        assert inv == {"indexed": 93, "omitted_scope": 21}
        ct = [
            json.loads(r[0])
            for r in s.db.execute(
                "SELECT properties FROM features WHERE layer='COUNTY' AND json_extract(properties,'$.STATEFP')='09'"
            )
        ]
        assert len(ct) == 9 and all("Planning Region" in p["NAMELSAD"] for p in ct)
        source_receipt = next(
            r
            for r in acquisition_rows
            if r["url"].endswith("tl_2025_36_place.zip") and r["status"] == 200
        )
        replay = failure_resume(
            s, next(r for r in accepted if r["name"] == "tl_2025_36_place.zip"), source_receipt
        )
        return {
            "vintage": census.VINTAGE,
            "published_on": census.PUBLISHED,
            "counts": dict(actual),
            "total_features": actual.total(),
            "inventory": dict(inv),
            "classes": dict(sorted(classes.items())),
            "functional_status": dict(sorted(functions.items())),
            "entity_types": dict(types),
            "duplicate_keys": 0,
            "missing_rtree_rows": 0,
            "invalid_source_polygons": invalid,
            "native_properties_exact": True,
            "crs": "EPSG:4269 → EPSG:4326, always_xy=True; no invented legal boundary precision",
            "all114_archive_bytes": plan["all_archive_bytes"],
            "selected93_archive_bytes": plan["selected_archive_bytes"],
            "selected93_uncompressed_bytes": plan["selected_uncompressed_bytes"],
            "cumulative_consumed_bytes": census.census_bytes(s),
            "download_cap_bytes": census.CAP,
            "receipt_http_statuses": dict(statuses),
            "unique_objects_rehashed": len(objects),
            "unique_object_bytes": sum(objects.values()),
            "acquisition_range": [
                min(r["observed_at"] for r in acquisition_rows),
                max(r["observed_at"] for r in acquisition_rows),
            ],
            "source_evidence": plan["source_evidence"],
            "archives": archive_results,
            "connecticut_county_equivalents": [
                {k: p[k] for k in ("GEOID", "NAME", "CLASSFP", "FUNCSTAT")} for p in ct
            ],
            "failure_resume": replay,
        }
    finally:
        s.close()


async def mcp_proof(data: Path) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()

        async def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            result = await session.call_tool(tool, arguments)
            assert not result.isError, result
            content = result.structuredContent
            assert isinstance(content, dict)
            encoded = json.dumps(content, ensure_ascii=False).encode()
            calls.append(
                {
                    "tool": tool,
                    "arguments": arguments,
                    "seconds": round(time.perf_counter() - started, 6),
                    "json_bytes": len(encoded),
                    "response_sha256": digest(encoded),
                }
            )
            return content

        points = {}
        for name, lon, lat, city, state in [
            ("nyc", -73.9857, 40.7484, "3651000", "us-ny"),
            ("portland", -122.675, 45.52, "4159000", "us-or"),
            ("hartford", -72.685, 41.7658, "0937000", "us-ct"),
            ("honolulu", -157.8583, 21.3069, "1571550", "us-hi"),
            ("baltimore", -76.6122, 39.2904, "2404000", "us-md"),
        ]:
            r = await call("legal_sources_at", {"longitude": lon, "latitude": lat})
            assert len(json.dumps(r).encode()) < 24576
            assert any(m["properties"]["GEOID"] == city for m in r["matches"])
            assert any(m["legal_sources"].get("jurisdiction") == state for m in r["matches"])
            assert not r["geometry_coverage"]["missing_point_scope_archives"]
            assert {c["id"] for c in r["federal_reference_sources"]["collections"]} >= {
                "uscode",
                "ecfr",
            }
            points[name] = {
                "entities": [
                    {
                        "layer": m["layer"],
                        "GEOID": m["properties"]["GEOID"],
                        "name": m["properties"]["NAME"],
                        "type": m["entity_type"],
                        "route_status": m["legal_sources"]["status"],
                        "collections": [c["id"] for c in m["legal_sources"]["collections"]],
                    }
                    for m in r["matches"]
                ],
                "geometry_coverage": r["geometry_coverage"],
            }
            if name not in {"nyc", "portland"}:
                continue
            collection = "nyc-zoning" if name == "nyc" else "portland-zoning"
            city_match = next(m for m in r["matches"] if m["properties"]["GEOID"] == city)
            assert collection in {c["id"] for c in city_match["legal_sources"]["collections"]}
            docs = await call(
                "legal_coverage", {"view": "documents", "collection": collection, "limit": 1}
            )
            text = await call(
                "legal_read", {"key_or_id": docs["documents"][0]["first_key"], "length": 3000}
            )
            assert text["text"]
            await call("source_receipt", {"acquisition_id": text["acquisition_id"]})
            zones = await call("zoning_at", {"longitude": lon, "latitude": lat})
            assert zones["matches"] and all(
                m["collection_id"].endswith("zoning-gis") for m in zones["matches"]
            )
            scoped = await call(
                "zoning_at", {"longitude": lon, "latitude": lat, "collection": collection + "-gis"}
            )
            assert scoped["matches"] == zones["matches"]
            points[name]["separate_zoning_match_ids"] = [m["id"] for m in zones["matches"]]
        # A real retained polygon vertex, not a rounded/geocoded boundary guess.
        source_store = Store(data, readonly=True)
        try:
            native_geometry = shape(
                json.loads(
                    source_store.db.execute(
                        "SELECT geometry FROM features WHERE source_key='PLACE/3651000'"
                    ).fetchone()[0]
                )
            )
            polygon = (
                native_geometry.geoms[0]
                if isinstance(native_geometry, MultiPolygon)
                else native_geometry
            )
            assert isinstance(polygon, Polygon)
            lon, lat = polygon.exterior.coords[0]
        finally:
            source_store.close()
        boundary = await call("legal_sources_at", {"longitude": lon, "latitude": lat})
        assert any(
            m["properties"]["GEOID"] == "3651000" and m["on_boundary"] for m in boundary["matches"]
        )
        points["nyc_exact_source_vertex"] = {
            "longitude": lon,
            "latitude": lat,
            "city_on_boundary": True,
            "match_geoids": [m["properties"]["GEOID"] for m in boundary["matches"]],
        }
        queens = await call("legal_sources_at", {"longitude": -73.7949, "latitude": 40.7282})
        assert {m["properties"]["GEOID"] for m in queens["matches"]} >= {"3651000", "36081"}
        points["nyc_second_county"] = {"same_city_geoid": "3651000", "county_geoid": "36081"}
        negatives: list[dict[str, Any]] = [
            {"longitude": -40, "latitude": 30},
            {"longitude": -73.9857, "latitude": 40.7484, "geometry_as_of": "2024-12-31"},
            {
                "longitude": -73.9857,
                "latitude": 40.7484,
                "observation_cutoff": "2026-01-01T00:00:00Z",
            },
        ]
        for args in negatives:
            assert not (await call("legal_sources_at", args))["matches"]
    return {
        "calls": calls,
        "points": points,
        "call_count": len(calls),
        "total_seconds": round(sum(c["seconds"] for c in calls), 6),
        "json_bytes": sum(c["json_bytes"] for c in calls),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    report: dict[str, Any] = {
        "created_at": utc_now(),
        "command": ".venv/bin/python scripts/verify_census.py --data data --output docs/geographic-discovery-verification.json",
        "python": platform.python_version(),
        "dependencies": {
            p: importlib.metadata.version(p)
            for p in ("httpx", "pyogrio", "pyproj", "shapely", "mcp")
        },
        "data": verify(args.data),
        "mcp": asyncio.run(mcp_proof(args.data)),
    }
    report["seconds"] = round(time.perf_counter() - started, 6)
    repo = Path(__file__).resolve().parents[1]
    report["runtime_hashes"] = {
        name: digest((repo / name).read_bytes())
        for name in (
            "src/psephos/census.py",
            "src/psephos/acquire.py",
            "src/psephos/geography.py",
            "src/psephos/server.py",
            "src/psephos/cli.py",
            "src/psephos/sources.py",
            "scripts/verify_census.py",
        )
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json_text(
            {
                "output": args.output.name,
                "seconds": report["seconds"],
                "features": report["data"]["total_features"],
                "mcp_calls": report["mcp"]["call_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
