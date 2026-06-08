"""Tests for numpy LayerNorm and feed-forward sub-layers.

These are the non-attention halves of OVERALL_GOAL.md's transformer block.
Correctness is established by finite-difference gradient checks over every
input and parameter, so the layers train and compose with the attention block
into a gradient-checked transformer.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.layers import (
    feed_forward_backward,
    feed_forward_forward,
    init_feed_forward,
    init_layer_norm,
    layer_norm_backward,
    layer_norm_forward,
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


def test_layer_norm_normalizes_rows() -> None:
    rng = np.random.default_rng(0)
    params = init_layer_norm(dim=5)
    x = rng.normal(size=(4, 5)) * 3.0 + 1.0
    output, _cache = layer_norm_forward(x, params)
    # With unit gamma / zero beta, each row has ~zero mean and ~unit variance.
    assert np.allclose(output.mean(axis=-1), 0.0, atol=1e-9)
    assert np.allclose(output.std(axis=-1), 1.0, atol=1e-6)


def test_layer_norm_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_layer_norm(dim=6)
    params = params.__class__(gamma=rng.normal(size=6) * 0.5 + 1.0, beta=rng.normal(size=6) * 0.3)
    x = rng.normal(size=(3, 6))
    upstream = rng.normal(size=(3, 6))

    output, cache = layer_norm_forward(x, params)
    d_x, grads = layer_norm_backward(upstream, cache=cache)

    def loss() -> float:
        result, _ = layer_norm_forward(x, params)
        return float(np.sum(result * upstream))

    assert np.allclose(d_x, _numerical_gradient(loss, x), atol=1e-6)
    assert np.allclose(grads.d_gamma, _numerical_gradient(loss, params.gamma), atol=1e-6)
    assert np.allclose(grads.d_beta, _numerical_gradient(loss, params.beta), atol=1e-6)


def test_feed_forward_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(2)
    params = init_feed_forward(d_model=5, d_hidden=8, rng=rng)
    x = rng.normal(size=(4, 5))
    upstream = rng.normal(size=(4, 5))

    output, cache = feed_forward_forward(x, params)
    assert output.shape == (4, 5)
    d_x, grads = feed_forward_backward(upstream, cache=cache, params=params)

    def loss() -> float:
        result, _ = feed_forward_forward(x, params)
        return float(np.sum(result * upstream))

    assert np.allclose(d_x, _numerical_gradient(loss, x), atol=1e-6)
    assert np.allclose(grads.d_w_in, _numerical_gradient(loss, params.w_in), atol=1e-6)
    assert np.allclose(grads.d_b_in, _numerical_gradient(loss, params.b_in), atol=1e-6)
    assert np.allclose(grads.d_w_out, _numerical_gradient(loss, params.w_out), atol=1e-6)
    assert np.allclose(grads.d_b_out, _numerical_gradient(loss, params.b_out), atol=1e-6)
