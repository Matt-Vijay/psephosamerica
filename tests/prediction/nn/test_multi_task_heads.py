"""Tests for the multi-task pretraining heads.

OVERALL_GOAL.md: predict votes *and* cosponsorship, donor-receipt,
statement-stance valence, committee reassignment, bill passage, and
endorsements off a shared representation, so the embedding is forced to encode
the whole political object rather than one downstream label. The joint loss's
gradient w.r.t. the shared representation is the signal that flows into the
transformer trunk; it (and the per-head grads) are gradient-checked here.
"""

from __future__ import annotations


import numpy as np
import numpy.typing as npt

from src.prediction.nn.multi_task_heads import (
    init_multi_task_heads,
    multi_task_forward,
    multi_task_loss_and_grad,
)

Array = npt.NDArray[np.float64]

_TASKS = ("votes", "cosponsorship", "donor_receipt", "endorsements")


def _numerical_gradient(loss_fn: object, tensor: Array, *, epsilon: float = 1e-6) -> Array:
    grad = np.zeros_like(tensor)
    flat = tensor.reshape(-1)
    flat_grad = grad.reshape(-1)
    for index in range(flat.size):
        original = flat[index]
        flat[index] = original + epsilon
        plus = float(loss_fn())  # type: ignore[operator]
        flat[index] = original - epsilon
        minus = float(loss_fn())  # type: ignore[operator]
        flat[index] = original
        flat_grad[index] = (plus - minus) / (2.0 * epsilon)
    return grad


def test_forward_emits_a_probability_per_task() -> None:
    rng = np.random.default_rng(0)
    params = init_multi_task_heads(d_model=6, task_names=list(_TASKS), rng=rng)
    shared = rng.normal(size=6)
    probabilities = multi_task_forward(shared, params)
    assert set(probabilities) == set(_TASKS)
    assert all(0.0 <= value <= 1.0 for value in probabilities.values())


def test_joint_loss_gradients_match_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_multi_task_heads(d_model=6, task_names=list(_TASKS), rng=rng)
    shared = rng.normal(size=6)
    targets = {"votes": 1.0, "cosponsorship": 0.0, "donor_receipt": 1.0, "endorsements": 0.0}
    task_weights = {"votes": 1.0, "cosponsorship": 0.5, "donor_receipt": 0.5, "endorsements": 0.3}

    loss, d_shared, grads = multi_task_loss_and_grad(
        shared, params, targets=targets, task_weights=task_weights
    )

    def loss_fn() -> float:
        value, _, _ = multi_task_loss_and_grad(
            shared, params, targets=targets, task_weights=task_weights
        )
        return value

    assert np.allclose(d_shared, _numerical_gradient(loss_fn, shared), atol=1e-6)
    assert np.allclose(
        grads.d_weights["votes"], _numerical_gradient(loss_fn, params.weights["votes"]), atol=1e-6
    )

    # Finite-difference the bias by rebuilding params (it is an immutable float).
    def bias_loss(delta: float) -> float:
        biases = dict(params.biases)
        biases["donor_receipt"] += delta
        bumped = params.__class__(weights=params.weights, biases=biases)
        value, _, _ = multi_task_loss_and_grad(
            shared, bumped, targets=targets, task_weights=task_weights
        )
        return value

    numerical_bias = (bias_loss(1e-6) - bias_loss(-1e-6)) / (2e-6)
    assert np.isclose(grads.d_biases["donor_receipt"], numerical_bias, atol=1e-6)


def test_unweighted_tasks_default_to_equal_weight() -> None:
    rng = np.random.default_rng(2)
    params = init_multi_task_heads(d_model=4, task_names=["votes", "endorsements"], rng=rng)
    shared = rng.normal(size=4)
    targets = {"votes": 1.0, "endorsements": 1.0}
    loss, _d_shared, _grads = multi_task_loss_and_grad(shared, params, targets=targets)
    assert loss > 0.0


def test_loss_only_counts_tasks_with_targets() -> None:
    rng = np.random.default_rng(3)
    params = init_multi_task_heads(d_model=4, task_names=list(_TASKS), rng=rng)
    shared = rng.normal(size=4)
    # Only the votes target is observed for this example; auxiliary tasks absent.
    loss, d_shared, grads = multi_task_loss_and_grad(shared, params, targets={"votes": 1.0})
    # Heads without a target receive no gradient.
    assert np.allclose(grads.d_weights["cosponsorship"], 0.0)
    assert grads.d_biases["cosponsorship"] == 0.0
    assert not np.allclose(grads.d_weights["votes"], 0.0)


def test_training_step_reduces_joint_loss() -> None:
    rng = np.random.default_rng(4)
    params = init_multi_task_heads(d_model=5, task_names=list(_TASKS), rng=rng)
    shared = rng.normal(size=5)
    targets = {task: float(index % 2) for index, task in enumerate(_TASKS)}

    def loss_of(p: object) -> float:
        value, _, _ = multi_task_loss_and_grad(shared, p, targets=targets)  # type: ignore[arg-type]
        return value

    before = loss_of(params)
    _loss, _d_shared, grads = multi_task_loss_and_grad(shared, params, targets=targets)
    learning_rate = 0.5
    updated = params.__class__(
        weights={
            task: params.weights[task] - learning_rate * grads.d_weights[task] for task in _TASKS
        },
        biases={
            task: params.biases[task] - learning_rate * grads.d_biases[task] for task in _TASKS
        },
    )
    assert loss_of(updated) < before
