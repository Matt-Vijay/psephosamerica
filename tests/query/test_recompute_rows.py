"""Tests for src/query/recompute_rows.py.

No live DB.  fetch_all is patched at the call site in each test class.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

from src.query.recompute_rows import (
    fetch_previous_score_snapshot_rows,
    fetch_recompute_members,
    index_latest_previous_snapshots,
)

CONN = MagicMock()
MODULE = "src.query.recompute_rows.fetch_all"

BIOGUIDE_A = "A000001"
BIOGUIDE_B = "B000002"

_MEMBER_ROW_A: dict[str, Any] = {
    "id": 1,
    "bioguide_id": BIOGUIDE_A,
    "slug": "jane-doe",
    "full_name": "Jane Doe",
    "first_name": "Jane",
    "last_name": "Doe",
    "party": "D",
    "state": "CA",
    "chamber": "house",
    "current_term_start": dt.date(2023, 1, 3),
    "current_term_end": None,
}

_MEMBER_ROW_B: dict[str, Any] = {
    "id": 2,
    "bioguide_id": BIOGUIDE_B,
    "slug": "john-smith",
    "full_name": "John Smith",
    "first_name": "John",
    "last_name": "Smith",
    "party": "R",
    "state": "TX",
    "chamber": "senate",
    "current_term_start": dt.date(2021, 1, 3),
    "current_term_end": None,
}

_SNAPSHOT_A_RECENT: dict[str, Any] = {
    "id": 10,
    "member_id": 1,
    "recompute_run_id": 5,
    "snapshot_at": dt.date(2024, 3, 1),
    "score_total": 42.5,
    "dimension_scores": {"conflict_of_interest_risk": 42.5},
    "published_at": dt.datetime(2024, 3, 2, 0, 0),
}

_SNAPSHOT_A_OLDER: dict[str, Any] = {
    "id": 9,
    "member_id": 1,
    "recompute_run_id": 4,
    "snapshot_at": dt.date(2024, 2, 1),
    "score_total": 35.0,
    "dimension_scores": {"conflict_of_interest_risk": 35.0},
    "published_at": dt.datetime(2024, 2, 2, 0, 0),
}

_SNAPSHOT_B: dict[str, Any] = {
    "id": 20,
    "member_id": 2,
    "recompute_run_id": 5,
    "snapshot_at": dt.date(2024, 3, 1),
    "score_total": 10.0,
    "dimension_scores": {"conflict_of_interest_risk": 10.0},
    "published_at": None,
}


# ---------------------------------------------------------------------------
# fetch_recompute_members
# ---------------------------------------------------------------------------

class TestFetchRecomputeMembers:
    def test_all_current_members_when_no_filter(self):
        with patch(MODULE, return_value=[_MEMBER_ROW_A, _MEMBER_ROW_B]) as mock_fa:
            result = fetch_recompute_members(CONN)
        mock_fa.assert_called_once()
        assert result == [_MEMBER_ROW_A, _MEMBER_ROW_B]

    def test_no_params_passed_when_no_filter(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recompute_members(CONN)
        # all-members variant has no params argument
        call_args = mock_fa.call_args[0]
        assert len(call_args) == 2  # conn + sql only

    def test_sql_references_is_current_for_all_members(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recompute_members(CONN)
        sql = mock_fa.call_args[0][1]
        assert "is_current" in sql

    def test_filtered_by_bioguide_ids(self):
        with patch(MODULE, return_value=[_MEMBER_ROW_A]) as mock_fa:
            result = fetch_recompute_members(CONN, bioguide_ids=[BIOGUIDE_A])
        mock_fa.assert_called_once()
        assert result == [_MEMBER_ROW_A]

    def test_passes_bioguide_ids_param(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recompute_members(CONN, bioguide_ids=[BIOGUIDE_A, BIOGUIDE_B])
        _, _, params = mock_fa.call_args[0]
        assert params["bioguide_ids"] == [BIOGUIDE_A, BIOGUIDE_B]

    def test_filtered_sql_still_constrains_is_current(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recompute_members(CONN, bioguide_ids=[BIOGUIDE_A])
        sql = mock_fa.call_args[0][1]
        assert "is_current" in sql
        assert "bioguide_id" in sql

    def test_returns_empty_list_when_no_members(self):
        with patch(MODULE, return_value=[]):
            result = fetch_recompute_members(CONN)
        assert result == []

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_recompute_members(CONN)
        assert mock_fa.call_args[0][0] is CONN

    def test_member_row_shape(self):
        with patch(MODULE, return_value=[_MEMBER_ROW_A]):
            result = fetch_recompute_members(CONN)
        row = result[0]
        assert "id" in row
        assert "bioguide_id" in row
        assert "chamber" in row
        assert "slug" in row


# ---------------------------------------------------------------------------
# fetch_previous_score_snapshot_rows
# ---------------------------------------------------------------------------

class TestFetchPreviousScoreSnapshotRows:
    def test_returns_snapshot_rows(self):
        with patch(MODULE, return_value=[_SNAPSHOT_A_RECENT]) as mock_fa:
            result = fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        mock_fa.assert_called_once()
        assert result == [_SNAPSHOT_A_RECENT]

    def test_passes_member_ids(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_previous_score_snapshot_rows(CONN, member_ids=[1, 2])
        _, _, params = mock_fa.call_args[0]
        assert params["member_ids"] == [1, 2]

    def test_sql_references_score_snapshot(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        sql = mock_fa.call_args[0][1]
        assert "score_snapshot" in sql

    def test_sql_orders_newest_first(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        sql = mock_fa.call_args[0][1]
        assert "snapshot_at DESC" in sql

    def test_empty_member_ids_returns_early(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            result = fetch_previous_score_snapshot_rows(CONN, member_ids=[])
        mock_fa.assert_not_called()
        assert result == []

    def test_returns_multiple_rows_per_member(self):
        rows = [_SNAPSHOT_A_RECENT, _SNAPSHOT_A_OLDER]
        with patch(MODULE, return_value=rows):
            result = fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        assert len(result) == 2

    def test_snapshot_row_shape(self):
        with patch(MODULE, return_value=[_SNAPSHOT_A_RECENT]):
            result = fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        row = result[0]
        assert "member_id" in row
        assert "snapshot_at" in row
        assert "score_total" in row
        assert "dimension_scores" in row

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_previous_score_snapshot_rows(CONN, member_ids=[1])
        assert mock_fa.call_args[0][0] is CONN


# ---------------------------------------------------------------------------
# index_latest_previous_snapshots
# ---------------------------------------------------------------------------

class TestIndexLatestPreviousSnapshots:
    def test_indexes_single_member(self):
        idx = index_latest_previous_snapshots([_SNAPSHOT_A_RECENT])
        assert idx[1] is _SNAPSHOT_A_RECENT

    def test_first_row_wins_for_each_member(self):
        # DESC order: recent is first, older is second
        idx = index_latest_previous_snapshots([_SNAPSHOT_A_RECENT, _SNAPSHOT_A_OLDER])
        assert idx[1]["snapshot_at"] == dt.date(2024, 3, 1)

    def test_indexes_multiple_members(self):
        idx = index_latest_previous_snapshots([_SNAPSHOT_A_RECENT, _SNAPSHOT_B])
        assert 1 in idx
        assert 2 in idx

    def test_each_member_has_one_entry(self):
        rows = [_SNAPSHOT_A_RECENT, _SNAPSHOT_A_OLDER, _SNAPSHOT_B]
        idx = index_latest_previous_snapshots(rows)
        assert len(idx) == 2

    def test_empty_rows_returns_empty_dict(self):
        assert index_latest_previous_snapshots([]) == {}

    def test_absent_member_not_in_index(self):
        idx = index_latest_previous_snapshots([_SNAPSHOT_A_RECENT])
        assert 2 not in idx

    def test_does_not_call_fetch_all(self):
        with patch(MODULE) as mock_fa:
            index_latest_previous_snapshots([_SNAPSHOT_A_RECENT])
        mock_fa.assert_not_called()

    def test_carries_all_snapshot_fields(self):
        idx = index_latest_previous_snapshots([_SNAPSHOT_A_RECENT])
        row = idx[1]
        assert row["score_total"] == 42.5
        assert row["recompute_run_id"] == 5
        assert row["dimension_scores"] == {"conflict_of_interest_risk": 42.5}
