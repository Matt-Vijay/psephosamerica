"""LayerNorm and position-wise feed-forward sub-layers (numpy, fwd + bwd).

The non-attention halves of OVERALL_GOAL.md's transformer block: a LayerNorm
for the residual stream and a two-layer ReLU feed-forward network. Pure numpy
with explicit analytic backward passes (gradient-checked), so they train by
gradient descent and compose with the multi-head attention block into a full
transformer that swaps cleanly for torch later.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

_LAYER_NORM_EPS = 1e-5


@dataclass(frozen=True)
class LayerNormParams:
    """Per-feature scale and shift; each is ``(dim,)``."""

    gamma: Array
    beta: Array


@dataclass(frozen=True)
class LayerNormCache:
    normalized: Array
    inverse_std: Array
    gamma: Array


@dataclass(frozen=True)
class LayerNormGradients:
    d_gamma: Array
    d_beta: Array


def init_layer_norm(*, dim: int) -> LayerNormParams:
    """Unit scale, zero shift."""
    return LayerNormParams(gamma=np.ones(dim), beta=np.zeros(dim))


def layer_norm_forward(x: Array, params: LayerNormParams) -> tuple[Array, LayerNormCache]:
    """Normalize each row over its features, then scale and shift."""
    mean = np.mean(x, axis=-1, keepdims=True)
    centered = x - mean
    variance = np.mean(centered**2, axis=-1, keepdims=True)
    inverse_std: Array = 1.0 / np.sqrt(variance + _LAYER_NORM_EPS)
    normalized: Array = centered * inverse_std
    output: Array = params.gamma * normalized + params.beta
    return output, LayerNormCache(
        normalized=normalized, inverse_std=inverse_std, gamma=params.gamma
    )


def layer_norm_backward(
    d_output: Array,
    *,
    cache: LayerNormCache,
) -> tuple[Array, LayerNormGradients]:
    """Gradients w.r.t. the input and the scale/shift parameters."""
    d_gamma: Array = np.sum(d_output * cache.normalized, axis=0)
    d_beta: Array = np.sum(d_output, axis=0)
    d_normalized = d_output * cache.gamma
    # The two means below divide by the feature dimension, so the standard
    # 1/dim factor of the LayerNorm Jacobian is already folded in.
    d_normalized_mean = np.mean(d_normalized, axis=-1, keepdims=True)
    d_normalized_dot = np.mean(d_normalized * cache.normalized, axis=-1, keepdims=True)
    d_x: Array = cache.inverse_std * (
        d_normalized - d_normalized_mean - cache.normalized * d_normalized_dot
    )
    return d_x, LayerNormGradients(d_gamma=d_gamma, d_beta=d_beta)


@dataclass(frozen=True)
class FeedForwardParams:
    """Two-layer MLP: ``(d_model -> d_hidden -> d_model)`` with ReLU between."""

    w_in: Array
    b_in: Array
    w_out: Array
    b_out: Array


@dataclass(frozen=True)
class FeedForwardCache:
    x: Array
    pre_activation: Array
    hidden: Array


@dataclass(frozen=True)
class FeedForwardGradients:
    d_w_in: Array
    d_b_in: Array
    d_w_out: Array
    d_b_out: Array


def init_feed_forward(
    *,
    d_model: int,
    d_hidden: int,
    rng: np.random.Generator,
) -> FeedForwardParams:
    """Initialize with He-style scaling for the ReLU layer."""
    return FeedForwardParams(
        w_in=rng.normal(size=(d_model, d_hidden)) * np.sqrt(2.0 / d_model),
        b_in=np.zeros(d_hidden),
        w_out=rng.normal(size=(d_hidden, d_model)) * np.sqrt(2.0 / d_hidden),
        b_out=np.zeros(d_model),
    )


def feed_forward_forward(x: Array, params: FeedForwardParams) -> tuple[Array, FeedForwardCache]:
    """Linear, ReLU, linear."""
    pre_activation: Array = x @ params.w_in + params.b_in
    hidden: Array = np.maximum(pre_activation, 0.0)
    output: Array = hidden @ params.w_out + params.b_out
    return output, FeedForwardCache(x=x, pre_activation=pre_activation, hidden=hidden)


def feed_forward_backward(
    d_output: Array,
    *,
    cache: FeedForwardCache,
    params: FeedForwardParams,
) -> tuple[Array, FeedForwardGradients]:
    """Gradients w.r.t. the input and all four parameters."""
    d_w_out: Array = cache.hidden.T @ d_output
    d_b_out: Array = np.sum(d_output, axis=0)
    d_hidden: Array = d_output @ params.w_out.T
    d_pre_activation: Array = d_hidden * (cache.pre_activation > 0.0)
    d_w_in: Array = cache.x.T @ d_pre_activation
    d_b_in: Array = np.sum(d_pre_activation, axis=0)
    d_x: Array = d_pre_activation @ params.w_in.T
    return d_x, FeedForwardGradients(
        d_w_in=d_w_in,
        d_b_in=d_b_in,
        d_w_out=d_w_out,
        d_b_out=d_b_out,
    )
