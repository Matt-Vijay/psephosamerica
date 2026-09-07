"""Focused, offline real-corpus source-discovery/MCP workflows. No whole-corpus audit."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from psephos.retrieve import RECEIPT_HEADERS
from psephos.store import Store, utc_now


def stored_receipt(data: Path) -> dict[str, Any]:
    store = Store(data, readonly=True)
    try:
        row = store.db.execute("SELECT * FROM acquisitions WHERE id=6037").fetchone()
        assert row is not None
        return dict(row)
    finally:
        store.close()


async def verify(data: Path) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    original = stored_receipt(data)
    original_headers = json.loads(original["headers"])
    assert "set-cookie" in {name.lower() for name in original_headers}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    started = time.monotonic()
    async with stdio_client(params) as (rd, wr), ClientSession(rd, wr) as session:
        await session.initialize()
        startup = time.monotonic() - started

        async def call(name: str, error: bool = False, **args: Any) -> dict[str, Any]:
            start = time.monotonic()
            response = await session.call_tool(name, args)
            elapsed = time.monotonic() - start
            assert response.isError == error
            content = response.structuredContent or {}
            raw = json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
            calls.append(
                {
                    "tool": name,
                    "arguments": args,
                    "seconds": round(elapsed, 6),
                    "structured_bytes": len(raw),
                    "mcp_response_bytes": len(response.model_dump_json().encode()),
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "status": content.get("status"),
                    "expected_tool_error": error,
                }
            )
            if name == "legal_coverage" and not error:
                assert len(raw) <= 24576
            return content

        directory = await call("legal_coverage")
        assert directory["view"] == "jurisdictions" and len(directory["jurisdictions"]) <= 10
        page = await call(
            "legal_coverage", view="jurisdictions", offset=directory["next_offset"], limit=2
        )
        assert not {r["id"] for r in page["jurisdictions"]} & {
            r["id"] for r in directory["jurisdictions"]
        }
        nyc = await call("legal_coverage", jurisdiction="us-ny-nyc")
        assert {c["id"] for c in nyc["collections"]} == {"nyc-zoning", "nyc-zoning-gis"}
        documents = await call("legal_coverage", view="documents", collection="nyc-zoning", limit=2)
        assert documents["documents"][0]["first_key"]
        matches = await call(
            "legal_search", query="qualifying residential site", collection="nyc-zoning", limit=3
        )
        definition = next(m for m in matches["matches"] if m["key"] == "nyc-zr:12-10")
        text = await call("legal_read", key_or_id=definition["id"], length=1000)
        found = await call(
            "legal_find", key_or_id=definition["id"], query="qualifying residential site", limit=1
        )
        assert found["matches"] and text["found"]
        receipt = await call("source_receipt", acquisition_id=text["acquisition_id"])
        assert receipt["sha256"] == text["artifact_sha"]
        projected = await call("source_receipt", acquisition_id=6037)
        assert projected["headers"] == {
            name.lower(): value
            for name, value in original_headers.items()
            if name.lower() in RECEIPT_HEADERS
        }
        assert projected["omitted_header_names"] == sorted(
            {name.lower() for name in original_headers if name.lower() not in RECEIPT_HEADERS}
        )
        assert "set-cookie" in projected["omitted_header_names"]
        assert "psephos_request_started_at" in projected["headers"]
        assert projected["sha256"] == original["sha256"]
        assert all(projected[key] == value for key, value in original.items() if key != "headers")
        florida = await call("legal_coverage", jurisdiction="us-fl")
        card = next(c for c in florida["collections"] if c["id"] == "florida-statutes-2026")
        review = card["metadata"]["discovery_review"]
        assert review["selected_title_numbers"] == [1, 2, 3, 9, 11, 12, 13]
        assert review["inventory_counts"]["titles_pending"] == 42 and card["documents"] == 52
        pending = await call(
            "legal_coverage", view="inventory", collection=card["id"], status="pending", limit=2
        )
        assert pending["total"] == 42 and len(pending["inventory"]) == 2
        docs = await call("legal_coverage", view="documents", collection=card["id"], limit=2)
        source = await call("legal_read", key_or_id=docs["documents"][0]["first_key"], length=1000)
        assert source["found"] and source["snapshot_date"] is None
        assert not (await call("legal_read", key_or_id=source["id"], as_of="2026-09-07"))["found"]
        ms = await call("legal_coverage", collection="ms-court-rules")
        assert "not local rules or all Mississippi law" in ms["collections"][0]["metadata"]["scope"]
        nj = await call("legal_coverage", collection="nj-statutes")
        currency = nj["collections"][0]["metadata"]["discovery_review"]["currency"]
        assert "2025" in currency["body_notice"] and "2026" in currency["toc_notice"]
        assert (await call("legal_coverage", jurisdiction="not-registered"))[
            "status"
        ] == "unknown_jurisdiction"
        assert (await call("legal_coverage", collection="not-registered"))[
            "status"
        ] == "unknown_collection"
        assert (
            await call("legal_coverage", jurisdiction="us-or-portland", collection="nyc-zoning")
        )["status"] == "scope_mismatch"
        empty = await call("legal_coverage", view="documents", collection="in-current-code")
        assert empty["status"] == "empty" and empty["source"]["documents"] == 0
        await call("legal_coverage", error=True, collection="")
    assert stored_receipt(data) == original
    root = Path(__file__).resolve().parents[1]
    return {
        "status": "PASS",
        "recorded_at": utc_now(),
        "scope": "Read-only actual-corpus MCP workflows; no downloads, replay campaign, national text-count scan or whole-corpus audit",
        "startup_seconds": round(startup, 6),
        "calls": calls,
        "header_output_check": {
            "acquisition_id": 6037,
            "artifact_sha256": original["sha256"],
            "stored_headers_sha256": hashlib.sha256(original["headers"].encode()).hexdigest(),
            "emitted_header_names": sorted(projected["headers"]),
            "omitted_header_names": projected["omitted_header_names"],
            "stored_receipt_unchanged": True,
            "policy": projected["header_projection"],
        },
        "total_seconds": round(time.monotonic() - started, 6),
        "measurement_note": "One observed sequential run, not a latency distribution or a controlled before/after benchmark. MCP duration includes local stdio overhead; byte counts distinguish structured JSON from the full MCP envelope.",
        "runtime_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in [
                "src/psephos/retrieve.py",
                "src/psephos/server.py",
                "src/psephos/cli.py",
                "scripts/verify_discovery.py",
            ]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--out", type=Path, required=True, help="New receipt file; never overwritten"
    )
    args = parser.parse_args()
    report = asyncio.run(verify(args.data))
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "calls": len(report["calls"]),
                "seconds": report["total_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
