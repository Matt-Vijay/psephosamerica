"""Replay Idaho's retained native inventories/PDF pages and exercise real MCP reads."""

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
from psephos.idaho import COLLECTION, INDEX, chapter_units, inventory
from psephos.store import Store, json_text, utc_now


def verify(data):
    s = Store(data, readonly=True)
    try:
        s.db.execute("BEGIN")
        inventory_objects = set()

        def raw(url):
            r = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            assert r is not None, url
            inventory_objects.add(r["sha256"])
            return s.artifact(r["sha256"])

        titles = inventory(raw(INDEX))
        expected, notices, unavailable, title_statuses = {}, {}, {}, Counter()
        for title, _, url in titles:
            statuses = []
            for chapter, label, pdf in inventory(raw(url), title):
                item = f"T{title}CH{chapter}"
                entry = s.db.execute(
                    "SELECT * FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, item)
                ).fetchone()
                assert entry is not None
                if pdf:
                    assert entry["url"] == pdf
                    if entry["status"] == "source_unavailable":
                        receipt = s.db.execute(
                            "SELECT * FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1",
                            (pdf,),
                        ).fetchone()
                        assert receipt and receipt["status"] in (404, 410)
                        assert "retry-after" not in json.loads(receipt["headers"])
                        assert (
                            entry["error"]
                            == f"Publisher HTTP {receipt['status']}; receipt {receipt['id']}"
                        )
                    expected[item] = dict(entry)
                elif re.search(r"\[(?:REPEALED|RESERVED)\]", label):
                    assert (
                        entry["status"] == "nonexport_notice"
                        and entry["url"] == url
                        and entry["error"] == label
                    )
                    notices[item] = label
                else:
                    assert entry["status"] == "missing_pdf_export" and entry["url"] == url
                    unavailable[item] = label
                statuses.append(entry["status"])
            state = (
                "indexed"
                if all(x in {"indexed", "nonexport_notice"} for x in statuses)
                else "partial"
            )
            assert (
                s.db.execute(
                    "SELECT status FROM inventories WHERE collection_id=? AND item=?",
                    (COLLECTION, "title:" + title),
                ).fetchone()[0]
                == state
            )
            title_statuses[state] += 1
        actual = {
            r[0]
            for r in s.db.execute(
                "SELECT item FROM inventories WHERE collection_id=? AND item NOT LIKE 'title:%'",
                (COLLECTION,),
            )
        }
        assert actual == expected.keys() | notices.keys() | unavailable.keys()
        documents = {
            r[0].split(":", 1)[1]
            for r in s.db.execute(
                "SELECT DISTINCT v.document_id FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id=?",
                (COLLECTION,),
            )
        }
        assert documents == {k for k, v in expected.items() if v["status"] == "indexed"}
        rows = s.db.execute(
            "SELECT v.*,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE v.parser='idaho-native-pdf-1/text-3' ORDER BY d.id"
        ).fetchall()
        assert rows
        pages = images = references = 0
        qualities = Counter()
        artifacts, sample = {}, None
        for row in rows:
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (row["acquisition_id"],)
            ).fetchone()
            body = s.artifact(row["artifact_sha"])
            assert hashlib.sha256(body).hexdigest() == row["artifact_sha"] == receipt["sha256"]
            assert (
                receipt["status"] == 200
                and receipt["error"] is None
                and receipt["url"] == row["url"]
            )
            assert receipt["observed_at"] == row["available_at"]
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
            meta = json.loads(row["metadata"])
            rec = Receipt(
                receipt["id"], receipt["url"], receipt["sha256"], receipt["observed_at"], len(body)
            )
            units = chapter_units(s, rec, str(meta["title"]), meta["chapter"], row["url"])
            compare_units(s, row["id"], units)
            pages += len(units)
            images += sum(p.metadata["embedded_images"] for p in units)
            references += sum(len(p.references) for p in units)
            qualities.update(p.metadata["text_quality"] for p in units)
            artifacts[row["artifact_sha"]] = len(body)
            if sample is None:
                sample = {
                    "key": units[0].key,
                    "text": units[0].text[:1500],
                    "sha256": row["artifact_sha"],
                }
        statuses = dict(Counter(v["status"] for v in expected.values()))
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "inventory": {
                "titles": len(titles),
                "title_statuses": dict(title_statuses),
                "chapter_exports": len(expected),
                "chapter_statuses": statuses,
                "nonexport_notices": len(notices),
            },
            "retained_pdf_inventory_closed": statuses.get("indexed") == len(expected),
            "missing_pdf_exports": unavailable,
            "projection_replay": {
                "documents": len(rows),
                "pdf_pages": pages,
                "embedded_image_appearances": images,
                "publisher_links": references,
                "text_qualities": dict(qualities),
            },
            "objects_rehashed": len(artifacts),
            "bytes_rehashed": sum(artifacts.values()),
            "inventory_objects": sorted(inventory_objects),
            "manifest_sha256": hashlib.sha256(
                json_text(
                    [
                        {k: r[k] for k in ("id", "artifact_sha", "acquisition_id", "available_at")}
                        for r in rows
                    ]
                ).encode()
            ).hexdigest(),
            "nonexport_notices": notices,
            "pending_manifest_sha256": hashlib.sha256(
                json_text(
                    [
                        {k: r[k] for k in ("item", "url")}
                        for r in expected.values()
                        if r["status"] == "pending"
                    ]
                ).encode()
            ).hexdigest(),
            "failed_exports": [
                {k: r[k] for k in ("item", "url", "status", "error")}
                for r in expected.values()
                if r["status"] not in {"indexed", "pending"}
            ],
            "limits": "Chapter PDF inventory only; printed official Code, constitution, regulations, later session laws and incorporated material excluded. Physical pages are not statutory sections. Layout text is unverified; table/image interpretation is not certified. Observation dates do not establish precise snapshot/effect. No raw-corpus redistribution permission asserted.",
        }, sample
    finally:
        s.close()


async def mcp(data, report, sample):
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
            coverage["collections"][0]["documents"]
            == report["inventory"]["chapter_statuses"]["indexed"]
        )
        missing = report["inventory"]["chapter_statuses"].get("source_unavailable", 0)
        if missing:
            gaps = await call(
                "legal_coverage",
                view="inventory",
                collection=COLLECTION,
                status="source_unavailable",
            )
            assert gaps["total"] == missing and all(
                row["status"] == "source_unavailable" for row in gaps["inventory"]
            )
        read = await call("legal_read", key_or_id=sample["key"], length=1500)
        assert read["text"] == sample["text"] and read["artifact_sha"] == sample["sha256"]
        receipt = await call("source_receipt", acquisition_id=read["acquisition_id"])
        assert receipt["sha256"] == sample["sha256"]
        assert not (await call("legal_read", key_or_id=read["id"], as_of="2026-09-17"))["found"]
        assert not (
            await call(
                "legal_read", key_or_id=read["id"], observation_cutoff="2026-09-17T00:00:00Z"
            )
        )["found"]
        source = await call(
            "legal_read", key_or_id="id-statutes:title-6/chapter-3/page-9", length=500
        )
        refs = await call("legal_references", provision_id=source["id"], limit=100)
        edge = next(r for r in refs["references"] if r["target"].endswith("/SECT6-310"))
        assert edge["resolution_status"] == "chapter_retained_section_location_unverified"
        assert edge["acquired_targets"] == []
        navigation = edge["navigation"]
        found = await call(
            "legal_search",
            query=navigation["search_query"],
            document=navigation["document"],
            limit=10,
        )
        assert found["matches"] and all(
            m["document"] == navigation["document"] for m in found["matches"]
        )
        heading = None
        inspected = 0
        for candidate in found["matches"]:
            page = await call("legal_read", key_or_id=candidate["id"], length=10000)
            inspected += 1
            match = re.search(r"(?m)^\s*6-310\.\s", page["text"])
            if match:
                heading = {
                    "id": page["id"],
                    "key": page["key"],
                    "native_heading_offset": match.start(),
                }
                break
        assert heading, "A cross-reference mention is not the requested source heading"
        report["navigation_check"] = {
            "navigation": navigation,
            "heading": heading,
            "pages_inspected": inspected,
            "limit": "Chapter-level navigation only. Lexical hits include cross-references; inspect the source heading and following pages.",
        }
        assert not (await call("legal_search", query="6-310", document="missing-document"))[
            "matches"
        ]
        assert not (
            await call(
                "legal_search", query="6-310", document=navigation["document"], as_of="2026-09-17"
            )
        )["matches"]
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report, sample = verify(args.data)
    report["mcp"] = asyncio.run(mcp(args.data, report, sample))
    report["runtime_sha256"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in (
            "src/psephos/idaho.py",
            "src/psephos/municipal.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "src/psephos/server.py",
            "scripts/verify_idaho.py",
        )
    }
    with args.out.open("x") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print(json.dumps({k: report[k] for k in ("status", "inventory", "projection_replay")}))


if __name__ == "__main__":
    main()
