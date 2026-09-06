"""Tests for the served-prediction assembler.

This is the per-prediction integration OVERALL_GOAL.md describes: given a
model's scoring function, its signals, and the source behind each signal, it
emits the full ServedPrediction -- calibrated probability, ensemble uncertainty
interval, conformal set, top-5 cited evidence anchors ordered by influence, the
LLM explanation, and a *computed* "what-would-change-this" counterfactual.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime

from src.prediction.prediction_assembly import SignalEvidence, assemble_served_prediction

_SHA = "d" * 64


def _linear_score(coefficients: Mapping[str, float]) -> object:
    def score(signals: Mapping[str, float]) -> float:
        raw = sum(coefficients[name] * signals.get(name, 0.0) for name in coefficients)
        return 1.0 / (1.0 + math.exp(-raw))

    return score


def _evidence(signal_name: str, url: str) -> SignalEvidence:
    return SignalEvidence(
        signal_name=signal_name,
        label=f"evidence for {signal_name}",
        source_url=url,
        content_sha256=_SHA,
        retrieved_at=datetime(2025, 1, 10, tzinfo=UTC),
    )


def _assemble() -> object:
    score = _linear_score({"party": 3.0, "sector": 0.5, "donor": -1.0})
    signals = {"party": 1.0, "sector": 1.0, "donor": 1.0}
    return assemble_served_prediction(
        canonical_person_id="person:c000003",
        canonical_bill_id="bill:hr1",
        model_name="per_member_signal_model",
        known_at=date(2025, 2, 1),
        ensemble_probabilities=[0.82, 0.79, 0.85],
        conformal_threshold=0.9,
        confidence_level=0.9,
        score_fn=score,  # type: ignore[arg-type]
        signals=signals,
        signal_evidence=[
            _evidence("party", "https://clerk.house.gov/Votes/1"),
            _evidence("sector", "https://www.fec.gov/x"),
            _evidence("donor", "https://www.fec.gov/y"),
        ],
        llm_explanation="Strong party alignment on this bill.",
    )


def test_assembled_prediction_has_calibrated_point_and_interval() -> None:
    served = _assemble()
    assert served.canonical_person_id == "person:c000003"  # type: ignore[attr-defined]
    assert served.probability_yea == sum([0.82, 0.79, 0.85]) / 3  # type: ignore[attr-defined]
    assert served.uncertainty.interval_lower <= served.probability_yea  # type: ignore[attr-defined]
    assert served.probability_yea <= served.uncertainty.interval_upper  # type: ignore[attr-defined]


def test_evidence_is_ordered_by_signal_influence() -> None:
    served = _assemble()
    # 'party' (coef 3.0) is the strongest driver, so its anchor leads.
    assert served.evidence_anchors[0].label == "evidence for party"  # type: ignore[attr-defined]
    magnitudes = [abs(a.contribution) for a in served.evidence_anchors]  # type: ignore[attr-defined]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_counterfactual_is_computed_and_names_the_strongest_driver() -> None:
    served = _assemble()
    assert "party" in served.counterfactual  # type: ignore[attr-defined]
    assert "%" in served.counterfactual  # type: ignore[attr-defined]


def test_assembler_caps_evidence_at_five() -> None:
    score = _linear_score({f"s{i}": float(i + 1) for i in range(8)})
    signals = {f"s{i}": 1.0 for i in range(8)}
    served = assemble_served_prediction(
        canonical_person_id="p",
        canonical_bill_id="b",
        model_name="m",
        known_at=date(2025, 2, 1),
        ensemble_probabilities=[0.6],
        conformal_threshold=0.9,
        confidence_level=0.9,
        score_fn=score,  # type: ignore[arg-type]
        signals=signals,
        signal_evidence=[_evidence(f"s{i}", f"https://example.gov/{i}") for i in range(8)],
        llm_explanation="x",
    )
    assert len(served.evidence_anchors) == 5  # type: ignore[attr-defined]
    # The strongest-coefficient signal (s7) leads.
    assert served.evidence_anchors[0].label == "evidence for s7"  # type: ignore[attr-defined]
