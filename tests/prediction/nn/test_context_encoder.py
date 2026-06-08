"""Tests for the context-token encoder.

The third of OVERALL_GOAL.md's four token streams: context tokens (election
cycle, whip status, procedural vs substantive, news salience, bill momentum).
The blueprint notes these are "mostly categorical" -- here categorical features
become learnable embeddings and scalar features become learned projections,
emitting one context token per feature. Forward and backward are gradient-
checked over the embedding rows and the scalar projections.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.context_encoder import (
    context_encoder_backward,
    context_encoder_forward,
    init_context_encoder,
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


def _params(rng: np.random.Generator) -> object:
    return init_context_encoder(
        d_model=6,
        categorical_vocabularies={"whip_status": 3, "procedural": 2},
        scalar_features=["news_salience", "bill_momentum"],
        rng=rng,
    )


def test_forward_emits_one_token_per_feature() -> None:
    rng = np.random.default_rng(0)
    params = _params(rng)
    tokens, _cache = context_encoder_forward(
        categorical={"whip_status": 1, "procedural": 0},
        scalar={"news_salience": 0.8, "bill_momentum": 0.2},
        params=params,  # type: ignore[arg-type]
    )
    # Two categorical + two scalar features -> four tokens of width d_model.
    assert tokens.shape == (4, 6)


def test_categorical_token_is_the_selected_embedding_row() -> None:
    rng = np.random.default_rng(1)
    params = _params(rng)
    tokens, _cache = context_encoder_forward(
        categorical={"whip_status": 2, "procedural": 1},
        scalar={"news_salience": 0.0, "bill_momentum": 0.0},
        params=params,  # type: ignore[arg-type]
    )
    # Features are emitted in sorted order: procedural, whip_status, then scalars.
    assert np.allclose(tokens[0], params.categorical_embeddings["procedural"][1])  # type: ignore[attr-defined]
    assert np.allclose(tokens[1], params.categorical_embeddings["whip_status"][2])  # type: ignore[attr-defined]


def test_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(2)
    params = _params(rng)
    categorical = {"whip_status": 1, "procedural": 0}
    scalar = {"news_salience": 0.7, "bill_momentum": 0.3}
    upstream = rng.normal(size=(4, 6))

    tokens, cache = context_encoder_forward(
        categorical=categorical,
        scalar=scalar,
        params=params,  # type: ignore[arg-type]
    )
    grads = context_encoder_backward(upstream, cache=cache, params=params)  # type: ignore[arg-type]

    def loss() -> float:
        result, _ = context_encoder_forward(
            categorical=categorical,
            scalar=scalar,
            params=params,  # type: ignore[arg-type]
        )
        return float(np.sum(result * upstream))

    # Scalar projection gradients.
    assert np.allclose(
        grads.d_scalar_weights["news_salience"],
        _numerical_gradient(loss, params.scalar_weights["news_salience"]),  # type: ignore[attr-defined]
        atol=1e-6,
    )
    # Categorical embedding-table gradient (only the selected row is nonzero).
    numerical_whip = _numerical_gradient(loss, params.categorical_embeddings["whip_status"])  # type: ignore[attr-defined]
    assert np.allclose(grads.d_categorical_embeddings["whip_status"], numerical_whip, atol=1e-6)
    assert np.allclose(grads.d_categorical_embeddings["whip_status"][0], 0.0)  # unselected row
    assert not np.allclose(grads.d_categorical_embeddings["whip_status"][1], 0.0)  # selected row


def test_unknown_category_index_raises() -> None:
    rng = np.random.default_rng(3)
    params = _params(rng)
    raised = False
    try:
        context_encoder_forward(
            categorical={"whip_status": 9, "procedural": 0},  # 9 out of vocab range
            scalar={"news_salience": 0.0, "bill_momentum": 0.0},
            params=params,  # type: ignore[arg-type]
        )
    except (IndexError, ValueError):
        raised = True
    assert raised
