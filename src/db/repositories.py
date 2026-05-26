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

    def rollback(self) -> object: ...


def _recover_test_connection(conn: Any) -> None:
    recover = getattr(conn, "_recover_test_isolation", None)
    if callable(recover):
        recover()


def rollback_if_available(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        rollback()


def ensure_transactional_for_commit(conn: Any, *, commit: bool) -> None:
    """Reject caller-owned transactions that cannot actually roll back.

    psycopg connections opened with autocommit=True commit each statement
    immediately.  Load runners that own a multi-phase transaction must fail
    before any write starts instead of pretending a later rollback can restore
    already-committed phase writes.
    """
    if commit and getattr(conn, "autocommit", False) is True:
        raise RuntimeError(
            "commit=True requires a transactional connection; received autocommit=True"
        )


def commit_or_rollback(conn: ConnectionLike) -> None:
    try:
        conn.commit()
    except Exception:
        rollback_if_available(conn)
        raise


def execute_one(
    conn: ConnectionLike,
    sql: str,
    params: Params = None,
    *,
    commit: bool = True,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    except Exception:
        if commit:
            rollback_if_available(conn)
        _recover_test_connection(conn)
        raise
    if commit:
        commit_or_rollback(conn)


def execute_many(
    conn: ConnectionLike,
    sql: str,
    params_seq: BatchParams,
    *,
    commit: bool = True,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_seq)
    except Exception:
        if commit:
            rollback_if_available(conn)
        _recover_test_connection(conn)
        raise
    if commit:
        commit_or_rollback(conn)


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
