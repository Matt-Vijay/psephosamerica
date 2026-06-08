"""Multi-head attention with learnable projections (numpy, forward + backward).

Stacks :func:`scaled_dot_product_attention` into the building block of
OVERALL_GOAL.md's cross-attention transformer: learnable query/key/value/output
projections, the model dimension split across heads, cross-attention shaped
(the query stream may differ in length from the key/value stream). Pure numpy
with an explicit analytic backward (gradient-checked), so the block trains by
gradient descent today and can be swapped for a torch ``MultiheadAttention``
with the same contract once Track A emits real dossier embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.attention import (
    scaled_dot_product_attention,
    scaled_dot_product_attention_backward,
)

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class MultiHeadAttentionParams:
    """Learnable projection matrices; each is ``(d_model, d_model)``."""

    w_query: Array
    w_key: Array
    w_value: Array
    w_output: Array
    num_heads: int

    def __post_init__(self) -> None:
        d_model = self.w_query.shape[0]
        for matrix in (self.w_query, self.w_key, self.w_value, self.w_output):
            if matrix.shape != (d_model, d_model):
                raise ValueError("all projection matrices must be (d_model, d_model)")
        if self.num_heads <= 0 or d_model % self.num_heads != 0:
            raise ValueError("num_heads must be positive and divide d_model")

    @property
    def d_model(self) -> int:
        return int(self.w_query.shape[0])

    @property
    def d_head(self) -> int:
        return self.d_model // self.num_heads


@dataclass(frozen=True)
class MultiHeadAttentionCache:
    """Forward-pass intermediates needed by the backward pass."""

    query: Array
    key: Array
    value: Array
    projected_query: Array
    projected_key: Array
    projected_value: Array
    head_weights: list[Array]
    attention_concat: Array


@dataclass(frozen=True)
class MultiHeadAttentionGradients:
    """Gradients w.r.t. the inputs and each projection matrix."""

    d_query: Array
    d_key: Array
    d_value: Array
    d_w_query: Array
    d_w_key: Array
    d_w_value: Array
    d_w_output: Array


def init_multi_head_attention(
    *,
    d_model: int,
    num_heads: int,
    rng: np.random.Generator,
) -> MultiHeadAttentionParams:
    """Initialize projections with small Gaussian weights (scaled by 1/sqrt(d_model))."""
    if num_heads <= 0 or d_model % num_heads != 0:
        raise ValueError("num_heads must be positive and divide d_model")
    scale = 1.0 / np.sqrt(d_model)

    def projection() -> Array:
        matrix: Array = rng.normal(size=(d_model, d_model)) * scale
        return matrix

    return MultiHeadAttentionParams(
        w_query=projection(),
        w_key=projection(),
        w_value=projection(),
        w_output=projection(),
        num_heads=num_heads,
    )


def _head_slice(matrix: Array, head: int, d_head: int) -> Array:
    column: Array = matrix[:, head * d_head : (head + 1) * d_head]
    return column


def multi_head_attention_forward(
    query: Array,
    key: Array,
    value: Array,
    params: MultiHeadAttentionParams,
) -> tuple[Array, MultiHeadAttentionCache]:
    """Project, attend per head, concatenate, and apply the output projection."""
    projected_query: Array = query @ params.w_query
    projected_key: Array = key @ params.w_key
    projected_value: Array = value @ params.w_value

    head_outputs: list[Array] = []
    head_weights: list[Array] = []
    for head in range(params.num_heads):
        head_output, weights = scaled_dot_product_attention(
            _head_slice(projected_query, head, params.d_head),
            _head_slice(projected_key, head, params.d_head),
            _head_slice(projected_value, head, params.d_head),
        )
        head_outputs.append(head_output)
        head_weights.append(weights)

    attention_concat: Array = np.concatenate(head_outputs, axis=-1)
    output: Array = attention_concat @ params.w_output
    cache = MultiHeadAttentionCache(
        query=query,
        key=key,
        value=value,
        projected_query=projected_query,
        projected_key=projected_key,
        projected_value=projected_value,
        head_weights=head_weights,
        attention_concat=attention_concat,
    )
    return output, cache


def multi_head_attention_backward(
    d_output: Array,
    *,
    cache: MultiHeadAttentionCache,
    params: MultiHeadAttentionParams,
) -> MultiHeadAttentionGradients:
    """Backpropagate through the output projection, heads, and input projections."""
    d_attention_concat: Array = d_output @ params.w_output.T
    d_w_output: Array = cache.attention_concat.T @ d_output

    d_head = params.d_head
    d_projected_query = np.zeros_like(cache.projected_query)
    d_projected_key = np.zeros_like(cache.projected_key)
    d_projected_value = np.zeros_like(cache.projected_value)
    for head in range(params.num_heads):
        span = slice(head * d_head, (head + 1) * d_head)
        d_query_head, d_key_head, d_value_head = scaled_dot_product_attention_backward(
            d_attention_concat[:, span],
            query=cache.projected_query[:, span],
            key=cache.projected_key[:, span],
            value=cache.projected_value[:, span],
            weights=cache.head_weights[head],
        )
        d_projected_query[:, span] = d_query_head
        d_projected_key[:, span] = d_key_head
        d_projected_value[:, span] = d_value_head

    d_query: Array = d_projected_query @ params.w_query.T
    d_key: Array = d_projected_key @ params.w_key.T
    d_value: Array = d_projected_value @ params.w_value.T
    d_w_query: Array = cache.query.T @ d_projected_query
    d_w_key: Array = cache.key.T @ d_projected_key
    d_w_value: Array = cache.value.T @ d_projected_value

    return MultiHeadAttentionGradients(
        d_query=d_query,
        d_key=d_key,
        d_value=d_value,
        d_w_query=d_w_query,
        d_w_key=d_w_key,
        d_w_value=d_w_value,
        d_w_output=d_w_output,
    )
