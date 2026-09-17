"""Offline New York PDF replay, original-record preservation and real MCP checks."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_washington import compare_units

from psephos.new_york import COLLECTION, VOLUMES, parse_pdf, volume_identity
from psephos.store import Store, json_text, utc_now


def without(row, *keys):
    return {k: row[k] for k in row.keys() if k not in keys}


def verify(data):
    s = Store(data, readonly=True)
    original = Store(data / "collectors/ny-northeast/store", readonly=True)
    try:
        s.db.execute("BEGIN")
        old_versions = {r["id"]: r for r in original.db.execute("SELECT * FROM versions")}
        old_records = 0
        for old in old_versions.values():
            current = s.db.execute("SELECT * FROM versions WHERE id=?", (old["id"],)).fetchone()
            assert current and without(old, "acquisition_id") == without(current, "acquisition_id")
            source = original.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (old["acquisition_id"],)
            ).fetchone()
            retained = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (current["acquisition_id"],)
            ).fetchone()
            assert without(source, "id") == without(retained, "id")
            before = original.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (old["id"],)
            ).fetchall()
            after = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (old["id"],)
            ).fetchall()
            assert len(before) == len(after)
            for left, right in zip(before, after, strict=True):
                assert without(left, "rowid") == without(right, "rowid")
            old_records += len(before)
        receipts = 0
        for old in original.db.execute("SELECT * FROM acquisitions"):
            matches = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND observed_at=? AND sha256 IS ?",
                (old["url"], old["observed_at"], old["sha256"]),
            ).fetchall()
            assert any(without(old, "id") == without(r, "id") for r in matches)
            receipts += 1
        assert original.db.execute("SELECT count(*) FROM legal_references").fetchone()[0] == 0
        rows = s.db.execute(
            "SELECT v.*,d.url,d.title FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=? ORDER BY d.id",
            (COLLECTION,),
        ).fetchall()
        added, page_count, empty, raw_bytes = [], 0, 0, 0
        titles = dict(VOLUMES)
        for row in rows:
            code = row["document_id"].split(":")[1]
            assert titles[code] == row["title"]
            raw_bytes += len(s.artifact(row["artifact_sha"]))
            stored = s.db.execute(
                "SELECT text FROM provisions WHERE version_id=? ORDER BY ordinal", (row["id"],)
            ).fetchall()
            identity = volume_identity([r["text"] for r in stored], row["title"])
            meta = json.loads(row["metadata"])
            assert len(stored) + len(meta["empty_pages_not_indexed"]) == meta["pdf_pages"]
            page_count += len(stored)
            empty += len(meta["empty_pages_not_indexed"])
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
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (row["acquisition_id"],)
            ).fetchone()
            assert receipt["status"] == 200 and receipt["error"] is None
            assert receipt["sha256"] == row["artifact_sha"] and receipt["url"] == row["url"]
            assert row["available_at"] == receipt["observed_at"]
            if row["id"] not in old_versions:
                units, parsed = parse_pdf(
                    s.object_path(row["artifact_sha"]), code, row["title"], row["url"]
                )
                compare_units(s, row["id"], units)
                assert parsed["pdf_pages"] == meta["pdf_pages"]
                assert parsed["empty_pages_not_indexed"] == meta["empty_pages_not_indexed"]
                added.append(
                    {
                        "law_id": code,
                        "title": row["title"],
                        "artifact_sha256": row["artifact_sha"],
                        "acquisition_id": row["acquisition_id"],
                        "pages": len(units),
                        "identity": identity,
                    }
                )
        inventory = [
            dict(r)
            for r in s.db.execute(
                "SELECT * FROM inventories WHERE collection_id=? ORDER BY item", (COLLECTION,)
            )
        ]
        for entry in inventory:
            if entry["status"] == "source_unavailable":
                receipt = s.db.execute(
                    "SELECT * FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
                    (entry["url"],),
                ).fetchone()
                assert receipt["status"] == 404 and "retry-after" not in json.loads(
                    receipt["headers"]
                )
            elif entry["status"] == "ingested":
                assert any(row["document_id"] == "ny:" + entry["item"] for row in rows)
            else:
                assert entry["status"] == "unverified_candidate"
        repair = json.loads(
            (data / "collectors/ny-northeast/identity-repair-20260917.json").read_bytes()
        )
        archive = Path(repair["withdrawn_projection_archive"])
        assert (
            hashlib.sha256(archive.read_bytes()).hexdigest()
            == repair["withdrawn_projection_archive_sha256"]
        )
        with gzip.open(archive, "rb") as stream:
            withdrawn = json.load(stream)
        assert not s.db.execute(
            "SELECT 1 FROM versions WHERE id=?", (repair["withdrawn_unpublished_projection"],)
        ).fetchone()
        corrected = s.db.execute(
            "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (repair["replacement"],)
        ).fetchall()
        assert len(corrected) == len(withdrawn["provisions"]) == 330
        for old, new in zip(withdrawn["provisions"], corrected, strict=True):
            assert old["text"] == new["text"] and old["markup"] == new["markup"]
            assert not s.db.execute(
                "SELECT 1 FROM provision_search WHERE rowid=? AND provision_search MATCH 'buildings'",
                (old["rowid"],),
            ).fetchone()
        budget = json.loads((data / "collectors/ny-northeast/http-budget.json").read_bytes())
        assert budget["cap_bytes"] == budget["cap"] == 180 * 1024**2
        assert not budget.get("reserved_bytes")
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "historical_preservation": {
                "versions": len(old_versions),
                "records": old_records,
                "receipts": receipts,
            },
            "retained_volumes": len(rows),
            "indexed_pages": page_count,
            "text_empty_pages_not_indexed": empty,
            "inventory_statuses": dict(Counter(r["status"] for r in inventory)),
            "statewide_inventory_complete": False,
            "selected_candidates": len(VOLUMES),
            "projection_replay": {
                "added_volumes": len(added),
                "added_pages": sum(r["pages"] for r in added),
                "volumes": added,
            },
            "identity_checks": len(rows),
            "artifacts_rehashed": len(rows),
            "bytes_rehashed": raw_bytes,
            "corrections": {
                k: repair[k]
                for k in (
                    "title_corrections",
                    "withdrawn_unpublished_projection",
                    "replacement",
                    "raw_artifact_preserved",
                    "acquisition_preserved",
                    "withdrawn_projection_archive_sha256",
                    "prior_alias_assertions",
                )
            },
            "budget": {
                "cap_bytes": budget["cap_bytes"],
                "charged_bytes": budget["newly_decoded_bytes"],
                "original_charged_bytes": 60637692,
                "reserve_bytes": 1024**2,
            },
            "limits": "Selected volumes, not a statewide inventory. PDF pages are not legal sections. Empty text pages remain in the raw PDFs, with no OCR or visual/table certification. Incorporation and legal-effective dates unknown; regulations, local laws and later session-law consolidation excluded. Prior API 401 and index 403 were not retried. No raw-corpus redistribution permission asserted.",
        }, withdrawn["provisions"][0]["id"]
    finally:
        original.close()
        s.close()


async def mcp(data, report, withdrawn_id):
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
        assert coverage["collections"][0]["documents"] == report["retained_volumes"]
        for status in ("source_unavailable", "unverified_candidate"):
            gaps = await call(
                "legal_coverage", view="inventory", collection=COLLECTION, status=status
            )
            assert gaps["total"] == report["inventory_statuses"][status]
        for code, title in (
            ("PBG", "Public Housing"),
            ("PBL", "Public Lands"),
            ("CAN", "Cannabis"),
        ):
            found = await call("legal_search", document="ny:" + code, query=title, limit=2)
            assert found["matches"] and all(
                m["citation"].startswith("NY " + title + " ") for m in found["matches"]
            )
            read = await call("legal_read", key_or_id=found["matches"][0]["id"], length=1000)
            assert read["document_title"] == title and read["unit_kind"] == "page"
            receipt = await call("source_receipt", acquisition_id=read["acquisition_id"])
            assert receipt["sha256"] == read["artifact_sha"]
        assert not (await call("legal_read", key_or_id=withdrawn_id))["found"]
        assert not (await call("legal_read", key_or_id="ny:PBG:page:1", as_of="2026-09-17"))[
            "found"
        ]
        assert not (
            await call(
                "legal_read", key_or_id="ny:PBG:page:1", observation_cutoff="2026-09-17T00:00:00Z"
            )
        )["found"]
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report, withdrawn_id = verify(args.data)
    report["mcp"] = asyncio.run(mcp(args.data, report, withdrawn_id))
    report["runtime_sha256"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in (
            "src/psephos/new_york.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "src/psephos/server.py",
            "scripts/verify_new_york.py",
        )
    }
    with args.out.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "status",
                    "historical_preservation",
                    "retained_volumes",
                    "indexed_pages",
                    "inventory_statuses",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
