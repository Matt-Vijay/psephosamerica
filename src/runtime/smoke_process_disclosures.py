"""Smoke helper for the disclosure parse-to-load pipeline.

smoke_process_disclosures drives run_disclosures_parse_load_runtime and returns
a compact summary dict for operator confidence checks.

No CLI, no network, no subprocesses.  The caller owns the DB connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.runtime.disclosures_load_from_parse import run_disclosures_parse_load_runtime


def smoke_process_disclosures(
    conn: Any,
    local_root: Path,
    *,
    chamber: str | None = None,
    limit: int | None = None,
    parser_name: str = "text_extract_v1",
    parser_version: str = "1",
) -> dict[str, Any]:
    """Run the disclosure parse-to-load pipeline and return a compact summary.

    Keys returned:
        run_id          — ingestion_run id from the load step
        source_slug     — data_source slug recorded by the load step
        parsed          — total artifacts attempted by the parse step
        parse_succeeded — artifacts that completed parse without exception
        parse_failed    — artifacts that raised an exception during parse
        transformed     — DisclosureTransformResult objects produced
        total_written   — rows inserted or updated by the load step
        load_ok         — False if the load step recorded any errors
    """
    result = run_disclosures_parse_load_runtime(
        conn,
        local_root=local_root,
        chamber=chamber,
        limit=limit,
        parser_name=parser_name,
        parser_version=parser_version,
    )
    ls = result.load_result.load_summary
    return {
        "run_id": result.load_result.run_id,
        "source_slug": result.load_result.data_source.get("slug"),
        "parsed": result.parse_result.processed_count,
        "parse_succeeded": result.parse_result.succeeded_count,
        "parse_failed": result.parse_result.failed_count,
        "transformed": result.transform_count,
        "total_written": ls.total_written,
        "load_ok": ls.ok,
    }
