"""Cross-attention transformer block (numpy, forward + backward).

The core repeating unit of OVERALL_GOAL.md's architecture: a query token stream
cross-attends over a context token stream (bill / retrieved-past-vote / context
tokens), followed by the standard post-norm residual structure --
``LayerNorm(query + Attention(query, context))`` then
``LayerNorm(h + FeedForward(h))``. It composes the gradient-checked multi-head
attention and the LayerNorm / feed-forward sub-layers, and its own backward is
validated end-to-end, so a stack of these trains by gradient descent today and
swaps cleanly for a torch ``TransformerDecoderLayer`` once Track A's dossier
embeddings arrive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.layers import (
    FeedForwardCache,
    FeedForwardGradients,
    FeedForwardParams,
    LayerNormCache,
    LayerNormGradients,
    LayerNormParams,
    feed_forward_backward,
    feed_forward_forward,
    init_feed_forward,
    init_layer_norm,
    layer_norm_backward,
    layer_norm_forward,
)
from src.prediction.nn.multihead import (
    MultiHeadAttentionCache,
    MultiHeadAttentionGradients,
    MultiHeadAttentionParams,
    init_multi_head_attention,
    multi_head_attention_backward,
    multi_head_attention_forward,
)

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class CrossAttentionBlockParams:
    """Parameters of one post-norm cross-attention transformer block."""

    attention: MultiHeadAttentionParams
    norm_attention: LayerNormParams
    feed_forward: FeedForwardParams
    norm_feed_forward: LayerNormParams


@dataclass(frozen=True)
class CrossAttentionBlockCache:
    query: Array
    attention_cache: MultiHeadAttentionCache
    norm_attention_cache: LayerNormCache
    feed_forward_cache: FeedForwardCache
    norm_feed_forward_cache: LayerNormCache


@dataclass(frozen=True)
class CrossAttentionBlockGradients:
    d_query: Array
    d_context: Array
    attention: MultiHeadAttentionGradients
    norm_attention: LayerNormGradients
    feed_forward: FeedForwardGradients
    norm_feed_forward: LayerNormGradients


def init_cross_attention_block(
    *,
    d_model: int,
    num_heads: int,
    d_hidden: int,
    rng: np.random.Generator,
) -> CrossAttentionBlockParams:
    """Initialize attention, feed-forward, and both LayerNorms."""
    return CrossAttentionBlockParams(
        attention=init_multi_head_attention(d_model=d_model, num_heads=num_heads, rng=rng),
        norm_attention=init_layer_norm(dim=d_model),
        feed_forward=init_feed_forward(d_model=d_model, d_hidden=d_hidden, rng=rng),
        norm_feed_forward=init_layer_norm(dim=d_model),
    )


def cross_attention_block_forward(
    query: Array,
    context: Array,
    params: CrossAttentionBlockParams,
) -> tuple[Array, CrossAttentionBlockCache]:
    """Cross-attend, residual + norm, feed-forward, residual + norm."""
    attention_out, attention_cache = multi_head_attention_forward(
        query, context, context, params.attention
    )
    residual_attention: Array = query + attention_out
    normed_attention, norm_attention_cache = layer_norm_forward(
        residual_attention, params.norm_attention
    )
    feed_forward_out, feed_forward_cache = feed_forward_forward(
        normed_attention, params.feed_forward
    )
    residual_feed_forward: Array = normed_attention + feed_forward_out
    output, norm_feed_forward_cache = layer_norm_forward(
        residual_feed_forward, params.norm_feed_forward
    )
    cache = CrossAttentionBlockCache(
        query=query,
        attention_cache=attention_cache,
        norm_attention_cache=norm_attention_cache,
        feed_forward_cache=feed_forward_cache,
        norm_feed_forward_cache=norm_feed_forward_cache,
    )
    return output, cache


def apply_block_gradients(
    params: CrossAttentionBlockParams,
    grads: CrossAttentionBlockGradients,
    learning_rate: float,
) -> CrossAttentionBlockParams:
    """Return new block parameters after one SGD step against ``grads``."""
    attention = params.attention
    updated_attention = MultiHeadAttentionParams(
        w_query=attention.w_query - learning_rate * grads.attention.d_w_query,
        w_key=attention.w_key - learning_rate * grads.attention.d_w_key,
        w_value=attention.w_value - learning_rate * grads.attention.d_w_value,
        w_output=attention.w_output - learning_rate * grads.attention.d_w_output,
        num_heads=attention.num_heads,
    )
    feed_forward = params.feed_forward
    updated_feed_forward = FeedForwardParams(
        w_in=feed_forward.w_in - learning_rate * grads.feed_forward.d_w_in,
        b_in=feed_forward.b_in - learning_rate * grads.feed_forward.d_b_in,
        w_out=feed_forward.w_out - learning_rate * grads.feed_forward.d_w_out,
        b_out=feed_forward.b_out - learning_rate * grads.feed_forward.d_b_out,
    )
    return CrossAttentionBlockParams(
        attention=updated_attention,
        norm_attention=LayerNormParams(
            gamma=params.norm_attention.gamma - learning_rate * grads.norm_attention.d_gamma,
            beta=params.norm_attention.beta - learning_rate * grads.norm_attention.d_beta,
        ),
        feed_forward=updated_feed_forward,
        norm_feed_forward=LayerNormParams(
            gamma=params.norm_feed_forward.gamma - learning_rate * grads.norm_feed_forward.d_gamma,
            beta=params.norm_feed_forward.beta - learning_rate * grads.norm_feed_forward.d_beta,
        ),
    )


def cross_attention_block_backward(
    d_output: Array,
    *,
    cache: CrossAttentionBlockCache,
    params: CrossAttentionBlockParams,
) -> CrossAttentionBlockGradients:
    """Backpropagate through both residual sub-blocks."""
    # Second residual sub-block: output = LayerNorm(normed_attention + ffn_out).
    d_residual_feed_forward, norm_feed_forward_grads = layer_norm_backward(
        d_output, cache=cache.norm_feed_forward_cache
    )
    d_feed_forward_in, feed_forward_grads = feed_forward_backward(
        d_residual_feed_forward, cache=cache.feed_forward_cache, params=params.feed_forward
    )
    # residual: normed_attention feeds both the FFN and the skip connection.
    d_normed_attention: Array = d_residual_feed_forward + d_feed_forward_in

    # First residual sub-block: normed_attention = LayerNorm(query + attn_out).
    d_residual_attention, norm_attention_grads = layer_norm_backward(
        d_normed_attention, cache=cache.norm_attention_cache
    )
    attention_grads = multi_head_attention_backward(
        d_residual_attention, cache=cache.attention_cache, params=params.attention
    )
    # query feeds both the attention query and the skip connection; context is
    # the key and the value, so its gradient sums both contributions.
    d_query: Array = d_residual_attention + attention_grads.d_query
    d_context: Array = attention_grads.d_key + attention_grads.d_value

    return CrossAttentionBlockGradients(
        d_query=d_query,
        d_context=d_context,
        attention=attention_grads,
        norm_attention=norm_attention_grads,
        feed_forward=feed_forward_grads,
        norm_feed_forward=norm_feed_forward_grads,
    )
