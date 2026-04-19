"""Tests for src/scoring/deltas.py — pure, no I/O, no network calls."""

from __future__ import annotations

import datetime as dt

import pytest

from src.scoring.deltas import (
    diff_many_members,
    diff_one_member,
    filter_changed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SNAP_PREV = dt.date(2025, 2, 1)
SNAP_CURR = dt.date(2025, 3, 1)
DIM = "conflict_of_interest_risk"


def _snap(
    bioguide_id: str,
    snapshot_at: dt.date = SNAP_CURR,
    *,
    coi: float = 80.0,
    extra_dims: dict | None = None,
) -> dict:
    dim_scores: dict = {DIM: coi}
    if extra_dims:
        dim_scores.update(extra_dims)
    return {
        "member_id": 1,
        "bioguide_id": bioguide_id,
        "snapshot_at": snapshot_at,
        "score_total": coi,
        "dimension_scores": dim_scores,
    }


# ---------------------------------------------------------------------------
# diff_one_member — basic
# ---------------------------------------------------------------------------


class TestDiffOneMemberBasic:
    def test_returns_delta_dict_per_dimension(self):
        curr = _snap("A000001", coi=70.0)
        prev = _snap("A000001", SNAP_PREV, coi=85.0)
        result = diff_one_member(curr, prev)
        assert len(result) == 1
        assert result[0]["dimension"] == DIM
        assert result[0]["delta"] == pytest.approx(70.0 - 85.0)

    def test_required_output_keys_present(self):
        curr = _snap("A000001", coi=70.0)
        prev = _snap("A000001", SNAP_PREV, coi=85.0)
        row = diff_one_member(curr, prev)[0]
        assert "member_bioguide_id" in row
        assert "dimension" in row
        assert "delta" in row
        assert "snapshot_date" in row
        assert "explanation" in row

    def test_snapshot_date_is_current(self):
        curr = _snap("A000001", SNAP_CURR, coi=70.0)
        prev = _snap("A000001", SNAP_PREV, coi=85.0)
        row = diff_one_member(curr, prev)[0]
        assert row["snapshot_date"] == SNAP_CURR

    def test_member_bioguide_id_matches_current(self):
        curr = _snap("B000002", coi=60.0)
        prev = _snap("B000002", SNAP_PREV, coi=60.0)
        row = diff_one_member(curr, prev)[0]
        assert row["member_bioguide_id"] == "B000002"

    def test_positive_delta_when_score_increases(self):
        curr = _snap("A000001", coi=90.0)
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["delta"] > 0

    def test_negative_delta_when_score_decreases(self):
        curr = _snap("A000001", coi=60.0)
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["delta"] < 0

    def test_zero_delta_when_no_change(self):
        curr = _snap("A000001", coi=80.0)
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["delta"] == 0.0


# ---------------------------------------------------------------------------
# diff_one_member — multiple dimensions
# ---------------------------------------------------------------------------


class TestDiffOneMemberMultiDimension:
    def test_one_row_per_dimension(self):
        curr = _snap("A000001", coi=70.0, extra_dims={"dim_b": 50.0})
        prev = _snap("A000001", SNAP_PREV, coi=80.0, extra_dims={"dim_b": 40.0})
        result = diff_one_member(curr, prev)
        assert len(result) == 2
        dims = {r["dimension"] for r in result}
        assert dims == {DIM, "dim_b"}

    def test_each_dimension_delta_is_independent(self):
        curr = _snap("A000001", coi=70.0, extra_dims={"dim_b": 55.0})
        prev = _snap("A000001", SNAP_PREV, coi=80.0, extra_dims={"dim_b": 40.0})
        by_dim = {r["dimension"]: r["delta"] for r in diff_one_member(curr, prev)}
        assert by_dim[DIM] == pytest.approx(70.0 - 80.0)
        assert by_dim["dim_b"] == pytest.approx(55.0 - 40.0)

    def test_dimension_absent_in_previous_treated_as_zero(self):
        curr = _snap("A000001", coi=70.0, extra_dims={"new_dim": 30.0})
        prev = _snap("A000001", SNAP_PREV, coi=80.0)  # no new_dim
        by_dim = {r["dimension"]: r["delta"] for r in diff_one_member(curr, prev)}
        assert by_dim["new_dim"] == pytest.approx(30.0 - 0.0)

    def test_dimension_absent_in_current_not_emitted(self):
        curr = _snap("A000001", coi=70.0)  # no dim_b
        prev = _snap("A000001", SNAP_PREV, coi=80.0, extra_dims={"dim_b": 40.0})
        dims = {r["dimension"] for r in diff_one_member(curr, prev)}
        assert "dim_b" not in dims


# ---------------------------------------------------------------------------
# diff_one_member — first-time snapshot handling
# ---------------------------------------------------------------------------


class TestDiffOneMemberFirstTime:
    def test_no_previous_returns_empty_by_default(self):
        curr = _snap("A000001", coi=70.0)
        result = diff_one_member(curr, None)
        assert result == []

    def test_no_previous_include_first_time_true(self):
        curr = _snap("A000001", coi=70.0)
        result = diff_one_member(curr, None, include_first_time=True)
        assert len(result) == 1
        assert result[0]["delta"] == pytest.approx(70.0)

    def test_first_time_delta_equals_current_score(self):
        curr = _snap("A000001", coi=42.5)
        row = diff_one_member(curr, None, include_first_time=True)[0]
        assert row["delta"] == pytest.approx(42.5)

    def test_first_time_multi_dimension(self):
        curr = _snap("A000001", coi=70.0, extra_dims={"dim_b": 30.0})
        result = diff_one_member(curr, None, include_first_time=True)
        assert len(result) == 2
        by_dim = {r["dimension"]: r["delta"] for r in result}
        assert by_dim[DIM] == pytest.approx(70.0)
        assert by_dim["dim_b"] == pytest.approx(30.0)

    def test_include_first_time_false_explicit(self):
        curr = _snap("A000001", coi=70.0)
        result = diff_one_member(curr, None, include_first_time=False)
        assert result == []


# ---------------------------------------------------------------------------
# diff_one_member — date coercion
# ---------------------------------------------------------------------------


class TestDiffOneMemberDateCoercion:
    def test_iso_string_snapshot_date(self):
        curr = _snap("A000001", coi=70.0)
        curr["snapshot_at"] = "2025-03-01"
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["snapshot_date"] == dt.date(2025, 3, 1)

    def test_datetime_snapshot_at(self):
        curr = _snap("A000001", coi=70.0)
        curr["snapshot_at"] = dt.datetime(2025, 3, 1, 12, 0, 0)
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["snapshot_date"] == dt.date(2025, 3, 1)


# ---------------------------------------------------------------------------
# diff_one_member — missing bioguide_id
# ---------------------------------------------------------------------------


class TestDiffOneMemberIdentity:
    def test_missing_bioguide_id_raises(self):
        curr = {
            "snapshot_at": SNAP_CURR,
            "score_total": 70.0,
            "dimension_scores": {DIM: 70.0},
        }
        with pytest.raises(KeyError):
            diff_one_member(curr, None, include_first_time=True)

    def test_member_bioguide_id_fallback_key(self):
        curr = _snap("A000001", coi=70.0)
        del curr["bioguide_id"]
        curr["member_bioguide_id"] = "A000001"
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        row = diff_one_member(curr, prev)[0]
        assert row["member_bioguide_id"] == "A000001"

    def test_empty_dimension_scores_are_treated_as_explicit_baseline(self):
        curr = _snap("A000001", coi=70.0)
        curr["dimension_scores"] = {}
        prev = _snap("A000001", SNAP_PREV, coi=80.0)
        result = diff_one_member(curr, prev)
        assert len(result) == 1
        assert result[0]["dimension"] == DIM
        assert result[0]["delta"] == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# diff_many_members
# ---------------------------------------------------------------------------


class TestDiffManyMembers:
    def test_processes_all_members(self):
        rows = [_snap("A000001", coi=70.0), _snap("B000002", coi=60.0)]
        prev = {
            "A000001": _snap("A000001", SNAP_PREV, coi=80.0),
            "B000002": _snap("B000002", SNAP_PREV, coi=65.0),
        }
        result = diff_many_members(rows, prev)
        bids = {r["member_bioguide_id"] for r in result}
        assert bids == {"A000001", "B000002"}

    def test_flat_output(self):
        rows = [
            _snap("A000001", coi=70.0, extra_dims={"d2": 50.0}),
            _snap("B000002", coi=60.0),
        ]
        prev = {
            "A000001": _snap("A000001", SNAP_PREV, coi=80.0, extra_dims={"d2": 45.0}),
            "B000002": _snap("B000002", SNAP_PREV, coi=65.0),
        }
        result = diff_many_members(rows, prev)
        # A has 2 dims, B has 1 dim
        assert len(result) == 3

    def test_missing_previous_excluded_by_default(self):
        rows = [_snap("A000001", coi=70.0), _snap("B000002", coi=60.0)]
        prev = {"A000001": _snap("A000001", SNAP_PREV, coi=80.0)}
        result = diff_many_members(rows, prev)
        bids = {r["member_bioguide_id"] for r in result}
        assert "B000002" not in bids

    def test_missing_previous_included_with_flag(self):
        rows = [_snap("A000001", coi=70.0), _snap("B000002", coi=60.0)]
        prev = {"A000001": _snap("A000001", SNAP_PREV, coi=80.0)}
        result = diff_many_members(rows, prev, include_first_time=True)
        bids = {r["member_bioguide_id"] for r in result}
        assert "B000002" in bids

    def test_empty_current_rows(self):
        assert diff_many_members([], {}) == []

    def test_empty_previous_all_first_time_excluded(self):
        rows = [_snap("A000001", coi=70.0)]
        result = diff_many_members(rows, {})
        assert result == []

    def test_empty_previous_all_first_time_included(self):
        rows = [_snap("A000001", coi=70.0)]
        result = diff_many_members(rows, {}, include_first_time=True)
        assert len(result) == 1

    def test_order_follows_current_rows(self):
        rows = [_snap("B000002", coi=60.0), _snap("A000001", coi=70.0)]
        prev = {
            "A000001": _snap("A000001", SNAP_PREV, coi=80.0),
            "B000002": _snap("B000002", SNAP_PREV, coi=65.0),
        }
        result = diff_many_members(rows, prev)
        # B is first in current_rows so first in output
        assert result[0]["member_bioguide_id"] == "B000002"
        assert result[1]["member_bioguide_id"] == "A000001"

    def test_correct_deltas_for_each_member(self):
        rows = [_snap("A000001", coi=70.0), _snap("B000002", coi=60.0)]
        prev = {
            "A000001": _snap("A000001", SNAP_PREV, coi=80.0),
            "B000002": _snap("B000002", SNAP_PREV, coi=55.0),
        }
        by_bid = {r["member_bioguide_id"]: r["delta"] for r in diff_many_members(rows, prev)}
        assert by_bid["A000001"] == pytest.approx(-10.0)
        assert by_bid["B000002"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# filter_changed
# ---------------------------------------------------------------------------


class TestFilterChanged:
    def _delta(self, bid: str, delta: float, dimension: str = DIM) -> dict:
        return {
            "member_bioguide_id": bid,
            "dimension": dimension,
            "delta": delta,
            "snapshot_date": SNAP_CURR,
            "explanation": "",
        }

    def test_keeps_nonzero(self):
        deltas = [self._delta("A000001", -10.0), self._delta("B000002", 5.0)]
        result = filter_changed(deltas)
        assert len(result) == 2

    def test_removes_zero(self):
        deltas = [self._delta("A000001", 0.0)]
        result = filter_changed(deltas)
        assert result == []

    def test_mixed(self):
        deltas = [
            self._delta("A000001", -10.0),
            self._delta("B000002", 0.0),
            self._delta("C000003", 5.0),
        ]
        result = filter_changed(deltas)
        assert len(result) == 2
        bids = {r["member_bioguide_id"] for r in result}
        assert "B000002" not in bids

    def test_empty_input(self):
        assert filter_changed([]) == []

    def test_all_zero_returns_empty(self):
        deltas = [self._delta("A000001", 0.0), self._delta("B000002", 0.0)]
        assert filter_changed(deltas) == []

    def test_all_nonzero_returns_all(self):
        deltas = [self._delta("A000001", -1.0), self._delta("B000002", 2.0)]
        assert len(filter_changed(deltas)) == 2

    def test_preserves_row_content(self):
        original = self._delta("A000001", -7.5)
        result = filter_changed([original])
        assert result[0] == original

    def test_very_small_nonzero_kept(self):
        deltas = [self._delta("A000001", 1e-9)]
        result = filter_changed(deltas)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Integration: diff_many_members → filter_changed → events_from_score_deltas
# ---------------------------------------------------------------------------


class TestIntegrationWithFeedChanges:
    def test_output_compatible_with_events_from_score_deltas(self):
        from src.feed.changes import events_from_score_deltas

        members = {
            "A000001": {"full_name": "Alice Smith", "slug": "alice-smith"},
            "B000002": {"full_name": "Bob Jones", "slug": "bob-jones"},
        }
        rows = [_snap("A000001", coi=70.0), _snap("B000002", coi=80.0)]
        prev = {
            "A000001": _snap("A000001", SNAP_PREV, coi=85.0),
            "B000002": _snap("B000002", SNAP_PREV, coi=80.0),  # no change
        }
        deltas = filter_changed(diff_many_members(rows, prev))
        events = events_from_score_deltas(deltas, members)
        # Only A changed
        assert len(events) == 1
        assert events[0].member_bioguide_id == "A000001"
        assert events[0].score_delta == pytest.approx(-15.0)

    def test_first_time_snapshot_flows_to_feed(self):
        from src.feed.changes import events_from_score_deltas

        members = {"C000003": {"full_name": "Carol Lee", "slug": "carol-lee"}}
        rows = [_snap("C000003", coi=50.0)]
        deltas = diff_many_members(rows, {}, include_first_time=True)
        events = events_from_score_deltas(deltas, members)
        assert len(events) == 1
        assert events[0].score_delta == pytest.approx(50.0)
