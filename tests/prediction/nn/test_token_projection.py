"""Tests for the embedding -> token projection.

The four-stream transformer's politician and bill streams are each built from a
structural embedding and a dossier embedding, which live in their own
dimensions and must be projected into the model's token space. This pins that
projection (gradient-checked) and the construction of the two-token entity
block the model consumes.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.token_projection import (
    build_entity_tokens,
    init_projection,
    project,
    project_backward,
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


def test_project_maps_to_d_model() -> None:
    rng = np.random.default_rng(0)
    params = init_projection(input_dim=32, d_model=8, rng=rng)
    token, _cache = project(rng.normal(size=32), params)
    assert token.shape == (8,)


def test_project_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(1)
    params = init_projection(input_dim=5, d_model=4, rng=rng)
    embedding = rng.normal(size=5)
    upstream = rng.normal(size=4)

    token, cache = project(embedding, params)
    d_embedding, grads = project_backward(upstream, cache=cache, params=params)

    def loss() -> float:
        result, _ = project(embedding, params)
        return float(np.sum(result * upstream))

    assert np.allclose(d_embedding, _numerical_gradient(loss, embedding), atol=1e-6)
    assert np.allclose(grads.d_weight, _numerical_gradient(loss, params.weight), atol=1e-6)
    assert np.allclose(grads.d_bias, _numerical_gradient(loss, params.bias), atol=1e-6)


def test_build_entity_tokens_stacks_structural_then_dossier() -> None:
    rng = np.random.default_rng(2)
    structural_proj = init_projection(input_dim=6, d_model=4, rng=rng)
    dossier_proj = init_projection(input_dim=10, d_model=4, rng=rng)
    structural = rng.normal(size=6)
    dossier = rng.normal(size=10)

    tokens = build_entity_tokens(
        structural_embedding=structural,
        dossier_embedding=dossier,
        structural_projection=structural_proj,
        dossier_projection=dossier_proj,
    )
    assert tokens.shape == (2, 4)
    assert np.allclose(tokens[0], project(structural, structural_proj)[0])
    assert np.allclose(tokens[1], project(dossier, dossier_proj)[0])


def test_build_entity_tokens_supports_structural_only() -> None:
    rng = np.random.default_rng(3)
    structural_proj = init_projection(input_dim=6, d_model=4, rng=rng)
    structural = rng.normal(size=6)
    # With no dossier embedding (Track A not yet delivering), only the structural
    # token is produced -- the model still runs.
    tokens = build_entity_tokens(
        structural_embedding=structural,
        dossier_embedding=None,
        structural_projection=structural_proj,
        dossier_projection=None,
    )
    assert tokens.shape == (1, 4)
    assert np.allclose(tokens[0], project(structural, structural_proj)[0])
