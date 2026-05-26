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
from src.db.repositories import (
    commit_or_rollback,
    ensure_transactional_for_commit,
    rollback_if_available,
)
from src.db.sql import quote_identifier
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
    *,
    warn_unresolved_supersedes: bool = True,
) -> list[dict[str, Any]]:
    """Return DB-ready rows for financial_disclosure.

    Strips ``member_bioguide_id`` and ``supersedes_filing_source_id`` natural-key
    hints and injects resolved integer FKs. Rows whose bioguide_id is not in the
    bundle are skipped with a warning. Unresolved supersession hints keep the row
    loadable and emit a warning because the FK is nullable.
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
        supersedes_source_id = row.get("supersedes_filing_source_id")
        if supersedes_source_id:
            superseded_id = bundle.disclosure_source_record_id_map.get(str(supersedes_source_id))
            if superseded_id is None:
                if warn_unresolved_supersedes:
                    warn_error.add_warning(
                        "Leaving financial_disclosure supersession unresolved: "
                        f"supersedes_filing_source_id={supersedes_source_id!r}"
                    )
            else:
                clean["supersedes_financial_disclosure_id"] = superseded_id
        out.append(clean)
    return out


def _resolve_supersession_update_rows(
    rows: tuple[dict[str, Any], ...],
    bundle: LookupBundle,
    warn_error: WarnErrorSummary,
) -> list[dict[str, Any]]:
    candidates = tuple(row for row in rows if row.get("supersedes_filing_source_id"))
    if not candidates:
        return []
    resolved = _resolve_disclosure_rows(candidates, bundle, warn_error)
    return [row for row in resolved if row.get("supersedes_financial_disclosure_id") is not None]


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
                f"Skipping {table} row: unresolved member_id for bioguide_id={bioguide_id!r}"
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
                f"Skipping {table} row: unresolved financial_disclosure_id for natural key={nk!r}"
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
    commit: bool = True,
) -> list[TableWriteResult]:
    """Run one table batch via execute_load_plan; return its TableWriteResult list."""
    if not rows:
        return []
    summary = execute_load_plan(
        conn,
        [{"table": table, "rows": rows, "conflict_columns": conflict_columns, "mode": mode}],
        run_id=run_id,
        warn_error=warn_error,
        commit=commit,
    )
    return list(summary.table_results)


def _resolve_loaded_disclosure_ids(
    rows: tuple[dict[str, Any], ...],
    bundle: LookupBundle,
    warn_error: WarnErrorSummary,
) -> set[int]:
    """Return DB ids for disclosure rows visible after the parent upsert phase."""
    fd_ids: set[int] = set()
    for row in rows:
        bioguide_id = row.get("member_bioguide_id")
        member_id = bundle.bioguide_map.get(bioguide_id) if bioguide_id else None
        if member_id is None:
            continue
        nk = (
            member_id,
            row["filing_year"],
            row["filing_type"],
            row["amendment_number"],
        )
        fd_id = bundle.disclosure_natural_key_map.get(nk)
        if fd_id is None:
            warn_error.add_warning(
                "Skipping stale disclosure child cleanup: unresolved "
                f"financial_disclosure_id for natural key={nk!r}"
            )
            continue
        fd_ids.add(fd_id)
    return fd_ids


def _line_numbers_by_disclosure(rows: list[dict[str, Any]]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for row in rows:
        out.setdefault(row["financial_disclosure_id"], set()).add(row["line_number"])
    return out


def _delete_stale_child_rows(
    conn: Any,
    table: str,
    disclosure_ids: set[int],
    keep_line_numbers: dict[int, set[int]],
) -> None:
    """Prune child rows omitted by the current full parse for each disclosure."""
    if table not in {"holding", "transaction"}:
        raise ValueError(f"unsupported disclosure child table: {table!r}")
    if not disclosure_ids:
        return

    table_sql = quote_identifier(table)
    fd_col = quote_identifier("financial_disclosure_id")
    line_col = quote_identifier("line_number")
    with conn.cursor() as cur:
        for disclosure_id in sorted(disclosure_ids):
            kept = tuple(sorted(keep_line_numbers.get(disclosure_id, set())))
            if kept:
                placeholders = ", ".join("%s" for _ in kept)
                # Safe dynamic SQL: table/column names come from a fixed allowlist.
                sql = (
                    f"DELETE FROM {table_sql} WHERE {fd_col} = %s "  # nosec B608
                    f"AND {line_col} NOT IN ({placeholders})"
                )
                params = (disclosure_id, *kept)
            else:
                sql = (
                    f"DELETE FROM {table_sql} WHERE {fd_col} = %s "  # nosec B608
                ).strip()
                params = (disclosure_id,)
            cur.execute(sql, params)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_disclosures_load(
    results: list[DisclosureTransformResult],
    conn: Any,
    *,
    lookup_loader: LookupLoaderFn,
    run_id: int | None = None,
    commit: bool = True,
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
    ensure_transactional_for_commit(conn, commit=commit)

    try:
        # Phase 1 — financial_disclosure (member_id resolution)
        bundle = lookup_loader(conn)
        fd_batch = batch_map.get("financial_disclosure")
        if fd_batch:
            resolved = _resolve_disclosure_rows(
                fd_batch.rows,
                bundle,
                warn_error,
                warn_unresolved_supersedes=False,
            )
            all_results.extend(
                _exec_phase(
                    conn,
                    "financial_disclosure",
                    resolved,
                    _FD_CONFLICT,
                    "upsert",
                    warn_error,
                    run_id,
                    commit=False,
                )
            )

        # Lookup refresh — captures IDs of rows written in phase 1
        bundle = lookup_loader(conn)
        if fd_batch:
            supersession_updates = _resolve_supersession_update_rows(
                fd_batch.rows,
                bundle,
                warn_error,
            )
            all_results.extend(
                _exec_phase(
                    conn,
                    "financial_disclosure",
                    supersession_updates,
                    _FD_CONFLICT,
                    "upsert",
                    warn_error,
                    run_id,
                    commit=False,
                )
            )
        loaded_disclosure_ids = (
            _resolve_loaded_disclosure_ids(fd_batch.rows, bundle, warn_error) if fd_batch else set()
        )

        # Phase 2 — holding (financial_disclosure_id resolution)
        holding_batch = batch_map.get("holding")
        if holding_batch:
            resolved_h = _resolve_child_rows(holding_batch.rows, bundle, warn_error, "holding")
            _delete_stale_child_rows(
                conn,
                "holding",
                loaded_disclosure_ids,
                _line_numbers_by_disclosure(resolved_h),
            )
            all_results.extend(
                _exec_phase(
                    conn,
                    "holding",
                    resolved_h,
                    _CHILD_CONFLICT,
                    "upsert",
                    warn_error,
                    run_id,
                    commit=False,
                )
            )

        # Phase 2 — transaction (financial_disclosure_id resolution)
        tx_batch = batch_map.get("transaction")
        if tx_batch:
            resolved_t = _resolve_child_rows(tx_batch.rows, bundle, warn_error, "transaction")
            _delete_stale_child_rows(
                conn,
                "transaction",
                loaded_disclosure_ids,
                _line_numbers_by_disclosure(resolved_t),
            )
            all_results.extend(
                _exec_phase(
                    conn,
                    "transaction",
                    resolved_t,
                    _CHILD_CONFLICT,
                    "upsert",
                    warn_error,
                    run_id,
                    commit=False,
                )
            )

        # Phase 3 — review_queue (self-contained rows; no FK resolution)
        rq_batch = batch_map.get("review_queue")
        if rq_batch and rq_batch.rows:
            all_results.extend(
                _exec_phase(
                    conn,
                    "review_queue",
                    list(rq_batch.rows),
                    [],
                    "insert",
                    warn_error,
                    run_id,
                    commit=False,
                )
            )
    except Exception:
        if commit:
            rollback_if_available(conn)
        raise

    if commit and all_results:
        commit_or_rollback(conn)

    summary = build_load_summary(all_results, warn_error=warn_error, run_id=run_id)
    return summary, plan.sidecars
