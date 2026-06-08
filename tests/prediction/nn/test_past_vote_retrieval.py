"""Tests for the past-vote RAG retrieval encoder.

The fourth of OVERALL_GOAL.md's token streams: retrieved past-vote tokens via
RAG over the K nearest historical (politician, bill) pairs. Retrieval is a
cosine nearest-neighbour over real historical vote key-embeddings; each
retrieved vote is encoded into a token as ``similarity * outcome_vector`` with
learnable yea/nay vectors whose gradients are checked.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from src.prediction.nn.past_vote_retrieval import (
    PastVote,
    encode_past_votes,
    encode_past_votes_backward,
    init_past_vote_encoder,
    retrieve_top_k,
)

Array = npt.NDArray[np.float64]


def _store() -> list[PastVote]:
    return [
        PastVote(key_embedding=np.array([1.0, 0.0]), is_yea=True),
        PastVote(key_embedding=np.array([0.9, 0.1]), is_yea=True),
        PastVote(key_embedding=np.array([0.0, 1.0]), is_yea=False),
        PastVote(key_embedding=np.array([-1.0, 0.0]), is_yea=False),
    ]


def test_retrieval_returns_k_nearest_by_cosine() -> None:
    query = np.array([1.0, 0.05])
    retrieved = retrieve_top_k(query, _store(), k=2)
    assert len(retrieved) == 2
    # The two yea votes near (1, 0) are nearest.
    assert all(item.vote.is_yea for item in retrieved)
    # Sorted by descending similarity.
    assert retrieved[0].similarity >= retrieved[1].similarity


def test_retrieval_caps_at_store_size() -> None:
    retrieved = retrieve_top_k(np.array([1.0, 0.0]), _store(), k=10)
    assert len(retrieved) == 4


def test_encode_past_votes_emits_token_per_retrieved_vote() -> None:
    rng = np.random.default_rng(0)
    params = init_past_vote_encoder(d_model=5, rng=rng)
    retrieved = retrieve_top_k(np.array([1.0, 0.0]), _store(), k=3)
    tokens, _cache = encode_past_votes(retrieved, params)
    assert tokens.shape == (3, 5)


def test_yea_and_nay_votes_use_their_outcome_vectors() -> None:
    rng = np.random.default_rng(1)
    params = init_past_vote_encoder(d_model=4, rng=rng)
    yea = retrieve_top_k(np.array([1.0, 0.0]), [PastVote(np.array([1.0, 0.0]), True)], k=1)
    nay = retrieve_top_k(np.array([1.0, 0.0]), [PastVote(np.array([1.0, 0.0]), False)], k=1)
    yea_tokens, _ = encode_past_votes(yea, params)
    nay_tokens, _ = encode_past_votes(nay, params)
    # similarity == 1 for the identical key, so the token equals the outcome vector.
    assert np.allclose(yea_tokens[0], params.yea_vector)
    assert np.allclose(nay_tokens[0], params.nay_vector)


def test_encode_backward_matches_finite_differences() -> None:
    rng = np.random.default_rng(2)
    params = init_past_vote_encoder(d_model=4, rng=rng)
    retrieved = retrieve_top_k(np.array([0.8, 0.2]), _store(), k=3)
    upstream = rng.normal(size=(3, 4))

    _tokens, cache = encode_past_votes(retrieved, params)
    grads = encode_past_votes_backward(upstream, cache=cache, params=params)

    def loss(vector: Array) -> float:
        bumped = params.__class__(
            yea_vector=vector if vector is params.yea_vector else params.yea_vector,
            nay_vector=params.nay_vector,
        )
        result, _ = encode_past_votes(retrieved, bumped)
        return float(np.sum(result * upstream))

    def numerical(vector: Array) -> Array:
        grad = np.zeros_like(vector)
        for index in range(vector.size):
            original = vector[index]
            vector[index] = original + 1e-6
            plus, _ = encode_past_votes(retrieved, params)
            vector[index] = original - 1e-6
            minus, _ = encode_past_votes(retrieved, params)
            vector[index] = original
            grad[index] = (float(np.sum(plus * upstream)) - float(np.sum(minus * upstream))) / 2e-6
        return grad

    assert np.allclose(grads.d_yea_vector, numerical(params.yea_vector), atol=1e-6)
    assert np.allclose(grads.d_nay_vector, numerical(params.nay_vector), atol=1e-6)
