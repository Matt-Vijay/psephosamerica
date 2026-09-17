"""Replay Oklahoma's complete retained PDF catalog and verify actual MCP navigation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_washington import compare_units

from psephos.acquire import Receipt
from psephos.oklahoma import COLLECTION, INDEX, inventory_page, pdf_units, slug
from psephos.store import Store, json_text, utc_now


def verify(data):
    s = Store(data, readonly=True)
    original = Store(data / "collectors/oklahoma/store", readonly=True)
    try:
        s.db.execute("BEGIN")
        baseline = json.loads(
            (data / "campaigns/oklahoma-statutes/baseline-20260917.json").read_bytes()
        )
        old_ids = {r["version"]["id"] for r in baseline["canonical_versions"]}
        old_records = 0
        for entry in baseline["canonical_versions"]:
            old = entry["version"]
            row = s.db.execute("SELECT * FROM versions WHERE id=?", (old["id"],)).fetchone()
            assert dict(row) == old
            units = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (old["id"],)
            ).fetchall()
            records = [{k: r[k] for k in r.keys() if k != "rowid"} for r in units]
            assert (
                hashlib.sha256(json_text(records).encode()).hexdigest()
                == entry["provisions_sha256"]
            )
            old_records += len(units)
            for unit in units:
                query = "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=? ORDER BY target,relation,label"
                assert list(map(tuple, original.db.execute(query, (unit["id"],)))) == list(
                    map(tuple, s.db.execute(query, (unit["id"],)))
                )
        for old in original.db.execute("SELECT * FROM acquisitions"):
            row = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?",
                (baseline["receipt_id_map"][str(old["id"])],),
            ).fetchone()
            assert {k: old[k] for k in old.keys() if k != "id"} == {
                k: row[k] for k in row.keys() if k != "id"
            }
        queue, visited, entries, inventories = {0}, set(), {}, []
        while queue:
            page = min(queue)
            queue.remove(page)
            url = INDEX + (f"?page={page}" if page else "")
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            assert receipt
            items, linked = inventory_page(s.artifact(receipt["sha256"]), url)
            inventories.append(
                {"url": url, "acquisition_id": receipt["id"], "sha256": receipt["sha256"]}
            )
            visited.add(page)
            queue.update(linked - visited)
            entries.update({label: target for label, target in items})
        assert visited == set(range(max(visited) + 1))
        rows = s.db.execute(
            "SELECT * FROM inventories WHERE collection_id=?", (COLLECTION,)
        ).fetchall()
        assert {r["item"]: r["url"] for r in rows} == entries
        assert all(r["status"] == "complete_publisher_pdf_text" for r in rows)
        counts = Counter(slug(label) for label in entries)
        artifacts, manifest = {}, []
        new_pages = pages = media = links = 0
        for label, url in entries.items():
            key = slug(label)
            if counts[key] > 1:
                key += "/" + Path(url).stem
            versions = s.db.execute(
                "SELECT * FROM versions WHERE document_id=?", ("ok-senate:" + key,)
            ).fetchall()
            assert len(versions) == 1
            version = versions[0]
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (version["acquisition_id"],)
            ).fetchone()
            raw = s.artifact(version["artifact_sha"])
            assert receipt["url"] == url and receipt["sha256"] == version["artifact_sha"]
            assert receipt["status"] == 200 and receipt["error"] is None
            assert receipt["observed_at"] == version["available_at"]
            assert all(
                version[k] is None
                for k in (
                    "snapshot_date",
                    "published_on",
                    "effective_on",
                    "amended_on",
                    "repealed_on",
                )
            )
            artifacts[version["artifact_sha"]] = len(raw)
            n = s.db.execute(
                "SELECT count(*) FROM provisions WHERE version_id=?", (version["id"],)
            ).fetchone()[0]
            pages += n
            if version["id"] not in old_ids:
                rec = Receipt(
                    receipt["id"], url, receipt["sha256"], receipt["observed_at"], len(raw)
                )
                units = pdf_units(s, rec, label, key)
                compare_units(s, version["id"], units)
                assert len(units) == json.loads(version["metadata"])["pages"]
                new_pages += len(units)
                media += sum(u.metadata["image_count"] for u in units)
                links += sum(len(u.references) for u in units)
            manifest.append(
                {
                    "document": version["document_id"],
                    "version": version["id"],
                    "artifact_sha": version["artifact_sha"],
                    "acquisition_id": receipt["id"],
                    "pages": n,
                }
            )
        budget = json.loads((data / "collectors/oklahoma/http_ledger.json").read_bytes())
        assert budget["cap_bytes"] == budget["cap"] == baseline["original_cap_bytes"]
        assert (
            budget["charged_bytes"] - budget["decoded_bytes"]
            == baseline["old_uncertain_charge_bytes"]
        )
        assert not budget.get("reserved_bytes")
        duplicate = [m for m in manifest if m["document"].startswith("ok-senate:title-85/")]
        assert len(duplicate) == 2 and duplicate[0]["artifact_sha"] == duplicate[1]["artifact_sha"]
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "retained_publisher_catalog_closed": True,
            "current_law_complete": False,
            "inventory": {
                "pages": len(visited),
                "pdf_links": len(entries),
                "title_labeled_exports": sum(
                    v for k, v in counts.items() if k.startswith("title-")
                ),
                "article_companions": sum(v for k, v in counts.items() if k.startswith("article-")),
                "whole_constitution": counts["constitution"],
                "receipts": inventories,
            },
            "historical_preservation": {
                "versions": len(old_ids),
                "records": old_records,
                "receipts": len(baseline["receipt_id_map"]),
                "receipt_id_remapping": "Original import remapped receipt IDs, including nested inventory_receipt; source fields and current canonical rows preserved.",
            },
            "projection_replay": {
                "added_documents": len(entries) - len(old_ids),
                "added_pages": new_pages,
                "added_image_appearances": media,
                "added_publisher_links": links,
            },
            "retained_page_records": pages,
            "unique_pdf_artifacts": len(artifacts),
            "bytes_rehashed": sum(artifacts.values()),
            "catalog_manifest_sha256": hashlib.sha256(
                json_text(sorted(manifest, key=lambda r: r["document"])).encode()
            ).hexdigest(),
            "parallel_title_85": {
                "document_ids": [m["document"] for m in duplicate],
                "identical_observed_sha256": duplicate[0]["artifact_sha"],
                "note": "Distinct publisher URLs retained, observed bytes identical; not two distinct laws. Raw bytes are deduplicated by hash.",
            },
            "source_identity_repairs": [
                "Article 28-A begins with SECTION XXVIII-A-1, not an article heading; exact article/section prefix verified.",
                "Catalog 74E is printed TITLE 74, APPENDIX I, ETHICS COMMISSION RULES; citations and document title use the printed identity.",
                "Four whitespace-normalized inventory keys created during this pass were archived and removed; original native keys retained, no source text deleted.",
            ],
            "budget": {
                "cap_bytes": budget["cap_bytes"],
                "charged_bytes": budget["charged_bytes"],
                "decoded_bytes": budget["decoded_bytes"],
                "original_charged_bytes": baseline["original_charged_bytes"],
                "prior_uncertain_charge_preserved": baseline["old_uncertain_charge_bytes"],
                "downloaded_this_pass": budget["decoded_bytes"]
                - baseline["original_decoded_bytes"],
            },
            "limits": "Closure of retained Senate-linked PDFs only, not complete or current Oklahoma law. Selected original PDFs have older creation/upload metadata; no legal incorporation/effect date inferred. Constitution article companions overlap the whole constitution; Title 85 has duplicate bytes. Pages include contents and notices, not counts of distinct legal provisions. Tables/figures are not visually certified. Separate regulations, session laws and later amendments are not acquired or consolidated. No blanket corpus redistribution license asserted.",
        }
    finally:
        original.close()
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
        assert coverage["collections"][0]["documents"] == report["inventory"]["pdf_links"]
        gaps = await call(
            "legal_coverage", collection=COLLECTION, view="inventory", status="failed"
        )
        assert gaps["total"] == 0
        found = await call(
            "legal_search", query="11-43-101", document="ok-senate:title-11", limit=10
        )
        assert found["matches"]
        operative = None
        for match in found["matches"]:
            read = await call("legal_read", key_or_id=match["id"], length=10000)
            heading = re.search(r"(?m)^\s*\u00a711-43-101\.[^\n]*$", read["text"])
            if heading and not re.search(r"\.{4,}", heading[0]):
                operative = read
                break
        assert operative, "A table-of-contents/cross-reference hit is not the actual source heading"
        report["navigation"] = {
            "source_heading": "11-43-101",
            "key": operative["key"],
            "artifact_sha256": operative["artifact_sha"],
            "scope": "Locates retained source heading; no current-effect or applicability conclusion.",
        }
        receipt = await call("source_receipt", acquisition_id=operative["acquisition_id"])
        assert receipt["sha256"] == operative["artifact_sha"]
        appendix = await call("legal_read", key_or_id="ok:title-74E:page:1", length=300)
        assert appendix["document_title"] == "Title 74, Appendix I, Ethics Commission Rules"
        assert "74E" not in appendix["citation"]
        twins = [
            await call("legal_read", key_or_id="ok:" + key + ":page:1", length=100)
            for key in ("title-85/os85", "title-85/os85_0")
        ]
        assert (
            twins[0]["id"] != twins[1]["id"]
            and twins[0]["artifact_sha"] == twins[1]["artifact_sha"]
        )
        assert not (await call("legal_read", key_or_id=operative["id"], as_of="2026-09-17"))[
            "found"
        ]
        assert not (
            await call(
                "legal_read", key_or_id=operative["id"], observation_cutoff="2026-09-17T00:00:00Z"
            )
        )["found"]
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
            "src/psephos/oklahoma.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "src/psephos/server.py",
            "scripts/verify_oklahoma.py",
        )
    }
    with args.out.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "historical_preservation", "projection_replay", "budget")
            }
        )
    )


if __name__ == "__main__":
    main()
