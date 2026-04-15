"""Compact local disclosure bundle-process smoke helper.

smoke_process_disclosures_local drives run_disclosures_bundle_process and
returns a compact structured summary suitable for operator confidence checks.
Keys are aligned with the existing smoke/output conventions used across the
runtime layer.  Stage-aware counts (staged, mirrored) distinguish this helper
from the non-local parse-to-load variant.

No CLI, no network, no subprocesses.  The caller owns the DB connection and
the pre-fetched bundle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.runtime.disclosures_bundle_process import run_disclosures_bundle_process


def smoke_process_disclosures_local(
    conn: Any,
    bundle: Any,
    *,
    local_root: Path | None = None,
) -> dict[str, Any]:
    """Run the disclosure bundle-process pipeline and return a compact summary.

    Keys returned:
        run_id          — ingestion_run id from the load step
        source_slug     — data_source slug recorded by the load step
        staged          — artifacts written to source_artifact by the stage step
        mirrored        — artifact files written to local_root (0 when absent)
        parsed          — total artifacts attempted by the parse step
        parse_succeeded — artifacts that completed parse without exception
        parse_failed    — artifacts that raised an exception during parse
        transformed     — DisclosureTransformResult objects produced
        total_written   — rows inserted or updated by the load step
        load_ok         — False if the load step recorded any errors
    """
    result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)
    ls = result.load_result.load_summary
    return {
        "run_id": result.load_result.run_id,
        "source_slug": result.load_result.data_source.get("slug"),
        "staged": result.stage_result.staged_count,
        "mirrored": result.stage_result.mirrored_count,
        "parsed": result.parse_result.processed_count,
        "parse_succeeded": result.parse_result.succeeded_count,
        "parse_failed": result.parse_result.failed_count,
        "transformed": result.transform_count,
        "total_written": ls.total_written,
        "load_ok": ls.ok,
    }
