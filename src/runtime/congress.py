"""Runtime entry point for the Congress canonical load.

Orchestrates provenance bookkeeping around the live pipeline load:
  1. ensure_data_source  — idempotent; creates the row on first run
  2. start_ingestion_run — opens a 'running' ingestion_run row
  3. run_congress_load   — executes the FK-phased DB writes
  4. finish / fail       — closes the run with outcome

Re-raises any exception after marking the run failed so the caller can
propagate or log at its own boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.pipeline.congress_load_run import CongressIngestInputs, run_congress_load
from src.db.load_report import LoadSummary
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import CONGRESS_CORE

_SOURCE_SLUG = CONGRESS_CORE.slug
_SOURCE_NAME = CONGRESS_CORE.name
_SOURCE_KIND = CONGRESS_CORE.source_kind
_SOURCE_BASE_URL = CONGRESS_CORE.base_url


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CongressLoadResult:
    """Outcome of a single Congress load runtime invocation."""

    data_source: dict[str, Any]
    run_id: int
    load_summary: LoadSummary


# ---------------------------------------------------------------------------
# Runtime function
# ---------------------------------------------------------------------------


def run_congress_load_runtime(
    conn: Any,
    inputs: CongressIngestInputs,
) -> CongressLoadResult:
    """Ensure provenance rows, run the Congress load, and close the run.

    Sequence:
      1. ensure_data_source   — idempotent upsert of the data_source row
      2. start_ingestion_run  — opens run in 'running' state
      3. run_congress_load    — executes phased DB writes
      4. finish_ingestion_run — records record_count and marks 'succeeded'

    On any exception in step 3: fail_ingestion_run is called before
    re-raising so the run row is never left in 'running' state.
    """
    data_source = ensure_data_source(
        conn,
        slug=_SOURCE_SLUG,
        name=_SOURCE_NAME,
        source_kind=_SOURCE_KIND,
        base_url=_SOURCE_BASE_URL,
    )

    run_id = start_ingestion_run(
        conn,
        data_source_id=data_source["id"],
        run_type="ingest",
    )

    try:
        load_summary = run_congress_load(inputs, conn, run_id=run_id)
    except Exception as exc:
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    finish_ingestion_run(conn, run_id, record_count=load_summary.total_inserted)

    return CongressLoadResult(
        data_source=data_source,
        run_id=run_id,
        load_summary=load_summary,
    )
