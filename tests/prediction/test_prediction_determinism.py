"""Deterministic re-render guarantees for the prediction pipeline.

OVERALL_GOAL.md's done-criterion "correction propagations re-render
deterministically" rests on the prediction layer being a *pure function of its
inputs*: identical inputs must produce byte-identical outputs, with no hidden
state or randomness, so that when a correction changes an input the affected
predictions re-render deterministically. These tests pin that property across
the model, the transformer, and the served-prediction assembler -- and confirm
that a corrected input yields a different but itself-reproducible result.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timezone

import numpy as np

from src.prediction.nn.vote_transformer import (
    init_vote_transformer,
    vote_transformer_forward,
)
from src.prediction.per_member_model import MemberVoteExample, train_per_member_model
from src.prediction.prediction_assembly import SignalEvidence, assemble_served_prediction

_SHA = "e" * 64


def _trained_member_model() -> object:
    examples = [
        MemberVoteExample(member_id="M1", signals={"party": 1.0, "sector": 0.5}, is_yea=True),
        MemberVoteExample(member_id="M1", signals={"party": -1.0, "sector": 0.2}, is_yea=False),
        MemberVoteExample(member_id="M2", signals={"party": 1.0, "sector": -0.5}, is_yea=True),
    ]
    return train_per_member_model(examples, pooling_penalty=1.0)


def test_per_member_model_predict_is_deterministic() -> None:
    model = _trained_member_model()
    signals = {"party": 1.0, "sector": 0.5}
    first = model.predict_probability("M1", signals)  # type: ignore[attr-defined]
    second = model.predict_probability("M1", signals)  # type: ignore[attr-defined]
    assert first == second


def test_transformer_forward_is_deterministic() -> None:
    rng = np.random.default_rng(0)
    params = init_vote_transformer(d_model=8, num_heads=2, d_hidden=16, rng=rng)
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    first, _ = vote_transformer_forward(politician, context, params)
    second, _ = vote_transformer_forward(politician, context, params)
    assert first == second


def _linear_score(coefficients: Mapping[str, float]) -> object:
    def score(signals: Mapping[str, float]) -> float:
        raw = sum(coefficients[name] * signals.get(name, 0.0) for name in coefficients)
        return 1.0 / (1.0 + math.exp(-raw))

    return score


def _assemble(signals: Mapping[str, float]) -> object:
    return assemble_served_prediction(
        canonical_person_id="person:c000003",
        canonical_bill_id="bill:hr1",
        model_name="per_member_signal_model",
        known_at=date(2025, 2, 1),
        ensemble_probabilities=[0.8, 0.78, 0.82],
        conformal_threshold=0.9,
        confidence_level=0.9,
        score_fn=_linear_score({"party": 3.0, "sector": 0.5}),  # type: ignore[arg-type]
        signals=signals,
        signal_evidence=[
            SignalEvidence(
                signal_name="party",
                label="prior vote",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256=_SHA,
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
            SignalEvidence(
                signal_name="sector",
                label="sector exposure",
                source_url="https://www.fec.gov/x",
                content_sha256=_SHA,
                retrieved_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            ),
        ],
        llm_explanation="Party alignment drives this prediction.",
    )


def test_assembled_prediction_renders_identically_for_identical_inputs() -> None:
    signals = {"party": 1.0, "sector": 1.0}
    first = _assemble(signals)
    second = _assemble(signals)
    # Byte-identical serialization: a re-render of the same inputs is reproducible.
    assert first.model_dump(mode="json") == second.model_dump(mode="json")  # type: ignore[attr-defined]


def test_correction_changes_output_but_re_render_stays_deterministic() -> None:
    original = _assemble({"party": 1.0, "sector": 1.0})
    # A correction flips the party-alignment signal.
    corrected_first = _assemble({"party": -1.0, "sector": 1.0})
    corrected_second = _assemble({"party": -1.0, "sector": 1.0})

    # The correction changes the rendered prediction (here, the influence-driven
    # counterfactual and evidence ordering)...
    assert (
        corrected_first.model_dump(mode="json")  # type: ignore[attr-defined]
        != original.model_dump(mode="json")  # type: ignore[attr-defined]
    )
    assert corrected_first.counterfactual != original.counterfactual  # type: ignore[attr-defined]
    # ...and re-rendering the corrected inputs is itself deterministic.
    assert (
        corrected_first.model_dump(mode="json")  # type: ignore[attr-defined]
        == corrected_second.model_dump(mode="json")  # type: ignore[attr-defined]
    )
