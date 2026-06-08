"""Deterministic synthetic benchmark for the CI no-regression gate.

OVERALL_GOAL.md requires a CI gate where Brier and log-loss must not regress on
the benchmark slice. To make that gate self-contained -- no database, no Track A
feed, fully reproducible -- this regenerates the blueprint's controlled-
experiment House (members with a hidden per-member sector polarity, a partisan
split, a strict train/eval split), scores the evaluation window with the
per-member model, and rolls the results up into the ``overall`` and
``cross_pressured`` benchmark slices the gate compares to a pinned baseline.

Deterministic given the seed (seeded stdlib randomness), so the committed
baseline and a fresh run agree exactly unless the model code changes the
metrics -- which is precisely what the gate exists to catch.
"""

from __future__ import annotations

import math
import random

from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.per_member_model import (
    MemberVoteExample,
    PerMemberModel,
    train_per_member_model,
)

_MEMBERS = 80
_TRAIN_BILLS = 120
_EVAL_BILLS = 60


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _log_loss(probability: float, is_yea: bool) -> float:
    clamped = min(1.0 - 1e-15, max(1e-15, probability))
    return -(math.log(clamped) if is_yea else math.log(1.0 - clamped))


def _evaluation_examples(
    rng: random.Random,
) -> tuple[list[MemberVoteExample], list[MemberVoteExample], list[bool]]:
    roster = [
        (f"M{index:03d}", "D" if index < int(_MEMBERS * 0.6) else "R", rng.uniform(-1.0, 1.0))
        for index in range(_MEMBERS)
    ]

    def make_bills(count: int) -> list[tuple[bool, float]]:
        return [(rng.random() < 0.5, rng.uniform(-1.0, 1.0)) for _ in range(count)]

    def votes(bills: list[tuple[bool, float]]) -> tuple[list[MemberVoteExample], list[bool]]:
        examples: list[MemberVoteExample] = []
        cross_pressured: list[bool] = []
        for d_favored, sector_exposure in bills:
            for member_id, party, polarity in roster:
                aligned = (party == "D") == d_favored
                party_pull = 2.0 if aligned else -2.0
                sector_pull = polarity * sector_exposure * 4.0
                examples.append(
                    MemberVoteExample(
                        member_id=member_id,
                        signals={
                            "party_alignment": 1.0 if aligned else -1.0,
                            "sector_exposure": sector_exposure,
                        },
                        is_yea=rng.random() < _sigmoid(party_pull + sector_pull),
                    )
                )
                cross_pressured.append(
                    (party_pull > 0) != (sector_pull > 0) and abs(sector_pull) >= 2.0
                )
        return examples, cross_pressured

    train_examples, _ = votes(make_bills(_TRAIN_BILLS))
    eval_examples, eval_cross = votes(make_bills(_EVAL_BILLS))
    return train_examples, eval_examples, eval_cross


def _slice_metrics(
    slice_name: str,
    model: PerMemberModel,
    examples: list[MemberVoteExample],
) -> BenchmarkSliceMetrics:
    brier_total = 0.0
    log_loss_total = 0.0
    for example in examples:
        probability = model.predict_probability(example.member_id, example.signals)
        target = 1.0 if example.is_yea else 0.0
        brier_total += (probability - target) ** 2
        log_loss_total += _log_loss(probability, example.is_yea)
    count = len(examples)
    return BenchmarkSliceMetrics(
        slice_name=slice_name,
        brier_score=brier_total / count,
        log_loss=log_loss_total / count,
        sample_count=count,
    )


def build_synthetic_benchmark(*, seed: int) -> list[BenchmarkSliceMetrics]:
    """Train the per-member model on the synthetic House and return its benchmark slices."""
    rng = random.Random(seed)
    train_examples, eval_examples, eval_cross = _evaluation_examples(rng)
    model = train_per_member_model(train_examples)
    cross_slice = [example for example, flag in zip(eval_examples, eval_cross, strict=True) if flag]
    return [
        _slice_metrics("overall", model, eval_examples),
        _slice_metrics("cross_pressured", model, cross_slice),
    ]
