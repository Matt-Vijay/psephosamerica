"""Integration: disclosure reloads prune stale child rows in Postgres."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from src.db.bootstrap import apply_sql, read_schema_sql
from src.db.repositories import execute_one, fetch_all
from src.db.runtime_lookups import load_lookup_bundle
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    FinancialDisclosurePayload,
    HoldingPayload,
    TransactionPayload,
)
from src.pipeline.disclosures_load_run import run_disclosures_load

pytestmark = pytest.mark.skipif(
    not os.environ.get("PSEPHOS_TEST_POSTGRES_DSN"),
    reason="PSEPHOS_TEST_POSTGRES_DSN not set",
)


def _seed_schema(conn) -> None:
    apply_sql(conn, read_schema_sql())


def _seed_member(conn, bioguide_id: str = "A000001") -> None:
    execute_one(
        conn,
        """
        INSERT INTO member (bioguide_id, slug, last_name, full_name, chamber, is_current)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (bioguide_id, bioguide_id.lower(), "Tester", "Rep Tester", "house", True),
    )


def _disclosure() -> FinancialDisclosurePayload:
    return FinancialDisclosurePayload(
        member_bioguide_id="A000001",
        chamber="house",
        filing_year=2025,
        filing_type="annual",
        amendment_number=0,
        is_amended=False,
        filed_at=dt.date(2026, 5, 15),
        filing_period_start=dt.date(2025, 1, 1),
        filing_period_end=dt.date(2025, 12, 31),
        source_record_id="A000001-2025-annual",
    )


def _result(
    *,
    holdings: list[HoldingPayload],
    transactions: list[TransactionPayload],
) -> DisclosureTransformResult:
    return DisclosureTransformResult(
        disclosure=_disclosure(),
        holdings=holdings,
        transactions=transactions,
    )


def _holding(line: int, issuer: str) -> HoldingPayload:
    return HoldingPayload(
        line_number=line,
        owner_type="self",
        issuer_name=issuer,
        issuer_ticker=issuer[:4].upper(),
    )


def _transaction(line: int, issuer: str) -> TransactionPayload:
    return TransactionPayload(
        line_number=line,
        owner_type="self",
        issuer_name=issuer,
        transaction_type="purchase",
        transaction_date=dt.date(2025, 6, line),
    )


def _child_rows(conn, table: str) -> list[dict]:
    return fetch_all(
        conn,
        f"""
        SELECT line_number, issuer_name
        FROM "{table}"
        ORDER BY line_number
        """,
    )


class TestDisclosureReloadPrunesChildren:
    def test_reload_with_fewer_lines_deletes_stale_holdings_and_transactions(self, pg_conn_clean):
        conn = pg_conn_clean
        _seed_schema(conn)
        _seed_member(conn)

        first = _result(
            holdings=[_holding(1, "Keep Holding"), _holding(2, "Stale Holding")],
            transactions=[
                _transaction(1, "Keep Transaction"),
                _transaction(2, "Stale Transaction"),
            ],
        )
        run_disclosures_load([first], conn, lookup_loader=load_lookup_bundle)

        second = _result(
            holdings=[_holding(1, "Updated Holding")],
            transactions=[_transaction(1, "Updated Transaction")],
        )
        run_disclosures_load([second], conn, lookup_loader=load_lookup_bundle)

        assert _child_rows(conn, "holding") == [
            {"line_number": 1, "issuer_name": "Updated Holding"},
        ]
        assert _child_rows(conn, "transaction") == [
            {"line_number": 1, "issuer_name": "Updated Transaction"},
        ]

    def test_reload_with_no_lines_deletes_all_existing_holdings_and_transactions(
        self, pg_conn_clean
    ):
        conn = pg_conn_clean
        _seed_schema(conn)
        _seed_member(conn)

        first = _result(
            holdings=[_holding(1, "Old Holding")],
            transactions=[_transaction(1, "Old Transaction")],
        )
        run_disclosures_load([first], conn, lookup_loader=load_lookup_bundle)

        second = _result(holdings=[], transactions=[])
        run_disclosures_load([second], conn, lookup_loader=load_lookup_bundle)

        assert _child_rows(conn, "holding") == []
        assert _child_rows(conn, "transaction") == []
