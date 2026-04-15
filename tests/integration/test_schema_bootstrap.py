"""Integration: schema bootstrap and migration against real Postgres."""

from __future__ import annotations

import pytest

from src.db.bootstrap import apply_sql, read_migration_sql, read_schema_sql


pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("OPENPACT_TEST_POSTGRES_DSN"),
    reason="OPENPACT_TEST_POSTGRES_DSN not set",
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
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        return {row[0] for row in cur.fetchall()}


# -- tests -------------------------------------------------------------------


class TestSchemaSQL:
    """Apply schema.sql (no transaction wrapper) to a clean database."""

    def test_creates_all_tables(self, pg_conn_clean):
        conn = pg_conn_clean
        sql = read_schema_sql()
        apply_sql(conn, sql)

        tables = _existing_tables(conn)
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Missing tables after schema.sql: {missing}"

    def test_idempotent_when_tables_exist(self, pg_conn_clean):
        """Running schema.sql twice should fail (CREATE TABLE, not IF NOT EXISTS)."""
        conn = pg_conn_clean
        sql = read_schema_sql()
        apply_sql(conn, sql)

        with pytest.raises(Exception):
            apply_sql(conn, sql)


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


def _drop_and_reapply_migration(conn) -> None:
    """Drop all tables and apply the migration from scratch."""
    from tests.integration.conftest import _drop_all_tables

    _drop_all_tables(conn)
    apply_sql(conn, read_migration_sql())
