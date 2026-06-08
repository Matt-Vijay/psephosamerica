"""Tests for the multi-layer cross-attention transformer stack.

Stacking the cross-attention block makes the model deep: the query (politician)
stream is refined layer by layer, each layer attending to the shared context
(bill / past-vote / context tokens). The whole stack's backward -- which must
accumulate the context gradient across every layer and flow the query gradient
back to layer 0 -- is validated by finite-difference gradient checks.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.transformer_stack import (
    init_transformer_stack,
    transformer_stack_backward,
    transformer_stack_forward,
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


def test_stack_has_requested_depth_and_output_shape() -> None:
    rng = np.random.default_rng(0)
    params = init_transformer_stack(num_layers=3, d_model=8, num_heads=2, d_hidden=16, rng=rng)
    assert len(params.layers) == 3
    query = rng.normal(size=(3, 8))
    context = rng.normal(size=(5, 8))
    output, _caches = transformer_stack_forward(query, context, params)
    assert output.shape == (3, 8)


def test_stack_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_transformer_stack(num_layers=2, d_model=8, num_heads=2, d_hidden=16, rng=rng)
    query = rng.normal(size=(3, 8))
    context = rng.normal(size=(4, 8))
    upstream = rng.normal(size=(3, 8))

    _output, caches = transformer_stack_forward(query, context, params)
    d_query, d_context, layer_grads = transformer_stack_backward(
        upstream, caches=caches, params=params
    )

    def loss() -> float:
        result, _ = transformer_stack_forward(query, context, params)
        return float(np.sum(result * upstream))

    assert np.allclose(d_query, _numerical_gradient(loss, query), atol=1e-6)
    assert np.allclose(d_context, _numerical_gradient(loss, context), atol=1e-6)
    # A parameter buried in the first layer must receive correct gradients
    # through the entire stack above it.
    assert np.allclose(
        layer_grads[0].attention.d_w_query,
        _numerical_gradient(loss, params.layers[0].attention.w_query),
        atol=1e-6,
    )
    assert np.allclose(
        layer_grads[1].feed_forward.d_w_out,
        _numerical_gradient(loss, params.layers[1].feed_forward.w_out),
        atol=1e-6,
    )
