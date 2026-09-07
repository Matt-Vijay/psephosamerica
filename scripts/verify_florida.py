"""Offline Florida edition membership, preservation, parser truth and MCP reading proof."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.acquire import Acquirer
from psephos.florida import (
    COLLECTION,
    chapter_inventory,
    chapter_units,
    edition_page,
    sync_florida,
    title_inventory,
)
from psephos.parse import readable
from psephos.store import Store, digest, json_text, utc_now


def verify_store(data: Path, *, resume: bool) -> dict[str, Any]:
    s = Store(data, readonly=not resume)
    plan = json.loads((data / "florida-edition/inventory-plan.json").read_bytes())
    before = json.loads((data / "florida-edition/before.json").read_bytes())
    report: dict[str, Any] = {}
    try:
        versions = [
            dict(r)
            for r in s.db.execute(
                "SELECT v.*,d.url FROM documents d JOIN versions v ON v.document_id=d.id "
                "WHERE d.collection_id=? ORDER BY v.id",
                (COLLECTION,),
            )
        ]
        by_document = {v["document_id"]: v for v in versions}
        assert len(versions) == len(by_document) == plan["chapter_count"] == 638
        assert len(plan["titles"]) == 49
        for old in before["versions"]:
            assert (
                dict(s.db.execute("SELECT * FROM versions WHERE id=?", (old["id"],)).fetchone())
                == old
            )
        old_ids = sorted(
            r[0]
            for v in before["versions"]
            for r in s.db.execute("SELECT id FROM provisions WHERE version_id=?", (v["id"],))
        )
        assert len(old_ids) == before["provision_ids_count"]
        assert digest(json.dumps(old_ids).encode()) == before["provision_ids_sha256"]
        meta = json.loads(
            s.db.execute("SELECT metadata FROM collections WHERE id=?", (COLLECTION,)).fetchone()[0]
        )
        old_meta = dict(meta)
        old_meta["discovery_review"] = old_meta.pop("historical_discovery_review")
        assert digest(json_text(old_meta).encode()) == before["collection_metadata_sha256"]
        assert meta["discovery_review"]["missing_chapter_count"] == 0
        report["historical_preservation"] = {
            "versions_unchanged": len(before["versions"]),
            "provision_ids_unchanged": len(old_ids),
            "provision_ids_sha256": before["provision_ids_sha256"],
            "original_collection_metadata_sha256": before["collection_metadata_sha256"],
            "policy": "Original review nested intact as historical_discovery_review; current scope is separate.",
        }
        objects: dict[str, int] = {}

        def retained(sha: str) -> bytes:
            raw = s.artifact(sha)
            objects[sha] = len(raw)
            assert (
                len(raw)
                == s.db.execute("SELECT bytes FROM artifacts WHERE sha256=?", (sha,)).fetchone()[0]
            )
            return raw

        receipts = {r["id"]: r for r in plan["receipts"]}
        for index_receipt in receipts.values():
            actual = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (index_receipt["id"],)
            ).fetchone()
            assert all(actual[k] == index_receipt[k] for k in ("url", "sha256", "observed_at"))
        root = plan["receipts"][0]
        assert title_inventory(retained(root["sha256"])) == [
            {k: t[k] for k in ("number", "url", "label")} for t in plan["titles"]
        ]
        section_count = index_count = duplicate_numbers = 0
        kinds: Counter[str] = Counter()
        truth: list[dict[str, Any]] = []
        for title in plan["titles"]:
            receipt = receipts[title["inventory_acquisition"]]
            assert (
                chapter_inventory(retained(receipt["sha256"]), title["number"]) == title["chapters"]
            )
            for chapter in title["chapters"]:
                v = by_document["fl:chapter/" + chapter["number"]]
                assert v["url"] == chapter["url"]
                assert all(v[k] is None for k in ("snapshot_date", "published_on", "effective_on"))
                acquired = s.db.execute(
                    "SELECT * FROM acquisitions WHERE id=?", (v["acquisition_id"],)
                ).fetchone()
                assert acquired["sha256"] == v["artifact_sha"] and acquired["url"] == v["url"]
                assert acquired["observed_at"] <= v["available_at"]
                raw = retained(v["artifact_sha"])
                page = edition_page(raw)
                body = Counter(
                    readable(n).strip()
                    for n in page.xpath(
                        '//div[@class="Chapter"]//div[@class="Section"]/span[@class="SectionNumber"]'
                    )
                )
                index = Counter(
                    readable(n).strip()
                    for n in page.xpath(
                        '//div[@class="Chapter"]//div[@class="CatchlineIndex"]//div[@class="IndexItem"]/span[@class="SectionNumber"]'
                    )
                )
                saved = s.db.execute(
                    "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (v["id"],)
                ).fetchall()
                stored = Counter(
                    json.loads(p["metadata"])["source_number"]
                    for p in saved
                    if p["unit_kind"] == "section"
                )
                assert body and body == index == stored, chapter["number"]
                assert len({p["key"] for p in saved}) == len(saved)
                assert all(p["url"] and p["text"] for p in saved)
                section_count += body.total()
                index_count += index.total()
                duplicate_numbers += sum(n > 1 for n in body.values())
                kinds.update(p["unit_kind"] for p in saved)
                if chapter["number"] not in {"14", "27", "83", "403", "560"}:
                    continue
                parsed = chapter_units(raw, chapter["number"], v["url"])
                assert len(parsed) == len(saved)
                for p, unit in zip(saved, parsed, strict=True):
                    for field in (
                        "key",
                        "citation",
                        "heading",
                        "text",
                        "markup",
                        "url",
                        "parent_key",
                        "unit_kind",
                    ):
                        assert p[field] == getattr(unit, field), (unit.key, field)
                    assert p["metadata"] == json_text(unit.metadata)
                    expected: dict[tuple[str, str, str], str] = {}
                    for ref in unit.references:
                        expected.setdefault((ref.target, ref.relation, ref.label), ref.evidence)
                    assert expected == {
                        tuple(r[:3]): r[3]
                        for r in s.db.execute(
                            "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                            (p["id"],),
                        )
                    }
                truth.append(
                    {
                        "chapter": chapter["number"],
                        "units": len(parsed),
                        "sha256": v["artifact_sha"],
                        "bytes": len(raw),
                        "url": v["url"],
                        "acquisition_id": v["acquisition_id"],
                    }
                )
        inventory = [
            dict(r)
            for r in s.db.execute(
                "SELECT status,count(*) AS items FROM inventories WHERE collection_id=? GROUP BY status",
                (COLLECTION,),
            )
        ]
        assert inventory == [{"status": "indexed", "items": 687}]
        report["edition"] = {
            "year": 2026,
            "titles": 49,
            "chapters": 638,
            "new_chapters": len(versions) - len(before["versions"]),
            "section_nodes": section_count,
            "index_entries": index_count,
            "unit_kinds": dict(kinds),
            "duplicated_native_numbers": duplicate_numbers,
            "inventory": inventory,
            "missing_chapters": 0,
            "parser_refusals_remaining": 0,
            "all_chapter_index_body_store_membership": "PASS",
            "exact_parser_truth": truth,
        }
        # Scoped to this acquisition mission, not an audit of the national catalog.
        acquisitions = [
            dict(r)
            for r in s.db.execute(
                "SELECT a.*,b.bytes FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 "
                "WHERE a.id>9962 AND a.url LIKE 'https://www.flsenate.gov/%' ORDER BY a.id"
            )
        ]
        for row in acquisitions:
            if row["sha256"]:
                retained(row["sha256"])
        unique_new = {
            r["sha256"]: r["bytes"] for r in acquisitions if r["status"] == 200 and r["sha256"]
        }
        old_objects = {
            r[0]
            for r in s.db.execute(
                "SELECT DISTINCT sha256 FROM acquisitions WHERE id<=9962 AND sha256 IS NOT NULL"
            )
        }
        report["acquisition"] = {
            "preflight_acquisition_floor": 9962,
            "request_statuses": dict(Counter(str(r["status"]) for r in acquisitions)),
            "successful_decoded_response_bytes": sum(
                r["bytes"] or 0 for r in acquisitions if r["status"] == 200
            ),
            "distinct_response_objects": len(unique_new),
            "distinct_response_bytes": sum(unique_new.values()),
            "new_object_bytes": sum(
                size for sha, size in unique_new.items() if sha not in old_objects
            ),
            "acquisition_rows_sha256": digest(json_text(acquisitions).encode()),
            "first_observed_at": acquisitions[0]["observed_at"],
            "last_observed_at": acquisitions[-1]["observed_at"],
            "errors": [
                {k: r[k] for k in ("id", "url", "status", "observed_at", "error")}
                for r in acquisitions
                if r["error"]
            ],
            "minimum_host_seconds": 10.1,
            "run_cap_bytes": 256 * 1024**2,
            "chapter_cap_bytes": 8 * 1024**2,
            "note": "Decoded retained bytes, not compressed network transfer size; cache reuse makes no new acquisition row.",
        }
        report["source_integrity"] = {
            "rehashed_objects": len(objects),
            "rehashed_bytes": sum(objects.values()),
            "object_manifest_sha256": digest(json_text(objects).encode()),
        }
        report["run_history"] = [
            {
                k: r[k]
                for k in (
                    "started_at",
                    "finished_at",
                    "new_versions",
                    "reused_versions",
                    "downloaded_bytes",
                    "rejections",
                )
            }
            for path in sorted((data / "florida-edition").glob("run-*.json"))
            for r in [json.loads(path.read_bytes())]
        ]
        if resume:
            floor = s.db.execute("SELECT max(id) FROM acquisitions").fetchone()[0]
            a = Acquirer(s)
            a.client.close()

            def forbidden(request: httpx.Request) -> httpx.Response:
                raise AssertionError("Offline resume attempted network: " + str(request.url))

            a.client = httpx.Client(transport=httpx.MockTransport(forbidden))
            try:
                repeat = sync_florida(s, a, None, None)
            finally:
                a.close()
            assert repeat["new_versions"] == repeat["downloaded_bytes"] == 0
            assert repeat["reused_versions"] == 638 and not repeat["rejections"]
            assert floor == s.db.execute("SELECT max(id) FROM acquisitions").fetchone()[0]
            assert versions == [
                dict(r)
                for r in s.db.execute(
                    "SELECT v.*,d.url FROM documents d JOIN versions v ON v.document_id=d.id WHERE d.collection_id=? ORDER BY v.id",
                    (COLLECTION,),
                )
            ]
            report["offline_resume"] = {
                "reused_versions": 638,
                "new_versions": 0,
                "new_acquisitions": 0,
                "downloaded_bytes": 0,
                "network_transport": "raises on every request",
            }
    finally:
        s.close()
    return report


async def verify_mcp(data: Path) -> dict[str, Any]:
    calls = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    start = time.monotonic()
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            began = time.monotonic()
            response = await session.call_tool(tool, args)
            assert not response.isError
            result = response.structuredContent or {}
            raw = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
            calls.append(
                {
                    "tool": tool,
                    "arguments": args,
                    "seconds": round(time.monotonic() - began, 6),
                    "bytes": len(raw),
                    "sha256": digest(raw),
                }
            )
            return result

        coverage = await call("legal_coverage", collection=COLLECTION)
        assert coverage["collections"][0]["metadata"]["discovery_review"]["chapters_indexed"] == 638
        landlord = await call("legal_read", key_or_id="Fla. Stat. §83.51 (2026)", length=1000)
        assert landlord["found"] and "RESIDENTIAL TENANCIES" in str(landlord["metadata"])
        found = await call(
            "legal_find", key_or_id=landlord["id"], query="single-family home or duplex", limit=1
        )
        assert found["matches"]
        await call(
            "legal_read",
            key_or_id=landlord["id"],
            offset=found["matches"][0]["read_offset"],
            length=1200,
        )
        definitions = await call("legal_read", key_or_id="Fla. Stat. §83.43 (2026)", length=3000)
        assert "unless some other meaning is plainly indicated" in definitions["text"]
        assert "s. 250.01" in definitions["text"]
        duty = await call("legal_read", key_or_id="Fla. Stat. §250.01 (2026)", length=1500)
        assert duty["found"] and "Active duty" in duty["text"]
        notice = await call("legal_read", key_or_id="Fla. Stat. §83.505 (2026)", length=1000)
        assert notice["found"] and "e-mail" in notice["text"].lower()
        permits = await call("legal_read", key_or_id="Fla. Stat. §403.087 (2026)", length=1500)
        assert "unless exempted by department rule" in permits["text"]
        business = await call("legal_read", key_or_id="Fla. Stat. §560.602 (2026)", length=2500)
        assert "Effective March 1, 2027" in business["text"] and "560.103" in business["text"]
        assert (await call("legal_read", key_or_id="Fla. Stat. §560.103 (2026)", length=1500))[
            "found"
        ]
        note = await call(
            "legal_find", key_or_id="Fla. Stat. §27.40 (2026)", query="July 1, 2027", limit=1
        )
        assert note["matches"]
        await call(
            "legal_read",
            key_or_id=note["id"],
            offset=note["matches"][0]["read_offset"],
            length=1500,
        )
        refs = await call("legal_references", provision_id=business["id"], limit=20)
        assert any(
            r["target"].endswith("#1") and not r["acquired_targets"] for r in refs["references"]
        )
        receipt = await call("source_receipt", acquisition_id=business["acquisition_id"])
        assert receipt["sha256"] == business["artifact_sha"]
        assert not (await call("legal_read", key_or_id=business["id"], as_of="2026-12-31"))["found"]
        assert not (
            await call(
                "legal_read", key_or_id=business["id"], observation_cutoff="2026-09-07T00:00:00Z"
            )
        )["found"]
        assert not (await call("legal_read", key_or_id="Fla. Stat. §560.602 (2025)"))["found"]
    return {
        "calls": calls,
        "seconds": round(time.monotonic() - start, 6),
        "scope": "One offline stdio run, not a latency distribution or legal applicability opinion. Printed citations followed explicitly; no prose-link inference.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--check-resume",
        action="store_true",
        help="One cache-only writer; run only after the acquisition writer exits",
    )
    args = parser.parse_args()
    report = verify_store(args.data, resume=args.check_resume)
    report["mcp"] = asyncio.run(verify_mcp(args.data))
    report.update(status="PASS", recorded_at=utc_now())
    report["software"] = {
        "python": platform.python_version(),
        **{name: importlib.metadata.version(name) for name in ("httpx", "lxml", "mcp")},
    }
    root = Path(__file__).resolve().parents[1]
    report["runtime_sha256"] = {
        name: digest((root / name).read_bytes())
        for name in (
            "src/psephos/florida.py",
            "src/psephos/retrieve.py",
            "scripts/verify_florida.py",
        )
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "chapters": report["edition"]["chapters"],
                "sections": report["edition"]["section_nodes"],
                "mcp_calls": len(report["mcp"]["calls"]),
            }
        )
    )


if __name__ == "__main__":
    main()
