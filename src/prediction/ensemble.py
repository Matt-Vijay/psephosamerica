"""LLM-forecaster ensemble member and held-out stacking.

OVERALL_GOAL.md calls for an independent LLM forecaster -- prompted with the
politician dossier, the bill, retrieved comparable votes, and relevant news --
that returns a probability and structured reasoning, stacked with the
cross-attention transformer (and the per-member model) on a held-out window.

This module owns:

* ``LlmForecast`` -- the typed output contract for the LLM member: a probability
  plus non-blank structured reasoning and the evidence it considered.
* ``Forecaster`` -- a Protocol for any ensemble member, so the actual frontier
  model call is injected at the edge and the stacking math stays offline and
  deterministic.
* ``StackedEnsemble`` / ``fit_stacked_ensemble`` -- logistic stacking that
  learns a weight per member (plus an intercept) on the *held-out* window, in
  member-logit space. Stacking on held-out forecasts -- not the training
  window -- is what keeps the combiner from simply trusting whichever member
  overfit; the caller is responsible (as elsewhere in this package) for passing
  held-out examples only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, Field, model_validator

_LOGIT_CLAMP = 40.0
_PROBABILITY_FLOOR = 1e-12
_DEFAULT_EPOCHS = 400
_DEFAULT_LEARNING_RATE = 0.3
_DEFAULT_L2 = 0.001


class LlmForecast(BaseModel, frozen=True):
    """An LLM ensemble member's structured output for one (person, bill) pair."""

    probability_yea: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    considered_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reasoning_is_non_blank(self) -> Self:
        if not self.reasoning.strip():
            raise ValueError("LLM forecast reasoning must not be blank")
        return self


@runtime_checkable
class Forecaster(Protocol):
    """Any ensemble member that can produce a yea probability for a pair."""

    def forecast(self, *, canonical_person_id: str, canonical_bill_id: str) -> float: ...


class EnsembleExample(BaseModel, frozen=True):
    """One held-out vote with each member's forecast and the realized label."""

    forecasts: Mapping[str, float]
    is_yea: bool


def _logit(probability: float) -> float:
    bounded = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, probability))
    return math.log(bounded / (1.0 - bounded))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, value))))


class StackedEnsemble(BaseModel):
    """A fitted logistic stack over ensemble members' forecasts."""

    member_names: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    intercept: float = 0.0

    def combine(self, forecasts: Mapping[str, float]) -> float:
        """Combine member forecasts into one stacked probability.

        Raises ``KeyError`` if any expected member's forecast is missing -- the
        ensemble must not silently drop a member at serving time.
        """
        raw = self.intercept
        for name in self.member_names:
            raw += self.weights[name] * _logit(forecasts[name])
        return _sigmoid(raw)


def fit_stacked_ensemble(
    examples: Iterable[EnsembleExample],
    *,
    member_names: list[str],
    epochs: int = _DEFAULT_EPOCHS,
    learning_rate: float = _DEFAULT_LEARNING_RATE,
    l2_penalty: float = _DEFAULT_L2,
) -> StackedEnsemble:
    """Fit logistic stacking weights on held-out member forecasts.

    Features are the members' forecast logits; the target is the realized vote.
    Full-batch gradient descent, deterministic, pure Python.
    """
    if not member_names:
        raise ValueError("member_names must not be empty")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0.0:
        raise ValueError("learning_rate must be positive")
    if l2_penalty < 0.0:
        raise ValueError("l2_penalty must be non-negative")

    materialized = list(examples)
    weights = {name: 0.0 for name in member_names}
    intercept = 0.0
    if not materialized:
        return StackedEnsemble(member_names=list(member_names), weights=weights, intercept=0.0)

    features = [
        ({name: _logit(example.forecasts[name]) for name in member_names}, example.is_yea)
        for example in materialized
    ]
    scale = 1.0 / len(features)
    for _ in range(epochs):
        intercept_gradient = 0.0
        weight_gradients = {name: 0.0 for name in member_names}
        for member_logits, is_yea in features:
            raw = intercept + sum(weights[name] * member_logits[name] for name in member_names)
            error = _sigmoid(raw) - (1.0 if is_yea else 0.0)
            intercept_gradient += error
            for name in member_names:
                weight_gradients[name] += error * member_logits[name]
        intercept -= learning_rate * intercept_gradient * scale
        for name in member_names:
            gradient = weight_gradients[name] * scale + l2_penalty * weights[name]
            weights[name] -= learning_rate * gradient

    return StackedEnsemble(member_names=list(member_names), weights=weights, intercept=intercept)
