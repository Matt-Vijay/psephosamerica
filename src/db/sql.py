"""Pure SQL generation helpers for psycopg %s-style placeholders.

All functions are deterministic: column order follows the dict insertion
order of the supplied row, which is guaranteed in Python 3.7+.

No DB I/O is performed here.
"""

from __future__ import annotations

import re
from typing import Any

Row = dict[str, Any]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _columns(row: Row) -> list[str]:
    return list(row.keys())


def _placeholders(row: Row) -> list[str]:
    return ["%s"] * len(row)


def _values(row: Row) -> list[Any]:
    return list(row.values())


def quote_identifier(identifier: str) -> str:
    """Quote an internal SQL identifier after strict validation."""
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"invalid SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def _identifier_list(identifiers: list[str]) -> str:
    return ", ".join(quote_identifier(identifier) for identifier in identifiers)


def build_insert(table: str, row: Row) -> tuple[str, list[Any]]:
    """Return (sql, params) for a plain INSERT."""
    if not row:
        raise ValueError("row must contain at least one column")

    cols = _columns(row)
    placeholders = _placeholders(row)
    # Safe dynamic SQL: table and columns are strictly validated identifiers.
    sql = (
        f"INSERT INTO {quote_identifier(table)} ({_identifier_list(cols)})"  # nosec B608
        f" VALUES ({', '.join(placeholders)})"
    )
    return sql, _values(row)


def build_upsert(
    table: str,
    row: Row,
    conflict_columns: list[str],
) -> tuple[str, list[Any]]:
    """Return (sql, params) for an INSERT … ON CONFLICT DO UPDATE.

    *conflict_columns* are used in the ON CONFLICT clause and excluded from
    the SET list.  When no non-conflict columns remain, emits DO NOTHING.
    params contains insert-values followed by update-values.
    """
    if not row:
        raise ValueError("row must contain at least one column")
    if not conflict_columns:
        raise ValueError("conflict_columns must contain at least one column")

    missing = [c for c in conflict_columns if c not in row]
    if missing:
        raise ValueError(f"conflict_columns not found in row: {missing}")

    update_cols = derive_update_columns(row, conflict_columns)
    if not update_cols:
        # Nothing to update: use DO NOTHING
        cols = _columns(row)
        placeholders = _placeholders(row)
        conflict_target = _identifier_list(conflict_columns)
        # Safe dynamic SQL: table, columns, and conflict target are validated identifiers.
        sql = (
            f"INSERT INTO {quote_identifier(table)} ({_identifier_list(cols)})"  # nosec B608
            f" VALUES ({', '.join(placeholders)})"
            f" ON CONFLICT ({conflict_target}) DO NOTHING"
        )
        return sql, _values(row)

    cols = _columns(row)
    placeholders = _placeholders(row)
    conflict_target = _identifier_list(conflict_columns)
    set_clause = ", ".join(f"{quote_identifier(c)} = %s" for c in update_cols)
    update_values = [row[c] for c in update_cols]

    # Safe dynamic SQL: table, columns, and conflict target are validated identifiers.
    sql = (
        f"INSERT INTO {quote_identifier(table)} ({_identifier_list(cols)})"  # nosec B608
        f" VALUES ({', '.join(placeholders)})"
        f" ON CONFLICT ({conflict_target}) DO UPDATE SET {set_clause}"
    )
    return sql, _values(row) + update_values


def derive_update_columns(row: Row, exclude: list[str]) -> list[str]:
    """Return columns from *row* not in *exclude*, preserving insertion order.

    Used to build the SET list for upserts, where PK and conflict columns
    must be excluded so the generated statement remains valid.
    """
    exclude_set = set(exclude)
    return [c for c in row if c not in exclude_set]
