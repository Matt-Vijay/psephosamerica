"""Tests for the numpy scaled dot-product attention primitive.

This is the core operation of OVERALL_GOAL.md's cross-attention transformer:
a query stream attends over a separate key/value stream (politician tokens
attending over bill tokens, retrieved-past-vote tokens, etc.). Correctness is
established by finite-difference gradient checks -- the analytic backward must
match numerical gradients to float64 precision -- so the block can be trained
and, later, swapped for a torch implementation with the same contract.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.attention import (
    scaled_dot_product_attention,
    scaled_dot_product_attention_backward,
    softmax,
)


def test_softmax_rows_sum_to_one() -> None:
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(4, 6))
    probabilities = softmax(logits, axis=-1)
    assert np.allclose(probabilities.sum(axis=-1), 1.0)
    assert np.all(probabilities >= 0.0)


def test_softmax_is_shift_invariant() -> None:
    logits = np.array([[1.0, 2.0, 3.0]])
    shifted = logits + 100.0
    assert np.allclose(softmax(logits, axis=-1), softmax(shifted, axis=-1))


def test_attention_output_is_convex_combination_of_values() -> None:
    rng = np.random.default_rng(1)
    query = rng.normal(size=(3, 5))
    key = rng.normal(size=(4, 5))
    value = rng.normal(size=(4, 7))
    output, weights = scaled_dot_product_attention(query, key, value)
    assert output.shape == (3, 7)
    assert weights.shape == (3, 4)
    assert np.allclose(weights.sum(axis=-1), 1.0)
    # Each output row lies inside the convex hull of the value rows.
    assert np.all(output.max(axis=0) <= value.max(axis=0) + 1e-9)
    assert np.all(output.min(axis=0) >= value.min(axis=0) - 1e-9)


def _numerical_gradient(
    function: object,
    tensor: npt.NDArray[np.float64],
    upstream: npt.NDArray[np.float64],
    *,
    epsilon: float = 1e-6,
) -> npt.NDArray[np.float64]:
    grad = np.zeros_like(tensor)
    flat = tensor.reshape(-1)
    flat_grad = grad.reshape(-1)
    for index in range(flat.size):
        original = flat[index]
        flat[index] = original + epsilon
        plus = float(np.sum(function() * upstream))  # type: ignore[operator]
        flat[index] = original - epsilon
        minus = float(np.sum(function() * upstream))  # type: ignore[operator]
        flat[index] = original
        flat_grad[index] = (plus - minus) / (2.0 * epsilon)
    return grad


def test_attention_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(2)
    query = rng.normal(size=(3, 4))
    key = rng.normal(size=(5, 4))
    value = rng.normal(size=(5, 6))
    upstream = rng.normal(size=(3, 6))

    output, weights = scaled_dot_product_attention(query, key, value)
    d_query, d_key, d_value = scaled_dot_product_attention_backward(
        upstream, query=query, key=key, value=value, weights=weights
    )

    numerical_query = _numerical_gradient(
        lambda: scaled_dot_product_attention(query, key, value)[0], query, upstream
    )
    numerical_key = _numerical_gradient(
        lambda: scaled_dot_product_attention(query, key, value)[0], key, upstream
    )
    numerical_value = _numerical_gradient(
        lambda: scaled_dot_product_attention(query, key, value)[0], value, upstream
    )

    assert np.allclose(d_query, numerical_query, atol=1e-6)
    assert np.allclose(d_key, numerical_key, atol=1e-6)
    assert np.allclose(d_value, numerical_value, atol=1e-6)
