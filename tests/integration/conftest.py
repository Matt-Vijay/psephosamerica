"""Integration test fixtures — real Postgres, no mocks.

Requires OPENPACT_TEST_POSTGRES_DSN in the environment.
All tests in this directory skip cleanly when the DSN is absent.
"""

from __future__ import annotations

import os

import pytest

_DSN = os.environ.get("OPENPACT_TEST_POSTGRES_DSN")

# Skip the entire integration directory when no DSN is available.
pytestmark = pytest.mark.skipif(not _DSN, reason="OPENPACT_TEST_POSTGRES_DSN not set")


@pytest.fixture(autouse=True)
def _block_network():
    """Override the root-level network guard for integration tests.

    Integration tests need real Postgres connections, so we disable
    the socket-level block that the root conftest installs.
    """
    yield


@pytest.fixture()
def pg_conn():
    """Yield a live psycopg connection and roll back after each test.

    The connection is opened with autocommit=False so the test runs
    inside a transaction that is always rolled back, leaving the
    database clean for the next test.
    """
    import psycopg

    conn = psycopg.connect(_DSN)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def pg_conn_clean(pg_conn):
    """Yield a connection with all openpact tables dropped first.

    Useful for schema-bootstrap tests that need a blank database.
    Tables are dropped inside the same transaction so rollback
    restores the prior state.
    """
    _drop_all_tables(pg_conn)
    yield pg_conn


def _drop_all_tables(conn) -> None:
    """Drop every table created by the openpact schema, respecting FK order."""
    tables = [
        "score_snapshot",
        "evidence_card",
        "rule_fire",
        "holding",
        '"transaction"',
        "financial_disclosure",
        "contribution",
        "fec_committee",
        "vote_cast",
        "vote_event",
        "bill_sponsor",
        "bill",
        "committee_membership",
        "committee",
        "member_term",
        "member",
        "review_queue",
        "match_decision",
        "parse_run",
        "source_artifact",
        "ingestion_run",
        "data_source",
    ]
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    conn.commit()
