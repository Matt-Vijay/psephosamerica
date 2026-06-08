"""Tests for the counterfactual generator.

OVERALL_GOAL.md: every prediction renders a "what would change this
prediction" counterfactual. This computes it for real -- ranking the feature
signals by how much the predicted probability moves when each is removed, and
describing the change -- over any model's scoring function.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pytest

from src.prediction.counterfactual import counterfactual_influences, describe_counterfactual


def _linear_scorer(coefficients: Mapping[str, float], intercept: float) -> object:
    def score(signals: Mapping[str, float]) -> float:
        raw = intercept + sum(coefficients[name] * signals.get(name, 0.0) for name in coefficients)
        return 1.0 / (1.0 + math.exp(-raw))

    return score


def test_influences_rank_by_probability_impact() -> None:
    score = _linear_scorer({"party": 3.0, "sector": 0.5, "noise": 0.0}, intercept=0.0)
    signals = {"party": 1.0, "sector": 1.0, "noise": 1.0}

    influences = counterfactual_influences(score, signals, top_k=3)  # type: ignore[arg-type]

    assert [item.signal_name for item in influences][0] == "party"
    # The zero-coefficient signal has no effect.
    noise = next(item for item in influences if item.signal_name == "noise")
    assert noise.probability_delta == pytest.approx(0.0, abs=1e-9)


def test_influence_delta_is_current_minus_without_signal() -> None:
    score = _linear_scorer({"party": 2.0}, intercept=0.0)
    signals = {"party": 1.0}
    current = score(signals)  # type: ignore[operator]
    without = score({"party": 0.0})  # type: ignore[operator]

    influences = counterfactual_influences(score, signals, top_k=1)  # type: ignore[arg-type]
    assert influences[0].probability_delta == pytest.approx(current - without)
    assert influences[0].direction == "increases"


def test_top_k_caps_the_returned_influences() -> None:
    score = _linear_scorer({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}, intercept=0.0)
    signals = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
    influences = counterfactual_influences(score, signals, top_k=2)  # type: ignore[arg-type]
    assert len(influences) == 2
    assert [item.signal_name for item in influences] == ["d", "c"]


def test_describe_counterfactual_mentions_the_strongest_driver() -> None:
    score = _linear_scorer({"party": 3.0, "sector": 0.5}, intercept=0.0)
    signals = {"party": 1.0, "sector": 1.0}
    influences = counterfactual_influences(score, signals, top_k=2)  # type: ignore[arg-type]
    text = describe_counterfactual(influences, current_probability=score(signals))  # type: ignore[operator]
    assert "party" in text
    assert "%" in text


def test_describe_counterfactual_handles_no_influences() -> None:
    text = describe_counterfactual([], current_probability=0.5)
    assert text


def test_empty_signals_yield_no_influences() -> None:
    score = _linear_scorer({"party": 2.0}, intercept=0.0)
    assert counterfactual_influences(score, {}, top_k=3) == []  # type: ignore[arg-type]
