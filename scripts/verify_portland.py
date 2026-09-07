"""One offline Portland inventory/text-preservation and real MCP reading proof."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.portland_code import BASE, CAP, COLLECTION, FILE_CAP, members, title_list, title_units
from psephos.store import Store, digest, json_text, utc_now


def verify(data: Path) -> dict[str, Any]:
    s = Store(data, readonly=True)
    plan = json.loads((data / "portland-code/plan.json").read_bytes())
    objects: dict[str, int] = {}

    def retained(receipt_id: int) -> tuple[Any, bytes]:
        row = s.db.execute("SELECT * FROM acquisitions WHERE id=?", (receipt_id,)).fetchone()
        assert row["status"] == 200 and row["sha256"] and not row["error"]
        raw = s.artifact(row["sha256"])
        objects[row["sha256"]] = len(raw)
        return row, raw

    try:
        _, root = retained(plan["root_acquisition_id"])
        assert title_list(root) == [
            {k: t[k] for k in ("number", "url", "label")} for t in plan["titles"]
        ]
        assert all(
            t["status"] in {"indexed", "reuse_retained_zoning_collection"} for t in plan["titles"]
        )
        totals: Counter[str] = Counter()
        titles = []
        media: Counter[str] = Counter()
        for title in plan["titles"]:
            if title["number"] == "33":
                continue
            membership = {}
            for url, receipt_id in title["membership_acquisitions"].items():
                receipt, raw = retained(receipt_id)
                assert receipt["url"] == url
                membership[url] = members(raw, url)
            assert membership == title["membership"]
            receipt, raw = retained(title["export_acquisition_id"])
            parsed = title_units(raw, title, membership)
            v = s.db.execute(
                "SELECT * FROM versions WHERE acquisition_id=? AND parser='portland-code-1/text-3'",
                (receipt["id"],),
            ).fetchone()
            assert v and v["artifact_sha"] == receipt["sha256"]
            assert v["snapshot_date"] is None and v["effective_on"] is None
            saved = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (v["id"],)
            ).fetchall()
            assert len(saved) == sum(title["counts"].values())
            projected = [p for p in saved if p["unit_kind"] != "changes_context"]
            for p, unit in zip(projected, parsed, strict=True):
                for field in (
                    "key",
                    "parent_key",
                    "unit_kind",
                    "citation",
                    "heading",
                    "text",
                    "markup",
                    "url",
                ):
                    assert p[field] == getattr(unit, field), (p["key"], field)
                assert json.loads(p["metadata"]) == unit.metadata
            counts = Counter(p["unit_kind"] for p in saved)
            assert dict(counts) == title["counts"]
            totals.update(counts)
            for outcome in title["media_outcomes"]:
                media[outcome["status"]] += 1
                if outcome.get("acquisition_id"):
                    retained(outcome["acquisition_id"])
            titles.append(
                {
                    "title": title["number"],
                    "counts": dict(counts),
                    "url": receipt["url"],
                    "acquisition_id": receipt["id"],
                    "sha256": receipt["sha256"],
                    "bytes": len(raw),
                    "observed_at": receipt["observed_at"],
                    "native_members": sum(map(len, membership.values())),
                }
            )
        old = s.db.execute(
            "SELECT v.id,v.artifact_sha,v.acquisition_id,v.snapshot_date,count(p.id) AS units FROM versions v JOIN provisions p ON p.version_id=v.id WHERE v.document_id='portland:title-33' GROUP BY v.id"
        ).fetchall()
        assert (
            len(old) == 1
            and old[0]["artifact_sha"]
            == "86ba1da866c9cc1acf56df185abfddff784c23b8fbd4601fbb1c0337abb0f354"
        )
        acquisitions = [
            dict(r)
            for r in s.db.execute(
                "SELECT a.*,b.bytes FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 WHERE a.id>=? AND a.url LIKE ? ORDER BY a.id",
                (plan["first_acquisition_id"], BASE + "/%"),
            )
        ]
        for row in acquisitions:
            if row["sha256"]:
                retained(row["id"])
        downloaded = sum(
            int(
                json.loads(r["headers"]).get(
                    "psephos_downloaded_bytes", r["bytes"] if r["status"] == 200 else 0
                )
                or 0
            )
            for r in acquisitions
        )
        assert downloaded <= CAP and all(r["bytes"] <= FILE_CAP for r in acquisitions if r["bytes"])
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "publisher_titles": len(plan["titles"]),
            "new_printable_titles": len(titles),
            "omitted_listed_titles": [],
            "title_8": "Not listed by publisher; not a guessed missing title",
            "new_unit_kinds": dict(totals),
            "titles": titles,
            "reused_title33": dict(old[0]),
            "membership_and_independent_chapter_body_comparison": "PASS for every new section",
            "media_outcomes": dict(media),
            "root_acquisition_id": plan["root_acquisition_id"],
            "terms_acquisition_id": plan["terms_acquisition_id"],
            "acquisition": {
                "request_statuses": dict(Counter(str(r["status"]) for r in acquisitions)),
                "downloaded_payload_bytes": downloaded,
                "cap_bytes": CAP,
                "file_cap_bytes": FILE_CAP,
                "minimum_host_seconds": 2.1,
                "first_observed_at": acquisitions[0]["observed_at"],
                "last_observed_at": acquisitions[-1]["observed_at"],
                "errors": [
                    {k: r[k] for k in ("id", "url", "status", "error")}
                    for r in acquisitions
                    if r["error"]
                ],
            },
            "integrity": {
                "rehashed_objects": len(objects),
                "rehashed_bytes": sum(objects.values()),
                "manifest_sha256": digest(json_text(objects).encode()),
            },
            "limits": [
                "Current publisher display; exact snapshot/effectiveness unknown for HTML",
                "Title 33 reused at July 1, 2026, not refreshed",
                "No certified comprehensive city-law claim; charter/policies/model codes excluded",
                "Images retained where available, not transcribed; source figure-label disagreements preserved",
                "No invented Title 33 section-to-PDF-page map",
            ],
        }
    finally:
        s.close()


async def mcp_proof(data: Path) -> dict[str, Any]:
    calls = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            start = time.monotonic()
            response = await session.call_tool(tool, args)
            assert not response.isError
            result = response.structuredContent or {}
            raw = json_text(result).encode()
            calls.append(
                {
                    "tool": tool,
                    "arguments": args,
                    "seconds": round(time.monotonic() - start, 6),
                    "bytes": len(raw),
                    "sha256": digest(raw),
                }
            )
            return result

        coverage = await call("legal_coverage", collection=COLLECTION)
        assert coverage["collections"][0]["documents"] == 33
        for path, query in (
            ("permit", "Application for Permits"),
            ("tree", "Tree Permit"),
            ("erosion", "Erosion"),
            ("public_improvements", "Local Improvement"),
            ("housing", "Housing"),
        ):
            search = await call("legal_search", query=query, collection=COLLECTION, limit=2)
            assert search["matches"], path
            key = search["matches"][0]["key"]
            read = await call("legal_read", key_or_id=key, length=1800)
            assert read["found"] and read["snapshot_date"] is None
            receipt = await call("source_receipt", acquisition_id=read["acquisition_id"])
            assert receipt["sha256"] == read["artifact_sha"]
        figures = await call("legal_read", key_or_id="PCC 24.70.090", length=6500)
        assert figures["text_completeness"] == "incomplete_without_source_media"
        assert any(m.get("acquired_receipt") for m in figures["media"])
        assert figures["metadata"]["media_warning"]
        assert not (await call("legal_read", key_or_id=figures["id"], as_of="2099-01-01"))["found"]
        assert not (
            await call(
                "legal_read", key_or_id=figures["id"], observation_cutoff="2026-09-06T00:00:00Z"
            )
        )["found"]
        for key, target, resolves in (
            ("pdx-code:20/12/265", "ors:166.155", True),
            ("pdx-code:7/07/035", "PCC 7.02.500", True),
            ("pdx-code:32/34/020", "PCC 33.420", False),
        ):
            source = await call("legal_read", key_or_id=key, length=1000)
            refs = await call("legal_references", provision_id=source["id"], limit=100)
            ref = next(r for r in refs["references"] if r["target"] == target)
            assert bool(ref["acquired_targets"]) == resolves
            if not resolves:
                assert ref["navigation"]["collection"] == "portland-zoning"
        assert (await call("legal_read", key_or_id="ORS 166.155", length=1500))["found"]
        discovery = await call("legal_sources_at", longitude=-122.65, latitude=45.52)
        assert COLLECTION in json_text(discovery)
    return {
        "calls": calls,
        "scope": "One offline real stdio MCP route proof, not a benchmark or legal applicability opinion",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.data)
    report["mcp"] = asyncio.run(mcp_proof(args.data))
    report["runtime_sha256"] = {
        name: digest(Path(name).read_bytes())
        for name in (
            "src/psephos/portland_code.py",
            "src/psephos/retrieve.py",
            "scripts/verify_portland.py",
        )
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "titles": report["new_printable_titles"],
                "unit_kinds": report["new_unit_kinds"],
            }
        )
    )


if __name__ == "__main__":
    main()
