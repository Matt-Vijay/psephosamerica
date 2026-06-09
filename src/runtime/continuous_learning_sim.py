"""Continuous-learning cron simulator.

OVERALL_GOAL.md: run continuous learning on a cron -- each real vote becomes a
training example, incremental retrain daily, full retrain weekly -- idempotently
and without leakage. This drives ``continuous_learning.plan_training`` across a
schedule of ``as_of`` dates, threading the last-full / last-incremental state so
the cutoff advances exactly once per tick, full retrains land on cadence, and a
tick never trains on a vote dated after its ``as_of``.

The simulation is a pure function of (corpus, schedule, cadence), so re-running
a tick yields the identical plan -- the idempotency a cron needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.prediction.continuous_learning import DatedExample, plan_training


@dataclass(frozen=True)
class CronTick:
    """One scheduled run: what kind of retrain and over how many examples."""

    as_of: date
    kind: str  # "incremental" | "full"
    example_count: int
    last_full_retrain: date


def simulate_cron(
    corpus: list[DatedExample],
    *,
    schedule: list[date],
    full_cadence_days: int = 7,
) -> list[CronTick]:
    """Run the planner across the schedule, advancing the cutoff idempotently.

    ``schedule`` is the sequence of cron fire times (e.g. one per day). Returns
    one :class:`CronTick` per fire time, in chronological order.
    """
    last_full: date | None = None
    last_incremental: date | None = None
    ticks: list[CronTick] = []
    for as_of in sorted(schedule):
        plan = plan_training(
            corpus,
            as_of=as_of,
            last_full_retrain=last_full,
            last_incremental_at=last_incremental,
            full_cadence_days=full_cadence_days,
        )
        if plan.kind == "full":
            last_full = as_of
        last_incremental = as_of
        # last_full is guaranteed set after the first (always-full) tick.
        ticks.append(
            CronTick(
                as_of=as_of,
                kind=plan.kind,
                example_count=len(plan.examples),
                last_full_retrain=last_full if last_full is not None else as_of,
            )
        )
    return ticks
