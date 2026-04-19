"""Integration: writer and load path against real Postgres."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from src.db.bootstrap import apply_sql, read_schema_sql
from src.db.repositories import fetch_all
from src.db.writer import write_table_batch, write_table_batches
from tests.integration.conftest import _open_external_connection


pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENPACT_TEST_POSTGRES_DSN"),
    reason="OPENPACT_TEST_POSTGRES_DSN not set",
)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture()
def db(pg_conn_clean):
    """Provide a connection with the full schema applied."""
    apply_sql(pg_conn_clean, read_schema_sql())
    return pg_conn_clean


def _make_data_source(db, slug="test-source") -> int:
    """Insert a minimal data_source row and return its id."""
    result = write_table_batch(
        db,
        table="data_source",
        rows=[{
            "slug": slug,
            "name": "Test Source",
            "source_kind": "internal",
        }],
    )
    assert result.inserted == 1
    rows = fetch_all(db, "SELECT id FROM data_source WHERE slug = %s", [slug])
    return rows[0]["id"]


def _make_source_artifact(db, data_source_id: int, sha: str = "a" * 64) -> int:
    """Insert a minimal source_artifact row and return its id."""
    write_table_batch(
        db,
        table="source_artifact",
        rows=[{
            "data_source_id": data_source_id,
            "artifact_kind": "json",
            "storage_uri": f"s3://test/{sha}",
            "sha256": sha,
        }],
    )
    rows = fetch_all(db, "SELECT id FROM source_artifact WHERE sha256 = %s", [sha])
    return rows[0]["id"]


# -- tests -------------------------------------------------------------------


class TestWriteTableBatch:
    """Basic insert, upsert, and ignore modes against a real table."""

    def test_insert_member(self, db):
        row = {
            "bioguide_id": "T000001",
            "slug": "test-member",
            "last_name": "Testson",
            "full_name": "Test Testson",
            "chamber": "senate",
        }
        result = write_table_batch(db, table="member", rows=[row])
        assert result.inserted == 1
        assert result.table == "member"

        rows = fetch_all(db, "SELECT bioguide_id, full_name FROM member")
        assert len(rows) == 1
        assert rows[0]["bioguide_id"] == "T000001"

    def test_upsert_member(self, db):
        row = {
            "bioguide_id": "U000001",
            "slug": "upsert-member",
            "last_name": "Before",
            "full_name": "Before Update",
            "chamber": "house",
        }
        write_table_batch(db, table="member", rows=[row])

        updated_row = {
            "bioguide_id": "U000001",
            "slug": "upsert-member",
            "last_name": "After",
            "full_name": "After Update",
            "chamber": "house",
        }
        result = write_table_batch(
            db,
            table="member",
            rows=[updated_row],
            conflict_columns=["bioguide_id"],
            mode="upsert",
        )
        assert result.inserted == 1  # upsert counts as inserted

        rows = fetch_all(db, "SELECT last_name FROM member WHERE bioguide_id = 'U000001'")
        assert rows[0]["last_name"] == "After"

    def test_ignore_duplicate(self, db):
        row = {
            "bioguide_id": "I000001",
            "slug": "ignore-member",
            "last_name": "Original",
            "full_name": "Original Name",
            "chamber": "senate",
        }
        write_table_batch(db, table="member", rows=[row])

        dup = {
            "bioguide_id": "I000001",
            "slug": "ignore-member",
            "last_name": "Duplicate",
            "full_name": "Duplicate Name",
            "chamber": "senate",
        }
        result = write_table_batch(
            db,
            table="member",
            rows=[dup],
            conflict_columns=["bioguide_id"],
            mode="ignore",
        )
        assert result.skipped == 1

        rows = fetch_all(db, "SELECT last_name FROM member WHERE bioguide_id = 'I000001'")
        assert rows[0]["last_name"] == "Original"

    def test_empty_batch_is_noop(self, db):
        result = write_table_batch(db, table="member", rows=[])
        assert result.total_attempted == 0

    def test_helper_commit_is_not_visible_outside_test_transaction(self, db):
        write_table_batch(
            db,
            table="member",
            rows=[{
                "bioguide_id": "V000001",
                "slug": "visible-member",
                "last_name": "Hidden",
                "full_name": "Should Stay Hidden",
                "chamber": "house",
            }],
        )

        observer = _open_external_connection(db.schema_name)
        try:
            rows = fetch_all(observer, "SELECT to_regclass('member') AS regclass_name")
            assert rows == [{"regclass_name": None}]
        finally:
            observer.close()

    def test_failed_write_preserves_prior_commits_and_allows_recovery(self, db):
        write_table_batch(
            db,
            table="member",
            rows=[{
                "bioguide_id": "S000001",
                "slug": "stable-member",
                "last_name": "Stable",
                "full_name": "Stable Member",
                "chamber": "house",
            }],
        )

        with pytest.raises(Exception):
            write_table_batch(
                db,
                table="member",
                rows=[{
                    "bioguide_id": "X000001",
                    "slug": "bad-chamber",
                    "last_name": "Bad",
                    "full_name": "Bad Chamber",
                    "chamber": "invalid_value",
                }],
            )

        rows = fetch_all(
            db,
            "SELECT bioguide_id FROM member ORDER BY bioguide_id",
        )
        assert rows == [{"bioguide_id": "S000001"}]

        result = write_table_batch(
            db,
            table="member",
            rows=[{
                "bioguide_id": "R000001",
                "slug": "recovered-member",
                "last_name": "Recovered",
                "full_name": "Recovered Member",
                "chamber": "senate",
            }],
        )
        assert result.inserted == 1

        rows = fetch_all(
            db,
            "SELECT bioguide_id FROM member ORDER BY bioguide_id",
        )
        assert rows == [
            {"bioguide_id": "R000001"},
            {"bioguide_id": "S000001"},
        ]

        observer = _open_external_connection(db.schema_name)
        try:
            rows = fetch_all(observer, "SELECT to_regclass('member') AS regclass_name")
            assert rows == [{"regclass_name": None}]
        finally:
            observer.close()


class TestWriteTableBatches:
    """Multi-table batch write via write_table_batches."""

    def test_multi_table_load(self, db):
        ds_id = _make_data_source(db)
        sa_id = _make_source_artifact(db, ds_id)

        batches = [
            {
                "table": "member",
                "rows": [{
                    "bioguide_id": "M000001",
                    "slug": "multi-member",
                    "last_name": "Multi",
                    "full_name": "Multi Member",
                    "chamber": "house",
                    "source_artifact_id": sa_id,
                }],
            },
            {
                "table": "committee",
                "rows": [{
                    "committee_code": "TSAG",
                    "congress": 119,
                    "chamber": "senate",
                    "committee_type": "standing",
                    "name": "Test Agriculture",
                    "review_tier": "deterministic",
                    "source_artifact_id": sa_id,
                }],
            },
        ]

        summary = write_table_batches(db, batches)
        assert summary.ok
        assert summary.total_inserted == 2
        assert len(summary.table_results) == 2

    def test_fk_chain_member_to_disclosure_to_holding(self, db):
        """Insert a member -> financial_disclosure -> holding chain."""
        ds_id = _make_data_source(db)
        sa_id = _make_source_artifact(db, ds_id)

        write_table_batch(
            db,
            table="member",
            rows=[{
                "bioguide_id": "F000001",
                "slug": "fk-chain",
                "last_name": "Chain",
                "full_name": "FK Chain",
                "chamber": "senate",
            }],
        )
        member_rows = fetch_all(db, "SELECT id FROM member WHERE bioguide_id = 'F000001'")
        member_id = member_rows[0]["id"]

        write_table_batch(
            db,
            table="financial_disclosure",
            rows=[{
                "member_id": member_id,
                "chamber": "senate",
                "filing_year": 2025,
                "filing_type": "annual",
                "source_artifact_id": sa_id,
            }],
        )
        fd_rows = fetch_all(
            db,
            "SELECT id FROM financial_disclosure WHERE member_id = %s",
            [member_id],
        )
        fd_id = fd_rows[0]["id"]

        result = write_table_batch(
            db,
            table="holding",
            rows=[{
                "financial_disclosure_id": fd_id,
                "line_number": 1,
                "owner_type": "self",
                "issuer_name": "ACME Corp",
                "issuer_ticker": "ACME",
                "value_min": Decimal("1000.00"),
                "value_max": Decimal("15000.00"),
                "source_artifact_id": sa_id,
            }],
        )
        assert result.inserted == 1

        holdings = fetch_all(
            db,
            "SELECT issuer_name, value_min, value_max FROM holding WHERE financial_disclosure_id = %s",
            [fd_id],
        )
        assert len(holdings) == 1
        assert holdings[0]["issuer_name"] == "ACME Corp"
        assert holdings[0]["value_min"] == Decimal("1000.00")

    def test_constraint_violation_raises(self, db):
        """Inserting a member with an invalid chamber value should fail."""
        with pytest.raises(Exception):
            write_table_batch(
                db,
                table="member",
                rows=[{
                    "bioguide_id": "X000001",
                    "slug": "bad-chamber",
                    "last_name": "Bad",
                    "full_name": "Bad Chamber",
                    "chamber": "invalid_value",
                }],
            )
