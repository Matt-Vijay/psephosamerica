"""Replay retained NH exports, check preservation and exercise the real MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_washington import compare_units

from psephos.new_hampshire import COLLECTION, TOC, inventory, parse_chapter, title_notice
from psephos.store import Store, json_text, utc_now


def verify(data):
    s = Store(data, readonly=True)
    try:
        s.db.execute("BEGIN")
        baseline = json.loads(
            (data / "campaigns/new-hampshire-statutes/baseline-20260917.json").read_bytes()
        )
        old_ids = {entry["version"]["id"] for entry in baseline["canonical_versions"]}
        old_records = 0
        for entry in baseline["canonical_versions"]:
            version = entry["version"]
            assert (
                dict(s.db.execute("SELECT * FROM versions WHERE id=?", (version["id"],)).fetchone())
                == version
            )
            units = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version["id"],)
            ).fetchall()
            records = [{k: row[k] for k in row.keys() if k != "rowid"} for row in units]
            assert (
                hashlib.sha256(json_text(records).encode()).hexdigest()
                == entry["provisions_sha256"]
            )
            old_records += len(units)
        for original in baseline["receipts"]:
            assert (
                dict(
                    s.db.execute(
                        "SELECT * FROM acquisitions WHERE id=?", (original["id"],)
                    ).fetchone()
                )
                == original
            )
        index = s.db.execute(
            "SELECT * FROM acquisitions WHERE url=? AND status=200 ORDER BY id DESC LIMIT 1", (TOC,)
        ).fetchone()
        titles = inventory(s.artifact(index["sha256"]))
        title_status = dict(
            s.db.execute(
                "SELECT item,status FROM inventories WHERE collection_id=? AND item LIKE 'title:%'",
                (COLLECTION,),
            )
        )
        assert set(title_status) == {"title:" + title for title, _, _ in titles}
        expanded, chapter_rows = [], {}
        for title, _, url in titles:
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND status=200 ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            if not receipt:
                assert title_status["title:" + title] == "pending"
                continue
            raw = s.artifact(receipt["sha256"])
            if title_notice(raw, url, title) is not None:
                assert title_status["title:" + title] == "source_repeal_notice"
                expanded.append(
                    {
                        "title": title,
                        "chapters": 0,
                        "status": "source_repeal_notice",
                        "receipt": receipt["id"],
                        "artifact_sha256": receipt["sha256"],
                    }
                )
                continue
            chapters = inventory(raw, title)
            statuses = {}
            for chapter, _, _ in chapters:
                row = s.db.execute(
                    "SELECT * FROM inventories WHERE collection_id=? AND item=?",
                    (COLLECTION, "chapter:" + chapter),
                ).fetchone()
                assert row, (title, chapter)
                statuses[chapter] = row["status"]
                assert chapter not in chapter_rows, (
                    "Chapter appears under multiple titles; review identity"
                )
                chapter_rows[chapter] = dict(row)
            closed = all(status == "acquired" for status in statuses.values())
            assert (title_status["title:" + title] == "complete") == closed
            expanded.append(
                {
                    "title": title,
                    "chapters": len(chapters),
                    "statuses": dict(Counter(statuses.values())),
                    "receipt": receipt["id"],
                    "artifact_sha256": receipt["sha256"],
                }
            )
        rows = s.db.execute(
            "SELECT v.*,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=? ORDER BY v.id",
            (COLLECTION,),
        ).fetchall()
        assert len(rows) == len({r["document_id"] for r in rows})
        metadata = [json.loads(row["metadata"]) for row in rows]
        assert {m["chapter"] for m in metadata if "chapter" in m} == {
            chapter for chapter, entry in chapter_rows.items() if entry["status"] == "acquired"
        }
        assert {m["title"] for m in metadata if m.get("scope") == "entire_title_repeal_notice"} == {
            key.removeprefix("title:")
            for key, status in title_status.items()
            if status == "source_repeal_notice"
        }
        units_count = new_count = sections = media = tables = notices = 0
        artifacts, manifest = {}, []
        for row in rows:
            meta = json.loads(row["metadata"])
            raw = s.artifact(row["artifact_sha"])
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (row["acquisition_id"],)
            ).fetchone()
            assert (
                receipt["sha256"] == row["artifact_sha"]
                and receipt["observed_at"] == row["available_at"]
            )
            assert receipt["status"] == 200 and receipt["error"] is None
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
            if meta.get("scope") == "entire_title_repeal_notice":
                unit = title_notice(raw, row["url"], meta["title"])
                assert unit is not None
                units = [unit]
                notices += 1
            else:
                assert chapter_rows[meta["chapter"]]["status"] == "acquired"
                units = parse_chapter(raw, row["url"], meta["title"], meta["chapter"])
            compare_units(s, row["id"], units)
            units_count += len(units)
            if row["id"] not in old_ids:
                new_count += len(units)
                sections += sum(u.unit_kind == "section" for u in units)
                media += sum(len(u.metadata.get("media", [])) for u in units)
                tables += sum(u.metadata.get("tables", 0) for u in units)
            artifacts[row["artifact_sha"]] = len(raw)
            manifest.append(
                {
                    "version": row["id"],
                    "sha256": row["artifact_sha"],
                    "receipt": receipt["id"],
                    "units": len(units),
                }
            )
        receipts = s.db.execute(
            "SELECT a.*,b.bytes FROM acquisitions a LEFT JOIN artifacts b ON b.sha256=a.sha256 WHERE a.url LIKE ? ORDER BY a.id",
            ("https://gc.nh.gov/%",),
        ).fetchall()
        old_receipt_ids = {r["id"] for r in baseline["receipts"]}
        new_receipts = [r for r in receipts if r["id"] not in old_receipt_ids]
        starts = [
            datetime.fromisoformat(json.loads(r["headers"])["psephos_request_started_at"])
            for r in new_receipts
        ]
        spacing = (
            min((b - a).total_seconds() for a, b in zip(starts, starts[1:], strict=False))
            if len(starts) > 1
            else None
        )
        assert spacing is None or spacing >= 11
        for receipt in new_receipts:
            assert receipt["status"] == 200 and receipt["error"] is None
            s.artifact(receipt["sha256"])
        budget = json.loads((data / "collectors/nh-northeast/budget.json").read_bytes())
        assert budget["cap_bytes"] == baseline["original_budget"]["cap_bytes"]
        assert not budget.get("reserved_bytes") and not budget.get("uncertain_reserved_bytes")
        downloaded = (
            budget["newly_decoded_bytes"] - baseline["original_budget"]["newly_decoded_bytes"]
        )
        assert downloaded == sum(r["bytes"] for r in new_receipts)
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "current_law_complete": False,
            "inventory": {
                "titles": len(titles),
                "title_statuses": dict(Counter(title_status.values())),
                "expanded_titles": expanded,
                "known_chapters": len(chapter_rows),
                "chapter_statuses": dict(Counter(r["status"] for r in chapter_rows.values())),
                "statewide_chapter_denominator": None
                if len(expanded) < len(titles)
                else len(chapter_rows),
            },
            "historical_preservation": {
                "versions": len(old_ids),
                "records": old_records,
                "receipts": len(old_receipt_ids),
            },
            "projection_replay": {
                "all_documents": len(rows),
                "all_chapters": len(rows) - notices,
                "title_notices": notices,
                "all_records": units_count,
                "new_chapters": len(rows) - notices - len(old_ids),
                "new_records": new_count,
                "new_section_occurrences": sections,
                "new_tables": tables,
                "new_media_locators": media,
            },
            "raw_artifacts": {"objects": len(artifacts), "bytes_rehashed": sum(artifacts.values())},
            "manifest_sha256": hashlib.sha256(json_text(manifest).encode()).hexdigest(),
            "budget": {**budget, "downloaded_this_pass": downloaded},
            "new_request_spacing_minimum_seconds": spacing,
            "limits": "Only enumerated retained chapter exports, not complete/current statewide law. Counts include scope headings, repeals and prospective variants. The publisher month/session statement is not an exact snapshot or effect date. Unknown legal clocks remain unknown. Media locators and tables are preserved, not certified visual transcriptions. No blanket source-corpus redistribution license asserted.",
        }
    finally:
        s.close()


async def mcp(data, report):
    calls = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()

        async def call(name, **args):
            result = await session.call_tool(name, args)
            assert not result.isError and result.structuredContent is not None
            value = result.structuredContent
            calls.append(
                {
                    "tool": name,
                    "arguments": args,
                    "response_sha256": hashlib.sha256(json_text(value).encode()).hexdigest(),
                }
            )
            return value

        coverage = await call("legal_coverage", collection=COLLECTION)
        assert (
            coverage["collections"][0]["documents"] == report["projection_replay"]["all_documents"]
        )
        found = await call(
            "legal_search", query="artificial intelligence", document="nh-rsa:chapter-5-D", limit=5
        )
        assert found["matches"]
        sample = await call(
            "legal_read", key_or_id="nh-rsa:5-D:1", length=10000, include_markup=True
        )
        assert sample["found"] and sample["unit_kind"] == "section" and "5-D:1" in sample["text"]
        receipt = await call("source_receipt", acquisition_id=sample["acquisition_id"])
        assert receipt["sha256"] == sample["artifact_sha"]
        local = await call("legal_read", key_or_id="nh-rsa:31:39", length=10000)
        assert local["found"] and local["unit_kind"] == "section" and "31:39" in local["text"]
        original = await call("legal_read", key_or_id="nh-rsa:672:14", length=10000)
        assert original["found"] and "shall not be construed as a subdivision" in original["text"]
        notice = await call("legal_read", key_or_id="nh-rsa:title-IV/_notice", length=10000)
        assert notice["found"] and notice["unit_kind"] == "title_notice"
        assert "Entire Title was repealed" in notice["text"] and "196" not in notice["text"]
        for args in ({"as_of": "2026-09-17"}, {"observation_cutoff": "2026-09-17T00:00:00Z"}):
            assert not (await call("legal_read", key_or_id=sample["id"], **args))["found"]
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.data)
    report["mcp"] = asyncio.run(mcp(args.data, report))
    report["runtime_sha256"] = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (
            "src/psephos/new_hampshire.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "src/psephos/server.py",
            "scripts/verify_new_hampshire.py",
        )
    }
    with args.out.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "projection_replay", "historical_preservation", "budget")
            }
        )
    )


if __name__ == "__main__":
    main()
