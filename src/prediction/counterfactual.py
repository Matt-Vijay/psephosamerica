"""Counterfactual generation -- the "what would change this prediction" view.

OVERALL_GOAL.md requires every served prediction to carry a
"what-would-change-this" counterfactual. This computes it for real over any
model's scoring function: for each feature signal, it measures how far the
predicted yea probability moves when that signal is removed (set to zero), then
ranks the signals by impact and renders a human-readable summary.

It is model-agnostic -- the scorer is any callable from a signal mapping to a
probability, so it works for the per-member model, the transformer, or a
stacked ensemble. The output both fills ``ServedPrediction.counterfactual`` and
backs the top-evidence ordering.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel

ScoreFn = Callable[[Mapping[str, float]], float]
InfluenceDirection = Literal["increases", "decreases", "neutral"]


class SignalInfluence(BaseModel, frozen=True):
    """How one signal moves the prediction, measured by removing it."""

    signal_name: str
    current_value: float
    probability_delta: float
    direction: InfluenceDirection


def _direction(delta: float) -> InfluenceDirection:
    if delta > 1e-12:
        return "increases"
    if delta < -1e-12:
        return "decreases"
    return "neutral"


def counterfactual_influences(
    score_fn: ScoreFn,
    signals: Mapping[str, float],
    *,
    top_k: int,
) -> list[SignalInfluence]:
    """Rank signals by how much removing each moves the predicted probability.

    ``probability_delta`` is ``current - probability_without_signal``: positive
    means the signal pushes the prediction toward yea. Returns the ``top_k``
    most influential signals by absolute impact.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if not signals:
        return []
    current = score_fn(signals)
    influences: list[SignalInfluence] = []
    for name, value in signals.items():
        without = dict(signals)
        without[name] = 0.0
        delta = current - score_fn(without)
        influences.append(
            SignalInfluence(
                signal_name=name,
                current_value=value,
                probability_delta=delta,
                direction=_direction(delta),
            )
        )
    influences.sort(key=lambda item: abs(item.probability_delta), reverse=True)
    return influences[:top_k]


def describe_counterfactual(
    influences: list[SignalInfluence],
    *,
    current_probability: float,
) -> str:
    """Render the human-readable "what would change this prediction" summary."""
    if not influences:
        return (
            "No single tracked signal materially changes this prediction; it rests on "
            "the model's baseline expectation."
        )
    strongest = influences[0]
    without_probability = current_probability - strongest.probability_delta
    verb = "drop" if strongest.probability_delta > 0 else "rise"
    return (
        f"Removing '{strongest.signal_name}' would {verb} the yea probability from "
        f"{current_probability * 100:.0f}% to {without_probability * 100:.0f}% -- "
        f"it is the strongest driver of this prediction."
    )
