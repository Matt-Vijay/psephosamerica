"""Offline Georgia edition/page/source reconciliation and real read-only MCP paths."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from complete_georgia import CAP, FILE_CAP, old_state
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pypdf import PdfReader

from psephos.georgia_rules import (
    COLLECTION,
    PARSER,
    inventory,
    layout_pages,
    outlines,
    pdf_projection,
)
from psephos.store import Store, digest, json_text, utc_now


def verify(data: Path) -> dict[str, Any]:
    s = Store(data, readonly=True)
    directory = data / "georgia-completion"
    plan = json.loads((directory / "plan.json").read_bytes())
    counts: Counter[str] = Counter()
    receipts = []
    images: dict[str, list[int]] = {}
    ocr: dict[str, list[int]] = {}
    media_only: dict[str, list[int]] = {}
    replay = []
    try:
        assert inventory(s.artifact(plan["index_sha256"])) == plan["items"]
        assert old_state(s, plan["old_state"]["version_ids"]) == plan["old_state"]
        for item in plan["items"]:
            v = s.db.execute(
                "SELECT v.*,a.url,a.observed_at,b.bytes FROM versions v "
                "JOIN acquisitions a ON a.id=v.acquisition_id JOIN artifacts b ON b.sha256=v.artifact_sha "
                "WHERE v.document_id=? AND v.snapshot_date=? AND v.parser=? ORDER BY v.rowid DESC LIMIT 1",
                (
                    "ga-rules:department-" + item["department"],
                    plan["target_snapshot"],
                    PARSER + "/text-3",
                ),
            ).fetchone()
            assert v is not None, "No complete target-edition projection for " + item["department"]
            raw = s.artifact(v["artifact_sha"])
            assert len(raw) == v["bytes"] and v["url"] == item["url"]
            path = s.object_path(v["artifact_sha"])
            reader = PdfReader(path)
            pages = layout_pages(path)
            meta = json.loads(v["metadata"])
            saved = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (v["id"],)
            ).fetchall()
            assert len(pages) == len(reader.pages) == len(saved) == meta["page_count"]
            assert meta["publisher_outlines"] == outlines(reader)
            assert meta["publisher_current_through"] == plan["target_snapshot"]
            assert all(
                v[k] is None for k in ("effective_on", "published_on", "amended_on", "repealed_on")
            )
            if item["department"] in {"20", "110"}:
                review_path = Path(__file__).resolve().parents[1] / "docs/georgia-media-review.json"
                units, _, projected_metadata = pdf_projection(
                    path,
                    item,
                    directory / "ocr",
                    json.loads(review_path.read_bytes())["media_only_pages"],
                )
                for row, unit in zip(saved, units, strict=True):
                    for key in (
                        "key",
                        "citation",
                        "heading",
                        "text",
                        "markup",
                        "url",
                        "parent_key",
                        "unit_kind",
                    ):
                        assert row[key] == getattr(unit, key), (item["department"], row["key"], key)
                    assert json.loads(row["metadata"]) == unit.metadata
                    refs = s.db.execute(
                        "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                        (row["id"],),
                    ).fetchall()
                    expected_refs: dict[tuple[str, str, str], str] = {}
                    for ref in unit.references:
                        # The shared catalog's key keeps the first source occurrence;
                        # every annotation remains separately preserved in page metadata.
                        expected_refs.setdefault(
                            (ref.target, ref.relation, ref.label), ref.evidence
                        )
                    assert {tuple(r) for r in refs} == {
                        (*key, evidence) for key, evidence in expected_refs.items()
                    }, (item["department"], row["key"], "reference identity")
                assert {
                    k: v for k, v in meta.items() if k != "inventory_acquisition_id"
                } == projected_metadata
                replay.append(
                    {
                        "department": item["department"],
                        "pages": len(units),
                        "status": "PASS",
                        "projection_sha256": digest(json_text([asdict(u) for u in units]).encode()),
                    }
                )
            for n, (text, p) in enumerate(zip(pages, saved, strict=True), 1):
                pm = json.loads(p["metadata"])
                assert p["key"] == f"ga-rules:department-{item['department']}:page-{n}"
                assert pm["physical_page"] == n and p["ordinal"] == n - 1
                if text.strip():
                    assert p["text"].startswith(text), (
                        item["department"],
                        n,
                        "native text changed",
                    )
                    assert pm["text_quality"] == "publisher_layout_text"
                    counts["publisher_text_pages"] += 1
                else:
                    assert pm["text_quality"] in {"machine_ocr_unverified", "source_media_only"}
                    assert pm["source_text_characters"] == 0 and pm["media"]
                    counts[pm["text_quality"] + "_pages"] += 1
                if pm["image_xobjects"]:
                    assert pm["media"][0]["source_sha256"] == v["artifact_sha"]
                    assert pm["media"][0]["physical_page"] == n
            if meta["machine_ocr_pages"]:
                ocr[item["department"]] = meta["machine_ocr_pages"]
            if meta["media_only_pages"]:
                media_only[item["department"]] = meta["media_only_pages"]
            if meta["image_pages"]:
                images[item["department"]] = meta["image_pages"]
            counts.update(
                departments=1,
                physical_pages=len(saved),
                rule_bookmarks=meta["rule_outline_count"],
                raw_pdf_bytes=len(raw),
            )
            receipts.append(
                {
                    "department": item["department"],
                    "pages": len(saved),
                    "version": v["id"],
                    "acquisition_id": v["acquisition_id"],
                    "sha256": v["artifact_sha"],
                    "bytes": len(raw),
                    "url": v["url"],
                    "observed_at": v["observed_at"],
                }
            )
            print(
                json_text({"verified_department": item["department"], "pages": len(saved)}),
                flush=True,
            )
        acquisitions = [
            dict(r)
            for r in s.db.execute(
                "SELECT * FROM acquisitions WHERE id>=? AND url LIKE 'https://rules.sos.ga.gov/%' ORDER BY id",
                (plan["first_acquisition_id"],),
            )
        ]
        exact_bytes = sum(
            int(json.loads(r["headers"]).get("psephos_downloaded_bytes", 0)) for r in acquisitions
        )
        budget = json.loads((directory / "budget.json").read_bytes())
        assert budget["consumed_bytes"] >= exact_bytes and budget["consumed_bytes"] <= CAP
        assert not budget.get("reserved_bytes")
        assert all(r["bytes"] <= FILE_CAP for r in receipts)
        full = {
            "department_receipts": receipts,
            "image_pages": images,
            "ocr_pages": ocr,
            "media_only_pages": media_only,
        }
        full_path = directory / "verified-inventory.json"
        full_path.write_text(json.dumps(full, indent=2) + "\n")
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "edition": plan["target_snapshot"],
            "scope": "All publisher-listed department PDFs at the same cover filing-through date. Not a certified complete current legal layer.",
            "counts": dict(counts),
            "missing_departments": [],
            "unrepresented_physical_pages": 0,
            "inventory_receipt": {"id": plan["index_receipt"], "sha256": plan["index_sha256"]},
            "all_page_order_native_text_and_bookmark_reconciliation": "PASS",
            "representative_exact_parser_replay": replay,
            "machine_ocr_pages": ocr,
            "media_only_pages": media_only,
            "image_page_count": sum(map(len, images.values())),
            "historical_preservation": plan["old_state"],
            "acquisition": {
                "budget_charge_bytes": budget["consumed_bytes"],
                "exact_receipted_payload_bytes": exact_bytes,
                "uncertain_reserved_bytes": budget.get("uncertain_reserved_bytes", 0),
                "cap_bytes": CAP,
                "per_file_cap_bytes": FILE_CAP,
                "minimum_host_seconds": 2.1,
                "prior_collector_consumed_bytes": plan["prior_collector_consumed_bytes"],
                "first_acquisition_id": plan["first_acquisition_id"],
                "last_acquisition_id": acquisitions[-1]["id"],
                "errors": [
                    {k: r[k] for k in ("id", "status", "error")} for r in acquisitions if r["error"]
                ],
            },
            "full_local_manifest": {
                "path": "data/georgia-completion/verified-inventory.json",
                "sha256": digest(full_path.read_bytes()),
            },
            "limits": [
                "OCR is unverified navigation, not legal wording or table-cell truth.",
                "Reviewed image-only diagrams are separately counted as media, not text.",
                "PDFs preserve table layout, forms, graphics and appendices; text extraction can misorder columns.",
                "Publisher filing-through dates are not publication/effective dates.",
                "Fastcase all-rights-reserved notices remain; no public corpus redistribution license asserted.",
                "Later filings, incorporated external standards, bulletins and statutes are outside this edition.",
            ],
        }
    finally:
        s.close()


async def mcp_proof(data: Path) -> list[dict[str, Any]]:
    records = []
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
            records.append(
                {
                    "tool": tool,
                    "arguments": args,
                    "seconds": round(time.monotonic() - start, 6),
                    "response_sha256": digest(json_text(result).encode()),
                }
            )
            return result

        coverage = await call("legal_coverage", collection=COLLECTION)
        assert coverage["collections"][0]["documents"] == 154
        for query in ("Water Well", "Wetlands", "Manufactured Homes"):
            result = await call("legal_search", collection=COLLECTION, query=query, limit=2)
            assert result["matches"]
            row = await call("legal_read", key_or_id=result["matches"][0]["key"], length=2500)
            assert row["found"] and row["snapshot_date"] == "2026-08-21"
            assert row["effective_on"] is None
            receipt = await call("source_receipt", acquisition_id=row["acquisition_id"])
            assert receipt["sha256"] == row["artifact_sha"]
        image = await call("legal_read", key_or_id="ga-rules:department-110:page-298", length=1600)
        assert image["media_count"] and image["metadata"]["text_quality"] == "source_media_only"
        assert image["media"][0]["acquired_receipt"]["sha256"] == image["artifact_sha"]
        ocr = await call("legal_read", key_or_id="ga-rules:department-40:page-842", length=1600)
        assert ocr["metadata"]["text_quality"] == "machine_ocr_unverified"
        assert not (await call("legal_read", key_or_id=image["id"], as_of="2026-08-20"))["found"]
        assert not (
            await call(
                "legal_read", key_or_id=image["id"], observation_cutoff="2026-09-07T19:00:00Z"
            )
        )["found"]
        older = await call(
            "legal_read", key_or_id="ga-rules:department-20:page-1", as_of="2026-08-14"
        )
        assert older["snapshot_date"] == "2026-08-14" and older["media_count"]
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.data)
    report["mcp_calls"] = asyncio.run(mcp_proof(args.data))
    # IDs remain in the local manifest; the committed receipt only needs the hash/count.
    old = report["historical_preservation"]
    old["version_count"] = len(old.pop("version_ids"))
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json_text({"status": report["status"], "counts": report["counts"]}))


if __name__ == "__main__":
    main()
