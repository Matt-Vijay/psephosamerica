"""Tests for src/query/conflict_inputs.py.

No live DB.  fetch_all is mocked; all other logic is exercised in-memory.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from src.normalize.taxonomy_runtime import CommitteeMapping
from src.query.conflict import (
    assemble_committee_sector_trade_bundle,
    assemble_late_or_amended_disclosure_bundle,
    assemble_repeated_committee_linked_trading_bundle,
    assemble_sector_holdings_overlap_bundle,
)
from src.query.conflict_inputs import (
    assemble_committee_sector_trade_rows,
    assemble_repeated_committee_linked_trading_rows,
    assemble_sector_holdings_overlap_rows,
    fetch_committee_membership_rows,
    fetch_contribution_rows,
    fetch_holding_rows,
    fetch_late_or_amended_rows,
    fetch_transaction_rows,
)
from src.rules.contexts import build_late_or_amended_disclosure_context
from src.rules.evaluator import evaluate_rule
from src.rules.loader import load_rule

# ---------------------------------------------------------------------------
# Shared in-memory fixtures
# ---------------------------------------------------------------------------

BIOGUIDE_A = "A000001"
BIOGUIDE_B = "B000002"
HOUSE_DISCLOSURE_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2023/100.pdf"
SENATE_DISCLOSURE_URL = "https://efdsearch.senate.gov/search/view/paper/DOC/"
COMMITTEE_MEMBERSHIP_URL = "https://api.congress.gov/v3/committee/house/HSEN?format=json"

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
    "committee_membership_source_url": COMMITTEE_MEMBERSHIP_URL,
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
    "source_artifact_id": 300,
    "source_record_id": "tx-30",
    "financial_disclosure_id": 100,
    "line_number": 2,
    "member_bioguide_id": BIOGUIDE_A,
    "filing_year": 2023,
    "disclosure_period_start": dt.date(2023, 1, 1),
    "disclosure_period_end": dt.date(2023, 12, 31),
    "issuer_name": "Exxon Corp",
    "issuer_ticker": "XOM",
    "asset_description": "Exxon stock",
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


def _committee_mapping(
    *,
    sector_id: str = "energy",
    mapping_tier: str = "deterministic",
    chamber: str = "House",
    committee_name: str = "Committee on Energy",
    subcommittee_name: str = "",
) -> CommitteeMapping:
    return CommitteeMapping(
        congress=118,
        chamber=chamber,
        committee_name=committee_name,
        subcommittee_name=subcommittee_name,
        sector_id=sector_id,
        mapping_tier=mapping_tier,
        jurisdiction_basis="Test basis",
        basis_source="test",
        notes="",
    )


def _committee_mapping_resolver(
    committee_code: str,
    congress: int,
) -> CommitteeMapping | None:
    if committee_code != "HSEN":
        return None
    return _committee_mapping()


_LATE_RULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "rules"
    / "conflict_of_interest"
    / "late_or_amended_disclosure.yaml"
)


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
            fetch_late_or_amended_rows(
                conn, bioguide_ids=[BIOGUIDE_A, BIOGUIDE_B], filing_year=2022
            )
        _, _, params = mock_fa.call_args[0]
        assert params["bioguide_ids"] == [BIOGUIDE_A, BIOGUIDE_B]
        assert params["filing_year"] == 2022

    def test_returns_empty_list_when_no_rows(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]):
            result = fetch_late_or_amended_rows(conn, bioguide_ids=[], filing_year=2023)
        assert result == []

    def test_fetch_sql_selects_source_artifact_url(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_late_or_amended_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)
        _, sql, _ = mock_fa.call_args[0]
        assert "fd.source_artifact_id" in sql
        assert "sa.source_url" in sql

    def test_fetched_source_url_reaches_evidence_anchor(self):
        conn = MagicMock()
        fetched = {**_LATE_ROW, "source_artifact_id": 300, "source_url": SENATE_DISCLOSURE_URL}
        with patch("src.query.conflict_inputs.fetch_all", return_value=[fetched]):
            rows = fetch_late_or_amended_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)

        bundle = assemble_late_or_amended_disclosure_bundle(rows[0])

        assert bundle.source_anchors[0].source_type == "financial_disclosure"
        assert bundle.source_anchors[0].url == SENATE_DISCLOSURE_URL


class TestLateOrAmendedRuleRealFilingKinds:
    def test_late_annual_filing_still_fires(self):
        rule = load_rule(_LATE_RULE_PATH)
        facts = build_late_or_amended_disclosure_context({**_LATE_ROW, "filing_id": "fd-annual-1"})

        fire = evaluate_rule(
            rule,
            {**facts, "parameters.late_threshold_days": rule.parameters["late_threshold_days"]},
            BIOGUIDE_A,
            "run-1",
        )

        assert fire is not None

    def test_late_ptr_filing_still_fires(self):
        rule = load_rule(_LATE_RULE_PATH)
        facts = build_late_or_amended_disclosure_context(
            {**_LATE_ROW, "filing_id": "fd-ptr-1", "filing_kind": "ptr"}
        )

        fire = evaluate_rule(
            rule,
            {**facts, "parameters.late_threshold_days": rule.parameters["late_threshold_days"]},
            BIOGUIDE_A,
            "run-1",
        )

        assert fire is not None


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

    def test_sql_selects_committee_resolution_context(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_committee_membership_rows(conn, bioguide_ids=[BIOGUIDE_A])
        _, sql, _ = mock_fa.call_args[0]
        assert "AS committee_chamber" in sql
        assert "AS committee_type" in sql
        assert "AS parent_committee_name" in sql
        assert "cm.source_record_id" in sql
        assert "AS committee_membership_source_url" in sql

    def test_fetched_committee_source_url_reaches_trade_evidence_anchor(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[_MEMBERSHIP_ROW]):
            memberships = fetch_committee_membership_rows(conn, bioguide_ids=[BIOGUIDE_A])

        rows = assemble_committee_sector_trade_rows(
            memberships,
            [_HOLDING_ROW],
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )
        bundle = assemble_committee_sector_trade_bundle(rows[0])

        committee_anchor = next(
            anchor
            for anchor in bundle.source_anchors
            if anchor.source_type == "committee_membership"
        )
        assert committee_anchor.url == COMMITTEE_MEMBERSHIP_URL


# ---------------------------------------------------------------------------
# fetch_contribution_rows
# ---------------------------------------------------------------------------


class TestFetchContributionRows:
    def test_delegates_to_fetch_all(self):
        conn = MagicMock()
        fake = [
            {
                "member_bioguide_id": BIOGUIDE_A,
                "contribution_id": 900,
                "donor_name": "SOLAR BUILDERS PAC",
            }
        ]
        with patch(
            "src.query.conflict_inputs.fetch_all",
            side_effect=[[{"exists": True}], fake],
        ) as mock_fa:
            result = fetch_contribution_rows(
                conn,
                bioguide_ids=[BIOGUIDE_A],
                as_of_date=dt.date(2024, 12, 31),
            )
        assert mock_fa.call_count == 2
        assert result is fake

    def test_passes_bioguide_ids_and_as_of_date(self):
        conn = MagicMock()
        cutoff = dt.date(2024, 12, 31)
        with patch(
            "src.query.conflict_inputs.fetch_all",
            side_effect=[[{"exists": True}], []],
        ) as mock_fa:
            fetch_contribution_rows(conn, bioguide_ids=[BIOGUIDE_A, BIOGUIDE_B], as_of_date=cutoff)
        _, _, params = mock_fa.call_args[0]
        assert params["bioguide_ids"] == [BIOGUIDE_A, BIOGUIDE_B]
        assert params["as_of_date"] == cutoff

    def test_sql_joins_fec_linkage_to_members_and_source_artifacts(self):
        conn = MagicMock()
        with patch(
            "src.query.conflict_inputs.fetch_all",
            side_effect=[[{"exists": True}], []],
        ) as mock_fa:
            fetch_contribution_rows(
                conn,
                bioguide_ids=[BIOGUIDE_A],
                as_of_date=dt.date(2024, 12, 31),
            )
        _, sql, _ = mock_fa.call_args[0]
        assert "FROM contribution c" in sql
        assert "JOIN fec_committee fc" in sql
        assert "JOIN fec_candidate_committee_linkage fcl" in sql
        assert "JOIN member m ON m.fec_candidate_id = fcl.fec_candidate_id" in sql
        assert "LEFT JOIN source_artifact sa" in sql
        assert "sa.source_url" in sql
        assert "c.donor_employer" in sql
        assert "c.donor_occupation" in sql
        assert "c.contribution_date <= %(as_of_date)s" in sql
        assert "m.bioguide_id = ANY(%(bioguide_ids)s)" in sql
        assert "AS source_url" in sql

    def test_returns_empty_when_fec_linkage_table_is_absent(self):
        conn = MagicMock()
        with patch(
            "src.query.conflict_inputs.fetch_all",
            return_value=[{"exists": False}],
        ) as mock_fa:
            result = fetch_contribution_rows(
                conn,
                bioguide_ids=[BIOGUIDE_A],
                as_of_date=dt.date(2024, 12, 31),
            )

        assert result == []
        mock_fa.assert_called_once()


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

    def test_fetch_sql_selects_source_artifact_url(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_holding_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2021)
        _, sql, _ = mock_fa.call_args[0]
        assert "COALESCE(h.source_artifact_id, fd.source_artifact_id)" in sql
        assert "sa.source_url" in sql
        assert "fd.filed_at" in sql

    def test_fetched_source_url_reaches_committee_trade_evidence_anchor(self):
        conn = MagicMock()
        fetched = {**_HOLDING_ROW, "source_artifact_id": 300, "source_url": HOUSE_DISCLOSURE_URL}
        with patch("src.query.conflict_inputs.fetch_all", return_value=[fetched]):
            holdings = fetch_holding_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)

        rows = assemble_committee_sector_trade_rows(
            [_MEMBERSHIP_ROW],
            holdings,
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )
        bundle = assemble_committee_sector_trade_bundle(rows[0])

        assert bundle.source_anchors[0].source_type == "financial_disclosure"
        assert bundle.source_anchors[0].url == HOUSE_DISCLOSURE_URL

    def test_fetched_source_url_reaches_holdings_overlap_evidence_anchor(self):
        conn = MagicMock()
        fetched = {**_HOLDING_ROW, "source_artifact_id": 300, "source_url": HOUSE_DISCLOSURE_URL}
        with patch("src.query.conflict_inputs.fetch_all", return_value=[fetched]):
            holdings = fetch_holding_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)

        rows = assemble_sector_holdings_overlap_rows(
            [_MEMBERSHIP_ROW],
            holdings,
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )
        bundle = assemble_sector_holdings_overlap_bundle(rows[0])

        assert bundle.source_anchors[0].source_type == "financial_disclosure"
        assert bundle.source_anchors[0].url == HOUSE_DISCLOSURE_URL


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

    def test_fetch_sql_selects_transaction_trace_fields(self):
        conn = MagicMock()
        with patch("src.query.conflict_inputs.fetch_all", return_value=[]) as mock_fa:
            fetch_transaction_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2020)
        _, sql, _ = mock_fa.call_args[0]
        assert "t.line_number" in sql
        assert "t.source_artifact_id" in sql
        assert "t.source_record_id" in sql
        assert "t.asset_description" in sql
        assert "sa.source_url" in sql

    def test_fetched_source_url_reaches_repeated_trading_evidence_anchor(self):
        conn = MagicMock()
        fetched = {**_TRANSACTION_ROW, "source_url": SENATE_DISCLOSURE_URL}
        with patch("src.query.conflict_inputs.fetch_all", return_value=[fetched]):
            transactions = fetch_transaction_rows(conn, bioguide_ids=[BIOGUIDE_A], filing_year=2023)

        rows = assemble_repeated_committee_linked_trading_rows(
            [_MEMBERSHIP_ROW],
            transactions,
            committee_sector_resolver=_committee_sector,
            issuer_sector_resolver=_issuer_sector,
        )
        bundle = assemble_repeated_committee_linked_trading_bundle(
            {**rows[0], "reference_date": dt.date(2023, 12, 31)}
        )

        assert bundle.source_anchors[0].source_type == "financial_disclosure"
        assert bundle.source_anchors[0].url == SENATE_DISCLOSURE_URL


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
        assert r["committee_membership_source_url"] == COMMITTEE_MEMBERSHIP_URL

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

    def test_output_row_carries_committee_mapping_metadata(self):
        rows = assemble_committee_sector_trade_rows(
            [_MEMBERSHIP_ROW],
            [_HOLDING_ROW],
            committee_sector_resolver=_committee_mapping_resolver,
            issuer_sector_resolver=_issuer_sector,
        )

        assert rows[0]["committee_sector"] == "energy"
        assert rows[0]["committee_mapping_tier"] == "deterministic"
        assert rows[0]["committee_chamber"] == "House"
        assert rows[0]["committee_subcommittee_name"] == ""

    def test_review_required_mapping_is_emitted_with_review_required_tier(self):
        def review_required_mapping(committee_code: str, congress: int) -> CommitteeMapping | None:
            if committee_code != "HSEN":
                return None
            return _committee_mapping(mapping_tier="review_required")

        rows = assemble_committee_sector_trade_rows(
            [_MEMBERSHIP_ROW],
            [_HOLDING_ROW],
            committee_sector_resolver=review_required_mapping,
            issuer_sector_resolver=_issuer_sector,
        )

        assert len(rows) == 1
        assert rows[0]["committee_sector"] == "energy"
        assert rows[0]["committee_mapping_tier"] == "review_required"


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

    def test_transaction_carries_source_trace_fields(self):
        rows = self._run()
        txn = rows[0]["transactions"][0]
        assert txn["transaction_id"] == 30
        assert txn["financial_disclosure_id"] == 100
        assert txn["source_artifact_id"] == 300
        assert txn["source_record_id"] == "tx-30"
        assert "source_url" in txn
        assert txn["line_number"] == 2
        assert txn["issuer_name"] == "Exxon Corp"
        assert txn["asset_description"] == "Exxon stock"
        assert txn["transaction_type"] == "purchase"
        assert txn["amount_label"] == "$1,001 - $15,000"

    def test_output_row_carries_committee_membership_source_url(self):
        rows = self._run()
        assert rows[0]["committee_membership_source_url"] == COMMITTEE_MEMBERSHIP_URL

    def test_nested_transactions_carry_source_url(self):
        tx = {
            **_TRANSACTION_ROW,
            "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC/",
        }
        rows = self._run(transactions=[tx])
        assert rows[0]["transactions"][0]["source_url"] == tx["source_url"]

    def test_row_carries_first_matching_financial_disclosure_id(self):
        txn2 = {
            **_TRANSACTION_ROW,
            "transaction_id": 31,
            "financial_disclosure_id": 101,
            "line_number": 5,
        }
        rows = self._run(transactions=[_TRANSACTION_ROW, txn2])
        assert rows[0]["financial_disclosure_id"] == 100

    def test_transactions_empty_when_no_match(self):
        txn = {**_TRANSACTION_ROW, "issuer_name": "Apple Inc"}
        rows = self._run(transactions=[txn])
        assert rows[0]["transactions"] == []
        assert rows[0]["financial_disclosure_id"] is None

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

    def test_review_required_mapping_is_emitted_with_review_required_tier(self):
        def review_required_mapping(committee_code: str, congress: int) -> CommitteeMapping | None:
            if committee_code != "HSEN":
                return None
            return _committee_mapping(mapping_tier="review_required")

        rows = assemble_repeated_committee_linked_trading_rows(
            [_MEMBERSHIP_ROW],
            [_TRANSACTION_ROW],
            committee_sector_resolver=review_required_mapping,
            issuer_sector_resolver=_issuer_sector,
        )

        assert len(rows) == 1
        assert rows[0]["committee_sector"] == "energy"
        assert rows[0]["committee_mapping_tier"] == "review_required"
        assert len(rows[0]["transactions"]) == 1


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
        assert r["committee_membership_source_url"] == COMMITTEE_MEMBERSHIP_URL

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

    def test_review_required_mapping_is_emitted_with_review_required_tier(self):
        def review_required_mapping(committee_code: str, congress: int) -> CommitteeMapping | None:
            if committee_code != "HSEN":
                return None
            return _committee_mapping(mapping_tier="review_required")

        rows = assemble_sector_holdings_overlap_rows(
            [_MEMBERSHIP_ROW],
            [_HOLDING_ROW],
            committee_sector_resolver=review_required_mapping,
            issuer_sector_resolver=_issuer_sector,
        )

        assert len(rows) == 1
        assert rows[0]["committee_sector"] == "energy"
        assert rows[0]["committee_mapping_tier"] == "review_required"


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
