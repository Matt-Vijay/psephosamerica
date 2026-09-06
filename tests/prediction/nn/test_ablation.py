"""Tests for the six-stream ablation harness.

OVERALL_GOAL.md wants an ablation table proving which of the six token streams
(politician dossier text, politician structural, bill dossier text, bill
structural, context, retrieved past-votes) contributes. The harness runs the
full four-stream model and each "minus one stream" variant and reports the
accuracy/Brier delta per stream. A synthetic example where the signal lives in
one stream must show that dropping that stream hurts the most.
"""

from __future__ import annotations

import numpy as np

from src.prediction.nn.ablation import STREAMS, FourStreamExample, run_ablation
from src.prediction.nn.vote_transformer import (
    VoteTransformerExample,
    init_vote_transformer,
    train_vote_transformer,
)


def _example(rng: np.random.Generator, *, signal_in_context: float) -> FourStreamExample:
    d = 4
    return FourStreamExample(
        politician_structural=rng.normal(size=d),
        politician_dossier=rng.normal(size=d),
        bill_structural=rng.normal(size=d),
        bill_dossier=rng.normal(size=d),
        # The label-carrying signal is injected into the context stream's first row.
        context_tokens=np.array([[signal_in_context, 0.0, 0.0, 0.0]]),
        past_vote_tokens=rng.normal(size=(2, d)),
        is_yea=signal_in_context > 0,
    )


def test_run_ablation_reports_full_and_each_stream() -> None:
    rng = np.random.default_rng(0)
    params = init_vote_transformer(d_model=4, num_heads=2, d_hidden=8, rng=rng)
    examples = [_example(rng, signal_in_context=1.0 if i % 2 else -1.0) for i in range(12)]
    table = run_ablation(examples, params)
    assert "full" in table
    for stream in STREAMS:
        assert f"minus:{stream}" in table
    for metrics in table.values():
        assert 0.0 <= metrics.accuracy <= 1.0
        assert 0.0 <= metrics.brier_score <= 1.0


def test_dropping_the_signal_stream_hurts_most() -> None:
    rng = np.random.default_rng(1)
    params = init_vote_transformer(d_model=4, num_heads=2, d_hidden=8, rng=rng)
    train = [
        VoteTransformerExample(
            politician_tokens=np.concatenate(
                [ex.politician_structural[None, :], ex.politician_dossier[None, :]]
            ),
            context_tokens=np.concatenate(
                [ex.bill_structural[None, :], ex.bill_dossier[None, :], ex.context_tokens]
            ),
            is_yea=ex.is_yea,
        )
        for ex in (_example(rng, signal_in_context=1.0 if i % 2 else -1.0) for i in range(60))
    ]
    trained, _history = train_vote_transformer(params, train, epochs=150, learning_rate=0.1)

    examples = [_example(rng, signal_in_context=1.0 if i % 2 else -1.0) for i in range(40)]
    table = run_ablation(examples, trained)

    full_accuracy = table["full"].accuracy
    # Removing the context stream (which carries the label signal) must degrade
    # accuracy at least as much as removing any other single stream.
    context_drop = full_accuracy - table["minus:context"].accuracy
    other_drops = [full_accuracy - table[f"minus:{s}"].accuracy for s in STREAMS if s != "context"]
    assert context_drop >= max(other_drops) - 1e-9


def test_ablation_handles_missing_dossier_streams() -> None:
    rng = np.random.default_rng(2)
    params = init_vote_transformer(d_model=4, num_heads=2, d_hidden=8, rng=rng)
    examples = []
    for i in range(8):
        ex = _example(rng, signal_in_context=1.0 if i % 2 else -1.0)
        examples.append(
            FourStreamExample(
                politician_structural=ex.politician_structural,
                politician_dossier=None,  # no dossier embedding yet (Track A pending)
                bill_structural=ex.bill_structural,
                bill_dossier=None,
                context_tokens=ex.context_tokens,
                past_vote_tokens=ex.past_vote_tokens,
                is_yea=ex.is_yea,
            )
        )
    table = run_ablation(examples, params)
    # Dossier streams are absent, so their ablation equals the full run.
    assert table["minus:politician_dossier"].accuracy == table["full"].accuracy
    assert table["minus:bill_dossier"].accuracy == table["full"].accuracy
