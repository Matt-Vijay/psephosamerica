"""Tests for src/scoring/snapshots.py.

All tests are deterministic and make no network calls.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.scoring.snapshots import (
    build_snapshot_row,
    build_snapshot_rows,
    clamp,
    compute_dimension_scores,
    compute_score_total,
    group_deltas_by_dimension,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

DATE = dt.date(2025, 1, 15)
RUN_ID = 42

DIM = "conflict_of_interest_risk"


def _member(mid: int = 1) -> dict:
    return {"id": mid, "bioguide_id": f"A{mid:06d}", "full_name": "Test Member"}


def _delta(dimension: str = DIM, delta: float = -10.0) -> dict:
    return {"dimension": dimension, "delta": delta}


def _evidence_card(dimension: str = DIM, score_delta: float = -10.0) -> dict:
    return {"dimension": dimension, "score_delta": score_delta}


# ---------------------------------------------------------------------------
# clamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_mid_range_unchanged(self):
        assert clamp(50.0) == 50.0

    def test_below_floor_returns_zero(self):
        assert clamp(-1.0) == 0.0

    def test_above_ceiling_returns_hundred(self):
        assert clamp(101.0) == 100.0

    def test_exactly_zero(self):
        assert clamp(0.0) == 0.0

    def test_exactly_hundred(self):
        assert clamp(100.0) == 100.0

    def test_custom_bounds_clamps_low(self):
        assert clamp(30.0, lo=50.0, hi=80.0) == 50.0

    def test_custom_bounds_clamps_high(self):
        assert clamp(90.0, lo=50.0, hi=80.0) == 80.0

    def test_custom_bounds_in_range(self):
        assert clamp(65.0, lo=50.0, hi=80.0) == 65.0

    def test_large_negative(self):
        assert clamp(-9999.0) == 0.0

    def test_large_positive(self):
        assert clamp(9999.0) == 100.0


# ---------------------------------------------------------------------------
# group_deltas_by_dimension
# ---------------------------------------------------------------------------

class TestGroupDeltasByDimension:
    def test_empty_list(self):
        assert group_deltas_by_dimension([]) == {}

    def test_single_delta_row(self):
        result = group_deltas_by_dimension([_delta(DIM, -10.0)])
        assert result == {DIM: [-10.0]}

    def test_single_evidence_card_row(self):
        result = group_deltas_by_dimension([_evidence_card(DIM, -15.0)])
        assert result == {DIM: [-15.0]}

    def test_mixed_key_styles(self):
        rows = [_delta(DIM, -5.0), _evidence_card(DIM, -3.0)]
        result = group_deltas_by_dimension(rows)
        assert result == {DIM: [-5.0, -3.0]}

    def test_multiple_dimensions(self):
        rows = [
            _delta("dim_a", -5.0),
            _delta("dim_b", -3.0),
            _delta("dim_a", -2.0),
        ]
        result = group_deltas_by_dimension(rows)
        assert result == {"dim_a": [-5.0, -2.0], "dim_b": [-3.0]}

    def test_preserves_insertion_order(self):
        rows = [_delta(DIM, -1.0), _delta(DIM, -2.0), _delta(DIM, -3.0)]
        result = group_deltas_by_dimension(rows)
        assert result[DIM] == [-1.0, -2.0, -3.0]

    def test_row_without_dimension_skipped(self):
        rows = [{"delta": -5.0}]  # no dimension key
        assert group_deltas_by_dimension(rows) == {}

    def test_row_without_delta_key_skipped(self):
        rows = [{"dimension": DIM, "something_else": 99}]
        assert group_deltas_by_dimension(rows) == {}

    def test_positive_delta(self):
        result = group_deltas_by_dimension([_delta(DIM, +5.0)])
        assert result == {DIM: [5.0]}

    def test_zero_delta(self):
        result = group_deltas_by_dimension([_delta(DIM, 0.0)])
        assert result == {DIM: [0.0]}

    def test_delta_key_takes_precedence_over_score_delta(self):
        # If a row has both keys, "delta" wins (documented behaviour).
        row = {"dimension": DIM, "delta": -7.0, "score_delta": -99.0}
        result = group_deltas_by_dimension([row])
        assert result == {DIM: [-7.0]}


# ---------------------------------------------------------------------------
# compute_dimension_scores
# ---------------------------------------------------------------------------

class TestComputeDimensionScores:
    def test_no_deltas_returns_baseline(self):
        result = compute_dimension_scores({"dim_a": []})
        assert result == {"dim_a": 100.0}

    def test_single_negative_delta(self):
        result = compute_dimension_scores({"dim_a": [-10.0]})
        assert result == {"dim_a": 90.0}

    def test_clamps_to_zero(self):
        result = compute_dimension_scores({"dim_a": [-200.0]})
        assert result == {"dim_a": 0.0}

    def test_clamps_to_hundred_on_positive_overflow(self):
        result = compute_dimension_scores({"dim_a": [+50.0]})
        assert result == {"dim_a": 100.0}

    def test_multiple_deltas_summed(self):
        result = compute_dimension_scores({"dim_a": [-10.0, -20.0]})
        assert result == {"dim_a": 70.0}

    def test_mixed_sign_deltas(self):
        # 100 - 30 + 10 = 80
        result = compute_dimension_scores({"dim_a": [-30.0, +10.0]})
        assert result == {"dim_a": 80.0}

    def test_multiple_dimensions_independent(self):
        result = compute_dimension_scores({"dim_a": [-10.0], "dim_b": [-25.0]})
        assert result["dim_a"] == 90.0
        assert result["dim_b"] == 75.0

    def test_deterministic_same_input_same_output(self):
        deltas = {"dim_a": [-5.0, -15.0, -10.0]}
        assert compute_dimension_scores(deltas) == compute_dimension_scores(deltas)

    def test_exact_boundary_zero(self):
        result = compute_dimension_scores({"dim_a": [-100.0]})
        assert result == {"dim_a": 0.0}

    def test_exact_boundary_hundred(self):
        result = compute_dimension_scores({"dim_a": [0.0]})
        assert result == {"dim_a": 100.0}

    def test_empty_dict_returns_empty(self):
        assert compute_dimension_scores({}) == {}


# ---------------------------------------------------------------------------
# compute_score_total
# ---------------------------------------------------------------------------

class TestComputeScoreTotal:
    def test_single_dimension_equals_that_score(self):
        assert compute_score_total({"dim_a": 90.0}) == 90.0

    def test_single_dimension_rounded_to_two(self):
        assert compute_score_total({"dim_a": 66.666}) == round(66.666, 2)

    def test_two_dimensions_arithmetic_mean(self):
        result = compute_score_total({"dim_a": 80.0, "dim_b": 60.0})
        assert result == 70.0

    def test_three_dimensions_mean_rounded(self):
        result = compute_score_total({"a": 100.0, "b": 100.0, "c": 99.0})
        expected = round((100.0 + 100.0 + 99.0) / 3, 2)
        assert result == expected

    def test_deterministic_regardless_of_insertion_order(self):
        # Python dicts preserve insertion order; results should be equal
        r1 = compute_score_total({"dim_a": 80.0, "dim_b": 60.0})
        r2 = compute_score_total({"dim_b": 60.0, "dim_a": 80.0})
        assert r1 == r2

    def test_empty_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_score_total({})

    def test_all_baseline_returns_100(self):
        assert compute_score_total({"a": 100.0, "b": 100.0}) == 100.0

    def test_returns_float(self):
        result = compute_score_total({"dim_a": 75.0})
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# build_snapshot_row
# ---------------------------------------------------------------------------

class TestBuildSnapshotRow:
    def test_no_deltas_returns_baseline_snapshot(self):
        row = build_snapshot_row(_member(1), [], DATE, RUN_ID)
        assert row["member_id"] == 1
        assert row["snapshot_at"] == DATE
        assert row["recompute_run_id"] == RUN_ID
        assert row["score_total"] == 100.0
        assert row["dimension_scores"] == {DIM: 100.0}

    def test_single_dimension_delta(self):
        row = build_snapshot_row(_member(1), [_delta(DIM, -20.0)], DATE, RUN_ID)
        assert row["score_total"] == 80.0
        assert row["dimension_scores"] == {DIM: 80.0}

    def test_evidence_card_rows_accepted(self):
        row = build_snapshot_row(_member(1), [_evidence_card(DIM, -15.0)], DATE, RUN_ID)
        assert row["score_total"] == 85.0

    def test_multiple_deltas_accumulated(self):
        deltas = [_delta(DIM, -10.0), _delta(DIM, -5.0)]
        row = build_snapshot_row(_member(1), deltas, DATE, RUN_ID)
        assert row["dimension_scores"][DIM] == 85.0
        assert row["score_total"] == 85.0

    def test_multiple_dimensions(self):
        deltas = [_delta("dim_a", -20.0), _delta("dim_b", -10.0)]
        row = build_snapshot_row(_member(1), deltas, DATE, RUN_ID)
        expected_total = round((80.0 + 90.0) / 2, 2)
        assert row["score_total"] == expected_total
        assert row["dimension_scores"]["dim_a"] == 80.0
        assert row["dimension_scores"]["dim_b"] == 90.0

    def test_clamping_applied(self):
        deltas = [_delta(DIM, -999.0)]
        row = build_snapshot_row(_member(1), deltas, DATE, RUN_ID)
        assert row["dimension_scores"][DIM] == 0.0
        assert row["score_total"] == 0.0

    def test_member_id_taken_from_member_dict(self):
        row = build_snapshot_row(_member(99), [_delta(DIM, -5.0)], DATE, RUN_ID)
        assert row["member_id"] == 99

    def test_missing_member_id_raises(self):
        with pytest.raises(KeyError):
            build_snapshot_row({}, [], DATE, RUN_ID)

    def test_snapshot_at_preserved(self):
        d = dt.date(2024, 6, 30)
        row = build_snapshot_row(_member(1), [], d, RUN_ID)
        assert row["snapshot_at"] == d

    def test_recompute_run_id_preserved(self):
        row = build_snapshot_row(_member(1), [], DATE, 7)
        assert row["recompute_run_id"] == 7

    def test_deterministic(self):
        deltas = [_delta(DIM, -10.0), _delta(DIM, -5.0)]
        r1 = build_snapshot_row(_member(1), deltas, DATE, RUN_ID)
        r2 = build_snapshot_row(_member(1), deltas, DATE, RUN_ID)
        assert r1 == r2


# ---------------------------------------------------------------------------
# build_snapshot_rows
# ---------------------------------------------------------------------------

class TestBuildSnapshotRows:
    def test_empty_members_returns_empty_list(self):
        assert build_snapshot_rows([], {}, DATE, RUN_ID) == []

    def test_one_member_no_deltas(self):
        rows = build_snapshot_rows([_member(1)], {}, DATE, RUN_ID)
        assert len(rows) == 1
        assert rows[0]["score_total"] == 100.0
        assert rows[0]["dimension_scores"] == {DIM: 100.0}

    def test_one_member_with_deltas(self):
        rows = build_snapshot_rows(
            [_member(1)],
            {1: [_delta(DIM, -30.0)]},
            DATE,
            RUN_ID,
        )
        assert rows[0]["score_total"] == 70.0

    def test_preserves_member_order(self):
        members = [_member(3), _member(1), _member(2)]
        delta_map = {
            3: [_delta(DIM, -30.0)],
            1: [_delta(DIM, -10.0)],
            2: [_delta(DIM, -20.0)],
        }
        rows = build_snapshot_rows(members, delta_map, DATE, RUN_ID)
        assert [r["member_id"] for r in rows] == [3, 1, 2]

    def test_members_without_deltas_get_baseline(self):
        members = [_member(1), _member(2)]
        delta_map = {1: [_delta(DIM, -10.0)]}
        rows = build_snapshot_rows(members, delta_map, DATE, RUN_ID)
        assert rows[0]["score_total"] == 90.0
        assert rows[1]["score_total"] == 100.0
        assert rows[1]["dimension_scores"] == {DIM: 100.0}

    def test_shared_snapshot_at_and_run_id(self):
        members = [_member(1), _member(2)]
        rows = build_snapshot_rows(members, {}, DATE, RUN_ID)
        for r in rows:
            assert r["snapshot_at"] == DATE
            assert r["recompute_run_id"] == RUN_ID

    def test_each_member_id_in_output(self):
        members = [_member(i) for i in range(1, 6)]
        rows = build_snapshot_rows(members, {}, DATE, RUN_ID)
        assert [r["member_id"] for r in rows] == list(range(1, 6))
