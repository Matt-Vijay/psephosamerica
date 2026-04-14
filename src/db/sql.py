"""Pure SQL generation helpers for psycopg %s-style placeholders.

All functions are deterministic: column order follows the dict insertion
order of the supplied row, which is guaranteed in Python 3.7+.

No DB I/O is performed here.
"""

from __future__ import annotations


def _columns(row: dict) -> list[str]:
    """Return column names in insertion order."""
    return list(row.keys())


def _placeholders(row: dict) -> list[str]:
    """Return one '%s' placeholder per column, in insertion order."""
    return ["%s"] * len(row)


def _values(row: dict) -> list:
    """Return values in insertion order."""
    return list(row.values())


def build_insert(table: str, row: dict) -> tuple[str, list]:
    """Return (sql, params) for a plain INSERT.

    Args:
        table: unquoted table name.
        row:   mapping of column→value; must be non-empty.

    Returns:
        A (sql_string, params_list) pair ready for psycopg execution.
    """
    if not row:
        raise ValueError("row must contain at least one column")

    cols = _columns(row)
    placeholders = _placeholders(row)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)})"
        f" VALUES ({', '.join(placeholders)})"
    )
    return sql, _values(row)


def build_upsert(
    table: str,
    row: dict,
    conflict_columns: list[str],
) -> tuple[str, list]:
    """Return (sql, params) for an INSERT … ON CONFLICT DO UPDATE.

    Columns listed in *conflict_columns* are used in the ON CONFLICT clause
    and are excluded from the SET list.  The SET list preserves the key order
    of *row* minus the conflict columns.

    Args:
        table:            unquoted table name.
        row:              mapping of column→value; must be non-empty.
        conflict_columns: columns that form the unique/conflict target;
                          all must be present in *row*.

    Returns:
        A (sql_string, params_list) pair ready for psycopg execution.
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
        conflict_target = ", ".join(conflict_columns)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)})"
            f" VALUES ({', '.join(placeholders)})"
            f" ON CONFLICT ({conflict_target}) DO NOTHING"
        )
        return sql, _values(row)

    cols = _columns(row)
    placeholders = _placeholders(row)
    conflict_target = ", ".join(conflict_columns)
    set_clause = ", ".join(f"{c} = %s" for c in update_cols)
    update_values = [row[c] for c in update_cols]

    sql = (
        f"INSERT INTO {table} ({', '.join(cols)})"
        f" VALUES ({', '.join(placeholders)})"
        f" ON CONFLICT ({conflict_target}) DO UPDATE SET {set_clause}"
    )
    return sql, _values(row) + update_values


def derive_update_columns(row: dict, exclude: list[str]) -> list[str]:
    """Return columns from *row* that are not in *exclude*, in insertion order.

    Used to build the SET list for upserts: primary-key and conflict columns
    are excluded so the generated statement remains valid.

    Args:
        row:     the row dict whose keys define available columns.
        exclude: columns to omit (typically PK + conflict target columns).

    Returns:
        List of column names in the original key order of *row*.
    """
    exclude_set = set(exclude)
    return [c for c in row if c not in exclude_set]
