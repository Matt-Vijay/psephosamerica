"""Tests for src/rules/contexts.py.

All tests are deterministic and make no network calls.
Validates all four launch rule family context builders and their
supporting derived-value helpers.
"""

from __future__ import annotations

import datetime as dt

from src.rules.contexts import (
    build_committee_sector_trade_context,
    build_late_or_amended_disclosure_context,
    build_repeated_committee_linked_trading_context,
    build_sector_holdings_overlap_context,
    count_distinct_trade_days,
    count_matching_transactions,
    days_gap,
    midpoint_value,
    overlap_days,
    overdue_days,
    sectors_overlap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

D = dt.date  # alias


def txn(date: dt.date, sector: str) -> dict:
    return {"transaction_date": date, "sector": sector}


# ---------------------------------------------------------------------------
# days_gap
# ---------------------------------------------------------------------------

class TestDaysGap:
    def test_same_date(self):
        assert days_gap(D(2024, 1, 1), D(2024, 1, 1)) == 0

    def test_one_day_apart(self):
        assert days_gap(D(2024, 1, 1), D(2024, 1, 2)) == 1

    def test_order_independent(self):
        assert days_gap(D(2024, 3, 1), D(2024, 1, 1)) == days_gap(D(2024, 1, 1), D(2024, 3, 1))

    def test_across_year_boundary(self):
        assert days_gap(D(2023, 12, 31), D(2024, 1, 1)) == 1

    def test_thirty_days(self):
        assert days_gap(D(2024, 1, 1), D(2024, 1, 31)) == 30


# ---------------------------------------------------------------------------
# overlap_days
# ---------------------------------------------------------------------------

class TestOverlapDays:
    def test_full_overlap(self):
        assert overlap_days(D(2024, 1, 1), D(2024, 6, 30), D(2024, 1, 1), D(2024, 6, 30)) == 182

    def test_no_overlap(self):
        assert overlap_days(D(2024, 1, 1), D(2024, 3, 31), D(2024, 4, 1), D(2024, 6, 30)) == 0

    def test_adjacent_no_overlap(self):
        # end1 is March 31, start2 is April 1 → gap of 1 day; our inclusive formula
        # gives overlap_end (Mar 31) - overlap_start (Apr 1) = -1 day → clamped to 0
        assert overlap_days(D(2024, 1, 1), D(2024, 3, 31), D(2024, 4, 1), D(2024, 6, 30)) == 0

    def test_single_day_overlap(self):
        assert overlap_days(D(2024, 1, 1), D(2024, 1, 1), D(2024, 1, 1), D(2024, 1, 1)) == 1

    def test_partial_overlap(self):
        # Jan 1 – Mar 31 overlapping Feb 1 – Apr 30 → Feb 1 – Mar 31 = 60 days
        result = overlap_days(D(2024, 1, 1), D(2024, 3, 31), D(2024, 2, 1), D(2024, 4, 30))
        assert result == 60

    def test_open_ended_range_uses_reference_date(self):
        ref = D(2024, 6, 30)
        # service: Jan 1 – Jun 30 (open), disclosure: Mar 1 – Apr 30
        result = overlap_days(D(2024, 1, 1), None, D(2024, 3, 1), D(2024, 4, 30), reference_date=ref)
        # overlap: Mar 1 – Apr 30 = 61 days
        assert result == 61

    def test_both_open_ended_uses_reference_date(self):
        ref = D(2024, 3, 1)
        result = overlap_days(D(2024, 1, 1), None, D(2024, 2, 1), None, reference_date=ref)
        # overlap: Feb 1 – Mar 1 = 30 days
        assert result == 30


# ---------------------------------------------------------------------------
# overdue_days
# ---------------------------------------------------------------------------

class TestOverdueDays:
    def test_on_time(self):
        assert overdue_days(D(2024, 5, 15), D(2024, 5, 15)) == 0

    def test_one_day_late(self):
        assert overdue_days(D(2024, 5, 16), D(2024, 5, 15)) == 1

    def test_thirty_days_late(self):
        assert overdue_days(D(2024, 6, 14), D(2024, 5, 15)) == 30

    def test_filed_early_returns_zero(self):
        assert overdue_days(D(2024, 5, 1), D(2024, 5, 15)) == 0

    def test_none_filed_at_returns_none(self):
        assert overdue_days(None, D(2024, 5, 15)) is None


# ---------------------------------------------------------------------------
# count_matching_transactions / count_distinct_trade_days
# ---------------------------------------------------------------------------

class TestCountMatchingTransactions:
    def _service(self):
        return D(2023, 1, 1), D(2023, 12, 31)

    def test_all_match(self):
        start, end = self._service()
        txns = [txn(D(2023, 3, 1), "finance"), txn(D(2023, 6, 1), "finance")]
        assert count_matching_transactions(txns, "finance", start, end) == 2

    def test_sector_mismatch_excluded(self):
        start, end = self._service()
        txns = [txn(D(2023, 3, 1), "energy"), txn(D(2023, 6, 1), "finance")]
        assert count_matching_transactions(txns, "finance", start, end) == 1

    def test_outside_service_window_excluded(self):
        start, end = self._service()
        txns = [txn(D(2022, 12, 31), "finance"), txn(D(2024, 1, 1), "finance")]
        assert count_matching_transactions(txns, "finance", start, end) == 0

    def test_boundary_dates_included(self):
        start, end = self._service()
        txns = [txn(start, "finance"), txn(end, "finance")]
        assert count_matching_transactions(txns, "finance", start, end) == 2

    def test_open_ended_service(self):
        ref = D(2023, 12, 31)
        txns = [txn(D(2023, 6, 1), "finance")]
        assert count_matching_transactions(txns, "finance", D(2023, 1, 1), None, reference_date=ref) == 1

    def test_empty_transactions(self):
        assert count_matching_transactions([], "finance", *self._service()) == 0

    def test_missing_transaction_date_skipped(self):
        start, end = self._service()
        txns = [{"sector": "finance"}]  # no transaction_date key
        assert count_matching_transactions(txns, "finance", start, end) == 0


class TestCountDistinctTradeDays:
    def _service(self):
        return D(2023, 1, 1), D(2023, 12, 31)

    def test_multiple_trades_same_day(self):
        start, end = self._service()
        txns = [txn(D(2023, 3, 1), "finance"), txn(D(2023, 3, 1), "finance")]
        assert count_distinct_trade_days(txns, "finance", start, end) == 1

    def test_two_distinct_days(self):
        start, end = self._service()
        txns = [txn(D(2023, 3, 1), "finance"), txn(D(2023, 4, 1), "finance")]
        assert count_distinct_trade_days(txns, "finance", start, end) == 2

    def test_sector_mismatch_not_counted(self):
        start, end = self._service()
        txns = [txn(D(2023, 3, 1), "energy")]
        assert count_distinct_trade_days(txns, "finance", start, end) == 0

    def test_empty_transactions(self):
        assert count_distinct_trade_days([], "finance", *self._service()) == 0


# ---------------------------------------------------------------------------
# sectors_overlap
# ---------------------------------------------------------------------------

class TestSectorsOverlap:
    def test_overlap_present(self):
        assert sectors_overlap({"finance", "energy"}, {"energy", "tech"}) is True

    def test_no_overlap(self):
        assert sectors_overlap({"finance"}, {"energy", "tech"}) is False

    def test_empty_holding_sectors(self):
        assert sectors_overlap({"finance"}, set()) is False

    def test_empty_committee_sectors(self):
        assert sectors_overlap(set(), {"finance"}) is False

    def test_accepts_lists(self):
        assert sectors_overlap(["finance"], ["finance"]) is True


# ---------------------------------------------------------------------------
# midpoint_value
# ---------------------------------------------------------------------------

class TestMidpointValue:
    def test_both_present(self):
        assert midpoint_value(1000.0, 5000.0) == 3000.0

    def test_only_min(self):
        assert midpoint_value(1000.0, None) == 1000.0

    def test_only_max(self):
        assert midpoint_value(None, 5000.0) == 5000.0

    def test_both_none(self):
        assert midpoint_value(None, None) is None

    def test_equal_bounds(self):
        assert midpoint_value(2000.0, 2000.0) == 2000.0


# ---------------------------------------------------------------------------
# build_committee_sector_trade_context
# ---------------------------------------------------------------------------

class TestBuildCommitteeSectorTradeContext:
    def _base_row(self) -> dict:
        return {
            "committee_name": "Senate Finance Committee",
            "committee_sector": "finance",
            "committee_start_date": D(2023, 1, 1),
            "committee_end_date": D(2023, 12, 31),
            "holding_sector": "finance",
            "sector_name": "Finance",
            "disclosure_period_start": D(2023, 3, 1),
            "disclosure_period_end": D(2023, 9, 30),
        }

    def test_required_fact_keys_present(self):
        ctx = build_committee_sector_trade_context(self._base_row())
        for key in (
            "committee_name",
            "committee_sector",
            "holding_sector",
            "sector_name",
            "committee_service_overlap_days",
            "holding_overlap_days",
            "overlap_start",
            "overlap_end",
        ):
            assert key in ctx, f"Missing key: {key}"

    def test_overlap_computed_correctly(self):
        ctx = build_committee_sector_trade_context(self._base_row())
        # committee: Jan 1 – Dec 31; disclosure: Mar 1 – Sep 30
        # overlap: Mar 1 – Sep 30 = 214 days
        assert ctx["committee_service_overlap_days"] == 214
        assert ctx["holding_overlap_days"] == 214

    def test_overlap_start_end_correct(self):
        ctx = build_committee_sector_trade_context(self._base_row())
        assert ctx["overlap_start"] == D(2023, 3, 1)
        assert ctx["overlap_end"] == D(2023, 9, 30)

    def test_no_overlap_returns_zero(self):
        row = self._base_row()
        row["committee_end_date"] = D(2023, 2, 28)
        row["disclosure_period_start"] = D(2023, 4, 1)
        ctx = build_committee_sector_trade_context(row)
        assert ctx["committee_service_overlap_days"] == 0

    def test_open_ended_committee(self):
        row = self._base_row()
        row["committee_end_date"] = None
        row["disclosure_period_end"] = D(2023, 6, 30)
        ctx = build_committee_sector_trade_context(row)
        # open-ended committee, but disclosure ends Jun 30
        assert ctx["committee_service_overlap_days"] > 0

    def test_sector_none_preserved(self):
        row = self._base_row()
        row["committee_sector"] = None
        ctx = build_committee_sector_trade_context(row)
        assert ctx["committee_sector"] is None


# ---------------------------------------------------------------------------
# build_repeated_committee_linked_trading_context
# ---------------------------------------------------------------------------

class TestBuildRepeatedCommitteeLinkedTradingContext:
    def _base_row(self) -> dict:
        return {
            "committee_name": "House Energy and Commerce Committee",
            "committee_sector": "energy",
            "committee_start_date": D(2023, 1, 1),
            "committee_end_date": D(2023, 12, 31),
            "transactions": [
                txn(D(2023, 3, 15), "energy"),
                txn(D(2023, 5, 20), "energy"),
                txn(D(2023, 5, 20), "energy"),  # same day, different trade
                txn(D(2022, 11, 1), "energy"),   # before service window
                txn(D(2023, 7, 1), "tech"),      # wrong sector
            ],
        }

    def test_required_fact_keys_present(self):
        ctx = build_repeated_committee_linked_trading_context(self._base_row())
        for key in (
            "committee_name",
            "committee_sector",
            "matching_transaction_count",
            "distinct_trade_days",
            "service_overlap_days",
        ):
            assert key in ctx, f"Missing key: {key}"

    def test_matching_transaction_count(self):
        ctx = build_repeated_committee_linked_trading_context(self._base_row())
        # 3 matching within service window (Mar 15, May 20×2)
        assert ctx["matching_transaction_count"] == 3

    def test_distinct_trade_days(self):
        ctx = build_repeated_committee_linked_trading_context(self._base_row())
        # 2 distinct days: Mar 15 and May 20
        assert ctx["distinct_trade_days"] == 2

    def test_service_overlap_days_positive(self):
        ctx = build_repeated_committee_linked_trading_context(self._base_row())
        assert ctx["service_overlap_days"] >= 365

    def test_null_committee_sector_zeroes_counts(self):
        row = self._base_row()
        row["committee_sector"] = None
        ctx = build_repeated_committee_linked_trading_context(row)
        assert ctx["matching_transaction_count"] == 0
        assert ctx["distinct_trade_days"] == 0
        assert ctx["committee_sector"] is None

    def test_empty_transactions(self):
        row = self._base_row()
        row["transactions"] = []
        ctx = build_repeated_committee_linked_trading_context(row)
        assert ctx["matching_transaction_count"] == 0
        assert ctx["distinct_trade_days"] == 0

    def test_open_ended_service(self):
        row = self._base_row()
        row["committee_end_date"] = None
        ctx = build_repeated_committee_linked_trading_context(row)
        assert ctx["service_overlap_days"] > 0


# ---------------------------------------------------------------------------
# build_late_or_amended_disclosure_context
# ---------------------------------------------------------------------------

class TestBuildLateOrAmendedDisclosureContext:
    def _original_late_row(self) -> dict:
        return {
            "filing_id": "fd-001",
            "filed_at": D(2023, 6, 14),
            "deadline": D(2023, 5, 15),
            "filing_is_amendment": False,
            "superseded_filing_id": None,
            "filing_kind": "original",
        }

    def _amendment_row(self) -> dict:
        return {
            "filing_id": "fd-002",
            "filed_at": D(2023, 7, 1),
            "deadline": D(2023, 5, 15),
            "filing_is_amendment": True,
            "superseded_filing_id": "fd-001",
            "filing_kind": "amendment",
        }

    def test_required_fact_keys_present(self):
        ctx = build_late_or_amended_disclosure_context(self._original_late_row())
        for key in (
            "filing_id",
            "days_late",
            "filing_kind",
            "filing_is_amendment",
            "superseded_filing_id",
            "amendment_clause",
        ):
            assert key in ctx, f"Missing key: {key}"

    def test_days_late_calculated_correctly(self):
        ctx = build_late_or_amended_disclosure_context(self._original_late_row())
        assert ctx["days_late"] == 30

    def test_on_time_filing_days_late_zero(self):
        row = self._original_late_row()
        row["filed_at"] = D(2023, 5, 15)
        ctx = build_late_or_amended_disclosure_context(row)
        assert ctx["days_late"] == 0

    def test_early_filing_days_late_zero(self):
        row = self._original_late_row()
        row["filed_at"] = D(2023, 4, 1)
        ctx = build_late_or_amended_disclosure_context(row)
        assert ctx["days_late"] == 0

    def test_unfiled_days_late_is_none(self):
        row = self._original_late_row()
        row["filed_at"] = None
        ctx = build_late_or_amended_disclosure_context(row)
        assert ctx["days_late"] is None

    def test_amendment_clause_populated(self):
        ctx = build_late_or_amended_disclosure_context(self._amendment_row())
        assert "fd-001" in ctx["amendment_clause"]

    def test_amendment_clause_empty_for_original(self):
        ctx = build_late_or_amended_disclosure_context(self._original_late_row())
        assert ctx["amendment_clause"] == ""

    def test_amendment_without_superseded_id(self):
        row = self._amendment_row()
        row["superseded_filing_id"] = None
        ctx = build_late_or_amended_disclosure_context(row)
        assert "(amendment)" in ctx["amendment_clause"]
        assert ctx["superseded_filing_id"] is None

    def test_filing_is_amendment_false_preserved(self):
        ctx = build_late_or_amended_disclosure_context(self._original_late_row())
        assert ctx["filing_is_amendment"] is False

    def test_filing_kind_preserved(self):
        ctx = build_late_or_amended_disclosure_context(self._original_late_row())
        assert ctx["filing_kind"] == "original"


# ---------------------------------------------------------------------------
# build_sector_holdings_overlap_context
# ---------------------------------------------------------------------------

class TestBuildSectorHoldingsOverlapContext:
    def _base_row(self) -> dict:
        return {
            "committee_name": "Senate Armed Services Committee",
            "committee_sector": "defense",
            "committee_start_date": D(2023, 1, 1),
            "committee_end_date": D(2023, 12, 31),
            "holding_sector": "defense",
            "sector_name": "Defense",
            "holding_value_min": 15000.0,
            "holding_value_max": 50000.0,
            "disclosure_period_start": D(2023, 1, 1),
            "disclosure_period_end": D(2023, 12, 31),
        }

    def test_required_fact_keys_present(self):
        ctx = build_sector_holdings_overlap_context(self._base_row())
        for key in (
            "committee_name",
            "committee_sector",
            "holding_sector",
            "sector_name",
            "holding_value_usd",
            "overlap_days",
        ):
            assert key in ctx, f"Missing key: {key}"

    def test_holding_value_usd_is_midpoint(self):
        ctx = build_sector_holdings_overlap_context(self._base_row())
        assert ctx["holding_value_usd"] == 32500.0

    def test_overlap_days_full_year(self):
        ctx = build_sector_holdings_overlap_context(self._base_row())
        assert ctx["overlap_days"] == 365

    def test_no_overlap(self):
        row = self._base_row()
        row["committee_end_date"] = D(2023, 3, 31)
        row["disclosure_period_start"] = D(2023, 6, 1)
        ctx = build_sector_holdings_overlap_context(row)
        assert ctx["overlap_days"] == 0

    def test_null_holding_value_handled(self):
        row = self._base_row()
        row["holding_value_min"] = None
        row["holding_value_max"] = None
        ctx = build_sector_holdings_overlap_context(row)
        assert ctx["holding_value_usd"] is None

    def test_only_min_value(self):
        row = self._base_row()
        row["holding_value_max"] = None
        ctx = build_sector_holdings_overlap_context(row)
        assert ctx["holding_value_usd"] == 15000.0

    def test_null_committee_sector(self):
        row = self._base_row()
        row["committee_sector"] = None
        ctx = build_sector_holdings_overlap_context(row)
        assert ctx["committee_sector"] is None

    def test_open_ended_service_and_disclosure(self):
        row = self._base_row()
        row["committee_end_date"] = None
        row["disclosure_period_end"] = None
        ctx = build_sector_holdings_overlap_context(row)
        assert ctx["overlap_days"] > 0
