"""Real-source/MCP smoke checks. Requires the documented acquired corpus, not private fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

from lxml import html
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.acquire import Acquirer
from psephos.geography import PDX_LAYER, zoning_at
from psephos.retrieve import Reader
from psephos.sources import USC_INDEX, checked_zip
from psephos.store import Store, digest, utc_now


def verify_publishers(store, allow_network):
    acquirer = Acquirer(store, max_bytes=5 * 1024**2)

    def fetch(url):
        if not allow_network and acquirer._cached(url) is None:
            raise ValueError("Missing verification receipt; rerun with --publisher-checks: " + url)
        return acquirer.fetch(url)

    try:
        index = html.fromstring(store.artifact(fetch(USC_INDEX).sha256))
        links = [
            urljoin(USC_INDEX, link) for link in index.xpath("//a/@href") if "htm_usc01@" in link
        ]
        assert len(links) == 1
        alternate = fetch(links[0])
        with checked_zip(store.object_path(alternate.sha256)) as archive:
            member = next(n for n in archive.namelist() if n.endswith(".htm"))
            root = html.fromstring(archive.read(member))
            paragraphs = [
                n.text_content() for n in root.xpath('//p[contains(.,"words importing")]')
            ]
        passage = Reader(store).read("1 USC 1")
        normalized = " ".join(passage["text"].split())
        assert len(paragraphs) == 3
        assert all(" ".join(p.split()) in normalized for p in paragraphs)
        query = (
            PDX_LAYER
            + "/query?"
            + urlencode(
                {
                    "geometry": "-122.6765,45.5231",
                    "geometryType": "esriGeometryPoint",
                    "inSR": 4326,
                    "spatialRel": "esriSpatialRelIntersects",
                    "returnIdsOnly": "true",
                    "f": "json",
                }
            )
        )
        point_receipt = fetch(query)
        point_ids = json.loads(store.artifact(point_receipt.sha256))["objectIds"]
        local = zoning_at(store, -122.6765, 45.5231, collection="portland-zoning-gis")
        assert sorted(point_ids) == sorted(int(m["source_key"]) for m in local["matches"])
        return {
            "uscode_alternate_html": {
                "sha256": alternate.sha256,
                "url": alternate.url,
                "member": member,
                "paragraphs_compared": len(paragraphs),
                "match": True,
            },
            "portland_independent_point": {
                "sha256": point_receipt.sha256,
                "url": point_receipt.url,
                "object_ids": point_ids,
                "match": True,
            },
        }
    finally:
        acquirer.close()


async def verify_mcp(root):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(root.resolve()), "serve"]
    )
    checks = []
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()
        tools = sorted(t.name for t in (await session.list_tools()).tools)

        async def call(name, **arguments):
            start = time.monotonic()
            result = await session.call_tool(name, arguments)
            assert not result.isError, result.content
            checks.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "seconds": round(time.monotonic() - start, 4),
                }
            )
            return result.structuredContent

        coverage = await call("legal_coverage")
        assert len(coverage["collections"]) == 9
        search = await call("legal_search", query="words denoting", collection="uscode", limit=3)
        assert search["matches"][0]["key"] == "usc:/us/usc/t1/s1"
        usc = await call("legal_read", key_or_id=search["matches"][0]["id"])
        assert "unless the context indicates otherwise" in usc["text"]
        receipt = await call("source_receipt", acquisition_id=usc["acquisition_id"])
        assert receipt["sha256"] == usc["artifact_sha"]
        key = "ecfr:title-1/chapter-I/subchap-A/part-1/section-1.1"
        history = await call("legal_versions", key=key)
        assert {v["snapshot_date"] for v in history["versions"]} >= {"2024-01-01", "2026-09-03"}
        past = await call("legal_read", key_or_id=key, as_of="2024-01-02")
        assert past["snapshot_date"] == "2024-01-01"
        unavailable = await call(
            "legal_read", key_or_id=past["id"], observation_cutoff="2020-01-01T00:00:00Z"
        )
        assert not unavailable["found"]
        image = await call(
            "legal_read",
            key_or_id="ecfr:title-21/chapter-I/subchap-A/part-10/subpart-B/section-10.31",
        )
        assert (
            image["text_completeness"] == "incomplete_without_source_media"
            and len(image["media"]) == 2
        )
        dc = await call("legal_read", key_or_id="dc-code:§42-3505.01", length=800)
        offset, law_edge = 0, None
        while True:
            refs = await call("legal_references", provision_id=dc["id"], offset=offset, limit=100)
            law_edge = next(
                (
                    ref
                    for ref in refs["references"]
                    if ref["target"].startswith("dc-law:") and ref["acquired_targets"]
                ),
                None,
            )
            if law_edge or refs["next_offset"] is None:
                break
            offset = refs["next_offset"]
        assert law_edge
        law = await call("legal_read", key_or_id=law_edge["acquired_targets"][0]["id"], length=500)
        assert law["unit_kind"] in {"law_metadata", "amending_instrument"}
        texas = await call("legal_search", query="security deposit", collection="texas", limit=2)
        assert texas["matches"]
        tx_past = await call("legal_read", key_or_id=texas["matches"][0]["id"], as_of="2099-01-01")
        assert not tx_past["found"]  # Undated export is not a fabricated historical edition.
        page = await call("legal_read", key_or_id="portland:title-33/page-169", length=24000)
        assert "Table 130-2" in page["text"] and page["unit_kind"] == "pdf_page"
        assert page["text_completeness"] == "layout_text_unverified"
        nyc = await call(
            "zoning_at", longitude=-73.985428, latitude=40.748817, collection="nyc-zoning-gis"
        )
        assert any(m["properties"].get("ZONEDIST") == "C5-3" for m in nyc["matches"])
        pdx = await call(
            "zoning_at", longitude=-122.6765, latitude=45.5231, collection="portland-zoning-gis"
        )
        assert any(m["properties"].get("ZONE") == "CX" for m in pdx["matches"])
        return {
            "tools": tools,
            "calls": checks,
            "dc_law_edge": {
                "source_key": dc["key"],
                "target_key": law["key"],
                "relation": law_edge["relation"],
                "target_representation": law["unit_kind"],
            },
            "point_matches": {
                "nyc": [
                    {"layer": m["layer"], "source_key": m["source_key"]} for m in nyc["matches"]
                ],
                "portland": [m["source_key"] for m in pdx["matches"]],
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--publisher-checks",
        action="store_true",
        help="Permit at most 5 MiB of independent publisher verification requests",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    store = Store(args.data)
    try:
        publishers = verify_publishers(store, args.publisher_checks)
    finally:
        store.close()
    report = {
        "generated_at": utc_now(),
        "status": "PASS",
        "python": platform.python_version(),
        "publishers": publishers,
        "mcp": asyncio.run(verify_mcp(args.data)),
    }
    report["seconds"] = round(time.monotonic() - started, 3)
    report["verification_script_sha256"] = digest(Path(__file__).read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "seconds": report["seconds"]}))


if __name__ == "__main__":
    main()
