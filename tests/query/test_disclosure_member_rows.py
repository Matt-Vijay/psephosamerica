"""Tests for src/query/disclosure_member_rows.py.

No live DB.  fetch_all is patched at the call site in each test class.

Tests focus on behavioral contracts: return values, dispatch logic,
row shape, and error handling — not SQL structure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.query.disclosure_member_rows import (
    fetch_house_member_rows,
    fetch_member_rows_for_disclosures,
    fetch_senate_member_rows,
)

CONN = MagicMock()
MODULE = "src.query.disclosure_member_rows.fetch_all"

_HOUSE_ROW_A: dict[str, Any] = {
    "bioguide_id": "P000197",
    "first_name": "Nancy",
    "last_name": "Pelosi",
    "state": "CA",
    "district": 11,
}

_HOUSE_ROW_B: dict[str, Any] = {
    "bioguide_id": "A000370",
    "first_name": "Alma",
    "last_name": "Adams",
    "state": "NC",
    "district": 12,
}

_SENATE_ROW_A: dict[str, Any] = {
    "bioguide_id": "W000779",
    "first_name": "Ron",
    "last_name": "Wyden",
    "state": "OR",
}

_SENATE_ROW_B: dict[str, Any] = {
    "bioguide_id": "C000141",
    "first_name": "Benjamin",
    "last_name": "Cardin",
    "state": "MD",
}


# ---------------------------------------------------------------------------
# fetch_house_member_rows
# ---------------------------------------------------------------------------


class TestFetchHouseMemberRows:
    def test_returns_house_rows(self):
        with patch(MODULE, return_value=[_HOUSE_ROW_A, _HOUSE_ROW_B]) as mock_fa:
            result = fetch_house_member_rows(CONN)
        mock_fa.assert_called_once()
        assert result == [_HOUSE_ROW_A, _HOUSE_ROW_B]

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_house_member_rows(CONN)
        assert mock_fa.call_args[0][0] is CONN

    def test_row_contains_district(self):
        with patch(MODULE, return_value=[_HOUSE_ROW_A]):
            result = fetch_house_member_rows(CONN)
        assert "district" in result[0]

    def test_row_contains_state(self):
        with patch(MODULE, return_value=[_HOUSE_ROW_A]):
            result = fetch_house_member_rows(CONN)
        assert "state" in result[0]

    def test_row_contains_name_fields(self):
        with patch(MODULE, return_value=[_HOUSE_ROW_A]):
            result = fetch_house_member_rows(CONN)
        row = result[0]
        assert "first_name" in row
        assert "last_name" in row
        assert "bioguide_id" in row

    def test_returns_empty_list_when_no_rows(self):
        with patch(MODULE, return_value=[]):
            result = fetch_house_member_rows(CONN)
        assert result == []


# ---------------------------------------------------------------------------
# fetch_senate_member_rows
# ---------------------------------------------------------------------------


class TestFetchSenateMemberRows:
    def test_returns_senate_rows(self):
        with patch(MODULE, return_value=[_SENATE_ROW_A, _SENATE_ROW_B]) as mock_fa:
            result = fetch_senate_member_rows(CONN)
        mock_fa.assert_called_once()
        assert result == [_SENATE_ROW_A, _SENATE_ROW_B]

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_senate_member_rows(CONN)
        assert mock_fa.call_args[0][0] is CONN

    def test_row_contains_state(self):
        with patch(MODULE, return_value=[_SENATE_ROW_A]):
            result = fetch_senate_member_rows(CONN)
        assert "state" in result[0]

    def test_row_contains_name_fields(self):
        with patch(MODULE, return_value=[_SENATE_ROW_A]):
            result = fetch_senate_member_rows(CONN)
        row = result[0]
        assert "first_name" in row
        assert "last_name" in row
        assert "bioguide_id" in row

    def test_row_does_not_contain_district(self):
        with patch(MODULE, return_value=[_SENATE_ROW_A]):
            result = fetch_senate_member_rows(CONN)
        assert "district" not in result[0]

    def test_returns_empty_list_when_no_rows(self):
        with patch(MODULE, return_value=[]):
            result = fetch_senate_member_rows(CONN)
        assert result == []


# ---------------------------------------------------------------------------
# fetch_member_rows_for_disclosures
# ---------------------------------------------------------------------------


class TestFetchMemberRowsForDisclosures:
    def test_dispatches_to_house_fetcher(self):
        with patch(MODULE, return_value=[_HOUSE_ROW_A]) as mock_fa:
            result = fetch_member_rows_for_disclosures(CONN, chamber="house")
        mock_fa.assert_called_once()
        assert result == [_HOUSE_ROW_A]

    def test_dispatches_to_senate_fetcher(self):
        with patch(MODULE, return_value=[_SENATE_ROW_A]) as mock_fa:
            result = fetch_member_rows_for_disclosures(CONN, chamber="senate")
        mock_fa.assert_called_once()
        assert result == [_SENATE_ROW_A]

    def test_invalid_chamber_raises_value_error(self):
        with pytest.raises(ValueError, match="chamber must be"):
            fetch_member_rows_for_disclosures(CONN, chamber="both")

    def test_unknown_chamber_error_includes_value(self):
        with pytest.raises(ValueError, match="unknown"):
            fetch_member_rows_for_disclosures(CONN, chamber="unknown")

    def test_passes_conn_through_for_house(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_member_rows_for_disclosures(CONN, chamber="house")
        assert mock_fa.call_args[0][0] is CONN

    def test_passes_conn_through_for_senate(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_member_rows_for_disclosures(CONN, chamber="senate")
        assert mock_fa.call_args[0][0] is CONN
