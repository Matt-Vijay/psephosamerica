"""Compact local Congress smoke helper.

smoke_congress_local drives run_congress_archive_load and returns a compact
structured summary suitable for operator confidence checks.  Keys are aligned
with the existing smoke/output conventions used across the runtime layer.

No CLI, no network, no subprocesses.  The caller owns the DB connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ingest.congress.archive import CongressArchive
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_options import CongressLoadOptions


def smoke_congress_local(
    conn: Any,
    archive: CongressArchive | Path,
    options: CongressLoadOptions,
) -> dict[str, Any]:
    """Run the local Congress archive load and return a compact operator summary.

    Keys returned:
        run_id      — ingestion run identifier
        source_slug — data source slug from the provenance row
        inserted    — total rows inserted across all tables
        updated     — total rows updated across all tables
        skipped     — total rows skipped (already present, unchanged)
        rejected    — total rows rejected (constraint or validation failures)
        ok          — True when the run completed with no errors
        warnings    — count of warnings collected during the run
        errors      — count of errors collected during the run
    """
    result = run_congress_archive_load(conn, archive, options)
    ls = result.load_summary
    return {
        "run_id": result.run_id,
        "source_slug": result.data_source.get("slug"),
        "inserted": ls.total_inserted,
        "updated": ls.total_updated,
        "skipped": ls.total_skipped,
        "rejected": ls.total_rejected,
        "ok": ls.ok,
        "warnings": ls.warn_error.warning_count,
        "errors": ls.warn_error.error_count,
    }
