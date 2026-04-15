from __future__ import annotations

from typing import Any, Sequence


def execute_one(conn, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def execute_many(
    conn,
    sql: str,
    params_seq: Sequence[Sequence[Any] | dict[str, Any]],
) -> None:
    with conn.cursor() as cur:
        cur.executemany(sql, params_seq)
    conn.commit()


def fetch_all(
    conn, sql: str, params: Sequence[Any] | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
