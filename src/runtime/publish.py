"""Runtime publish flow: wire provenance bookkeeping around publish_snapshot_run.

Entry point: run_publish_runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.pipeline.publish_snapshot_run import ZipBundleInputs, publish_snapshot_run
from src.pipeline.publish_pipeline import PublishResult
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import SNAPSHOT_PUBLISH

_SOURCE_SLUG = SNAPSHOT_PUBLISH.slug
_SOURCE_NAME = SNAPSHOT_PUBLISH.name
_SOURCE_KIND = SNAPSHOT_PUBLISH.source_kind


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRuntimeResult:
    data_source: dict[str, Any]
    run_id: int
    snapshot_id: str
    publish_result: PublishResult


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def default_snapshot_id(snapshot_date: date) -> str:
    """Return the canonical snapshot identifier for *snapshot_date*."""
    return snapshot_date.isoformat()


def run_publish_runtime(
    conn: Any,
    snapshot_date: date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
    *,
    snapshot_id: str | None = None,
) -> PublishRuntimeResult:
    """Orchestrate a provenance-tracked publish run.

    Steps:
    1. Ensure the internal data_source row exists.
    2. Open an ingestion_run (run_type='export').
    3. Derive snapshot_id from snapshot_date when not supplied.
    4. Delegate to publish_snapshot_run for the DB-backed publish.
    5. Close the run as succeeded or failed; re-raise on failure.
    """
    data_source = ensure_data_source(
        conn, _SOURCE_SLUG, _SOURCE_NAME, _SOURCE_KIND
    )
    run_id = start_ingestion_run(
        conn,
        data_source["id"],
        "export",
        parameters={"snapshot_date": snapshot_date.isoformat()},
    )

    resolved_snapshot_id = snapshot_id if snapshot_id is not None else default_snapshot_id(snapshot_date)

    try:
        publish_result = publish_snapshot_run(
            conn,
            snapshot_id=resolved_snapshot_id,
            snapshot_date=snapshot_date,
            target_dir=target_dir,
            zip_bundle_inputs=zip_bundle_inputs,
        )
    except Exception as exc:
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    if not publish_result.succeeded:
        failure_summary = "; ".join(publish_result.verification_failures) or "publish pipeline failed"
        fail_ingestion_run(conn, run_id, failure_summary)
        raise RuntimeError(failure_summary)

    finish_ingestion_run(conn, run_id, publish_result.written_count)

    return PublishRuntimeResult(
        data_source=data_source,
        run_id=run_id,
        snapshot_id=resolved_snapshot_id,
        publish_result=publish_result,
    )
