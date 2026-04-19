from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

Row = dict[str, Any]
Params = Sequence[Any] | Mapping[str, Any] | None
ParamRow = Sequence[Any] | Mapping[str, Any]
BatchParams = Sequence[ParamRow]


class CursorLike(Protocol):
    def __enter__(self) -> CursorLike: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...

    def execute(self, sql: str, params: Params = None) -> object: ...

    def executemany(self, sql: str, params_seq: BatchParams) -> object: ...

    def fetchall(self) -> list[Row]: ...


class ConnectionLike(Protocol):
    def cursor(self, *, row_factory: Any | None = None) -> CursorLike: ...

    def commit(self) -> object: ...


def _recover_test_connection(conn: Any) -> None:
    recover = getattr(conn, "_recover_test_isolation", None)
    if callable(recover):
        recover()


def execute_one(conn: ConnectionLike, sql: str, params: Params = None) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    except Exception:
        _recover_test_connection(conn)
        raise
    conn.commit()


def execute_many(
    conn: ConnectionLike,
    sql: str,
    params_seq: BatchParams,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_seq)
    except Exception:
        _recover_test_connection(conn)
        raise
    conn.commit()


def fetch_all(
    conn: ConnectionLike,
    sql: str,
    params: Params = None,
) -> list[Row]:
    from psycopg.rows import dict_row

    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        _recover_test_connection(conn)
        raise
