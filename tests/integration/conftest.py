"""Integration test fixtures — real Postgres, no mocks.

Accepts either PSEPHOS_TEST_POSTGRES_DSN or PSEPHOS_POSTGRES_DSN.
All tests in this directory skip cleanly when neither DSN is present.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

_TEST_DSN_ENV = "PSEPHOS_TEST_POSTGRES_DSN"
_APP_DSN_ENV = "PSEPHOS_POSTGRES_DSN"
_DSN = os.environ.get(_TEST_DSN_ENV) or os.environ.get(_APP_DSN_ENV)

if _DSN:
    os.environ.setdefault(_TEST_DSN_ENV, _DSN)
    os.environ.setdefault(_APP_DSN_ENV, _DSN)

# Skip the entire integration directory when no DSN is available.
pytestmark = pytest.mark.skipif(
    not _DSN,
    reason="PSEPHOS_TEST_POSTGRES_DSN or PSEPHOS_POSTGRES_DSN not set",
)

_SAVEPOINT_NAME = "psephosamerica_test_guard"
_SCHEMA_PRIVILEGE_SQLSTATE = "42501"
_LEADING_TX_WRAPPER_RE = re.compile(
    r"(?is)\A(?:\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/\s*)*)"
    r"(?:BEGIN(?:\s+TRANSACTION)?|START\s+TRANSACTION)\s*;\s*"
)
_TRAILING_TX_WRAPPER_RE = re.compile(
    r"(?is)\s*(?:COMMIT|ROLLBACK)\s*;\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/\s*)*\Z"
)
_INNER_TX_CONTROL_RE = re.compile(
    r"(?im)^\s*(?:BEGIN(?:\s+TRANSACTION)?|START\s+TRANSACTION|COMMIT|ROLLBACK)\s*;\s*$"
)


@pytest.fixture(autouse=True)
def _block_network():
    """Override the root-level network guard for integration tests.

    Integration tests need real Postgres connections, so we disable
    the socket-level block that the root conftest installs.
    """
    yield


class _IsolatedCursor:
    def __init__(self, cursor: Any, owner: "_IsolatedConnection") -> None:
        self._cursor = cursor
        self._owner = owner

    def __enter__(self) -> "_IsolatedCursor":
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        return self._cursor.__exit__(exc_type, exc, tb)

    def execute(self, sql: Any, params: Any = None) -> Any:
        sql = _normalize_sql_for_test_isolation(sql, params)
        if isinstance(sql, str) and not sql.strip():
            return None
        try:
            return self._cursor.execute(sql, params)
        except Exception:
            self._owner._recover_test_isolation()
            raise

    def executemany(self, sql: Any, params_seq: Any) -> Any:
        try:
            return self._cursor.executemany(sql, params_seq)
        except Exception:
            self._owner._recover_test_isolation()
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _IsolatedConnection:
    def __init__(self, conn: Any, *, schema_name: str) -> None:
        self._conn = conn
        self.schema_name = schema_name
        self._scope_started = False

    def begin_test_scope(self) -> None:
        if self._scope_started:
            return
        with self._conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(f"SET search_path TO {self.schema_name}, public")
            cur.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")
        self._scope_started = True

    def commit(self) -> None:
        if not self._scope_started:
            self._conn.commit()
            return
        self._restart_guard_savepoint()

    def rollback(self) -> None:
        if not self._scope_started:
            self._conn.rollback()
            return
        self._recover_test_isolation()

    def cursor(self, *args: Any, **kwargs: Any) -> _IsolatedCursor:
        return _IsolatedCursor(self._conn.cursor(*args, **kwargs), self)

    def finish_test_scope(self) -> None:
        if not self._scope_started:
            return
        try:
            self._conn.rollback()
        finally:
            self._scope_started = False

    def close(self) -> None:
        self._conn.close()

    def _recover_test_isolation(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT_NAME}")
        self._restart_guard_savepoint()

    def _restart_guard_savepoint(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f"RELEASE SAVEPOINT {_SAVEPOINT_NAME}")
            cur.execute(f"SAVEPOINT {_SAVEPOINT_NAME}")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _new_schema_name() -> str:
    return f"psephosamerica_test_{uuid.uuid4().hex}"


def _normalize_sql_for_test_isolation(sql: Any, params: Any = None) -> Any:
    if params is not None or not isinstance(sql, str):
        return sql

    normalized = _strip_outer_transaction_wrapper(sql)
    if _INNER_TX_CONTROL_RE.search(normalized):
        raise RuntimeError(
            "Disposable-schema harness does not allow in-script transaction control "
            "inside isolated tests"
        )
    return normalized


def _strip_outer_transaction_wrapper(sql: str) -> str:
    normalized = sql
    leading = _LEADING_TX_WRAPPER_RE.match(normalized)
    if leading is None:
        return normalized

    normalized = normalized[leading.end() :]
    trailing = _TRAILING_TX_WRAPPER_RE.search(normalized)
    if trailing is None:
        return normalized
    return normalized[: trailing.start()]


def _set_search_path(conn: Any, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_name}, public")


def _open_admin_connection():
    import psycopg

    return psycopg.connect(_DSN, autocommit=True)


def _create_schema(schema_name: str) -> None:
    try:
        with _open_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA {schema_name}")
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == _SCHEMA_PRIVILEGE_SQLSTATE:
            pytest.skip(
                "Disposable-schema integration harness requires CREATE SCHEMA "
                "and DROP SCHEMA privileges on the target database"
            )
        raise


def _drop_schema(schema_name: str) -> None:
    with _open_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


def _open_external_connection(schema_name: str):
    import psycopg

    conn = psycopg.connect(_DSN, autocommit=True)
    _set_search_path(conn, schema_name)
    return conn


def _current_schema_name(conn: Any) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT current_schema()")
        row = cur.fetchone()
    assert row is not None
    return row[0]


def _cleanup_test_connection(conn: Any, schema_name: str) -> None:
    cleanup_error: Exception | None = None

    try:
        conn.finish_test_scope()
    except Exception as exc:
        cleanup_error = exc

    try:
        conn.close()
    except Exception as exc:
        if cleanup_error is None:
            cleanup_error = exc

    try:
        _drop_schema(schema_name)
    except Exception as exc:
        if cleanup_error is None:
            cleanup_error = exc

    if cleanup_error is not None:
        raise cleanup_error


@contextmanager
def _managed_test_connection() -> Iterator[_IsolatedConnection]:
    import psycopg

    schema_name = _new_schema_name()
    _create_schema(schema_name)
    conn: _IsolatedConnection | None = None
    try:
        raw_conn = psycopg.connect(_DSN)
        conn = _IsolatedConnection(raw_conn, schema_name=schema_name)
        conn.begin_test_scope()
        yield conn
    finally:
        if conn is None:
            _drop_schema(schema_name)
        else:
            _cleanup_test_connection(conn, schema_name)


@pytest.fixture()
def pg_conn():
    """Yield an isolated psycopg connection scoped to a disposable schema.

    The test gets its own schema plus an outer transaction guarded by a
    savepoint. Helper-level commits only rotate the savepoint, so writes
    never escape the test scope even when production helpers call commit().
    """
    with _managed_test_connection() as conn:
        yield conn


@pytest.fixture()
def pg_conn_clean(pg_conn):
    """Yield a connection with the current disposable schema reset to blank."""
    _drop_all_tables(pg_conn)
    yield pg_conn


def _drop_all_tables(conn) -> None:
    """Recreate the current schema so each test starts from a blank namespace."""
    schema_name = getattr(conn, "schema_name", None) or _current_schema_name(conn)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO public")
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
        cur.execute(f"CREATE SCHEMA {schema_name}")
    _set_search_path(conn, schema_name)
    conn.commit()
