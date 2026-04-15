"""Tests for parse.disclosures.index_lookup.

No network calls; fetch_house_index and fetch_senate_index are patched at the
index_lookup module boundary.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.parse.disclosures.index_lookup import fetch_disclosure_rows_by_doc_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PTR_ROW = HouseIndexRow(
    last_name="GARCIA",
    first_name="Mike",
    suffix="",
    raw_filing_type="P",
    state_dst="CA27",
    year=2023,
    filing_date=date(2024, 1, 10),
    doc_id="10023432",
    filing_kind=HouseFilingKind.PTR,
)

_ANNUAL_ROW = HouseIndexRow(
    last_name="ADAMS",
    first_name="Alma",
    suffix="",
    raw_filing_type="O",
    state_dst="NC12",
    year=2023,
    filing_date=date(2024, 6, 14),
    doc_id="20024001",
    filing_kind=HouseFilingKind.ANNUAL,
)

_SENATE_ROW = SenateIndexRow(
    first_name="John",
    last_name="Smith",
    office="Senator, TX",
    report_type="Annual Report for CY2023",
    date_filed="01/15/2024",
    doc_id="abc-123-def",
    filing_year=2023,
)

_SENATE_ROW_2 = SenateIndexRow(
    first_name="Jane",
    last_name="Doe",
    office="Senator, CA",
    report_type="PTR",
    date_filed="03/20/2024",
    doc_id="xyz-789",
    filing_year=2023,
)

_HOUSE_PATCH = "src.parse.disclosures.index_lookup.fetch_house_index"
_SENATE_PATCH = "src.parse.disclosures.index_lookup.fetch_senate_index"


# ---------------------------------------------------------------------------
# House — keyed lookup structure
# ---------------------------------------------------------------------------


class TestHouseLookupStructure:
    def test_returns_dict_keyed_by_doc_id(self):
        with patch(_HOUSE_PATCH, return_value=[_PTR_ROW]) as _mock:
            result = fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        assert isinstance(result, dict)
        assert "10023432" in result

    def test_value_is_house_index_row(self):
        with patch(_HOUSE_PATCH, return_value=[_PTR_ROW]):
            result = fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        assert isinstance(result["10023432"], HouseIndexRow)

    def test_row_identity_preserved(self):
        with patch(_HOUSE_PATCH, return_value=[_PTR_ROW]):
            result = fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        assert result["10023432"] is _PTR_ROW

    def test_empty_index_returns_empty_dict(self):
        with patch(_HOUSE_PATCH, return_value=[]):
            result = fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        assert result == {}

    def test_multiple_rows_all_present(self):
        with patch(_HOUSE_PATCH, return_value=[_PTR_ROW, _ANNUAL_ROW]):
            result = fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        assert set(result.keys()) == {"10023432", "20024001"}


# ---------------------------------------------------------------------------
# House — filing_kind filtering
# ---------------------------------------------------------------------------


class TestHouseFilingKindFilter:
    def test_ptr_kind_calls_fetch_with_ptr_enum(self):
        with patch(_HOUSE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr")
        mock.assert_called_once_with(2023, HouseFilingKind.PTR, client=None)

    def test_annual_kind_calls_fetch_with_annual_enum(self):
        with patch(_HOUSE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="annual")
        mock.assert_called_once_with(2023, HouseFilingKind.ANNUAL, client=None)

    def test_none_kind_fetches_both_ptr_and_annual(self):
        with patch(_HOUSE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind=None)
        called_kinds = {call.args[1] for call in mock.call_args_list}
        assert called_kinds == {HouseFilingKind.PTR, HouseFilingKind.ANNUAL}

    def test_none_kind_merges_rows_from_both_kinds(self):
        def _side_effect(year, kind, *, client):
            return [_PTR_ROW] if kind == HouseFilingKind.PTR else [_ANNUAL_ROW]

        with patch(_HOUSE_PATCH, side_effect=_side_effect):
            result = fetch_disclosure_rows_by_doc_id("house", 2023)
        assert set(result.keys()) == {"10023432", "20024001"}

    def test_invalid_filing_kind_raises_value_error(self):
        with pytest.raises(ValueError):
            fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="quarterly")

    def test_year_passed_through_to_fetch(self):
        with patch(_HOUSE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("house", 2021, filing_kind="ptr")
        assert mock.call_args.args[0] == 2021

    def test_client_passed_through_to_fetch(self):
        sentinel = object()
        with patch(_HOUSE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("house", 2023, filing_kind="ptr", client=sentinel)
        assert mock.call_args.kwargs["client"] is sentinel


# ---------------------------------------------------------------------------
# Senate — keyed lookup structure
# ---------------------------------------------------------------------------


class TestSenateLookupStructure:
    def test_returns_dict_keyed_by_doc_id(self):
        with patch(_SENATE_PATCH, return_value=[_SENATE_ROW]):
            result = fetch_disclosure_rows_by_doc_id("senate", 2023)
        assert isinstance(result, dict)
        assert "abc-123-def" in result

    def test_value_is_senate_index_row(self):
        with patch(_SENATE_PATCH, return_value=[_SENATE_ROW]):
            result = fetch_disclosure_rows_by_doc_id("senate", 2023)
        assert isinstance(result["abc-123-def"], SenateIndexRow)

    def test_row_identity_preserved(self):
        with patch(_SENATE_PATCH, return_value=[_SENATE_ROW]):
            result = fetch_disclosure_rows_by_doc_id("senate", 2023)
        assert result["abc-123-def"] is _SENATE_ROW

    def test_empty_result_returns_empty_dict(self):
        with patch(_SENATE_PATCH, return_value=[]):
            result = fetch_disclosure_rows_by_doc_id("senate", 2023)
        assert result == {}

    def test_multiple_rows_all_keyed(self):
        with patch(_SENATE_PATCH, return_value=[_SENATE_ROW, _SENATE_ROW_2]):
            result = fetch_disclosure_rows_by_doc_id("senate", 2023)
        assert set(result.keys()) == {"abc-123-def", "xyz-789"}

    def test_year_passed_through_to_fetch(self):
        with patch(_SENATE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("senate", 2021)
        assert mock.call_args.args[0] == 2021

    def test_client_passed_through_to_fetch(self):
        sentinel = object()
        with patch(_SENATE_PATCH, return_value=[]) as mock:
            fetch_disclosure_rows_by_doc_id("senate", 2023, client=sentinel)
        assert mock.call_args.kwargs["client"] is sentinel

    def test_filing_kind_has_no_effect_on_senate_fetch(self):
        """Senate fetch is not filtered by filing_kind; both values produce same call."""
        with patch(_SENATE_PATCH, return_value=[]) as mock_none:
            fetch_disclosure_rows_by_doc_id("senate", 2023, filing_kind=None)
        with patch(_SENATE_PATCH, return_value=[]) as mock_ptr:
            fetch_disclosure_rows_by_doc_id("senate", 2023, filing_kind="ptr")

        # Both call senate fetch exactly once with the same year.
        assert mock_none.call_count == 1
        assert mock_ptr.call_count == 1
        assert mock_none.call_args.args[0] == mock_ptr.call_args.args[0] == 2023


# ---------------------------------------------------------------------------
# Unknown chamber
# ---------------------------------------------------------------------------


class TestUnknownChamber:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="chamber"):
            fetch_disclosure_rows_by_doc_id("congress", 2023)  # type: ignore[arg-type]

    def test_error_message_contains_bad_value(self):
        with pytest.raises(ValueError, match="congress"):
            fetch_disclosure_rows_by_doc_id("congress", 2023)  # type: ignore[arg-type]
