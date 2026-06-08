"""Scaled dot-product attention in numpy (forward + backward).

The core operation of OVERALL_GOAL.md's cross-attention transformer: a query
stream attends over a separate key/value stream. Shapes are cross-attention
friendly -- the query and key/value sequences may differ in length -- so this
backs politician-tokens-attend-over-bill-tokens, retrieved-past-vote tokens,
and context tokens alike.

Implemented in numpy (the agreed interim stack: validatable against existing
data today, mypy-strict clean via ``numpy.typing``, CI-cheap) with an explicit
analytic backward so the block trains by gradient descent. The function
signatures mirror a torch ``scaled_dot_product_attention`` so the
implementation can be swapped for torch once Track A emits real dossier
embeddings, without changing callers.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]


def softmax(logits: Array, *, axis: int) -> Array:
    """Numerically stable softmax along ``axis``."""
    shifted = logits - np.max(logits, axis=axis, keepdims=True)
    exponentiated = np.exp(shifted)
    probabilities: Array = exponentiated / np.sum(exponentiated, axis=axis, keepdims=True)
    return probabilities


def scaled_dot_product_attention(
    query: Array,
    key: Array,
    value: Array,
) -> tuple[Array, Array]:
    """Attention output and weights for ``(Lq, d)`` query over ``(Lk, d)`` key/value.

    Returns ``(output, weights)`` where ``output`` is ``(Lq, dv)`` and
    ``weights`` is the ``(Lq, Lk)`` attention distribution (rows sum to 1).
    """
    if query.shape[-1] != key.shape[-1]:
        raise ValueError("query and key must share their feature dimension")
    if key.shape[0] != value.shape[0]:
        raise ValueError("key and value must share their sequence length")
    scale = 1.0 / np.sqrt(query.shape[-1])
    scores: Array = (query @ key.T) * scale
    weights = softmax(scores, axis=-1)
    output: Array = weights @ value
    return output, weights


def scaled_dot_product_attention_backward(
    d_output: Array,
    *,
    query: Array,
    key: Array,
    value: Array,
    weights: Array,
) -> tuple[Array, Array, Array]:
    """Gradients of the attention output w.r.t. query, key and value.

    ``weights`` is the forward pass's attention distribution; ``d_output`` is
    the upstream gradient with the shape of the forward output ``(Lq, dv)``.
    """
    scale = 1.0 / np.sqrt(query.shape[-1])
    d_value: Array = weights.T @ d_output
    d_weights: Array = d_output @ value.T
    # Backprop through the row-wise softmax: for each row,
    # d_scores = weights * (d_weights - sum(d_weights * weights)).
    weighted = np.sum(d_weights * weights, axis=-1, keepdims=True)
    d_scores: Array = weights * (d_weights - weighted)
    d_query: Array = (d_scores @ key) * scale
    d_key: Array = (d_scores.T @ query) * scale
    return d_query, d_key, d_value
