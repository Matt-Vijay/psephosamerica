"""Generic canonical table write layer.

Exposes two public helpers:

  write_table_batch(conn, *, table, rows, conflict_columns, mode)
      Write one batch of rows to a single table.
      Returns a TableWriteResult.

  write_table_batches(conn, batches, *, run_id, warn_error)
      Write many table batches in order.
      Returns a LoadSummary.

Modes
-----
  'insert'  — plain INSERT; raises on unique-constraint violations.
  'upsert'  — INSERT … ON CONFLICT DO UPDATE SET (non-conflict columns).
  'ignore'  — INSERT … ON CONFLICT DO NOTHING; conflicting rows are skipped.

Empty batches are skipped cleanly: a TableWriteResult with all-zero counts is
returned without touching the database.

No source-specific logic lives here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from psycopg.types.json import Jsonb

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.repositories import (
    ConnectionLike,
    Row,
    commit_or_rollback,
    ensure_transactional_for_commit,
    execute_many,
)
from src.db.sql import build_insert, build_upsert, quote_identifier

# Internal helpers

_VALID_MODES = frozenset({"insert", "upsert", "ignore"})


def _build_ignore_sql(table: str, row: Row, conflict_columns: list[str]) -> tuple[str, list[Any]]:
    """Return (sql, params) for INSERT … ON CONFLICT DO NOTHING.

    conflict_columns may be empty, in which case no conflict target is emitted.
    """
    sql_base, params = build_insert(table, row)
    if conflict_columns:
        target = ", ".join(quote_identifier(column) for column in conflict_columns)
        sql = f"{sql_base} ON CONFLICT ({target}) DO NOTHING"
    else:
        sql = f"{sql_base} ON CONFLICT DO NOTHING"
    return sql, params


def _params_for_rows(
    table: str,
    rows: list[Row],
    conflict_columns: list[str],
    mode: str,
) -> tuple[str, list[list[Any]]]:
    """Derive the SQL template and list of param lists for *rows*.

    All rows in a batch are assumed to have the same column set (keyed by the
    first row).  The SQL is built once from the first row; subsequent rows
    supply values in the same column order.

    Returns (sql, [params_for_row_0, params_for_row_1, ...]).
    """
    first = rows[0]
    cols = list(first.keys())

    if mode == "insert":
        sql, _ = build_insert(table, first)
    elif mode == "upsert":
        sql, _ = build_upsert(table, first, conflict_columns)
    elif mode == "ignore":
        sql, _ = _build_ignore_sql(table, first, conflict_columns)
    else:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")

    # For upsert the param list has insert-values + update-values; rebuild each
    # row's params using the same structure (values in col order, duplicated for
    # the SET clause when needed).
    all_params: list[list[Any]] = []
    for row in rows:
        if mode == "upsert":
            _, row_params = build_upsert(table, row, conflict_columns)
        elif mode == "ignore":
            _, row_params = _build_ignore_sql(table, row, conflict_columns)
        else:
            # insert
            row_params = [row[c] for c in cols]
        all_params.append([_adapt_param(param) for param in row_params])

    return sql, all_params


def _adapt_param(value: Any) -> Any:
    if isinstance(value, dict | list):
        return Jsonb(value)
    return value


# Public API


def write_table_batch(
    conn: ConnectionLike,
    *,
    table: str,
    rows: list[Row],
    conflict_columns: list[str] | None = None,
    mode: str = "insert",
    commit: bool = True,
) -> TableWriteResult:
    """Write one batch of rows to *table* and return a TableWriteResult.

    For 'ignore' mode all rows are counted as *skipped*: psycopg execute_many
    does not distinguish accepted vs conflicting rows without per-row inspection.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")

    conflict_columns = conflict_columns or []

    if not rows:
        return TableWriteResult(table=table)
    ensure_transactional_for_commit(conn, commit=commit)

    sql, all_params = _params_for_rows(table, rows, conflict_columns, mode)
    execute_many(conn, sql, all_params, commit=commit)

    n = len(rows)
    if mode == "ignore":
        return TableWriteResult(table=table, skipped=n)
    else:
        return TableWriteResult(table=table, inserted=n)


def write_table_batches(
    conn: ConnectionLike,
    batches: Sequence[dict[str, Any]],
    *,
    run_id: int | None = None,
    warn_error: WarnErrorSummary | None = None,
    commit: bool = True,
) -> LoadSummary:
    """Write many table batches in order and return an aggregated LoadSummary.

    Each element of *batches* is a plain dict with keys:
        table            (str)        — required
        rows             (list[dict]) — required
        conflict_columns (list[str])  — optional, default []
        mode             (str)        — optional, default 'insert'

    When *commit* is true, the helper owns the transaction boundary: all
    non-empty batches are written with deferred commits, then committed once
    after every table succeeds.  On failure it rolls back once and re-raises.
    Pass ``commit=False`` when the caller already owns a broader transaction.
    """
    results: list[TableWriteResult] = []
    wrote_any = False
    ensure_transactional_for_commit(conn, commit=commit)

    try:
        for batch in batches:
            rows = cast(list[Row], batch.get("rows", []))
            result = write_table_batch(
                conn,
                table=batch["table"],
                rows=rows,
                conflict_columns=cast(list[str], batch.get("conflict_columns") or []),
                mode=cast(str, batch.get("mode", "insert")),
                commit=False,
            )
            wrote_any = wrote_any or bool(rows)
            results.append(result)
    except Exception:
        if commit:
            conn.rollback()
        raise

    if commit and wrote_any:
        commit_or_rollback(conn)

    return build_load_summary(results, warn_error=warn_error, run_id=run_id)
