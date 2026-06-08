"""Tests for the assembled cross-attention vote transformer.

This is OVERALL_GOAL.md's prediction model in numpy form: a politician token
stream cross-attends over a bill / context / past-vote stream, the result is
pooled and passed through a yea-probability head. The head/pooling backward is
gradient-checked (the block backward is checked in its own module), and an
integration test confirms the whole stack trains -- loss falls and accuracy
rises on a separable task.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.vote_transformer import (
    VoteTransformerExample,
    init_vote_transformer,
    train_vote_transformer,
    vote_transformer_backward,
    vote_transformer_forward,
    vote_transformer_logit,
)

Array = npt.NDArray[np.float64]


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


def test_vote_transformer_forward_returns_probability() -> None:
    rng = np.random.default_rng(0)
    params = init_vote_transformer(d_model=8, num_heads=2, d_hidden=16, rng=rng)
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(4, 8))
    probability, _cache = vote_transformer_forward(politician, context, params)
    assert 0.0 <= probability <= 1.0


def test_vote_transformer_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_vote_transformer(d_model=8, num_heads=2, d_hidden=16, rng=rng)
    politician = rng.normal(size=(3, 8))
    context = rng.normal(size=(4, 8))

    _probability, cache = vote_transformer_forward(politician, context, params)
    grads = vote_transformer_backward(1.0, cache=cache, params=params)

    # Use the raw logit as the scalar so d_logit = 1.0.
    def loss() -> float:
        return vote_transformer_logit(politician, context, params)

    assert np.allclose(grads.d_politician, _numerical_gradient(loss, politician), atol=1e-6)
    assert np.allclose(grads.d_context, _numerical_gradient(loss, context), atol=1e-6)
    assert np.allclose(grads.d_w_head, _numerical_gradient(loss, params.w_head), atol=1e-6)
    # loss == logit, so d(loss)/d(b_head) is exactly 1.
    assert np.isclose(grads.d_b_head, 1.0, atol=1e-9)


# A zero-sum direction: a contrast over features that survives LayerNorm's
# per-row mean removal (so the signal actually reaches the head).
_CONTRAST = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])


def _separable_dataset(seed: int, *, count: int = 60) -> list[VoteTransformerExample]:
    """Label is the sign of the politician stream's projection on a fixed contrast."""
    rng = np.random.default_rng(seed)
    examples: list[VoteTransformerExample] = []
    for _ in range(count):
        politician = rng.normal(size=(2, 8))
        context = rng.normal(size=(3, 8))
        is_yea = bool((politician @ _CONTRAST).sum() > 0.0)
        examples.append(
            VoteTransformerExample(
                politician_tokens=politician, context_tokens=context, is_yea=is_yea
            )
        )
    return examples


def test_vote_transformer_learns_a_separable_task() -> None:
    train = _separable_dataset(seed=2, count=80)
    rng = np.random.default_rng(3)
    params = init_vote_transformer(d_model=8, num_heads=2, d_hidden=16, rng=rng)

    trained, history = train_vote_transformer(params, train, epochs=300, learning_rate=0.1)

    assert history[-1] < history[0]  # training loss fell

    correct = 0
    for example in train:
        probability, _ = vote_transformer_forward(
            example.politician_tokens, example.context_tokens, trained
        )
        if (probability >= 0.5) == example.is_yea:
            correct += 1
    assert correct / len(train) >= 0.8
