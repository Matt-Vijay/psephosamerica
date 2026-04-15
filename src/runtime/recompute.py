"""Top-level orchestrator for a full conflict-of-interest recompute pass.

Wraps provenance bookkeeping (ensure data source, open/close ingestion run)
around the DB-backed recompute in src/pipeline/recompute_run.py.

Call run_recompute_runtime and pass an open psycopg connection.
Everything else has a sensible default.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

from src.pipeline.recompute_run import RecomputeRunResult, run_recompute
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.paths import repo_root
from src.runtime.sources import CONFLICT_RECOMPUTE

_SOURCE_SLUG = CONFLICT_RECOMPUTE.slug
_SOURCE_NAME = CONFLICT_RECOMPUTE.name
_SOURCE_KIND = CONFLICT_RECOMPUTE.source_kind

# ---------------------------------------------------------------------------
# Default resolvers
# ---------------------------------------------------------------------------

IssuerSectorResolver = Callable[[str, str | None], str | None]


def _null_issuer_sector_resolver(issuer_name: str, issuer_ticker: str | None) -> str | None:  # noqa: ARG001
    """Deterministic no-op: always returns None (no sector inferred from issuer)."""
    return None


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RuntimeRecomputeResult:
    data_source: dict[str, Any]
    run_id: int
    recompute_result: RecomputeRunResult


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_recompute_runtime(
    conn: Any,
    snapshot_date: dt.date,
    *,
    taxonomy: Any | None = None,
    issuer_sector_resolver: IssuerSectorResolver | None = None,
) -> RuntimeRecomputeResult:
    """Run a full conflict-of-interest recompute and return its result.

    Steps:
        1. Ensure the internal data_source row exists.
        2. Open an ingestion_run row in 'running' status.
        3. Delegate to run_recompute (DB-backed six-step orchestration).
        4. Mark the run succeeded (or failed on any exception, then re-raise).

    Args:
        conn:                   Open psycopg connection.
        snapshot_date:          Date key for score snapshots and disclosure
                                filing-year boundary.
        taxonomy:               Pre-loaded TaxonomyRuntime.  When None this
                                module loads the artifacts from data/ at the
                                repo root.
        issuer_sector_resolver: Callable(issuer_name, issuer_ticker) → sector
                                slug or None.  When None the deterministic
                                null resolver is used (no sector inferred).
    """
    if taxonomy is None:
        from src.normalize.taxonomy_runtime import load_taxonomy_runtime

        taxonomy = load_taxonomy_runtime(repo_root() / "data")

    if issuer_sector_resolver is None:
        issuer_sector_resolver = _null_issuer_sector_resolver

    # ------------------------------------------------------------------ 1. --
    data_source = ensure_data_source(
        conn,
        slug=_SOURCE_SLUG,
        name=_SOURCE_NAME,
        source_kind=_SOURCE_KIND,
    )

    # ------------------------------------------------------------------ 2. --
    run_id = start_ingestion_run(
        conn,
        data_source_id=data_source["id"],
        run_type="recompute",
        parameters={"snapshot_date": snapshot_date.isoformat()},
    )

    # ------------------------------------------------------------------ 3. --
    try:
        recompute_result = run_recompute(
            conn,
            recompute_run_id=run_id,
            snapshot_date=snapshot_date,
            taxonomy=taxonomy,
            issuer_sector_resolver=issuer_sector_resolver,
        )
    except Exception as exc:
        fail_ingestion_run(conn, run_id, error_message=str(exc))
        raise

    # ------------------------------------------------------------------ 4. --
    record_count = len(recompute_result.rule_fires) + len(recompute_result.evidence_cards)
    finish_ingestion_run(conn, run_id, record_count=record_count)

    return RuntimeRecomputeResult(
        data_source=data_source,
        run_id=run_id,
        recompute_result=recompute_result,
    )
