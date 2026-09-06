from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

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


def insert_returning_id(
    conn: Any,
    sql: str,
    params: tuple[Any, ...],
    *,
    commit: bool = True,
) -> int:
    """Execute an INSERT ... RETURNING id and return the new id."""
    from psycopg.rows import dict_row

    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cast(dict[str, object] | None, cur.fetchone())
        if row is None:
            raise ValueError("INSERT ... RETURNING id produced no row")
        row_id = row.get("id")
        if isinstance(row_id, bool) or not isinstance(row_id, int):
            raise TypeError(f"expected integer id from INSERT ... RETURNING, got {row_id!r}")
    except Exception:
        if commit:
            rollback_if_available(conn)
        raise
    if commit:
        conn.commit()
    return row_id


def relation_exists(conn: ConnectionLike, relation_name: str) -> bool:
    rows = fetch_all(
        conn,
        "SELECT to_regclass(%s) IS NOT NULL AS exists",
        (relation_name,),
    )
    if not rows:
        return False
    return bool(rows[0].get("exists"))
