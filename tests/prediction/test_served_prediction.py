"""Tests for the served prediction contract.

OVERALL_GOAL.md: every served prediction emits a calibrated yea probability,
a calibrated uncertainty interval, the top-5 evidence anchors
(URL + sha256 + timestamp), an LLM explanation, and a
"what-would-change-this" counterfactual -- and never cites evidence newer
than the prediction's information cutoff (`known_at`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.prediction.served_prediction import (
    PredictionEvidenceAnchor,
    ServedPrediction,
    build_served_prediction,
    ensemble_probability_interval,
)

_SHA = "a" * 64


def _anchor(
    *,
    label: str = "Prior energy vote",
    url: str = "https://clerk.house.gov/Votes/2024123",
    contribution: float = 0.4,
    retrieved_at: datetime | None = None,
) -> PredictionEvidenceAnchor:
    return PredictionEvidenceAnchor(
        label=label,
        source_url=url,
        content_sha256=_SHA,
        retrieved_at=retrieved_at or datetime(2025, 1, 15, tzinfo=timezone.utc),
        contribution=contribution,
    )


def _build(**overrides: object) -> ServedPrediction:
    kwargs: dict[str, object] = {
        "canonical_person_id": "person:us/congress/C000003",
        "canonical_bill_id": "bill:us/congress/119/hr/1",
        "model_name": "per_member_signal_model",
        "known_at": date(2025, 2, 1),
        "ensemble_probabilities": [0.82, 0.78, 0.80, 0.85, 0.79],
        "conformal_threshold": 0.9,
        "confidence_level": 0.9,
        "evidence_anchors": [
            _anchor(label="Energy vote", contribution=0.5),
            _anchor(label="Donor receipt", contribution=-0.2, url="https://www.fec.gov/x"),
        ],
        "llm_explanation": "Votes with party on energy and is funded by the sector.",
        "counterfactual": "A leadership whip against the bill would flip this to nay.",
    }
    kwargs.update(overrides)
    return build_served_prediction(**kwargs)  # type: ignore[arg-type]


def test_ensemble_probability_interval_brackets_the_members() -> None:
    lower, upper = ensemble_probability_interval([0.2, 0.5, 0.9, 0.4, 0.6], confidence_level=0.8)
    assert 0.0 <= lower <= upper <= 1.0
    # Trimming the tails keeps the interval inside the observed range.
    assert lower >= 0.2
    assert upper <= 0.9


def test_ensemble_probability_interval_degenerate_for_single_member() -> None:
    assert ensemble_probability_interval([0.73], confidence_level=0.9) == (0.73, 0.73)


def test_build_served_prediction_assembles_calibrated_payload() -> None:
    served = _build()
    assert served.canonical_person_id == "person:us/congress/C000003"
    # Point estimate is the ensemble mean.
    assert served.probability_yea == pytest.approx(sum([0.82, 0.78, 0.80, 0.85, 0.79]) / 5)
    assert served.uncertainty.interval_lower <= served.probability_yea
    assert served.probability_yea <= served.uncertainty.interval_upper
    assert served.uncertainty.confidence_level == 0.9
    # A confident yea keeps "yea" in the conformal set.
    assert "yea" in served.uncertainty.conformal_label_set


def test_build_served_prediction_orders_and_caps_evidence_at_five() -> None:
    anchors = [
        _anchor(label=f"e{index}", contribution=contribution, url=f"https://example.gov/{index}")
        for index, contribution in enumerate([0.1, -0.9, 0.5, 0.05, -0.7, 0.3, 0.2])
    ]
    served = _build(evidence_anchors=anchors)
    assert len(served.evidence_anchors) == 5
    magnitudes = [abs(anchor.contribution) for anchor in served.evidence_anchors]
    assert magnitudes == sorted(magnitudes, reverse=True)
    # The strongest drivers survive the cap; the weakest are dropped.
    assert magnitudes[0] == pytest.approx(0.9)
    assert 0.05 not in magnitudes


def test_served_prediction_rejects_evidence_after_known_at() -> None:
    late = _anchor(retrieved_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
    with pytest.raises(ValidationError, match="known_at"):
        _build(evidence_anchors=[late])


def test_served_prediction_requires_at_least_one_evidence_anchor() -> None:
    with pytest.raises(ValidationError):
        _build(evidence_anchors=[])


def test_served_prediction_requires_sha256_hex_anchor() -> None:
    with pytest.raises(ValidationError):
        PredictionEvidenceAnchor(
            label="bad",
            source_url="https://example.gov/x",
            content_sha256="not-a-hash",
            retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            contribution=0.1,
        )


def test_served_prediction_requires_nonblank_explanation_and_counterfactual() -> None:
    with pytest.raises(ValidationError):
        _build(llm_explanation="   ")
    with pytest.raises(ValidationError):
        _build(counterfactual="")


def test_served_prediction_point_outside_interval_is_rejected() -> None:
    with pytest.raises(ValidationError, match="interval"):
        ServedPrediction(
            canonical_person_id="person:x",
            canonical_bill_id="bill:y",
            model_name="per_member_signal_model",
            known_at=date(2025, 2, 1),
            probability_yea=0.95,
            uncertainty={
                "confidence_level": 0.9,
                "interval_lower": 0.1,
                "interval_upper": 0.5,
                "conformal_label_set": ["nay", "yea"],
            },  # type: ignore[arg-type]
            evidence_anchors=[_anchor()],
            llm_explanation="x",
            counterfactual="y",
        )
