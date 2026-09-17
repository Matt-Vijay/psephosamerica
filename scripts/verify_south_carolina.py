"""Offline native inventory, full-text fidelity and real MCP checks for SC code."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urljoin

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.parse import readable
from psephos.south_carolina import BASE, INDEX, chapter_units, content
from psephos.store import Store, json_text, utc_now


def verify(data: Path) -> tuple[dict, dict]:
    s = Store(data, readonly=True)
    try:
        s.db.execute("BEGIN")
        inventory_artifacts = set()

        def retained(url):
            receipt = s.db.execute(
                "SELECT * FROM acquisitions WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            assert receipt is not None, url
            inventory_artifacts.add(receipt["sha256"])
            return content(s.artifact(receipt["sha256"]))

        titles = {
            urljoin(BASE, href)
            for href in retained(INDEX).xpath(".//a/@href")
            if re.fullmatch(r"/code/title\d+\.php", href)
        }
        assert titles
        expected = {}
        for title in sorted(titles):
            for href in retained(title).xpath(".//a/@href"):
                match = re.fullmatch(r"/code/t(\d+)c(\d+[a-z]?)\.php", href, re.I)
                if match:
                    expected[f"t{int(match[1])}c{match[2]}"] = urljoin(BASE, href)
        inventory = {
            r["item"]: dict(r)
            for r in s.db.execute(
                "SELECT item,url,status,error FROM inventories WHERE collection_id='sc-code' AND item NOT LIKE 'title:%'"
            )
        }
        assert expected and expected.keys() == inventory.keys()
        assert all(inventory[item]["url"] == url for item, url in expected.items())
        rows = s.db.execute(
            "SELECT v.*,d.url FROM versions v JOIN documents d ON d.id=v.document_id "
            "WHERE d.collection_id='sc-code' AND v.parser='sc-native-1/text-3' ORDER BY d.id"
        ).fetchall()
        assert rows, "No maintained SC projections"
        kinds, objects, manifest = Counter(), {}, []
        sample = None
        for row in rows:
            match = re.fullmatch(r"sc-code:t(\d+)c(\d+[a-z]?)", row["document_id"], re.I)
            assert match
            title, chapter = int(match[1]), match[2]
            raw = s.artifact(row["artifact_sha"])
            units = chapter_units(raw, row["url"], title, chapter)
            assert " ".join(readable(content(raw)).split()) == " ".join(
                " ".join(p.text for p in units).split()
            ), row["document_id"]
            saved = s.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (row["id"],)
            ).fetchall()
            assert len(saved) == len(units)
            for actual, unit in zip(saved, units, strict=True):
                wanted = asdict(unit)
                refs = wanted.pop("references")
                wanted["metadata"] = json_text(wanted["metadata"])
                assert all(actual[key] == value for key, value in wanted.items()), unit.key
                references = {}
                for ref in refs:
                    references.setdefault(
                        (ref["target"], ref["relation"], ref["label"]), ref["evidence"]
                    )
                assert references == {
                    tuple(r[:3]): r[3]
                    for r in s.db.execute(
                        "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                        (actual["id"],),
                    )
                }, unit.key
                kinds[unit.unit_kind] += 1
                if sample is None and unit.unit_kind == "section":
                    sample = {
                        "key": unit.key,
                        "text": unit.text[:1500],
                        "artifact_sha": row["artifact_sha"],
                    }
            assert all(
                row[name] is None
                for name in (
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
            assert (
                receipt["sha256"] == row["artifact_sha"]
                and receipt["observed_at"] == row["available_at"]
            )
            assert (
                receipt["url"] == row["url"]
                and receipt["status"] == 200
                and receipt["error"] is None
            )
            assert inventory[row["document_id"].split(":")[1]]["status"] == "ingested"
            objects[row["artifact_sha"]] = len(raw)
            manifest.append(
                {
                    key: row[key]
                    for key in (
                        "id",
                        "document_id",
                        "artifact_sha",
                        "acquisition_id",
                        "available_at",
                    )
                }
            )
        assert sample
        statuses = dict(Counter(r["status"] for r in inventory.values()))
        return {
            "status": "PASS",
            "recorded_at": utc_now(),
            "inventory": {"titles": len(titles), "chapters": len(expected), "statuses": statuses},
            "retained_inventory_closed": statuses.get("ingested") == len(expected),
            "native_inventory_objects": sorted(inventory_artifacts),
            "exact_projection_replay": {"documents": len(rows), "unit_kinds": dict(kinds)},
            "whole_chapter_text_reconstruction": "PASS: normalized source text equals all projected text in source order",
            "source_objects_rehashed": len(objects),
            "source_bytes_rehashed": sum(objects.values()),
            "source_manifest_sha256": hashlib.sha256(json_text(manifest).encode()).hexdigest(),
            "observed_range": [
                min(r["available_at"] for r in rows),
                max(r["available_at"] for r in rows),
            ],
            "remaining": [r for r in inventory.values() if r["status"] != "ingested"],
            "limitations": "Retained publisher inventory, not a complete current legal layer. Mixed observation dates; no inferred publication or legal-effect dates. Later acts, local law, incorporated external standards and media transcription are excluded. Whole-chapter units remain explicit where native section markup is absent.",
        }, sample
    finally:
        s.close()


async def mcp(data: Path, sample: dict) -> list[dict]:
    calls = []
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()

        async def call(name, **args):
            result = await session.call_tool(name, args)
            assert not result.isError, result.content
            value = result.structuredContent
            assert value is not None
            calls.append(
                {
                    "tool": name,
                    "arguments": args,
                    "response_sha256": hashlib.sha256(json_text(value).encode()).hexdigest(),
                }
            )
            return value

        coverage = await call("legal_coverage", collection="sc-code")
        assert [c["id"] for c in coverage["collections"]] == ["sc-code"]
        read = await call("legal_read", key_or_id=sample["key"], length=1500)
        assert read["text"] == sample["text"] and read["artifact_sha"] == sample["artifact_sha"]
        receipt = await call("source_receipt", acquisition_id=read["acquisition_id"])
        assert receipt["sha256"] == sample["artifact_sha"]
        await call("legal_references", provision_id=read["id"], limit=10)
        assert not (await call("legal_read", key_or_id=read["id"], as_of="2026-09-17"))["found"]
        assert not (
            await call(
                "legal_read", key_or_id=read["id"], observation_cutoff="2026-09-17T00:00:00Z"
            )
        )["found"]
    return calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report, sample = verify(args.data)
    report["mcp"] = asyncio.run(mcp(args.data, sample))
    repo = Path(__file__).resolve().parents[1]
    report["runtime_sha256"] = {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
        for name in (
            "src/psephos/south_carolina.py",
            "src/psephos/campaign.py",
            "src/psephos/retrieve.py",
            "scripts/verify_south_carolina.py",
        )
    }
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "inventory",
                    "retained_inventory_closed",
                    "exact_projection_replay",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
