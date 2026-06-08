"""The assembled cross-attention vote transformer (numpy, trainable).

OVERALL_GOAL.md's prediction model: a politician token stream (dossier +
structural embedding tokens) cross-attends over a context stream (bill dossier
+ structural embedding, context tokens, and retrieved past-vote tokens, all
concatenated by the caller), the attended politician representation is mean
pooled, and a linear head emits a yea probability.

This wires the gradient-checked cross-attention block to a pooling + sigmoid
head and a binary-cross-entropy trainer, all in numpy. It trains on existing
federal data today; when Track A's dossier embeddings arrive the same forward
contract is reimplemented in torch. The four-stream token construction (which
embeddings become politician vs context tokens, RAG retrieval of past votes)
layers on top of this core; here the streams arrive as ``(L, d_model)`` arrays.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.transformer import (
    CrossAttentionBlockCache,
    CrossAttentionBlockGradients,
    CrossAttentionBlockParams,
    cross_attention_block_backward,
    cross_attention_block_forward,
    init_cross_attention_block,
)

Array = npt.NDArray[np.float64]

_PROBABILITY_FLOOR = 1e-12


@dataclass(frozen=True)
class VoteTransformerParams:
    """The cross-attention block plus a linear yea-probability head."""

    block: CrossAttentionBlockParams
    w_head: Array
    b_head: float


@dataclass(frozen=True)
class VoteTransformerCache:
    block_cache: CrossAttentionBlockCache
    block_output: Array
    pooled: Array
    sequence_length: int


@dataclass(frozen=True)
class VoteTransformerGradients:
    d_politician: Array
    d_context: Array
    block: CrossAttentionBlockGradients
    d_w_head: Array
    d_b_head: float


@dataclass(frozen=True)
class VoteTransformerExample:
    """One training example: token streams and the realized binary vote."""

    politician_tokens: Array
    context_tokens: Array
    is_yea: bool


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return float(1.0 / (1.0 + np.exp(-value)))
    exponential = np.exp(value)
    return float(exponential / (1.0 + exponential))


def init_vote_transformer(
    *,
    d_model: int,
    num_heads: int,
    d_hidden: int,
    rng: np.random.Generator,
) -> VoteTransformerParams:
    """Initialize the block and a small linear head."""
    block = init_cross_attention_block(
        d_model=d_model, num_heads=num_heads, d_hidden=d_hidden, rng=rng
    )
    head: Array = rng.normal(size=d_model) * (1.0 / np.sqrt(d_model))
    return VoteTransformerParams(block=block, w_head=head, b_head=0.0)


def _forward(
    politician_tokens: Array,
    context_tokens: Array,
    params: VoteTransformerParams,
) -> tuple[float, VoteTransformerCache]:
    block_output, block_cache = cross_attention_block_forward(
        politician_tokens, context_tokens, params.block
    )
    pooled: Array = block_output.mean(axis=0)
    logit = float(pooled @ params.w_head + params.b_head)
    cache = VoteTransformerCache(
        block_cache=block_cache,
        block_output=block_output,
        pooled=pooled,
        sequence_length=block_output.shape[0],
    )
    return logit, cache


def vote_transformer_logit(
    politician_tokens: Array,
    context_tokens: Array,
    params: VoteTransformerParams,
) -> float:
    """The pre-sigmoid logit (exposed for gradient checking and stacking)."""
    logit, _cache = _forward(politician_tokens, context_tokens, params)
    return logit


def vote_transformer_forward(
    politician_tokens: Array,
    context_tokens: Array,
    params: VoteTransformerParams,
) -> tuple[float, VoteTransformerCache]:
    """Yea probability for one (politician, bill) pair, plus the backward cache."""
    logit, cache = _forward(politician_tokens, context_tokens, params)
    return _sigmoid(logit), cache


def vote_transformer_backward(
    d_logit: float,
    *,
    cache: VoteTransformerCache,
    params: VoteTransformerParams,
) -> VoteTransformerGradients:
    """Gradients w.r.t. the token streams and all parameters given d(loss)/d(logit)."""
    d_w_head: Array = d_logit * cache.pooled
    d_pooled: Array = d_logit * params.w_head
    # Mean-pool backward: each of the L pooled rows receives an equal share.
    d_block_output: Array = np.broadcast_to(
        d_pooled / cache.sequence_length, cache.block_output.shape
    ).copy()
    block_grads = cross_attention_block_backward(
        d_block_output, cache=cache.block_cache, params=params.block
    )
    return VoteTransformerGradients(
        d_politician=block_grads.d_query,
        d_context=block_grads.d_context,
        block=block_grads,
        d_w_head=d_w_head,
        d_b_head=d_logit,
    )


def _apply_block_gradients(
    block: CrossAttentionBlockParams,
    grads: CrossAttentionBlockGradients,
    learning_rate: float,
) -> CrossAttentionBlockParams:
    attention = block.attention
    updated_attention = attention.__class__(
        w_query=attention.w_query - learning_rate * grads.attention.d_w_query,
        w_key=attention.w_key - learning_rate * grads.attention.d_w_key,
        w_value=attention.w_value - learning_rate * grads.attention.d_w_value,
        w_output=attention.w_output - learning_rate * grads.attention.d_w_output,
        num_heads=attention.num_heads,
    )
    feed_forward = block.feed_forward
    updated_feed_forward = feed_forward.__class__(
        w_in=feed_forward.w_in - learning_rate * grads.feed_forward.d_w_in,
        b_in=feed_forward.b_in - learning_rate * grads.feed_forward.d_b_in,
        w_out=feed_forward.w_out - learning_rate * grads.feed_forward.d_w_out,
        b_out=feed_forward.b_out - learning_rate * grads.feed_forward.d_b_out,
    )
    norm_attention = block.norm_attention.__class__(
        gamma=block.norm_attention.gamma - learning_rate * grads.norm_attention.d_gamma,
        beta=block.norm_attention.beta - learning_rate * grads.norm_attention.d_beta,
    )
    norm_feed_forward = block.norm_feed_forward.__class__(
        gamma=block.norm_feed_forward.gamma - learning_rate * grads.norm_feed_forward.d_gamma,
        beta=block.norm_feed_forward.beta - learning_rate * grads.norm_feed_forward.d_beta,
    )
    return block.__class__(
        attention=updated_attention,
        norm_attention=norm_attention,
        feed_forward=updated_feed_forward,
        norm_feed_forward=norm_feed_forward,
    )


def train_vote_transformer(
    params: VoteTransformerParams,
    examples: list[VoteTransformerExample],
    *,
    epochs: int,
    learning_rate: float,
) -> tuple[VoteTransformerParams, list[float]]:
    """Full-batch binary-cross-entropy training; returns trained params + loss history.

    Each example's logit gradient is ``probability - label`` (the BCE-on-sigmoid
    gradient). Parameter gradients are averaged across the batch per epoch.
    """
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if not examples:
        raise ValueError("examples must not be empty")

    history: list[float] = []
    current = params
    batch_scale = 1.0 / len(examples)
    for _ in range(epochs):
        epoch_loss = 0.0
        accumulated_w_head = np.zeros_like(current.w_head)
        accumulated_b_head = 0.0
        accumulated_block: CrossAttentionBlockGradients | None = None
        for example in examples:
            probability, cache = vote_transformer_forward(
                example.politician_tokens, example.context_tokens, current
            )
            target = 1.0 if example.is_yea else 0.0
            clamped = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, probability))
            epoch_loss += -(target * np.log(clamped) + (1.0 - target) * np.log(1.0 - clamped))
            grads = vote_transformer_backward(probability - target, cache=cache, params=current)
            accumulated_w_head = accumulated_w_head + grads.d_w_head
            accumulated_b_head += grads.d_b_head
            accumulated_block = (
                grads.block
                if accumulated_block is None
                else _add_block_gradients(accumulated_block, grads.block)
            )
        history.append(float(epoch_loss * batch_scale))
        assert accumulated_block is not None
        averaged_block = _scale_block_gradients(accumulated_block, batch_scale)
        current = VoteTransformerParams(
            block=_apply_block_gradients(current.block, averaged_block, learning_rate),
            w_head=current.w_head - learning_rate * accumulated_w_head * batch_scale,
            b_head=current.b_head - learning_rate * accumulated_b_head * batch_scale,
        )
    return current, history


def _add_block_gradients(
    left: CrossAttentionBlockGradients,
    right: CrossAttentionBlockGradients,
) -> CrossAttentionBlockGradients:
    return CrossAttentionBlockGradients(
        d_query=left.d_query,
        d_context=left.d_context,
        attention=left.attention.__class__(
            d_query=left.attention.d_query,
            d_key=left.attention.d_key,
            d_value=left.attention.d_value,
            d_w_query=left.attention.d_w_query + right.attention.d_w_query,
            d_w_key=left.attention.d_w_key + right.attention.d_w_key,
            d_w_value=left.attention.d_w_value + right.attention.d_w_value,
            d_w_output=left.attention.d_w_output + right.attention.d_w_output,
        ),
        norm_attention=left.norm_attention.__class__(
            d_gamma=left.norm_attention.d_gamma + right.norm_attention.d_gamma,
            d_beta=left.norm_attention.d_beta + right.norm_attention.d_beta,
        ),
        feed_forward=left.feed_forward.__class__(
            d_w_in=left.feed_forward.d_w_in + right.feed_forward.d_w_in,
            d_b_in=left.feed_forward.d_b_in + right.feed_forward.d_b_in,
            d_w_out=left.feed_forward.d_w_out + right.feed_forward.d_w_out,
            d_b_out=left.feed_forward.d_b_out + right.feed_forward.d_b_out,
        ),
        norm_feed_forward=left.norm_feed_forward.__class__(
            d_gamma=left.norm_feed_forward.d_gamma + right.norm_feed_forward.d_gamma,
            d_beta=left.norm_feed_forward.d_beta + right.norm_feed_forward.d_beta,
        ),
    )


def _scale_block_gradients(
    grads: CrossAttentionBlockGradients,
    scale: float,
) -> CrossAttentionBlockGradients:
    return CrossAttentionBlockGradients(
        d_query=grads.d_query,
        d_context=grads.d_context,
        attention=grads.attention.__class__(
            d_query=grads.attention.d_query,
            d_key=grads.attention.d_key,
            d_value=grads.attention.d_value,
            d_w_query=grads.attention.d_w_query * scale,
            d_w_key=grads.attention.d_w_key * scale,
            d_w_value=grads.attention.d_w_value * scale,
            d_w_output=grads.attention.d_w_output * scale,
        ),
        norm_attention=grads.norm_attention.__class__(
            d_gamma=grads.norm_attention.d_gamma * scale,
            d_beta=grads.norm_attention.d_beta * scale,
        ),
        feed_forward=grads.feed_forward.__class__(
            d_w_in=grads.feed_forward.d_w_in * scale,
            d_b_in=grads.feed_forward.d_b_in * scale,
            d_w_out=grads.feed_forward.d_w_out * scale,
            d_b_out=grads.feed_forward.d_b_out * scale,
        ),
        norm_feed_forward=grads.norm_feed_forward.__class__(
            d_gamma=grads.norm_feed_forward.d_gamma * scale,
            d_beta=grads.norm_feed_forward.d_beta * scale,
        ),
    )
