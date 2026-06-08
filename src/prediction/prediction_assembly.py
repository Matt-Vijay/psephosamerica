"""Served-prediction assembler.

The per-prediction integration point: given a model's scoring function, its
input signals, and the audited source behind each signal, this emits the full
``ServedPrediction`` OVERALL_GOAL.md specifies -- calibrated yea probability
(the ensemble mean), the uncertainty interval (the ensemble band), the
conformal label set, the top-5 cited evidence anchors **ordered by each
signal's influence on this prediction**, the LLM explanation, and a *computed*
"what-would-change-this" counterfactual.

It is the seam that ties the model (signal influences), the uncertainty
(``deep_ensemble`` probabilities + ``calibration`` conformal threshold), the
counterfactual generator, and the served contract into one audited object. The
caller supplies already-calibrated ensemble probabilities so calibration policy
stays at the edge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from src.prediction.counterfactual import (
    ScoreFn,
    counterfactual_influences,
    describe_counterfactual,
)
from src.prediction.served_prediction import (
    PredictionEvidenceAnchor,
    ServedPrediction,
    build_served_prediction,
)

_MAX_EVIDENCE_ANCHORS = 5


@dataclass(frozen=True)
class SignalEvidence:
    """The audited source backing one model signal."""

    signal_name: str
    label: str
    source_url: str
    content_sha256: str
    retrieved_at: datetime


def assemble_served_prediction(
    *,
    canonical_person_id: str,
    canonical_bill_id: str,
    model_name: str,
    known_at: date,
    ensemble_probabilities: Sequence[float],
    conformal_threshold: float,
    confidence_level: float,
    score_fn: ScoreFn,
    signals: Mapping[str, float],
    signal_evidence: Sequence[SignalEvidence],
    llm_explanation: str,
) -> ServedPrediction:
    """Compose model outputs, uncertainty, evidence, and a counterfactual into a served object.

    Each signal's influence (the probability swing when it is removed) both
    drives the counterfactual narrative and orders the cited evidence anchors,
    so the top-5 evidence are the signals that most moved this prediction.
    """
    influence_count = max(1, len(signals))
    influences = counterfactual_influences(score_fn, signals, top_k=influence_count)
    delta_by_signal = {item.signal_name: item.probability_delta for item in influences}

    point = sum(ensemble_probabilities) / len(ensemble_probabilities)
    counterfactual = describe_counterfactual(
        influences[:_MAX_EVIDENCE_ANCHORS], current_probability=point
    )

    anchors = [
        PredictionEvidenceAnchor(
            label=evidence.label,
            source_url=evidence.source_url,
            content_sha256=evidence.content_sha256,
            retrieved_at=evidence.retrieved_at,
            contribution=delta_by_signal.get(evidence.signal_name, 0.0),
        )
        for evidence in signal_evidence
    ]

    return build_served_prediction(
        canonical_person_id=canonical_person_id,
        canonical_bill_id=canonical_bill_id,
        model_name=model_name,
        known_at=known_at,
        ensemble_probabilities=ensemble_probabilities,
        conformal_threshold=conformal_threshold,
        confidence_level=confidence_level,
        evidence_anchors=anchors,
        llm_explanation=llm_explanation,
        counterfactual=counterfactual,
    )
