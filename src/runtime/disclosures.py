"""Runtime disclosures load flow: wire provenance bookkeeping around the
disclosure pipeline.

Entry point: run_disclosures_load_runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db.load_report import LoadSummary
from src.db.runtime_lookups import load_lookup_bundle
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    OutsidePositionSidecar,
)
from src.pipeline.disclosures_load_run import run_disclosures_load
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import DISCLOSURE_LOAD

_SOURCE_SLUG = DISCLOSURE_LOAD.slug
_SOURCE_NAME = DISCLOSURE_LOAD.name
_SOURCE_KIND = DISCLOSURE_LOAD.source_kind
_SOURCE_BASE_URL = DISCLOSURE_LOAD.base_url

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosuresLoadRuntimeResult:
    data_source: dict[str, Any]
    run_id: int
    load_summary: LoadSummary
    sidecars: tuple[OutsidePositionSidecar, ...]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_disclosures_load_runtime(
    conn: Any,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult:
    """Orchestrate a provenance-tracked disclosures load.

    Steps:
    1. Ensure the data_source row exists.
    2. Open an ingestion_run (run_type='ingest').
    3. Execute the live disclosures load pipeline.
    4. Close the run as succeeded or failed; re-raise on failure.
    """
    data_source = ensure_data_source(
        conn, _SOURCE_SLUG, _SOURCE_NAME, _SOURCE_KIND, _SOURCE_BASE_URL
    )
    run_id = start_ingestion_run(conn, data_source["id"], "ingest")

    try:
        load_summary, sidecars = run_disclosures_load(
            results,
            conn,
            lookup_loader=load_lookup_bundle,
            run_id=run_id,
        )
    except Exception as exc:
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    finish_ingestion_run(conn, run_id, load_summary.total_written)

    return DisclosuresLoadRuntimeResult(
        data_source=data_source,
        run_id=run_id,
        load_summary=load_summary,
        sidecars=sidecars,
    )
