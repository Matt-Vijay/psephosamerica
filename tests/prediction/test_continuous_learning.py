"""Tests for the continuous-learning planner.

OVERALL_GOAL.md: each real vote becomes a training example; retrain
incrementally daily and fully weekly. The planner decides, as of a given date,
whether the next cycle is a full retrain or an incremental update and which
examples it trains on -- never including votes that have not happened yet
(the temporal discipline that keeps continuous learning leakage-safe).
"""

from __future__ import annotations

from datetime import date

from src.prediction.continuous_learning import DatedExample, plan_training


def _corpus() -> list[DatedExample]:
    return [
        DatedExample(example="v1", vote_date=date(2025, 1, 1)),
        DatedExample(example="v2", vote_date=date(2025, 1, 5)),
        DatedExample(example="v3", vote_date=date(2025, 1, 8)),
        DatedExample(example="v4", vote_date=date(2025, 1, 12)),
    ]


def test_first_run_is_a_full_retrain_over_all_available_examples() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 12),
        last_full_retrain=None,
        last_incremental_at=None,
    )
    assert plan.kind == "full"
    assert plan.examples == ["v1", "v2", "v3", "v4"]


def test_full_retrain_excludes_future_votes() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 6),
        last_full_retrain=None,
        last_incremental_at=None,
    )
    # v3 (Jan 8) and v4 (Jan 12) have not happened as of Jan 6.
    assert plan.examples == ["v1", "v2"]


def test_within_cadence_is_incremental_on_new_votes_only() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 12),
        last_full_retrain=date(2025, 1, 8),
        last_incremental_at=date(2025, 1, 8),
        full_cadence_days=7,
    )
    assert plan.kind == "incremental"
    # Only the vote after the last cycle (v4 on Jan 12).
    assert plan.examples == ["v4"]


def test_cadence_elapsed_triggers_full_retrain() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 15),
        last_full_retrain=date(2025, 1, 8),
        last_incremental_at=date(2025, 1, 12),
        full_cadence_days=7,
    )
    assert plan.kind == "full"
    assert plan.examples == ["v1", "v2", "v3", "v4"]


def test_incremental_with_no_new_votes_is_empty() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 13),
        last_full_retrain=date(2025, 1, 12),
        last_incremental_at=date(2025, 1, 12),
        full_cadence_days=7,
    )
    assert plan.kind == "incremental"
    assert plan.examples == []


def test_incremental_falls_back_to_last_full_when_no_prior_incremental() -> None:
    plan = plan_training(
        _corpus(),
        as_of=date(2025, 1, 9),
        last_full_retrain=date(2025, 1, 5),
        last_incremental_at=None,
        full_cadence_days=7,
    )
    assert plan.kind == "incremental"
    # New since the last full retrain (Jan 5): v3 (Jan 8).
    assert plan.examples == ["v3"]
