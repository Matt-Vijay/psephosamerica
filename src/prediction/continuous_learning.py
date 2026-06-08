"""Continuous-learning planner for the vote model.

OVERALL_GOAL.md: each real vote becomes a training example, with an incremental
retrain daily and a full retrain weekly. This planner decides, as of a given
date, whether the next cycle is a full retrain or an incremental update and
which examples it should train on.

Two invariants matter:

* **Temporal discipline.** A cycle never trains on a vote whose date is after
  ``as_of`` -- you cannot learn from a vote that has not happened. This is the
  continuous-learning face of the strict no-leakage rule enforced at prediction
  time by ``backtest.py``.
* **Full beats incremental on cadence.** Once the full-retrain cadence has
  elapsed (or on the first ever run) the cycle is a full retrain over the whole
  available corpus; otherwise it is an incremental update over only the votes
  resolved since the last cycle.

The planner is generic over opaque example objects and pure (dates are passed
in), so the actual trainer -- the per-member model or the transformer -- plugs
in at the edge and the schedule is deterministic and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

TrainingKind = Literal["incremental", "full"]


@dataclass(frozen=True)
class DatedExample:
    """A training example tagged with the date its vote was resolved."""

    example: object
    vote_date: date


@dataclass(frozen=True)
class TrainingPlan:
    """The decided training cycle: its kind, examples, and the date it runs."""

    kind: TrainingKind
    examples: list[object]
    as_of: date


def plan_training(
    corpus: list[DatedExample],
    *,
    as_of: date,
    last_full_retrain: date | None,
    last_incremental_at: date | None,
    full_cadence_days: int = 7,
) -> TrainingPlan:
    """Decide the next training cycle as of ``as_of``.

    A full retrain runs on the first cycle (``last_full_retrain is None``) or
    once ``full_cadence_days`` have elapsed since the last full retrain; it
    covers every vote resolved on or before ``as_of``. Otherwise the cycle is
    incremental over the votes resolved strictly after the last cycle's date.
    """
    if full_cadence_days <= 0:
        raise ValueError("full_cadence_days must be positive")

    available = [item for item in corpus if item.vote_date <= as_of]

    needs_full = last_full_retrain is None or (as_of - last_full_retrain).days >= full_cadence_days
    if needs_full:
        return TrainingPlan(
            kind="full",
            examples=[item.example for item in available],
            as_of=as_of,
        )

    # In this branch last_full_retrain is a concrete date (else needs_full would
    # be True), so `since` is too; the `is None` guard only narrows the type.
    since = last_incremental_at or last_full_retrain
    incremental = [item.example for item in available if since is None or item.vote_date > since]
    return TrainingPlan(kind="incremental", examples=incremental, as_of=as_of)
