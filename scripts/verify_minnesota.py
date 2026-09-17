"""Offline native inventory/text checks and live MCP navigation for Minnesota statutes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from verify_washington import compare_units

from psephos.minnesota import BASE, INDEX, chapter_units, publisher_page
from psephos.parse import readable
from psephos.retrieve import Reader
from psephos.store import Store, json_text, utc_now


def verify(data):
    s = Store(data, readonly=True)
    try:
        s.db.execute("BEGIN")
        inventory_objects = set()

        def page(url):
            row = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            assert row is not None, url
            inventory_objects.add(row["sha256"])
            return publisher_page(s.artifact(row["sha256"]))

        root, edition = page(INDEX)
        parts = {
            readable(row.xpath("./td")[1]): urljoin(BASE, row.xpath(".//a/@href")[0])
            for row in root.xpath('//*[@id="toc_table"]/tbody/tr')
            if len(row.xpath("./td")) == 2
        }
        expected, memberships = {}, {}
        for name, url in parts.items():
            root, label = page(url)
            assert label == edition
            members = {
                readable(a).strip(): urljoin(BASE, a.get("href")) + "/full"
                for a in root.xpath('//*[@id="chapters_table"]//a[@href]')
            }
            assert members
            memberships[name] = members
            expected.update(members)
        inventory = {
            r["item"].removeprefix("chapter:"): dict(r)
            for r in s.db.execute(
                "SELECT * FROM inventories WHERE collection_id='mn-statutes' AND item LIKE 'chapter:%'"
            )
        }
        assert inventory.keys() == expected.keys()
        assert all(inventory[key]["url"] == url for key, url in expected.items())
        for part, members in memberships.items():
            row = s.db.execute(
                "SELECT status FROM inventories WHERE collection_id='mn-statutes' AND item=?",
                ("part:" + part,),
            ).fetchone()
            assert row[0] == (
                "indexed"
                if all(inventory[c]["status"] == "indexed" for c in members)
                else "partial"
            )
        documents = {
            r[0].rsplit("/", 1)[1]
            for r in s.db.execute(
                "SELECT DISTINCT v.document_id FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.collection_id='mn-statutes'"
            )
        }
        assert documents == {c for c, r in inventory.items() if r["status"] == "indexed"}
        rows = s.db.execute(
            "SELECT v.*,d.url FROM versions v JOIN documents d ON d.id=v.document_id WHERE v.parser='mn-statutes-native-1/text-3' ORDER BY d.id"
        ).fetchall()
        assert rows
        artifacts, kinds, sample, navigation = {}, Counter(), None, None
        for row in rows:
            chapter = row["document_id"].rsplit("/", 1)[1]
            raw = s.artifact(row["artifact_sha"])
            root, label = publisher_page(raw)
            assert label == edition
            units = chapter_units(raw, chapter, row["url"])
            compare_units(s, row["id"], units)
            body = deepcopy(
                root.xpath('//*[@id="xtend"]//h2[@class="chapter_title" or @class="chapter_no"]')[
                    0
                ].getparent()
            )
            for catalog in body.xpath('./div[@id="chapter_analysis"]'):
                catalog.drop_tree()
            assert " ".join(readable(body).split()) == " ".join(
                " ".join(u.text for u in units).split()
            ), row["document_id"]
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE id=?", (row["acquisition_id"],)
            ).fetchone()
            assert (
                receipt["sha256"] == row["artifact_sha"]
                and receipt["observed_at"] == row["available_at"]
            )
            assert (
                receipt["url"] == row["url"]
                and receipt["status"] == 200
                and receipt["error"] is None
            )
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
            artifacts[row["artifact_sha"]] = len(raw)
            for unit in units:
                kinds[unit.unit_kind] += 1
                if sample is None and unit.unit_kind == "section":
                    sample = {
                        "key": unit.key,
                        "text": unit.text[:1500],
                        "sha256": row["artifact_sha"],
                    }
                if navigation is None and unit.unit_kind == "section" and unit.references:
                    p = s.db.execute(
                        "SELECT id FROM provisions WHERE version_id=? AND key=?",
                        (row["id"], unit.key),
                    ).fetchone()
                    references = Reader(s).references(p[0], limit=100)["references"]
                    edge = next(
                        (
                            r
                            for r in references
                            if r.get("publisher_citation_family") == "mn-statutes"
                            and r["acquired_targets"]
                        ),
                        None,
                    )
                    if edge:
                        navigation = {
                            "source": p[0],
                            "target_url": edge["target"],
                            "destination": edge["acquired_targets"][0]["key"],
                        }
        assert sample and navigation, "No demonstrated retained section-to-section navigation"
        statuses = dict(Counter(r["status"] for r in inventory.values()))
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "edition_label": edition,
            "inventory": {"parts": len(parts), "chapters": len(expected), "statuses": statuses},
            "retained_html_inventory_closed": statuses.get("indexed") == len(expected),
            "inventory_objects": sorted(inventory_objects),
            "projection_replay": {"documents": len(rows), "unit_kinds": dict(kinds)},
            "full_legal_container_text": "PASS: all non-catalog text reconstructed in order; chapter tables, history, notes and native occurrences retained",
            "objects_rehashed": len(artifacts),
            "bytes_rehashed": sum(artifacts.values()),
            "manifest_sha256": hashlib.sha256(
                json_text(
                    [
                        {
                            k: r[k]
                            for k in (
                                "id",
                                "document_id",
                                "artifact_sha",
                                "acquisition_id",
                                "available_at",
                            )
                        }
                        for r in rows
                    ]
                ).encode()
            ).hexdigest(),
            "navigation": navigation,
            "remaining": [
                {k: r[k] for k in ("item", "url", "status", "error")}
                for r in inventory.values()
                if r["status"] != "indexed"
            ],
            "limits": "Publisher HTML inventory only, not authenticated PDF completeness or complete current law. New PDFs linked, not acquired or authenticated. Original PDF companions unchanged. Mixed observation dates; precise snapshot/effect unknown. Dated/subdivision-specific hyperlinks are not silently mapped to current whole sections. Separate rules, session, local/special laws and external incorporated material remain outside this family.",
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

        coverage = await call("legal_coverage", collection="mn-statutes")
        report["coverage_documents_at_mcp_probe"] = coverage["collections"][0]["documents"]
        assert report["coverage_documents_at_mcp_probe"] >= report["inventory"]["statuses"]["indexed"]
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
        navigation = report["navigation"]
        refs = await call("legal_references", provision_id=navigation["source"], limit=100)
        edge = next(r for r in refs["references"] if r["target"] == navigation["target_url"])
        target = edge["acquired_targets"][0]
        assert target["key"] == navigation["destination"]
        assert (await call("legal_read", key_or_id=target["id"], length=500))["key"] == target[
            "key"
        ]
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report, sample = verify(args.data)
    report["mcp"] = asyncio.run(mcp(args.data, report, sample))
    root = Path(__file__).resolve().parents[1]
    report["runtime_sha256"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "src/psephos/minnesota.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "scripts/verify_minnesota.py",
            "scripts/verify_washington.py",
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
                    "inventory",
                    "retained_html_inventory_closed",
                    "projection_replay",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
