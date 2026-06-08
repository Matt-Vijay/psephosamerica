"""Tests for the LLM-forecaster ensemble member and held-out stacking.

OVERALL_GOAL.md: an independent LLM forecaster (prompted with dossier + bill +
retrieved comparable votes + news) returns a probability and structured
reasoning, and is stacked with the transformer on a held-out window. This
module owns the forecast contract and the stacking combiner; the actual LLM
call is injected behind a Protocol so the math stays testable and offline.
"""

from __future__ import annotations

import math
import random

import pytest
from pydantic import ValidationError

from src.prediction.ensemble import (
    EnsembleExample,
    LlmForecast,
    fit_stacked_ensemble,
)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _stacking_examples(
    seed: int, *, count: int = 4000, members: tuple[str, ...] = ("transformer", "llm")
) -> list[EnsembleExample]:
    """Two complementary members, each a noisy view of the same latent logit."""
    rng = random.Random(seed)
    examples: list[EnsembleExample] = []
    for _ in range(count):
        latent = rng.uniform(-3.0, 3.0)
        label = rng.random() < _sigmoid(latent)
        forecasts = {
            members[0]: _sigmoid(latent + rng.gauss(0.0, 1.2)),
            members[1]: _sigmoid(latent + rng.gauss(0.0, 1.2)),
        }
        examples.append(EnsembleExample(forecasts=forecasts, is_yea=label))
    return examples


def _log_loss(probability: float, is_yea: bool) -> float:
    clamped = min(1 - 1e-15, max(1e-15, probability))
    return -(math.log(clamped) if is_yea else math.log(1 - clamped))


def _mean_member_log_loss(examples: list[EnsembleExample], member: str) -> float:
    return sum(_log_loss(example.forecasts[member], example.is_yea) for example in examples) / len(
        examples
    )


def _mean_stacked_log_loss(ensemble: object, examples: list[EnsembleExample]) -> float:
    combine = ensemble.combine  # type: ignore[attr-defined]
    return sum(_log_loss(combine(example.forecasts), example.is_yea) for example in examples) / len(
        examples
    )


def test_llm_forecast_requires_structured_reasoning() -> None:
    forecast = LlmForecast(
        probability_yea=0.7,
        reasoning="Member votes with party on energy and is funded by the sector.",
        considered_evidence=["2024 energy vote", "sector PAC receipts"],
    )
    assert forecast.probability_yea == 0.7
    with pytest.raises(ValidationError):
        LlmForecast(probability_yea=0.7, reasoning="   ", considered_evidence=[])


def test_llm_forecast_rejects_out_of_range_probability() -> None:
    with pytest.raises(ValidationError):
        LlmForecast(probability_yea=1.4, reasoning="x", considered_evidence=[])


def test_stacked_ensemble_beats_each_member_on_holdout() -> None:
    members = ("transformer", "llm")
    fit_examples = _stacking_examples(seed=1, members=members)
    holdout = _stacking_examples(seed=2, members=members)

    ensemble = fit_stacked_ensemble(fit_examples, member_names=list(members))

    stacked = _mean_stacked_log_loss(ensemble, holdout)
    assert stacked < _mean_member_log_loss(holdout, "transformer")
    assert stacked < _mean_member_log_loss(holdout, "llm")


def test_stacked_ensemble_learns_positive_weight_for_each_informative_member() -> None:
    members = ("transformer", "llm")
    ensemble = fit_stacked_ensemble(
        _stacking_examples(seed=3, members=members), member_names=list(members)
    )
    assert ensemble.weights["transformer"] > 0.0
    assert ensemble.weights["llm"] > 0.0


def test_stacked_ensemble_downweights_a_noise_member() -> None:
    rng = random.Random(7)
    examples: list[EnsembleExample] = []
    for _ in range(4000):
        latent = rng.uniform(-3.0, 3.0)
        label = rng.random() < _sigmoid(latent)
        examples.append(
            EnsembleExample(
                forecasts={
                    "signal": _sigmoid(latent + rng.gauss(0.0, 0.5)),
                    "noise": rng.random(),  # pure noise, uninformative
                },
                is_yea=label,
            )
        )
    ensemble = fit_stacked_ensemble(examples, member_names=["signal", "noise"])
    assert ensemble.weights["signal"] > ensemble.weights["noise"]
    assert ensemble.weights["signal"] > 0.5


def test_combine_requires_all_member_forecasts() -> None:
    ensemble = fit_stacked_ensemble(_stacking_examples(seed=4), member_names=["transformer", "llm"])
    with pytest.raises(KeyError):
        ensemble.combine({"transformer": 0.6})


def test_single_member_stack_is_a_recalibration() -> None:
    members = ("only",)
    fit_examples = _stacking_examples(seed=5, members=("only", "ignored"))
    # Keep just the single member's forecast.
    single = [
        EnsembleExample(forecasts={"only": example.forecasts["only"]}, is_yea=example.is_yea)
        for example in fit_examples
    ]
    ensemble = fit_stacked_ensemble(single, member_names=list(members))
    holdout_examples = _stacking_examples(seed=6, members=("only", "ignored"))
    holdout = [
        EnsembleExample(forecasts={"only": example.forecasts["only"]}, is_yea=example.is_yea)
        for example in holdout_examples
    ]
    stacked = _mean_stacked_log_loss(ensemble, holdout)
    member = _mean_member_log_loss(holdout, "only")
    # Recalibration never meaningfully worse than the raw member.
    assert stacked <= member + 0.02
