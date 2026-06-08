"""Context-token encoder (numpy, forward + backward).

The third of OVERALL_GOAL.md's four token streams. Vote context -- election
cycle phase, leadership whip status, procedural vs substantive, news-cycle
salience, bill momentum -- is, as the blueprint notes, mostly categorical. This
encoder maps each categorical feature through a learnable embedding table and
each scalar feature through a learned projection vector, emitting one context
token per feature for the cross-attention transformer to attend over.

Pure numpy with an explicit analytic backward (gradient-checked): categorical
features accumulate gradient only on their selected embedding row; scalar
features accumulate on their projection vector. The feature values come from
federal vote/bill metadata (mocked from Track A's contract where a field is not
yet populated, as the blueprint permits).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ContextEncoderParams:
    """Embedding tables for categorical features, projection vectors for scalars."""

    d_model: int
    categorical_embeddings: dict[str, Array]
    scalar_weights: dict[str, Array]


@dataclass(frozen=True)
class ContextEncoderCache:
    categorical: Mapping[str, int]
    scalar: Mapping[str, float]
    categorical_features: list[str]
    scalar_features: list[str]


@dataclass(frozen=True)
class ContextEncoderGradients:
    d_categorical_embeddings: dict[str, Array]
    d_scalar_weights: dict[str, Array]


def init_context_encoder(
    *,
    d_model: int,
    categorical_vocabularies: Mapping[str, int],
    scalar_features: list[str],
    rng: np.random.Generator,
) -> ContextEncoderParams:
    """Initialize embedding tables (vocab x d_model) and scalar projection vectors."""
    scale = 1.0 / np.sqrt(d_model)
    categorical_embeddings = {
        name: rng.normal(size=(vocab, d_model)) * scale
        for name, vocab in categorical_vocabularies.items()
    }
    scalar_weights = {name: rng.normal(size=d_model) * scale for name in scalar_features}
    return ContextEncoderParams(
        d_model=d_model,
        categorical_embeddings=categorical_embeddings,
        scalar_weights=scalar_weights,
    )


def context_encoder_forward(
    *,
    categorical: Mapping[str, int],
    scalar: Mapping[str, float],
    params: ContextEncoderParams,
) -> tuple[Array, ContextEncoderCache]:
    """Emit one context token per feature (categorical first, then scalar, sorted)."""
    categorical_features = sorted(params.categorical_embeddings)
    scalar_features = sorted(params.scalar_weights)

    rows: list[Array] = []
    for name in categorical_features:
        table = params.categorical_embeddings[name]
        index = categorical[name]
        if index < 0 or index >= table.shape[0]:
            raise ValueError(f"category index {index} out of range for feature {name}")
        rows.append(table[index])
    for name in scalar_features:
        rows.append(scalar[name] * params.scalar_weights[name])

    tokens: Array = np.stack(rows, axis=0)
    cache = ContextEncoderCache(
        categorical=dict(categorical),
        scalar=dict(scalar),
        categorical_features=categorical_features,
        scalar_features=scalar_features,
    )
    return tokens, cache


def context_encoder_backward(
    d_tokens: Array,
    *,
    cache: ContextEncoderCache,
    params: ContextEncoderParams,
) -> ContextEncoderGradients:
    """Gradients for the selected embedding rows and the scalar projection vectors."""
    d_categorical = {
        name: np.zeros_like(table) for name, table in params.categorical_embeddings.items()
    }
    d_scalar = {name: np.zeros_like(weight) for name, weight in params.scalar_weights.items()}

    row = 0
    for name in cache.categorical_features:
        d_categorical[name][cache.categorical[name]] += d_tokens[row]
        row += 1
    for name in cache.scalar_features:
        d_scalar[name] = d_scalar[name] + cache.scalar[name] * d_tokens[row]
        row += 1

    return ContextEncoderGradients(
        d_categorical_embeddings=d_categorical,
        d_scalar_weights=d_scalar,
    )
