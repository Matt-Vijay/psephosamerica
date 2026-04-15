"""Live disclosure load runner: transform results → FK-safe DB writes.

Orchestrates three write phases, refreshing lookup maps between phase 1 and
phase 2 so newly inserted financial_disclosure rows are visible when holdings
and transactions are resolved.

Phase order
-----------
1. financial_disclosure  — needs member_id from bioguide_map
   [lookup refresh]
2. holding               — needs financial_disclosure_id from refreshed map
3. transaction           — same
4. review_queue          — rows are self-contained; no FK resolution required

The caller injects a ``lookup_loader`` callable so this module has no DB
query logic and remains fully testable without a real database.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.db.load_executor import execute_load_plan
from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.lookups import LookupBundle
from src.load.disclosures import plan_disclosure_load
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    OutsidePositionSidecar,
)

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

LookupLoaderFn = Callable[[Any], LookupBundle]

# ---------------------------------------------------------------------------
# Conflict columns per table
# ---------------------------------------------------------------------------

_FD_CONFLICT = ["member_id", "filing_year", "filing_type", "amendment_number"]
_CHILD_CONFLICT = ["financial_disclosure_id", "line_number"]

# ---------------------------------------------------------------------------
# Internal row resolvers
# ---------------------------------------------------------------------------


def _resolve_disclosure_rows(
    rows: tuple[dict[str, Any], ...],
    bundle: LookupBundle,
    warn_error: WarnErrorSummary,
) -> list[dict[str, Any]]:
    """Return DB-ready rows for financial_disclosure.

    Strips ``member_bioguide_id`` and ``supersedes_filing_source_id`` (natural-key
    hints) and injects the resolved ``member_id`` integer FK.  Rows whose
    bioguide_id is not in the bundle are skipped with a warning.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        bioguide_id = row.get("member_bioguide_id")
        member_id = bundle.bioguide_map.get(bioguide_id) if bioguide_id else None
        if member_id is None:
            warn_error.add_warning(
                f"Skipping financial_disclosure row: unresolved member_id for "
                f"bioguide_id={bioguide_id!r}"
            )
            continue
        clean = {
            k: v
            for k, v in row.items()
            if k not in ("member_bioguide_id", "supersedes_filing_source_id")
        }
        clean["member_id"] = member_id
        out.append(clean)
    return out


def _resolve_child_rows(
    rows: tuple[dict[str, Any], ...],
    bundle: LookupBundle,
    warn_error: WarnErrorSummary,
    table: str,
) -> list[dict[str, Any]]:
    """Return DB-ready rows for holding or transaction.

    Strips the four ``disclosure_*`` natural-key fields and injects the
    resolved ``financial_disclosure_id`` integer FK.  Rows that cannot be
    resolved (member or disclosure not found) are skipped with a warning.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        bioguide_id = row.get("disclosure_member_bioguide_id")
        member_id = bundle.bioguide_map.get(bioguide_id) if bioguide_id else None
        if member_id is None:
            warn_error.add_warning(
                f"Skipping {table} row: unresolved member_id for "
                f"bioguide_id={bioguide_id!r}"
            )
            continue
        nk = (
            member_id,
            row["disclosure_filing_year"],
            row["disclosure_filing_type"],
            row["disclosure_amendment_number"],
        )
        fd_id = bundle.disclosure_natural_key_map.get(nk)
        if fd_id is None:
            warn_error.add_warning(
                f"Skipping {table} row: unresolved financial_disclosure_id for "
                f"natural key={nk!r}"
            )
            continue
        clean = {k: v for k, v in row.items() if not k.startswith("disclosure_")}
        clean["financial_disclosure_id"] = fd_id
        out.append(clean)
    return out


# ---------------------------------------------------------------------------
# Phase execution helper
# ---------------------------------------------------------------------------


def _exec_phase(
    conn: Any,
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    mode: str,
    warn_error: WarnErrorSummary,
    run_id: int | None,
) -> list[TableWriteResult]:
    """Run one table batch via execute_load_plan; return its TableWriteResult list."""
    if not rows:
        return []
    summary = execute_load_plan(
        conn,
        [{"table": table, "rows": rows, "conflict_columns": conflict_columns, "mode": mode}],
        run_id=run_id,
        warn_error=warn_error,
    )
    return list(summary.table_results)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_disclosures_load(
    results: list[DisclosureTransformResult],
    conn: Any,
    *,
    lookup_loader: LookupLoaderFn,
    run_id: int | None = None,
) -> tuple[LoadSummary, tuple[OutsidePositionSidecar, ...]]:
    """Execute a disclosure load in FK-safe phases with inter-phase lookup refreshes.

    Args:
        results:       Batch of transform results to write.
        conn:          Open psycopg connection (or compatible mock in tests).
        lookup_loader: ``Callable[[conn], LookupBundle]`` — called twice: once
                       before phase 1 (member resolution) and once after phase 1
                       (disclosure FK resolution).  Inject a mock in tests.
        run_id:        Optional ingestion run id embedded in the returned summary.

    Returns:
        ``(LoadSummary, sidecars)`` — sidecars are outside-position payloads
        with no canonical table in v1; callers may forward them to review_queue
        or archive them separately.
    """
    plan = plan_disclosure_load(results)
    warn_error = WarnErrorSummary()
    all_results: list[TableWriteResult] = []
    batch_map = {b.table: b for b in plan.batches}

    # Phase 1 — financial_disclosure (member_id resolution)
    bundle = lookup_loader(conn)
    fd_batch = batch_map.get("financial_disclosure")
    if fd_batch:
        resolved = _resolve_disclosure_rows(fd_batch.rows, bundle, warn_error)
        all_results.extend(
            _exec_phase(conn, "financial_disclosure", resolved, _FD_CONFLICT, "upsert", warn_error, run_id)
        )

    # Lookup refresh — captures IDs of rows written in phase 1
    bundle = lookup_loader(conn)

    # Phase 2 — holding (financial_disclosure_id resolution)
    holding_batch = batch_map.get("holding")
    if holding_batch:
        resolved_h = _resolve_child_rows(holding_batch.rows, bundle, warn_error, "holding")
        all_results.extend(
            _exec_phase(conn, "holding", resolved_h, _CHILD_CONFLICT, "upsert", warn_error, run_id)
        )

    # Phase 2 — transaction (financial_disclosure_id resolution)
    tx_batch = batch_map.get("transaction")
    if tx_batch:
        resolved_t = _resolve_child_rows(tx_batch.rows, bundle, warn_error, "transaction")
        all_results.extend(
            _exec_phase(conn, "transaction", resolved_t, _CHILD_CONFLICT, "upsert", warn_error, run_id)
        )

    # Phase 3 — review_queue (self-contained rows; no FK resolution)
    rq_batch = batch_map.get("review_queue")
    if rq_batch and rq_batch.rows:
        all_results.extend(
            _exec_phase(conn, "review_queue", list(rq_batch.rows), [], "insert", warn_error, run_id)
        )

    summary = build_load_summary(all_results, warn_error=warn_error, run_id=run_id)
    return summary, plan.sidecars
