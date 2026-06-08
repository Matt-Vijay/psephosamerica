"""Multi-layer cross-attention transformer stack (numpy, forward + backward).

Stacking :func:`cross_attention_block_forward` makes the model deep: the query
(politician) stream is refined layer by layer, each layer cross-attending to
the same context (bill / retrieved-past-vote / context tokens), as in a
transformer decoder attending to fixed encoder memory.

The backward pass threads the query gradient down through every layer to the
inputs and *accumulates* the context gradient across layers (the context feeds
each layer's attention independently). Gradient-checked end to end, so a deep
stack trains by gradient descent and swaps for a torch decoder stack later.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.transformer import (
    CrossAttentionBlockCache,
    CrossAttentionBlockGradients,
    CrossAttentionBlockParams,
    apply_block_gradients,
    cross_attention_block_backward,
    cross_attention_block_forward,
    init_cross_attention_block,
)

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class TransformerStackParams:
    """The per-layer parameters of a deep cross-attention stack."""

    layers: list[CrossAttentionBlockParams]


def init_transformer_stack(
    *,
    num_layers: int,
    d_model: int,
    num_heads: int,
    d_hidden: int,
    rng: np.random.Generator,
) -> TransformerStackParams:
    """Initialize ``num_layers`` independent cross-attention blocks."""
    if num_layers <= 0:
        raise ValueError("num_layers must be positive")
    return TransformerStackParams(
        layers=[
            init_cross_attention_block(
                d_model=d_model, num_heads=num_heads, d_hidden=d_hidden, rng=rng
            )
            for _ in range(num_layers)
        ]
    )


def transformer_stack_forward(
    query: Array,
    context: Array,
    params: TransformerStackParams,
) -> tuple[Array, list[CrossAttentionBlockCache]]:
    """Run the query through each layer, cross-attending to the shared context."""
    caches: list[CrossAttentionBlockCache] = []
    hidden = query
    for layer in params.layers:
        hidden, cache = cross_attention_block_forward(hidden, context, layer)
        caches.append(cache)
    return hidden, caches


def transformer_stack_backward(
    d_output: Array,
    *,
    caches: list[CrossAttentionBlockCache],
    params: TransformerStackParams,
) -> tuple[Array, Array, list[CrossAttentionBlockGradients]]:
    """Backpropagate through the stack, accumulating the context gradient.

    Returns ``(d_query, d_context, per_layer_gradients)`` where the per-layer
    gradients are ordered from the first (input-side) layer to the last.
    """
    layer_gradients_reversed: list[CrossAttentionBlockGradients] = []
    d_hidden = d_output
    d_context = np.zeros_like(caches[0].attention_cache.key)
    for layer, cache in zip(reversed(params.layers), reversed(caches), strict=True):
        block_grads = cross_attention_block_backward(d_hidden, cache=cache, params=layer)
        d_hidden = block_grads.d_query
        d_context = d_context + block_grads.d_context
        layer_gradients_reversed.append(block_grads)
    return d_hidden, d_context, list(reversed(layer_gradients_reversed))


def apply_stack_gradients(
    params: TransformerStackParams,
    layer_gradients: list[CrossAttentionBlockGradients],
    learning_rate: float,
) -> TransformerStackParams:
    """Return new stack parameters after one SGD step over each layer's gradients."""
    if len(layer_gradients) != len(params.layers):
        raise ValueError("layer_gradients must align with the stack layers")
    return TransformerStackParams(
        layers=[
            apply_block_gradients(layer, grads, learning_rate)
            for layer, grads in zip(params.layers, layer_gradients, strict=True)
        ]
    )
