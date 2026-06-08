"""Multi-task pretraining heads over a shared representation (numpy, fwd + grad).

OVERALL_GOAL.md: predict votes *and* cosponsorship, donor-receipt,
statement-stance valence, committee reassignment, bill passage, and
endorsements off the same shared (pooled transformer) representation. Training
all heads jointly forces the shared embedding to encode the whole political
object instead of one downstream label.

Each head is a linear logistic readout of the shared vector. The joint loss is
a (task-weighted) sum of per-task binary cross-entropies over whichever tasks
have an observed target for this example -- so an example missing an auxiliary
label simply contributes nothing for that head. The returned gradient w.r.t.
the shared representation is the signal that flows back into the transformer
trunk; per-head gradients update the readouts. Auxiliary targets are mocked
from Track A's contract until the real feeds arrive, exactly as the blueprint
permits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

_PROBABILITY_FLOOR = 1e-12
_VOTES_TASK = "votes"


@dataclass(frozen=True)
class MultiTaskHeadsParams:
    """A linear logistic head per task over the shared representation."""

    weights: dict[str, Array]
    biases: dict[str, float]


@dataclass(frozen=True)
class MultiTaskHeadsGradients:
    """Per-head gradients of the joint loss."""

    d_weights: dict[str, Array]
    d_biases: dict[str, float]


def init_multi_task_heads(
    *,
    d_model: int,
    task_names: list[str],
    rng: np.random.Generator,
) -> MultiTaskHeadsParams:
    """Initialize one small linear head per task."""
    if not task_names:
        raise ValueError("task_names must not be empty")
    scale = 1.0 / np.sqrt(d_model)
    weights = {name: rng.normal(size=d_model) * scale for name in task_names}
    biases = {name: 0.0 for name in task_names}
    return MultiTaskHeadsParams(weights=weights, biases=biases)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return float(1.0 / (1.0 + np.exp(-value)))
    exponential = np.exp(value)
    return float(exponential / (1.0 + exponential))


def multi_task_forward(shared: Array, params: MultiTaskHeadsParams) -> dict[str, float]:
    """Probability for every task head given the shared representation."""
    return {
        name: _sigmoid(float(shared @ weight) + params.biases[name])
        for name, weight in params.weights.items()
    }


def multi_task_loss_and_grad(
    shared: Array,
    params: MultiTaskHeadsParams,
    *,
    targets: Mapping[str, float],
    task_weights: Mapping[str, float] | None = None,
) -> tuple[float, Array, MultiTaskHeadsGradients]:
    """Weighted joint BCE loss, plus gradients w.r.t. the shared vector and heads.

    Only tasks present in ``targets`` contribute to the loss and gradients;
    a head with no observed target this example gets a zero gradient.
    """
    loss = 0.0
    d_shared: Array = np.zeros_like(shared)
    d_weights = {name: np.zeros_like(weight) for name, weight in params.weights.items()}
    d_biases = {name: 0.0 for name in params.weights}

    for name, weight in params.weights.items():
        if name not in targets:
            continue
        weight_for_task = task_weights.get(name, 1.0) if task_weights is not None else 1.0
        probability = _sigmoid(float(shared @ weight) + params.biases[name])
        target = targets[name]
        clamped = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, probability))
        loss += weight_for_task * -(
            target * np.log(clamped) + (1.0 - target) * np.log(1.0 - clamped)
        )
        error = weight_for_task * (probability - target)
        d_shared = d_shared + error * weight
        d_weights[name] = error * shared
        d_biases[name] = error

    return float(loss), d_shared, MultiTaskHeadsGradients(d_weights=d_weights, d_biases=d_biases)
