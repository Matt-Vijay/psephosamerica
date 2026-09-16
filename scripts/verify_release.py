"""Small offline smoke check for an installed package and the public U.S. Code bundle."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import psephos
from psephos.store import Store, utc_now


async def verify(data: Path) -> dict:
    calls = []
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "psephos.cli", "--data", str(data.resolve()), "serve"]
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        names = sorted(tool.name for tool in (await client.list_tools()).tools)
        assert len(names) == 9, names

        async def call(tool: str, **arguments):
            start = time.monotonic()
            result = await client.call_tool(tool, arguments)
            assert not result.isError, result.content
            value = result.structuredContent
            if value is None:
                value = json.loads(
                    next(item.text for item in result.content if item.type == "text")
                )
            calls.append(
                {
                    "tool": tool,
                    "arguments": arguments,
                    "seconds": round(time.monotonic() - start, 3),
                }
            )
            return value

        coverage = await call("legal_coverage", collection="uscode")
        assert coverage["collections"][0]["documents"] == 58
        matches = await call(
            "legal_search", query="reasonable accommodation", collection="uscode", limit=3
        )
        assert matches["matches"]
        provision = await call("legal_read", key_or_id="42 USC 3604")
        assert provision["found"] and "reasonable accommodations" in provision["text"]
        found = await call(
            "legal_find", key_or_id=provision["id"], query="reasonable accommodations"
        )
        assert found["matches"]
        references = await call("legal_references", provision_id=provision["id"], limit=5)
        assert references["references"]
        dated = await call("legal_read", key_or_id=provision["id"], as_of="2000-01-01")
        assert not dated["found"]
        absent = await call("legal_coverage", jurisdiction="us-mi-ann-arbor")
        assert absent["total"] == 0 and absent["status"] != "ok"
        versions = await call("legal_versions", key=provision["key"])
        assert versions
    store = Store(data, readonly=True)
    try:
        snapshot = store.db.execute(
            "SELECT snapshot_date,snapshot_basis FROM versions LIMIT 1"
        ).fetchone()
        counts = {
            table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("documents", "versions", "provisions", "artifacts")
        }
    finally:
        store.close()
    return {
        "verified_at": utc_now(),
        "status": "PASS",
        "python": platform.python_version(),
        "package_path": str(Path(psephos.__file__).resolve()),
        "tools": names,
        "counts": counts,
        "snapshot_date": snapshot[0],
        "snapshot_basis": snapshot[1],
        "calls": calls,
        "meaning": "Installed-package and imported-source MCP smoke check, not legal-answer accuracy or nationwide coverage.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(args.data))
    encoded = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.write_text(encoded)
    print(encoded)
