"""Tests for the continuous-learning cron simulator."""

from __future__ import annotations

from datetime import date, timedelta

from src.prediction.continuous_learning import DatedExample
from src.runtime.continuous_learning_sim import simulate_cron


def _corpus() -> list[DatedExample]:
    # One vote per day across three weeks.
    return [
        DatedExample(example=f"vote-{index}", vote_date=date(2024, 1, 1) + timedelta(days=index))
        for index in range(21)
    ]


def _weekly_then_daily_schedule() -> list[date]:
    return [date(2024, 1, 1) + timedelta(days=index) for index in range(1, 22)]


def test_first_tick_is_full_then_incremental_until_cadence() -> None:
    ticks = simulate_cron(_corpus(), schedule=_weekly_then_daily_schedule(), full_cadence_days=7)
    assert ticks[0].kind == "full"
    # Within the first week the runs are incremental.
    assert all(tick.kind == "incremental" for tick in ticks[1:6])
    # A full retrain lands again once the 7-day cadence elapses.
    assert any(tick.kind == "full" for tick in ticks[7:])


def test_cutoff_advances_and_never_includes_future_votes() -> None:
    corpus = _corpus()
    ticks = simulate_cron(corpus, schedule=_weekly_then_daily_schedule(), full_cadence_days=7)
    for tick in ticks:
        # A full tick covers exactly the votes dated on or before its as_of.
        if tick.kind == "full":
            available = sum(1 for item in corpus if item.vote_date <= tick.as_of)
            assert tick.example_count == available


def test_simulation_is_idempotent() -> None:
    corpus = _corpus()
    schedule = _weekly_then_daily_schedule()
    first = simulate_cron(corpus, schedule=schedule, full_cadence_days=7)
    second = simulate_cron(corpus, schedule=schedule, full_cadence_days=7)
    assert first == second


def test_incremental_only_covers_new_votes() -> None:
    ticks = simulate_cron(_corpus(), schedule=_weekly_then_daily_schedule(), full_cadence_days=7)
    # The day-2 incremental run sees only the single vote newly dated that day.
    second = ticks[1]
    assert second.kind == "incremental"
    assert second.example_count == 1
