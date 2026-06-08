"""Tests for the four-stream cross-attention model wiring.

OVERALL_GOAL.md's architecture: the politician token stream (dossier +
structural embedding) cross-attends over the bill stream, the context tokens,
and the retrieved past-vote tokens. This pins the operational wiring -- the
query is the politician stream and the context is the concatenation of the
other three streams -- run through the cross-attention transformer to a yea
probability. Structural/context/past-vote tokens are real; the dossier token
is mockable until Track A provides embeddings.
"""

from __future__ import annotations

import numpy as np

from src.prediction.nn.four_stream_model import (
    assemble_context,
    four_stream_predict,
)
from src.prediction.nn.vote_transformer import init_vote_transformer


def test_assemble_context_concatenates_present_streams() -> None:
    bill = np.ones((1, 4))
    context = np.ones((3, 4)) * 2
    past_vote = np.ones((2, 4)) * 3
    assembled = assemble_context(bill, context, past_vote)
    assert assembled.shape == (6, 4)
    assert np.allclose(assembled[0], 1.0)
    assert np.allclose(assembled[1], 2.0)
    assert np.allclose(assembled[4], 3.0)


def test_assemble_context_skips_empty_streams() -> None:
    bill = np.ones((2, 4))
    context = np.ones((1, 4)) * 2
    empty_past_vote = np.zeros((0, 4))
    assembled = assemble_context(bill, context, empty_past_vote)
    assert assembled.shape == (3, 4)


def test_four_stream_predict_returns_probability() -> None:
    rng = np.random.default_rng(0)
    params = init_vote_transformer(d_model=4, num_heads=2, d_hidden=8, rng=rng)
    politician = rng.normal(size=(2, 4))
    bill = rng.normal(size=(1, 4))
    context = rng.normal(size=(3, 4))
    past_vote = rng.normal(size=(2, 4))

    probability, cache = four_stream_predict(
        politician_tokens=politician,
        bill_tokens=bill,
        context_tokens=context,
        past_vote_tokens=past_vote,
        params=params,
    )
    assert 0.0 <= probability <= 1.0
    # The transformer attended over all six context tokens (1 + 3 + 2).
    assert cache.block_output.shape[0] == politician.shape[0]


def test_four_stream_predict_works_without_past_votes() -> None:
    rng = np.random.default_rng(1)
    params = init_vote_transformer(d_model=4, num_heads=2, d_hidden=8, rng=rng)
    politician = rng.normal(size=(2, 4))
    bill = rng.normal(size=(2, 4))
    context = rng.normal(size=(2, 4))
    empty_past_vote = np.zeros((0, 4))

    probability, _cache = four_stream_predict(
        politician_tokens=politician,
        bill_tokens=bill,
        context_tokens=context,
        past_vote_tokens=empty_past_vote,
        params=params,
    )
    assert 0.0 <= probability <= 1.0


def test_assemble_context_requires_at_least_one_token() -> None:
    empty = np.zeros((0, 4))
    raised = False
    try:
        assemble_context(empty, empty, empty)
    except ValueError:
        raised = True
    assert raised
