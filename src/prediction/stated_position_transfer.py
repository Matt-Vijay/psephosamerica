"""Stated-position-to-vote-stance transfer head.

OVERALL_GOAL.md's thin-record mechanism #2: a brand-new official may have no
votes but plenty of *declared* positions -- campaign questionnaire answers,
debate statements, scored advocacy positions. This head learns the map from
those declared positions to realized vote stance on officials who have both,
then applies it to voteless officials from their declarations alone.

It is a plain logistic regression over declared-position features (each a
signed valence per issue), trained full-batch in pure Python. No-leakage: the
declared positions used for an example must predate that vote -- enforced by
the caller, as everywhere else in this package -- so the transfer never learns
from a statement made after the vote it explains.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_DEFAULT_EPOCHS = 300
_DEFAULT_LEARNING_RATE = 0.3
_DEFAULT_L2 = 0.01
_LOGIT_CLAMP = 40.0


@dataclass(frozen=True)
class StatedPositionExample:
    """A declared-position vector paired with the official's realized vote."""

    declared_positions: Mapping[str, float]
    is_yea: bool


@dataclass(frozen=True)
class StatedPositionTransferModel:
    """A fitted logistic map from declared positions to vote stance."""

    coefficients: dict[str, float]
    intercept: float


def _sigmoid(value: float) -> float:
    bounded = max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def predict_stance(
    model: StatedPositionTransferModel,
    declared_positions: Mapping[str, float],
) -> float:
    """Predicted yea probability from declared positions; unseen issues are ignored."""
    raw = model.intercept + sum(
        coefficient * declared_positions.get(issue, 0.0)
        for issue, coefficient in model.coefficients.items()
    )
    return _sigmoid(raw)


def train_stated_position_transfer(
    examples: Iterable[StatedPositionExample],
    *,
    epochs: int = _DEFAULT_EPOCHS,
    learning_rate: float = _DEFAULT_LEARNING_RATE,
    l2_penalty: float = _DEFAULT_L2,
) -> StatedPositionTransferModel:
    """Fit the transfer head with deterministic full-batch logistic gradient descent."""
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0.0:
        raise ValueError("learning_rate must be positive")
    if l2_penalty < 0.0:
        raise ValueError("l2_penalty must be non-negative")

    materialized = list(examples)
    issues = sorted({issue for example in materialized for issue in example.declared_positions})
    if not materialized or not issues:
        return StatedPositionTransferModel(
            coefficients={issue: 0.0 for issue in issues}, intercept=0.0
        )

    coefficients = {issue: 0.0 for issue in issues}
    intercept = 0.0
    scale = 1.0 / len(materialized)
    for _ in range(epochs):
        intercept_gradient = 0.0
        coefficient_gradients = {issue: 0.0 for issue in issues}
        for example in materialized:
            raw = intercept + sum(
                coefficients[issue] * example.declared_positions.get(issue, 0.0) for issue in issues
            )
            error = _sigmoid(raw) - (1.0 if example.is_yea else 0.0)
            intercept_gradient += error
            for issue in issues:
                coefficient_gradients[issue] += error * example.declared_positions.get(issue, 0.0)
        intercept -= learning_rate * intercept_gradient * scale
        for issue in issues:
            gradient = coefficient_gradients[issue] * scale + l2_penalty * coefficients[issue]
            coefficients[issue] -= learning_rate * gradient

    return StatedPositionTransferModel(coefficients=coefficients, intercept=intercept)
