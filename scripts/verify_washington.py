"""Offline Washington source integrity, exact parser truth, resume and MCP reading path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.acquire import Acquirer
from psephos.collect_washington import chapter_units
from psephos.store import Store, json_text, utc_now
from psephos.washington_rules import filing_unit, sync_washington_rules, wac_chapter


def compare_units(s: Store, version: str, units: list[Any]) -> None:
    actual = s.db.execute(
        "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version,)
    ).fetchall()
    assert len(actual) == len(units)
    for saved, unit in zip(actual, units, strict=True):
        for name in (
            "key",
            "citation",
            "heading",
            "text",
            "markup",
            "url",
            "parent_key",
            "unit_kind",
        ):
            assert saved[name] == getattr(unit, name), (unit.key, name)
        assert saved["metadata"] == json_text(unit.metadata), unit.key
        refs: dict[tuple[str, str, str], str] = {}
        for ref in unit.references:
            refs.setdefault((ref.target, ref.relation, ref.label), ref.evidence)
        assert refs == {
            tuple(r[:3]): r[3]
            for r in s.db.execute(
                "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                (saved["id"],),
            )
        }, unit.key


def verify_rcw(data: Path) -> dict[str, Any]:
    """Check maintained RCW projections without relabeling older text-2 versions."""
    s = Store(data, readonly=True)
    try:
        s.db.execute("BEGIN")
        rows = s.db.execute(
            "SELECT v.*,d.url,a.observed_at,a.sha256 FROM versions v "
            "JOIN documents d ON d.id=v.document_id "
            "JOIN acquisitions a ON a.id=v.acquisition_id "
            "WHERE d.collection_id='wa-rcw' AND v.parser='washington-full-chapter-html-v1/text-3' "
            "ORDER BY v.document_id"
        ).fetchall()
        assert rows, "No maintained RCW projections"
        artifacts: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for row in rows:
            metadata = json.loads(row["metadata"])
            raw = s.artifact(row["artifact_sha"])
            assert row["artifact_sha"] == row["sha256"]
            assert all(
                row[k] is None
                for k in (
                    "snapshot_date",
                    "published_on",
                    "effective_on",
                    "amended_on",
                    "repealed_on",
                )
            )
            units = chapter_units(raw, metadata["chapter"], row["url"])
            compare_units(s, row["id"], units)
            assert metadata["section_count"] == sum(p.unit_kind == "section" for p in units)
            inventory = s.db.execute(
                "SELECT status FROM inventories WHERE collection_id='wa-rcw' AND item=?",
                ("chapter:" + metadata["chapter"],),
            ).fetchone()
            assert inventory and inventory[0] == "ingested"
            artifacts[row["artifact_sha"]] = len(raw)
            for unit in units:
                kinds[unit.unit_kind] = kinds.get(unit.unit_kind, 0) + 1
        inventory = dict(
            s.db.execute(
                "SELECT status,count(*) FROM inventories WHERE collection_id='wa-rcw' "
                "AND item LIKE 'chapter:%' GROUP BY status"
            )
        )
        return {
            "exact_parser_truth": {"documents": len(rows), "unit_kinds": kinds},
            "retained_source_bytes": sum(artifacts.values()),
            "retained_source_objects": len(artifacts),
            "chapter_inventory": inventory,
            "source_manifest_sha256": hashlib.sha256(
                json_text(
                    [
                        {
                            k: r[k]
                            for k in (
                                "document_id",
                                "id",
                                "artifact_sha",
                                "acquisition_id",
                                "observed_at",
                            )
                        }
                        for r in rows
                    ]
                ).encode()
            ).hexdigest(),
            "observation_range": [
                min(r["observed_at"] for r in rows),
                max(r["observed_at"] for r in rows),
            ],
            "clocks": "Retrieval observations only; no inferred snapshot or legal effectiveness dates",
            "failures": [
                dict(r)
                for r in s.db.execute(
                    "SELECT item,url,error FROM inventories WHERE collection_id='wa-rcw' AND status='failed'"
                )
            ],
            "scope": "Maintained text-3 RCW projections; earlier text-2 and land-use evidence unchanged. Chapter inventory is not current or comprehensive law coverage.",
        }
    finally:
        s.close()


def verify_store(data: Path, *, resume: bool) -> dict[str, Any]:
    s = Store(data, readonly=not resume)
    run = json.loads((data / "washington-land-use/acquisition-run.json").read_bytes())
    report: dict[str, Any] = {}
    try:
        rows = s.db.execute(
            "SELECT v.*,d.collection_id,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE parser='washington-land-use-v1/text-3' ORDER BY v.id"
        ).fetchall()
        assert len(rows) == 18
        report["collections"] = [
            dict(r)
            for r in s.db.execute(
                "SELECT d.collection_id,count(DISTINCT d.id) AS documents,count(p.id) AS units FROM versions v JOIN documents d ON d.id=v.document_id JOIN provisions p ON p.version_id=v.id WHERE parser='washington-land-use-v1/text-3' GROUP BY d.collection_id"
            )
        ]
        report["unit_kinds"] = [
            dict(r)
            for r in s.db.execute(
                "SELECT d.collection_id,p.unit_kind,count(*) AS units FROM versions v JOIN documents d ON d.id=v.document_id JOIN provisions p ON p.version_id=v.id WHERE parser='washington-land-use-v1/text-3' GROUP BY d.collection_id,p.unit_kind"
            )
        ]
        report["wac_inventory"] = [
            dict(r)
            for r in s.db.execute(
                "SELECT status,count(*) AS items FROM inventories WHERE collection_id='wa-wac' GROUP BY status"
            )
        ]
        report["filings"] = [
            dict(r)
            for r in s.db.execute(
                "SELECT p.key,json_extract(p.metadata,'$.filing_type') AS type,json_extract(p.metadata,'$.filed_on') AS filed_on,json_extract(p.metadata,'$.deletion_spans') AS deletion_spans,json_extract(p.metadata,'$.underlined_spans') AS underlined_spans FROM provisions p WHERE p.key IN ('wa-wsr:25-13-090','wa-wsr:26-01-181','wa-wsr:26-10-066') ORDER BY p.key"
            )
        ]
        artifacts = {}
        for receipt in run["receipts"]:
            raw = s.artifact(receipt["sha256"])
            assert len(raw) == receipt["bytes"]
            stored = s.db.execute(
                "SELECT url,sha256,observed_at FROM acquisitions WHERE id=?", (receipt["id"],)
            ).fetchone()
            assert stored is not None and all(
                stored[k] == receipt[k] for k in ("url", "sha256", "observed_at")
            )
            artifacts[receipt["sha256"]] = len(raw)
        report["retained_source_objects"] = len(artifacts)
        report["retained_source_bytes"] = sum(artifacts.values())
        report["receipts"] = run["receipts"]
        report["new_acquisition_summary"] = [
            dict(r)
            for r in s.db.execute(
                "SELECT status,count(*) AS requests FROM acquisitions WHERE id>? GROUP BY status",
                (run["preflight_acquisition_floor"],),
            )
        ]
        report["new_retained_bytes"] = s.db.execute(
            "SELECT sum(bytes) FROM artifacts WHERE sha256 IN (SELECT sha256 FROM acquisitions WHERE id>? EXCEPT SELECT sha256 FROM acquisitions WHERE id<=?)",
            (run["preflight_acquisition_floor"], run["preflight_acquisition_floor"]),
        ).fetchone()[0]
        tested_documents = tested_units = 0
        for row in rows:
            raw = s.artifact(row["artifact_sha"])
            metadata = json.loads(row["metadata"])
            if row["collection_id"] == "wa-wac":
                units = wac_chapter(raw, metadata["chapter"], row["url"])
            elif row["collection_id"] == "wa-rcw":
                units = chapter_units(raw, "43.21C", row["url"])
            elif row["document_id"] in {"wa-wsr:25-13-090", "wa-wsr:26-01-181", "wa-wsr:26-10-066"}:
                units = [filing_unit(raw, row["document_id"].split(":")[1], row["url"])]
            else:
                continue
            compare_units(s, row["id"], units)
            tested_documents += 1
            tested_units += len(units)
        report["exact_parser_truth"] = {
            "documents": tested_documents,
            "units": tested_units,
            "fields": "identity/text/markup/metadata/hierarchy/first-write reference evidence",
        }
        for collection in ("florida-statutes-2026", "nj-statutes"):
            raw_metadata = s.db.execute(
                "SELECT metadata FROM collections WHERE id=?", (collection,)
            ).fetchone()[0]
            assert (
                hashlib.sha256(raw_metadata.encode()).hexdigest()
                == run["before_collection_metadata_sha256"][collection]
            )
        report["unrelated_annotations_unchanged"] = True
        if resume:
            before = [tuple(r) for r in rows]
            acquisitions_before = s.db.execute("SELECT max(id) FROM acquisitions").fetchone()[0]
            a = Acquirer(s)
            a.client.close()

            def denied(request: httpx.Request) -> httpx.Response:
                raise AssertionError("Offline resume attempted a network request")

            a.client = httpx.Client(transport=httpx.MockTransport(denied))
            try:
                repeat = sync_washington_rules(s, a, None, None)
            finally:
                a.close()
            after = [
                tuple(r)
                for r in s.db.execute(
                    "SELECT v.*,d.collection_id,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE parser='washington-land-use-v1/text-3' ORDER BY v.id"
                )
            ]
            assert before == after and repeat["new_documents"] == repeat["downloaded_this_run"] == 0
            assert (
                s.db.execute("SELECT max(id) FROM acquisitions").fetchone()[0]
                == acquisitions_before
            )
            report["offline_resume"] = {
                "new_documents": 0,
                "new_versions": 0,
                "new_acquisitions": 0,
                "downloaded_bytes": 0,
                "network_transport": "raises on every attempted request",
                "note": "Inventory checked_at may advance; source bytes and versions do not.",
            }
    finally:
        s.close()
    return report


async def verify_mcp(data: Path, *, rcw: bool = False) -> dict[str, Any]:
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
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
            return result

        if rcw:
            coverage = await call("legal_coverage", collection="wa-rcw")
            assert [c["id"] for c in coverage["collections"]] == ["wa-rcw"]
            found = await call("legal_search", query="supreme court", collection="wa-rcw", limit=3)
            assert found["matches"]
            # This chapter was absent before the completion run, not an old smoke fixture.
            statute = await call("legal_read", key_or_id="wa-rcw:2.04.010", length=1500)
            assert statute["found"] and "supreme court" in statute["text"].lower()
            receipt = await call("source_receipt", acquisition_id=statute["acquisition_id"])
            assert receipt["sha256"] == statute["artifact_sha"]
            contents = await call("legal_read", key_or_id="wa-rcw:2.04/contents-notes", length=500)
            refs = await call("legal_references", provision_id=contents["id"], limit=100)
            assert any(
                r["target"].endswith("&full=true#2.04.010")
                and any(t["id"] == statute["id"] for t in r["acquired_targets"])
                for r in refs["references"]
            )
            assert not (
                await call(
                    "legal_read", key_or_id=statute["id"], observation_cutoff="2026-09-17T00:00:00Z"
                )
            )["found"]
            assert not (await call("legal_read", key_or_id=statute["id"], as_of="2026-09-17"))[
                "found"
            ]
            return {"calls": calls, "seconds": round(time.monotonic() - start, 6)}

        coverage = await call("legal_coverage", jurisdiction="us-wa")
        assert {c["id"] for c in coverage["collections"]} >= {
            "wa-rcw",
            "wa-wac",
            "wa-wsr",
            "wa-rulemaking-notices",
        }
        matches = await call(
            "legal_search", query="Content of environmental review", collection="wa-wac", limit=3
        )
        assert any(m["key"] == "wa-wac:197-11-060" for m in matches["matches"])
        sepa = await call("legal_read", key_or_id="wa-wac:197-11-060", length=1000)
        found = await call(
            "legal_find", key_or_id=sepa["id"], query="Phased review is not appropriate", limit=1
        )
        assert found["matches"]
        await call(
            "legal_read",
            key_or_id=sepa["id"],
            offset=found["matches"][0]["match_offset"],
            length=1500,
        )
        refs = (await call("legal_references", provision_id=sepa["id"], limit=50))["references"]
        authority = next(r for r in refs if r["relation"] == "publisher_statutory_authority")
        assert authority["acquired_targets"][0]["key"] == "wa-rcw:43.21C.110"
        statute = await call(
            "legal_read", key_or_id=authority["acquired_targets"][0]["id"], length=1000
        )
        assert statute["found"]
        linked_rule = next(
            r["acquired_targets"][0]
            for r in refs
            if r["acquired_targets"] and r["acquired_targets"][0]["key"] == "wa-wac:197-11-330"
        )
        await call("legal_read", key_or_id=linked_rule["id"], length=1000)
        assert any(
            r["target"] == "wa-wsr:97-21-030"
            and not r["acquired_targets"]
            and r["resolution_status"] == "not_acquired_at_cutoffs"
            for r in refs
        )
        gma = await call("legal_read", key_or_id="wa-wac:365-195-900", length=1000)
        refs = (await call("legal_references", provision_id=gma["id"], limit=50))["references"]
        final_target = next(
            r["acquired_targets"][0] for r in refs if r["target"] == "wa-wsr:26-01-181"
        )
        final = await call("legal_read", key_or_id=final_target["id"], length=2500)
        assert "365-196-840 is not adopted" in final["text"]
        refs_page = await call("legal_references", provision_id=final["id"], limit=100)
        refs = refs_page["references"]
        if refs_page["next_offset"] is not None:
            refs.extend(
                (
                    await call(
                        "legal_references",
                        provision_id=final["id"],
                        limit=100,
                        offset=refs_page["next_offset"],
                    )
                )["references"]
            )
        proposal_target = next(
            r["acquired_targets"][0] for r in refs if r["target"] == "wa-wsr:25-13-090"
        )
        proposal = await call("legal_read", key_or_id=proposal_target["id"], length=1000)
        assert proposal["metadata"]["filing_type"] == "PROPOSED RULES"
        redline = await call("legal_find", key_or_id=proposal["id"], query="[DELETED]", limit=1)
        assert redline["matches"]
        table = await call("legal_read", key_or_id="wa-wsr:table:2025:365", length=500)
        assert "365-195-900\tAMD-P\t25-13-090" in table["text"]
        assert "365-195-900\tAMD\t26-01-181" in table["text"]
        for field in ("metadata", "version_metadata"):
            assert "rows" not in table[field] and table[field]["rows_count"] == 163
        await call("legal_read", key_or_id="wa-wsr:action-legend", length=1000)
        agency_notice = await call(
            "legal_find", key_or_id="wa-commerce:rulemaking-notice", query="June 6, 2026", limit=1
        )
        assert agency_notice["matches"]
        adjudication = await call("legal_read", key_or_id="wa-wsr:26-10-066", length=1000)
        assert "effective June 5, 2026" in adjudication["text"]
        receipt = await call("source_receipt", acquisition_id=adjudication["acquisition_id"])
        assert receipt["sha256"] == adjudication["artifact_sha"]
        assert not (
            await call(
                "legal_read", key_or_id=sepa["id"], observation_cutoff="2026-09-07T04:00:00Z"
            )
        )["found"]
        assert not (await call("legal_read", key_or_id=sepa["id"], as_of="2026-09-07"))["found"]
    return {
        "calls": calls,
        "seconds": round(time.monotonic() - start, 6),
        "scope": "One observed offline stdio run, not a latency distribution or proof of legal completeness",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--rcw-completion",
        action="store_true",
        help="Verify maintained RCW chapters, not the earlier land-use tranche",
    )
    parser.add_argument(
        "--check-resume",
        action="store_true",
        help="Run one cache-only writer; transport refuses every network request",
    )
    args = parser.parse_args()
    if args.rcw_completion and args.check_resume:
        parser.error("--check-resume only applies to the bounded land-use tranche")
    report = (
        verify_rcw(args.data)
        if args.rcw_completion
        else verify_store(args.data, resume=args.check_resume)
    )
    report["mcp"] = asyncio.run(verify_mcp(args.data, rcw=args.rcw_completion))
    report.update(status="PASS", recorded_at=utc_now())
    root = Path(__file__).resolve().parents[1]
    report["runtime_sha256"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "src/psephos/washington_rules.py",
            "src/psephos/collect_washington.py",
            "src/psephos/retrieve.py",
            "scripts/verify_washington.py",
        )
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": "PASS",
                "mcp_calls": len(report["mcp"]["calls"]),
                "mcp_seconds": report["mcp"]["seconds"],
                "source_bytes": report["retained_source_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
