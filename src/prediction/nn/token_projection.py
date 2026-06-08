"""Embedding -> token projection (numpy, forward + backward).

The four-stream transformer's politician and bill streams are each built from a
structural embedding (``structural_embeddings.py``) and -- when Track A
delivers it -- a dossier embedding, which live in their own dimensions. This
projects an embedding of any width into the model's ``d_model`` token space
with a learned affine map, and assembles the two-token entity block (structural
token, dossier token) the four-stream model consumes.

Pure numpy with an analytic, gradient-checked backward. ``build_entity_tokens``
gracefully emits a single structural token when no dossier embedding is
available yet, so the model runs on federal data today and gains the dossier
token row unchanged once Track A's embeddings arrive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ProjectionParams:
    """A learned affine map ``embedding @ weight + bias`` of width ``d_model``."""

    weight: Array
    bias: Array


@dataclass(frozen=True)
class ProjectionCache:
    embedding: Array


@dataclass(frozen=True)
class ProjectionGradients:
    d_weight: Array
    d_bias: Array


def init_projection(*, input_dim: int, d_model: int, rng: np.random.Generator) -> ProjectionParams:
    """Initialize the projection with 1/sqrt(input_dim) scaling and zero bias."""
    scale = 1.0 / np.sqrt(input_dim)
    return ProjectionParams(
        weight=rng.normal(size=(input_dim, d_model)) * scale,
        bias=np.zeros(d_model),
    )


def project(embedding: Array, params: ProjectionParams) -> tuple[Array, ProjectionCache]:
    """Project one embedding vector into ``d_model`` token space."""
    token: Array = embedding @ params.weight + params.bias
    return token, ProjectionCache(embedding=embedding)


def project_backward(
    d_token: Array,
    *,
    cache: ProjectionCache,
    params: ProjectionParams,
) -> tuple[Array, ProjectionGradients]:
    """Gradients w.r.t. the embedding and the projection parameters."""
    d_weight: Array = np.outer(cache.embedding, d_token)
    d_bias: Array = d_token
    d_embedding: Array = params.weight @ d_token
    return d_embedding, ProjectionGradients(d_weight=d_weight, d_bias=d_bias)


def build_entity_tokens(
    *,
    structural_embedding: Array,
    dossier_embedding: Array | None,
    structural_projection: ProjectionParams,
    dossier_projection: ProjectionParams | None,
) -> Array:
    """Assemble the (structural, dossier) token block for a politician or bill.

    Emits one row per available embedding: the structural token always, and the
    dossier token when both the embedding and its projection are present.
    """
    rows: list[Array] = [project(structural_embedding, structural_projection)[0]]
    if dossier_embedding is not None and dossier_projection is not None:
        rows.append(project(dossier_embedding, dossier_projection)[0])
    tokens: Array = np.stack(rows, axis=0)
    return tokens
