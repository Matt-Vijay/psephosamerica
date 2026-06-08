"""Tests for the integrated stacked ensemble forecaster.

OVERALL_GOAL.md: the LLM forecaster is an independent ensemble member, stacked
with the transformer on a held-out window. This wires concrete member
forecasters (a transformer-backed one and an LLM-backed one, both behind the
Forecaster Protocol so the real model/LLM call lives at the edge) through the
held-out logistic stack into one combined forecaster.
"""

from __future__ import annotations

import math
import random

from src.prediction.ensemble_forecaster import (
    HeldoutVote,
    NamedForecaster,
    fit_stacked_forecaster,
)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


class _DictForecaster:
    """A member forecaster backed by precomputed per-(person, bill) probabilities."""

    def __init__(self, probabilities: dict[tuple[str, str], float]) -> None:
        self._probabilities = probabilities

    def forecast(self, *, canonical_person_id: str, canonical_bill_id: str) -> float:
        return self._probabilities[(canonical_person_id, canonical_bill_id)]


def _synthetic(
    seed: int, *, count: int
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float], list[HeldoutVote]]:
    rng = random.Random(seed)
    transformer: dict[tuple[str, str], float] = {}
    llm: dict[tuple[str, str], float] = {}
    votes: list[HeldoutVote] = []
    for index in range(count):
        key = (f"person:{index}", f"bill:{index}")
        latent = rng.uniform(-3.0, 3.0)
        label = rng.random() < _sigmoid(latent)
        transformer[key] = _sigmoid(latent + rng.gauss(0.0, 1.2))
        llm[key] = _sigmoid(latent + rng.gauss(0.0, 1.2))
        votes.append(
            HeldoutVote(canonical_person_id=key[0], canonical_bill_id=key[1], is_yea=label)
        )
    return transformer, llm, votes


def _log_loss(probability: float, is_yea: bool) -> float:
    clamped = min(1 - 1e-15, max(1e-15, probability))
    return -(math.log(clamped) if is_yea else math.log(1 - clamped))


def test_stacked_forecaster_combines_members() -> None:
    transformer, llm, votes = _synthetic(1, count=10)
    members = [
        NamedForecaster(name="transformer", forecaster=_DictForecaster(transformer)),
        NamedForecaster(name="llm", forecaster=_DictForecaster(llm)),
    ]
    stacked = fit_stacked_forecaster(members, votes, epochs=50, learning_rate=0.1)
    probability = stacked.forecast(canonical_person_id="person:0", canonical_bill_id="bill:0")
    assert 0.0 <= probability <= 1.0
    assert set(stacked.stack.weights) == {"transformer", "llm"}


def test_stacked_forecaster_beats_each_member_on_holdout() -> None:
    fit_transformer, fit_llm, fit_votes = _synthetic(1, count=400)
    members = [
        NamedForecaster(name="transformer", forecaster=_DictForecaster(fit_transformer)),
        NamedForecaster(name="llm", forecaster=_DictForecaster(fit_llm)),
    ]
    stacked = fit_stacked_forecaster(members, fit_votes, epochs=300, learning_rate=0.1)

    eval_transformer, eval_llm, eval_votes = _synthetic(2, count=400)
    eval_members = {
        "transformer": _DictForecaster(eval_transformer),
        "llm": _DictForecaster(eval_llm),
    }
    stacked_for_eval = stacked.with_members(
        [
            NamedForecaster(name="transformer", forecaster=eval_members["transformer"]),
            NamedForecaster(name="llm", forecaster=eval_members["llm"]),
        ]
    )

    def mean_loss(probabilities: dict[tuple[str, str], float]) -> float:
        return sum(
            _log_loss(probabilities[(v.canonical_person_id, v.canonical_bill_id)], v.is_yea)
            for v in eval_votes
        ) / len(eval_votes)

    stacked_loss = sum(
        _log_loss(
            stacked_for_eval.forecast(
                canonical_person_id=v.canonical_person_id, canonical_bill_id=v.canonical_bill_id
            ),
            v.is_yea,
        )
        for v in eval_votes
    ) / len(eval_votes)

    assert stacked_loss < mean_loss(eval_transformer)
    assert stacked_loss < mean_loss(eval_llm)


def test_fit_requires_members_and_votes() -> None:
    raised = 0
    for members, votes in (
        ([], [HeldoutVote("p", "b", True)]),
        ([NamedForecaster("m", _DictForecaster({}))], []),
    ):
        try:
            fit_stacked_forecaster(members, votes, epochs=10, learning_rate=0.1)
        except ValueError:
            raised += 1
    assert raised == 2
