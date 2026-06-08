"""Tests for numpy multi-head attention with learnable projections.

This stacks scaled dot-product attention into the building block of
OVERALL_GOAL.md's cross-attention transformer: learnable Q/K/V/O projections,
heads split across the model dimension, cross-attention shaped. Correctness is
established by finite-difference gradient checks over every input and every
weight matrix, so the block trains and matches a torch ``MultiheadAttention``
contract for a later swap.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.multihead import (
    init_multi_head_attention,
    multi_head_attention_backward,
    multi_head_attention_forward,
)

Array = npt.NDArray[np.float64]


def test_multi_head_attention_output_shape() -> None:
    rng = np.random.default_rng(0)
    params = init_multi_head_attention(d_model=8, num_heads=2, rng=rng)
    query = rng.normal(size=(3, 8))
    key = rng.normal(size=(5, 8))
    value = rng.normal(size=(5, 8))
    output, _cache = multi_head_attention_forward(query, key, value, params)
    assert output.shape == (3, 8)


def _numerical_gradient(
    loss_fn: object,
    tensor: Array,
    *,
    epsilon: float = 1e-6,
) -> Array:
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


def test_multi_head_attention_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_multi_head_attention(d_model=8, num_heads=2, rng=rng)
    query = rng.normal(size=(3, 8))
    key = rng.normal(size=(4, 8))
    value = rng.normal(size=(4, 8))
    upstream = rng.normal(size=(3, 8))

    output, cache = multi_head_attention_forward(query, key, value, params)
    grads = multi_head_attention_backward(upstream, cache=cache, params=params)

    def loss() -> float:
        result, _ = multi_head_attention_forward(query, key, value, params)
        return float(np.sum(result * upstream))

    assert np.allclose(grads.d_query, _numerical_gradient(loss, query), atol=1e-6)
    assert np.allclose(grads.d_key, _numerical_gradient(loss, key), atol=1e-6)
    assert np.allclose(grads.d_value, _numerical_gradient(loss, value), atol=1e-6)
    assert np.allclose(grads.d_w_query, _numerical_gradient(loss, params.w_query), atol=1e-6)
    assert np.allclose(grads.d_w_key, _numerical_gradient(loss, params.w_key), atol=1e-6)
    assert np.allclose(grads.d_w_value, _numerical_gradient(loss, params.w_value), atol=1e-6)
    assert np.allclose(grads.d_w_output, _numerical_gradient(loss, params.w_output), atol=1e-6)


def test_init_rejects_indivisible_head_count() -> None:
    rng = np.random.default_rng(2)
    raised = False
    try:
        init_multi_head_attention(d_model=8, num_heads=3, rng=rng)
    except ValueError:
        raised = True
    assert raised


def test_single_head_matches_plain_attention() -> None:
    rng = np.random.default_rng(3)
    params = init_multi_head_attention(d_model=6, num_heads=1, rng=rng)
    query = rng.normal(size=(2, 6))
    key = rng.normal(size=(4, 6))
    value = rng.normal(size=(4, 6))
    output, _cache = multi_head_attention_forward(query, key, value, params)
    # With one head, the block is q@Wq attending over k@Wk / v@Wv, then @Wo.
    from src.prediction.nn.attention import scaled_dot_product_attention

    projected, _ = scaled_dot_product_attention(
        query @ params.w_query, key @ params.w_key, value @ params.w_value
    )
    assert np.allclose(output, projected @ params.w_output)
