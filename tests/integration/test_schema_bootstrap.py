"""Integration: schema bootstrap and migration against real Postgres."""

from __future__ import annotations

import os

import pytest

from src.db.bootstrap import apply_sql, read_migration_sql, read_schema_sql
from tests.integration import conftest as integration_conftest
from tests.integration.conftest import (
    _IsolatedConnection,
    _cleanup_test_connection,
    _current_schema_name,
    _managed_test_connection,
    _open_admin_connection,
    _open_external_connection,
)


REAL_PG_REQUIRED = pytest.mark.skipif(
    not os.environ.get("PSEPHOS_TEST_POSTGRES_DSN"),
    reason="PSEPHOS_TEST_POSTGRES_DSN not set",
)


# -- helpers -----------------------------------------------------------------

EXPECTED_TABLES = {
    "data_source",
    "ingestion_run",
    "source_artifact",
    "parse_run",
    "match_decision",
    "review_queue",
    "member",
    "member_term",
    "committee",
    "committee_membership",
    "bill",
    "bill_sponsor",
    "vote_event",
    "vote_cast",
    "fec_committee",
    "contribution",
    "financial_disclosure",
    "holding",
    "transaction",
    "rule_fire",
    "evidence_card",
    "score_snapshot",
}


def _existing_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        return {row[0] for row in cur.fetchall()}


def _schema_exists(schema_name: str) -> bool:
    with _open_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
                [schema_name],
            )
            row = cur.fetchone()
    assert row is not None
    return bool(row[0])


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self._conn.commands.append((sql, params))
        if self._conn.fail_on == sql:
            raise RuntimeError("boom")

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.commands: list[tuple[str, object]] = []
        self.fail_on = fail_on
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self, *args, **kwargs) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class TestHarnessIsolationGuard:
    def test_commit_restarts_savepoint_without_committing_root_transaction(self):
        raw = _FakeConn()
        conn = _IsolatedConnection(raw, schema_name="psephosamerica_test_schema")

        conn.begin_test_scope()
        raw.commands.clear()

        conn.commit()

        assert raw.commit_calls == 0
        assert [sql for sql, _ in raw.commands] == [
            "RELEASE SAVEPOINT psephosamerica_test_guard",
            "SAVEPOINT psephosamerica_test_guard",
        ]

    def test_statement_error_rolls_back_to_guard_savepoint(self):
        raw = _FakeConn(fail_on="SELECT broken")
        conn = _IsolatedConnection(raw, schema_name="psephosamerica_test_schema")

        conn.begin_test_scope()

        with pytest.raises(RuntimeError, match="boom"):
            with conn.cursor() as cur:
                cur.execute("SELECT broken")

        assert raw.rollback_calls == 0
        assert [sql for sql, _ in raw.commands][-3:] == [
            "ROLLBACK TO SAVEPOINT psephosamerica_test_guard",
            "RELEASE SAVEPOINT psephosamerica_test_guard",
            "SAVEPOINT psephosamerica_test_guard",
        ]

    def test_outer_begin_commit_wrapper_is_stripped_before_execute(self):
        raw = _FakeConn()
        conn = _IsolatedConnection(raw, schema_name="psephosamerica_test_schema")

        conn.begin_test_scope()
        raw.commands.clear()

        with conn.cursor() as cur:
            cur.execute("-- migration wrapper\nBEGIN;\nSELECT 1;\nCOMMIT;\n")

        assert len(raw.commands) == 1
        executed_sql = raw.commands[0][0]
        assert "SELECT 1" in executed_sql
        assert "BEGIN" not in executed_sql
        assert "COMMIT" not in executed_sql

    def test_rejects_inner_transaction_control_statements(self):
        raw = _FakeConn()
        conn = _IsolatedConnection(raw, schema_name="psephosamerica_test_schema")

        conn.begin_test_scope()
        raw.commands.clear()

        with pytest.raises(RuntimeError, match="transaction control"):
            with conn.cursor() as cur:
                cur.execute("SELECT 1;\nCOMMIT;\nSELECT 2;")

        assert raw.commands == []


class TestHarnessPrivilegesAndCleanup:
    def test_create_schema_skips_cleanly_when_privileges_are_missing(self, monkeypatch):
        class _PrivilegeDenied(Exception):
            sqlstate = "42501"

        class _FailingCursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def execute(self, sql, params=None):
                raise _PrivilegeDenied("permission denied for database postgres")

        class _FailingConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def cursor(self):
                return _FailingCursor()

        monkeypatch.setattr(
            integration_conftest,
            "_open_admin_connection",
            lambda: _FailingConn(),
        )

        with pytest.raises(pytest.skip.Exception, match="CREATE SCHEMA and DROP SCHEMA"):
            integration_conftest._create_schema("psephosamerica_test_schema")

    def test_cleanup_surfaces_schema_drop_failures(self, monkeypatch):
        events: list[str] = []

        class _CleanupConn:
            def finish_test_scope(self) -> None:
                events.append("finish")

            def close(self) -> None:
                events.append("close")

        def _raise_on_drop(schema_name: str) -> None:
            events.append(f"drop:{schema_name}")
            raise RuntimeError("schema leak")

        monkeypatch.setattr(integration_conftest, "_drop_schema", _raise_on_drop)

        with pytest.raises(RuntimeError, match="schema leak"):
            _cleanup_test_connection(_CleanupConn(), "psephosamerica_test_schema")

        assert events == ["finish", "close", "drop:psephosamerica_test_schema"]


# -- tests -------------------------------------------------------------------


@REAL_PG_REQUIRED
class TestSchemaSQL:
    """Apply schema.sql (no transaction wrapper) to a clean database."""

    def test_uses_disposable_non_public_schema(self, pg_conn_clean):
        assert _current_schema_name(pg_conn_clean) != "public"

    def test_creates_all_tables(self, pg_conn_clean):
        conn = pg_conn_clean
        sql = read_schema_sql()
        apply_sql(conn, sql)

        tables = _existing_tables(conn)
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables after schema.sql: {missing}"

    def test_schema_sql_commit_stays_invisible_outside_test_scope(self, pg_conn_clean):
        conn = pg_conn_clean
        apply_sql(conn, read_schema_sql())

        observer = _open_external_connection(conn.schema_name)
        try:
            assert _existing_tables(observer) == set()
        finally:
            observer.close()

    def test_idempotent_when_tables_exist(self, pg_conn_clean):
        """Running schema.sql twice should fail (CREATE TABLE, not IF NOT EXISTS)."""
        conn = pg_conn_clean
        sql = read_schema_sql()
        apply_sql(conn, sql)

        with pytest.raises(Exception):
            apply_sql(conn, sql)


@REAL_PG_REQUIRED
class TestMigration:
    """Apply the 0001_init migration to a clean database."""

    def test_migration_creates_all_tables(self, pg_conn_clean):
        conn = pg_conn_clean
        sql = read_migration_sql()
        apply_sql(conn, sql)

        tables = _existing_tables(conn)
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables after migration: {missing}"

    def test_schema_and_migration_produce_same_tables(self, pg_conn_clean):
        """schema.sql and 0001_init.sql should produce the same table set."""
        conn = pg_conn_clean

        schema_sql = read_schema_sql()
        apply_sql(conn, schema_sql)
        schema_tables = _existing_tables(conn)

        _drop_and_reapply_migration(conn)
        migration_tables = _existing_tables(conn)

        assert schema_tables == migration_tables


@REAL_PG_REQUIRED
class TestRuntimeBootstrapCommand:
    """Apply the runtime bootstrap authority to a clean database."""

    def test_bootstrap_database_creates_all_tables(self, pg_conn_clean):
        from src.runtime.bootstrap import bootstrap_database

        conn = pg_conn_clean

        bootstrap_database(conn)

        tables = _existing_tables(conn)
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables after bootstrap_database(): {missing}"


@REAL_PG_REQUIRED
class TestDisposableSchemaLifecycle:
    def test_wrapped_migration_stays_isolated_and_connection_remains_usable(self, pg_conn_clean):
        conn = pg_conn_clean

        apply_sql(conn, read_migration_sql())

        observer = _open_external_connection(conn.schema_name)
        try:
            assert _existing_tables(observer) == set()
        finally:
            observer.close()

        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('member')")
            row = cur.fetchone()
        assert row == ("member",)

    def test_failed_wrapped_script_recovers_without_leaking_objects(self, pg_conn_clean):
        conn = pg_conn_clean

        broken_sql = """
        BEGIN;
        CREATE TABLE rollback_probe (id integer PRIMARY KEY);
        SELECT * FROM missing_relation;
        COMMIT;
        """

        with pytest.raises(Exception):
            apply_sql(conn, broken_sql)

        assert _existing_tables(conn) == set()

        apply_sql(conn, "CREATE TABLE recovered_probe (id integer PRIMARY KEY);")
        assert _existing_tables(conn) == {"recovered_probe"}

        observer = _open_external_connection(conn.schema_name)
        try:
            assert _existing_tables(observer) == set()
        finally:
            observer.close()

    def test_cleanup_drops_schema_between_harness_runs(self):
        with _managed_test_connection() as first:
            apply_sql(first, "CREATE TABLE leak_probe (id integer PRIMARY KEY);")
            first_schema = first.schema_name

            assert _schema_exists(first_schema)

            observer = _open_external_connection(first_schema)
            try:
                assert _existing_tables(observer) == set()
            finally:
                observer.close()

        assert not _schema_exists(first_schema)

        with _managed_test_connection() as second:
            assert second.schema_name != first_schema
            assert _existing_tables(second) == set()


def _drop_and_reapply_migration(conn) -> None:
    """Drop all tables and apply the migration from scratch."""
    from tests.integration.conftest import _drop_all_tables

    _drop_all_tables(conn)
    apply_sql(conn, read_migration_sql())
