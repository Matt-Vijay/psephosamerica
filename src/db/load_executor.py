"""Load executor: runs ordered operation plans against a DB connection.

Bridges the pure load-plan layer (src/load/*) with the write layer (db/writer.py).

Responsibilities
----------------
1. Skip non-canonical hint tables (table names starting with ``_``).
2. Resolve FK hint keys (underscore-prefixed row keys) to real DB ids.
3. Strip hint keys before handing rows to the writer.
4. Return a LoadSummary.

FK resolvers are injected so this module stays free of DB queries:
the caller provides resolver callables that already have access to the
relevant DB state (cached lookups, pre-fetched dicts, etc.).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.writer import write_table_batch

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

# A resolver entry pairs the target FK column name with a callable that
# receives the *entire* raw row dict and returns the resolved integer id
# (or None when resolution fails).  Receiving the full row lets callers
# implement multi-key lookups, e.g. committee resolved from both
# ``_committee_code`` and ``_congress``.
ResolverFn = Callable[[dict[str, Any]], int | None]

# Resolvers dict: hint_key -> (target_fk_column, resolver_fn)
# Example:
#   {
#     "_bioguide_id":    ("member_id",    lambda row: member_cache[row["_bioguide_id"]]),
#     "_committee_code": ("committee_id", lambda row: committee_cache[(row["_committee_code"],
#                                                                        row["_congress"])]),
#   }
Resolvers = dict[str, tuple[str, ResolverFn]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_hint_table(table: str) -> bool:
    """Return True for underscore-prefixed hint sentinels (e.g. ``_fec_linkage_hint``)."""
    return table.startswith("_")


def _resolve_row(row: dict[str, Any], resolvers: Resolvers) -> dict[str, Any]:
    """Return a new row dict with hint keys stripped and FK columns filled in.

    For each underscore-prefixed key:
      - If a resolver entry exists for it, call the resolver with the full
        raw row and write the result into the entry's target column.
      - If no resolver entry exists, the hint key is silently dropped.

    The original *row* dict is not mutated.
    """
    out: dict[str, Any] = {}
    resolved_columns: set[str] = set()

    for key, value in row.items():
        if not key.startswith("_"):
            out[key] = value
            continue
        entry = resolvers.get(key)
        if entry is not None:
            target_col, fn = entry
            if target_col not in resolved_columns:
                out[target_col] = fn(row)
                resolved_columns.add(target_col)
        # hint keys without a resolver are stripped (not forwarded to writer)

    return out


def _prepare_rows(rows: list[dict[str, Any]], resolvers: Resolvers) -> list[dict[str, Any]]:
    """Apply FK resolution and hint-key stripping to every row in *rows*.

    When *resolvers* is empty every hint key is stripped without any
    resolution calls (fast path).
    """
    if not resolvers:
        return [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    return [_resolve_row(row, resolvers) for row in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def execute_load_plan(
    conn: Any,
    operations: Sequence[dict[str, Any]],
    *,
    resolvers: Resolvers | None = None,
    run_id: int | None = None,
    warn_error: WarnErrorSummary | None = None,
) -> LoadSummary:
    """Execute an ordered sequence of load-plan operation dicts.

    Args:
        conn:       Open psycopg connection (or compatible mock in tests).
        operations: Ordered list of operation dicts produced by src/load/*.
                    Each dict must carry:
                      ``table``              str         Canonical table name or hint sentinel.
                      ``rows``               list[dict]  Row dicts; may contain hint keys.
                      ``conflict_columns``   list[str]   Optional conflict target (default []).
                      ``mode``               str         Optional write mode (default 'insert').
        resolvers:  Optional mapping of hint keys to (target_column, resolver_fn) pairs.
                    The resolver callable receives the full raw row dict so multi-key
                    lookups are supported.  Hint keys without a matching entry are
                    stripped silently.
        run_id:     Optional ingestion run id to embed in the returned LoadSummary.
        warn_error: Optional WarnErrorSummary to embed; a fresh one is created if omitted.

    """
    if warn_error is None:
        warn_error = WarnErrorSummary()

    _resolvers: Resolvers = resolvers or {}
    table_results: list[TableWriteResult] = []

    for op in operations:
        table: str = op["table"]

        if _is_hint_table(table):
            continue

        rows: list[dict[str, Any]] = list(op.get("rows", []))
        conflict_columns: list[str] = op.get("conflict_columns") or []
        mode: str = op.get("mode", "insert")

        clean_rows = _prepare_rows(rows, _resolvers)

        result = write_table_batch(
            conn,
            table=table,
            rows=clean_rows,
            conflict_columns=conflict_columns,
            mode=mode,
        )
        table_results.append(result)

    return build_load_summary(table_results, warn_error=warn_error, run_id=run_id)
