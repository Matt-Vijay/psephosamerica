"""Tests for src/query/conflict_inputs.py.

No live DB.  fetch_all is mocked; all other logic is exercised in-memory.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

from src.query.conflict_inputs import (
    assemble_committee_sector_trade_rows,
    assemble_repeated_committee_linked_trading_rows,
    assemble_sector_holdings_overlap_rows,
    fetch_committee_membership_rows,
    fetch_holding_rows,
    fetch_late_or_amended_rows,
    fetch_transaction_rows,
)


# ---------------------------------------------------------------------------
# Shared in-memory fixtures
# ---------------------------------------------------------------------------

BIOGUIDE_A = "A000001"
BIOGUIDE_B = "B000002"

_MEMBERSHIP_ROW: dict[str, Any] = {
    "committee_membership_id": 10,
    "member_bioguide_id": BIOGUIDE_A,
    "committee_id": 1,
    "committee_code": "HSEN",
    "committee_name": "Committee on Energy",
    "congress": 118,
    "role": "member",
    "committee_start_date": dt.date(2023, 1, 3),
    "committee_end_date": None,
    "is_current": True,
}

_HOLDING_ROW: dict[str, Any] = {
    "holding_id": 20,
    "financial_disclosure_id": 100,
    "member_bioguide_id": BIOGUIDE_A,
    "filing_year": 2023,
    "disclosure_period_start": dt.date(2023, 1, 1),
    "disclosure_period_end": dt.date(2023, 12, 31),
    "issuer_name": "Exxon Corp",
    "issuer_ticker": "XOM",
    "asset_category": "stock",
    "owner_type": "self",
    "holding_value_min": 15_001.0,
    "holding_value_max": 50_000.0,
    "value_label": "$15,001 - $50,000",
}

_TRANSACTION_ROW: dict[str, Any] = {
    "transaction_id": 30,
    "financial_disclosure_id": 100,
    "member_bioguide_id": BIOGUIDE_A,
    "filing_year": 2023,
    "disclosure_period_start": dt.date(2023, 1, 1),
    "disclosure_period_end": dt.date(2023, 12, 31),
    "issuer_name": "Exxon Corp",
    "issuer_ticker": "XOM",
    "transaction_type": "purchase",
    "transaction_date": dt.date(2023, 6, 1),
    "owner_type": "self",
    "amount_min": 1_001.0,
    "amount_max": 15_000.0,
    "amount_label": "$1,001 - $15,000",
}

_LATE_ROW: dict[str, Any] = {
    "financial_disclosure_id": 100,
    "member_bioguide_id": BIOGUIDE_A,
    "filing_kind": "annual",
    "filed_at": dt.date(2023, 8, 15),
    "deadline": dt.date(2023, 5, 15),
    "filing_is_amendment": False,
    "superseded_filing_id": None,
}

_AMENDMENT_ROW: dict[str, Any] = {
    "financial_disclosure_id": 101,
    "member_bioguide_id": BIOGUIDE_A,
    "filing_kind": "amendment",
    "filed_at": dt.date(2023, 9, 1),
    "deadline": dt.date(2023, 5, 15),
    "filing_is_amendment": True,
    "superseded_filing_id": 100,
}


# ---------------------------------------------------------------------------
# Sector resolver stubs
# ---------------------------------------------------------------------------

def _committee_sector(committee_code: str, congress: int) -> str | None:
    return "energy" if committee_code == "HSEN" else None


def _issuer_sector(issuer_name: str, issuer_ticker: str | None) -> str | None:
    return "energy" if "Exxon" in issuer_name else None


# ---------------------------------------------------------------------------
# fetch_late_or_amended_rows
# ---------------------------------------------------------------------------

class TestFetchLateOrAmendedRows:
    def test_delegates_to_fetch_all(self):
        conn = MagicMock()
        fake_rows = [_LATE_ROW]
        with patch("src.query.conflict_inputs.fetch_all", return_value=fake_rows) as mock_fa:
            result = fetch_late_or_amended_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)
        mock_fa.assert_called_once()
        assert result is fake_rows

    def test_passes_bioguide_ids_and_year(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_late_or_amended_rows(conn, bioguide_ids=[BIOGUIDE_A, BIOGUIDE_B], filing_year=2022)
        _, _, params = mock_fa.call_args[0]
        assert params["bioguide_ids"] == [BIOGUIDE_A, BIOGUIDE_B]
        assert params["filing_year"] == 2022

    def test_returns_empty_list_when_no_rows(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]):
            result = fetch_late_or_amended_rows(conn, bioguide_ids=[], filing_year=2023)
        assert result == []


# ---------------------------------------------------------------------------
# fetch_committee_membership_rows
# ---------------------------------------------------------------------------

class TestFetchCommitteeMembershipRows:
    def test_delegates_to_fetch_all(self):
        conn = MagicMock()
        fake = [_MEMBERSHIP_ROW]
        with patch("src.query.conflict_inputs.fetch_all", return_value=fake) as mock_fa:
            result = fetch_committee_membership_rows(conn, bioguide_ids=[BIOGUIDE_A])
        mock_fa.assert_called_once()
        assert result is fake

    def test_passes_bioguide_ids(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_committee_membership_rows(conn, bioguide_ids=[BIOGUIDE_A])
        _, _, params = mock_fa.call_args[0]
        assert params["bioguide_ids"] == [BIOGUIDE_A]


# ---------------------------------------------------------------------------
# fetch_holding_rows
# ---------------------------------------------------------------------------

class TestFetchHoldingRows:
    def test_delegates_to_fetch_all(self):
        conn = MagicMock()
        fake = [_HOLDING_ROW]
        with patch("src.query.conflict_inputs.fetch_all", return_value=fake) as mock_fa:
            result = fetch_holding_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)
        mock_fa.assert_called_once()
        assert result is fake

    def test_passes_params(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_holding_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2021)
        _, _, params = mock_fa.call_args[0]
        assert params["filing_year"] == 2021


# ---------------------------------------------------------------------------
# fetch_transaction_rows
# ---------------------------------------------------------------------------

class TestFetchTransactionRows:
    def test_delegates_to_fetch_all(self):
        conn = MagicMock()
        fake = [_TRANSACTION_ROW]
        with patch("src.query.conflict_inputs.fetch_all", return_value=fake) as mock_fa:
            result = fetch_transaction_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)
        mock_fa.assert_called_once()
        assert result is fake

    def test_passes_params(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_transaction_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2020)
        _, _, params = mock_fa.call_args[0]
        assert params["filing_year"] == 2020


# ---------------------------------------------------------------------------
# assemble_committee_sector_trade_rows
# ---------------------------------------------------------------------------

class TestAssembleCommitteeSectorTradeRows:
    def _run(self, memberships=None, holdings=None):
        return assemble_committee_sector_trade_rows(
            memberships or [_MEMBERSHIP_ROW],
            holdings or [_HOLDING_ROW],
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )

    def test_returns_one_row_when_sectors_match(self):
        rows = self._run()
        assert len(rows) == 1

    def test_output_row_carries_bioguide(self):
        rows = self._run()
        assert rows[0]["member_bioguide_id"] == BIOGUIDE_A

    def test_output_row_carries_committee_fields(self):
        rows = self._run()
        r = rows[0]
        assert r["committee_membership_id"] == 10
        assert r["committee_name"] == "Committee on Energy"
        assert r["committee_sector"] == "energy"

    def test_output_row_carries_holding_fields(self):
        rows = self._run()
        r = rows[0]
        assert r["financial_disclosure_id"] == 100
        assert r["holding_value_min"] == 15_001.0

    def test_no_match_when_sectors_differ(self):
        holding = {**_HOLDING_ROW, "issuer_name": "Microsoft Corp"}
        rows = self._run(holdings=[holding])
        assert rows == []

    def test_skips_committee_with_unmapped_sector(self):
        membership = {**_MEMBERSHIP_ROW, "committee_code": "HJUD"}
        rows = self._run(memberships=[membership])
        assert rows == []

    def test_no_cross_member_pairing(self):
        holding_b = {**_HOLDING_ROW, "member_bioguide_id": BIOGUIDE_B}
        rows = self._run(holdings=[holding_b])
        assert rows == []

    def test_multiple_matching_holdings_per_member(self):
        holding2 = {**_HOLDING_ROW, "holding_id": 21, "issuer_name": "BP Exxon"}
        rows = self._run(holdings=[_HOLDING_ROW, holding2])
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# assemble_repeated_committee_linked_trading_rows
# ---------------------------------------------------------------------------

class TestAssembleRepeatedCommitteeLinkedTradingRows:
    def _run(self, memberships=None, transactions=None):
        return assemble_repeated_committee_linked_trading_rows(
            memberships or [_MEMBERSHIP_ROW],
            transactions or [_TRANSACTION_ROW],
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )

    def test_returns_one_row_per_membership(self):
        rows = self._run()
        assert len(rows) == 1

    def test_output_row_carries_bioguide(self):
        rows = self._run()
        assert rows[0]["member_bioguide_id"] == BIOGUIDE_A

    def test_transactions_list_is_populated(self):
        rows = self._run()
        assert len(rows[0]["transactions"]) == 1

    def test_transaction_carries_date_and_sector(self):
        rows = self._run()
        txn = rows[0]["transactions"][0]
        assert txn["transaction_date"] == dt.date(2023, 6, 1)
        assert txn["sector"] == "energy"

    def test_transactions_empty_when_no_match(self):
        txn = {**_TRANSACTION_ROW, "issuer_name": "Apple Inc"}
        rows = self._run(transactions=[txn])
        assert rows[0]["transactions"] == []

    def test_skips_committee_with_unmapped_sector(self):
        membership = {**_MEMBERSHIP_ROW, "committee_code": "HJUD"}
        rows = self._run(memberships=[membership])
        assert rows == []

    def test_transactions_for_different_member_excluded(self):
        txn_b = {**_TRANSACTION_ROW, "member_bioguide_id": BIOGUIDE_B}
        rows = self._run(transactions=[txn_b])
        assert rows[0]["transactions"] == []

    def test_multiple_transactions_collected(self):
        txn2 = {**_TRANSACTION_ROW, "transaction_id": 31, "transaction_date": dt.date(2023, 7, 1)}
        rows = self._run(transactions=[_TRANSACTION_ROW, txn2])
        assert len(rows[0]["transactions"]) == 2


# ---------------------------------------------------------------------------
# assemble_sector_holdings_overlap_rows
# ---------------------------------------------------------------------------

class TestAssembleSectorHoldingsOverlapRows:
    def _run(self, memberships=None, holdings=None):
        return assemble_sector_holdings_overlap_rows(
            memberships or [_MEMBERSHIP_ROW],
            holdings or [_HOLDING_ROW],
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )

    def test_returns_one_row_when_sectors_match(self):
        rows = self._run()
        assert len(rows) == 1

    def test_output_row_carries_bioguide(self):
        rows = self._run()
        assert rows[0]["member_bioguide_id"] == BIOGUIDE_A

    def test_output_row_carries_committee_and_holding_fields(self):
        rows = self._run()
        r = rows[0]
        assert r["committee_sector"] == "energy"
        assert r["holding_sector"] == "energy"
        assert r["holding_value_min"] == 15_001.0
        assert r["holding_value_max"] == 50_000.0

    def test_no_match_when_sectors_differ(self):
        holding = {**_HOLDING_ROW, "issuer_name": "Microsoft Corp"}
        rows = self._run(holdings=[holding])
        assert rows == []

    def test_skips_committee_with_unmapped_sector(self):
        membership = {**_MEMBERSHIP_ROW, "committee_code": "HJUD"}
        rows = self._run(memberships=[membership])
        assert rows == []

    def test_no_cross_member_pairing(self):
        holding_b = {**_HOLDING_ROW, "member_bioguide_id": BIOGUIDE_B}
        rows = self._run(holdings=[holding_b])
        assert rows == []


# ---------------------------------------------------------------------------
# Boundary: assembly helpers do not call fetch_all
# ---------------------------------------------------------------------------

class TestAssembleHelpersDoNotHitDB:
    def test_committee_sector_trade_no_db(self):
        with patch("src.query.conflict_inputs.fetch_all") as mock_fa:
            assemble_committee_sector_trade_rows(
                [_MEMBERSHIP_ROW],
                [_HOLDING_ROW],
                committee_sector_resolver=_committee_sector,
                issuer_sector_resolver=_issuer_sector,
            )
        mock_fa.assert_not_called()

    def test_repeated_trading_no_db(self):
        with patch("src.query.conflict_inputs.fetch_all") as mock_fa:
            assemble_repeated_committee_linked_trading_rows(
                [_MEMBERSHIP_ROW],
                [_TRANSACTION_ROW],
                committee_sector_resolver=_committee_sector,
                issuer_sector_resolver=_issuer_sector,
            )
        mock_fa.assert_not_called()

    def test_sector_holdings_overlap_no_db(self):
        with patch("src.query.conflict_inputs.fetch_all") as mock_fa:
            assemble_sector_holdings_overlap_rows(
                [_MEMBERSHIP_ROW],
                [_HOLDING_ROW],
                committee_sector_resolver=_committee_sector,
                issuer_sector_resolver=_issuer_sector,
            )
        mock_fa.assert_not_called()
