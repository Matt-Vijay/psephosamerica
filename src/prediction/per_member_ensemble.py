"""Bootstrap seed-ensemble of the per-member model for uncertainty intervals.

OVERALL_GOAL.md wants calibrated uncertainty via a Bayesian deep ensemble
across seeds. For the per-member model the practical analogue is a bootstrap
bag: train one model per seed on a resample of the votes, then read the spread
of the members' predictions as the epistemic uncertainty. A well-behaved
ensemble is *wider* (less certain) exactly where it is more likely wrong -- on
the cross-pressured slice -- which this module measures on real data.

Pure Python over the existing per-member model; the interval reuses the serving
layer's central-band helper so the served prediction and the report agree.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.prediction.per_member_model import (
    MemberVoteExample,
    PerMemberModel,
    train_per_member_model,
)
from src.prediction.served_prediction import ensemble_probability_interval


@dataclass(frozen=True)
class UncertaintySlice:
    """Real uncertainty/accuracy summary for one evaluation slice."""

    slice_name: str
    mean_brier: float
    mean_interval_width: float
    sample_count: int


def train_per_member_seed_ensemble(
    examples: list[MemberVoteExample],
    *,
    seeds: list[int],
    pooling_penalty: float = 1.0,
) -> list[PerMemberModel]:
    """Train one per-member model per seed on a bootstrap resample of the votes."""
    if not seeds:
        raise ValueError("seeds must not be empty")
    if not examples:
        raise ValueError("examples must not be empty")
    models: list[PerMemberModel] = []
    count = len(examples)
    for seed in seeds:
        rng = random.Random(seed)
        resample = [examples[rng.randrange(count)] for _ in range(count)]
        models.append(train_per_member_model(resample, pooling_penalty=pooling_penalty))
    return models


def predict_interval(
    models: list[PerMemberModel],
    member_id: str,
    signals: Mapping[str, float],
    *,
    confidence_level: float,
) -> tuple[float, float, float]:
    """Ensemble ``(mean, lower, upper)`` yea probability for one prediction."""
    probabilities = [model.predict_probability(member_id, signals) for model in models]
    mean = sum(probabilities) / len(probabilities)
    lower, upper = ensemble_probability_interval(probabilities, confidence_level=confidence_level)
    return mean, min(lower, mean), max(upper, mean)


def ensemble_uncertainty_report(
    models: list[PerMemberModel],
    vote_rows: list[VoteRow],
    *,
    confidence_level: float = 0.9,
) -> dict[str, UncertaintySlice]:
    """Per-slice mean Brier and mean ensemble interval width over real eval votes."""
    buckets: dict[str, list[tuple[float, float, bool]]] = {"all": []}
    for row in vote_rows:
        if row.vote_option not in {"yea", "nay"}:
            continue
        mean, lower, upper = predict_interval(
            models, row.member_bioguide_id, row.signals, confidence_level=confidence_level
        )
        entry = (mean, upper - lower, row.is_yea)
        buckets["all"].append(entry)
        if row.is_cross_pressured:
            buckets.setdefault("cross_pressured", []).append(entry)
        if row.party:
            buckets.setdefault(f"party:{row.party}", []).append(entry)

    report: dict[str, UncertaintySlice] = {}
    for name, entries in buckets.items():
        if not entries:
            continue
        brier = sum((mean - (1.0 if y else 0.0)) ** 2 for mean, _w, y in entries) / len(entries)
        width = sum(w for _m, w, _y in entries) / len(entries)
        report[name] = UncertaintySlice(
            slice_name=name,
            mean_brier=brier,
            mean_interval_width=width,
            sample_count=len(entries),
        )
    return report


if TYPE_CHECKING:
    from src.prediction.real_data_eval import VoteRow
