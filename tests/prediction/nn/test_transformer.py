"""Tests for the numpy cross-attention transformer block.

This is the core repeating unit of OVERALL_GOAL.md's architecture: a query
stream (e.g. politician tokens) cross-attends over a context stream (e.g. bill
/ past-vote / context tokens), with residual connections, LayerNorm, and a
feed-forward network. The whole block's backward is validated end-to-end by
finite-difference gradient checks over the inputs and a representative
parameter from each sub-module.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.transformer import (
    cross_attention_block_backward,
    cross_attention_block_forward,
    init_cross_attention_block,
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


def test_cross_attention_block_output_shape() -> None:
    rng = np.random.default_rng(0)
    params = init_cross_attention_block(d_model=8, num_heads=2, d_hidden=16, rng=rng)
    query = rng.normal(size=(3, 8))
    context = rng.normal(size=(5, 8))
    output, _cache = cross_attention_block_forward(query, context, params)
    assert output.shape == (3, 8)


def test_cross_attention_block_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_cross_attention_block(d_model=8, num_heads=2, d_hidden=16, rng=rng)
    query = rng.normal(size=(3, 8))
    context = rng.normal(size=(4, 8))
    upstream = rng.normal(size=(3, 8))

    output, cache = cross_attention_block_forward(query, context, params)
    grads = cross_attention_block_backward(upstream, cache=cache, params=params)

    def loss() -> float:
        result, _ = cross_attention_block_forward(query, context, params)
        return float(np.sum(result * upstream))

    assert np.allclose(grads.d_query, _numerical_gradient(loss, query), atol=1e-6)
    assert np.allclose(grads.d_context, _numerical_gradient(loss, context), atol=1e-6)
    # One representative parameter from each sub-module.
    assert np.allclose(
        grads.attention.d_w_query,
        _numerical_gradient(loss, params.attention.w_query),
        atol=1e-6,
    )
    assert np.allclose(
        grads.feed_forward.d_w_in,
        _numerical_gradient(loss, params.feed_forward.w_in),
        atol=1e-6,
    )
    assert np.allclose(
        grads.norm_attention.d_gamma,
        _numerical_gradient(loss, params.norm_attention.gamma),
        atol=1e-6,
    )
    assert np.allclose(
        grads.norm_feed_forward.d_beta,
        _numerical_gradient(loss, params.norm_feed_forward.beta),
        atol=1e-6,
    )
