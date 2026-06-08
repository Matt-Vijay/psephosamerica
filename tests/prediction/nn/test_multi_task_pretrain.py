"""Tests for multi-task pretraining over the shared transformer trunk.

OVERALL_GOAL.md: training votes alongside cosponsorship / donor-receipt /
endorsements etc. off a shared representation forces the embedding to encode
the whole political object. This pins the integration: the auxiliary heads'
gradients actually flow into the shared transformer trunk (not just their own
readouts), and joint training reduces the total multi-task loss.
"""

from __future__ import annotations

import numpy as np

from src.prediction.nn.multi_task_pretrain import (
    MultiTaskExample,
    init_multi_task_model,
    multi_task_model_forward,
    train_multi_task_model,
)

_CONTRAST = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
_TASKS = ["votes", "endorsements"]


def _example(rng: np.random.Generator, *, tasks: list[str]) -> MultiTaskExample:
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    signal = float((politician @ _CONTRAST).sum())
    label = 1.0 if signal > 0.0 else 0.0
    return MultiTaskExample(
        politician_tokens=politician,
        context_tokens=context,
        targets={task: label for task in tasks},
    )


def test_forward_returns_probability_per_task() -> None:
    rng = np.random.default_rng(0)
    params = init_multi_task_model(
        num_layers=2, d_model=8, num_heads=2, d_hidden=16, task_names=_TASKS, rng=rng
    )
    probabilities, _pooled, _caches = multi_task_model_forward(
        np.zeros((2, 8)), np.zeros((3, 8)), params
    )
    assert set(probabilities) == set(_TASKS)
    assert all(0.0 <= value <= 1.0 for value in probabilities.values())


def test_auxiliary_task_gradient_shapes_the_shared_trunk() -> None:
    rng = np.random.default_rng(1)
    params = init_multi_task_model(
        num_layers=2, d_model=8, num_heads=2, d_hidden=16, task_names=_TASKS, rng=rng
    )
    probe_politician = rng.normal(size=(2, 8))
    probe_context = rng.normal(size=(3, 8))
    votes_before = multi_task_model_forward(probe_politician, probe_context, params)[0]["votes"]

    # Train on examples that ONLY carry the 'endorsements' (auxiliary) target.
    train_rng = np.random.default_rng(2)
    examples = [_example(train_rng, tasks=["endorsements"]) for _ in range(40)]
    trained, _history = train_multi_task_model(params, examples, epochs=40, learning_rate=0.1)

    # The votes head never received a gradient, yet its prediction moves --
    # because the auxiliary task reshaped the shared trunk it reads from.
    votes_after = multi_task_model_forward(probe_politician, probe_context, trained)[0]["votes"]
    assert abs(votes_after - votes_before) > 1e-4
    # The votes head weights themselves are untouched (only the trunk moved).
    assert np.allclose(trained.heads.weights["votes"], params.heads.weights["votes"])


def test_joint_training_reduces_total_loss() -> None:
    rng = np.random.default_rng(3)
    params = init_multi_task_model(
        num_layers=2, d_model=8, num_heads=2, d_hidden=16, task_names=_TASKS, rng=rng
    )
    train_rng = np.random.default_rng(4)
    examples = [_example(train_rng, tasks=_TASKS) for _ in range(60)]
    _trained, history = train_multi_task_model(params, examples, epochs=60, learning_rate=0.1)
    assert history[-1] < history[0]
