"""Past-vote RAG retrieval encoder (numpy, forward + backward).

The fourth of OVERALL_GOAL.md's token streams: retrieved past-vote tokens via
RAG over the K nearest historical (politician, bill) pairs. Retrieval is a
cosine nearest-neighbour over the historical key-embeddings (the real prior
votes, no fabrication); each retrieved vote is then encoded into a token as
``similarity * outcome_vector``, with learnable yea/nay outcome vectors so the
transformer can learn how much a similar prior yea/nay should move the current
prediction.

Retrieval (the arg-top-k) is non-differentiable selection done in pure Python;
the encoding step has an analytic, gradient-checked backward into the outcome
vectors.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

_NORM_FLOOR = 1e-12


@dataclass(frozen=True)
class PastVote:
    """A historical (politician, bill) vote: its key embedding and outcome."""

    key_embedding: Array
    is_yea: bool


@dataclass(frozen=True)
class RetrievedVote:
    """A retrieved past vote and its cosine similarity to the query."""

    similarity: float
    vote: PastVote


@dataclass(frozen=True)
class PastVoteEncoderParams:
    """Learnable outcome vectors for yea and nay retrieved votes."""

    yea_vector: Array
    nay_vector: Array


@dataclass(frozen=True)
class PastVoteEncoderCache:
    retrieved: list[RetrievedVote]


@dataclass(frozen=True)
class PastVoteEncoderGradients:
    d_yea_vector: Array
    d_nay_vector: Array


def init_past_vote_encoder(*, d_model: int, rng: np.random.Generator) -> PastVoteEncoderParams:
    """Initialize the yea/nay outcome vectors."""
    scale = 1.0 / np.sqrt(d_model)
    return PastVoteEncoderParams(
        yea_vector=rng.normal(size=d_model) * scale,
        nay_vector=rng.normal(size=d_model) * scale,
    )


def _cosine(left: Array, right: Array) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator < _NORM_FLOOR:
        return 0.0
    return float(np.dot(left, right) / denominator)


def retrieve_top_k(query: Array, store: list[PastVote], k: int) -> list[RetrievedVote]:
    """Return the ``k`` most cosine-similar historical votes, highest first."""
    if k <= 0:
        raise ValueError("k must be positive")
    scored = [
        RetrievedVote(similarity=_cosine(query, vote.key_embedding), vote=vote) for vote in store
    ]
    scored.sort(key=lambda item: item.similarity, reverse=True)
    return scored[:k]


def encode_past_votes(
    retrieved: list[RetrievedVote],
    params: PastVoteEncoderParams,
) -> tuple[Array, PastVoteEncoderCache]:
    """Encode each retrieved vote as ``similarity * outcome_vector``."""
    rows = [
        item.similarity * (params.yea_vector if item.vote.is_yea else params.nay_vector)
        for item in retrieved
    ]
    dimension = params.yea_vector.shape[0]
    tokens: Array = np.stack(rows, axis=0) if rows else np.zeros((0, dimension))
    return tokens, PastVoteEncoderCache(retrieved=list(retrieved))


def encode_past_votes_backward(
    d_tokens: Array,
    *,
    cache: PastVoteEncoderCache,
    params: PastVoteEncoderParams,
) -> PastVoteEncoderGradients:
    """Accumulate gradients into the yea/nay outcome vectors."""
    d_yea = np.zeros_like(params.yea_vector)
    d_nay = np.zeros_like(params.nay_vector)
    for row, item in enumerate(cache.retrieved):
        contribution = item.similarity * d_tokens[row]
        if item.vote.is_yea:
            d_yea = d_yea + contribution
        else:
            d_nay = d_nay + contribution
    return PastVoteEncoderGradients(d_yea_vector=d_yea, d_nay_vector=d_nay)
