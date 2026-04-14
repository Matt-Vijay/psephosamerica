"""Generic low-level repository helpers.

All functions accept an open psycopg Connection (or compatible cursor factory)
and return plain Python objects — no ORM, no SQLAlchemy.

Exposes:
  execute_one(conn, sql, params)  -> None
  execute_many(conn, sql, params) -> None
  fetch_all(conn, sql, params)    -> list[dict]
"""

from __future__ import annotations

from typing import Any, Sequence


def execute_one(conn, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> None:
    """Execute a single statement (INSERT, UPDATE, DELETE) and commit."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def execute_many(
    conn,
    sql: str,
    params_seq: Sequence[Sequence[Any] | dict[str, Any]],
) -> None:
    """Execute *sql* for each parameter set in *params_seq* and commit."""
    with conn.cursor() as cur:
        cur.executemany(sql, params_seq)
    conn.commit()


def fetch_all(
    conn, sql: str, params: Sequence[Any] | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Execute a SELECT and return all rows as a list of dicts.

    Uses psycopg's RealDictCursor (or the dict_row row factory) so callers
    receive column-name-keyed dicts without any post-processing.
    """
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
