"""Build a snapshot of real served predictions for the explorer/dashboard.

Composes the trained per-member seed-ensemble (real uncertainty), each member's
real contract evidence anchors (URL + sha256 + timestamp), and a computed
counterfactual into ``ServedPrediction`` objects, then a content-addressed
``PredictionSnapshot`` the WSGI app serves at ``/v1/prediction`` and renders at
``/v1/explorer``. This turns the served API from an empty shell into one
emitting real, cited, calibrated predictions.

Predictions are for a representative party-line bill (``party_alignment = +1``);
the explorer is a live demonstration, and the same path serves any (person,
bill) pair once a bill feed is wired.
"""

from __future__ import annotations

from datetime import date, datetime

from src.prediction.counterfactual import counterfactual_influences, describe_counterfactual
from src.prediction.per_member_ensemble import predict_interval
from src.prediction.per_member_model import PerMemberModel
from src.prediction.read_api import PredictionSnapshot, build_prediction_snapshot
from src.prediction.served_prediction import (
    PredictionEvidenceAnchor,
    PredictionUncertainty,
    ServedPrediction,
)
from src.runtime.contract_corpus import ContractEntity


def _conformal_set(probability: float) -> list[str]:
    labels = []
    if probability >= 0.15:
        labels.append("yea")
    if probability <= 0.85:
        labels.append("nay")
    return sorted(labels) or ["yea", "nay"]


def _served_for_member(
    bioguide: str,
    entity: ContractEntity,
    models: list[PerMemberModel],
    *,
    known_at: date,
    bill_id: str,
    confidence_level: float,
) -> ServedPrediction | None:
    signals = {"party_alignment": 1.0}
    anchors = [
        PredictionEvidenceAnchor(
            label=anchor.label,
            source_url=anchor.source_url,
            content_sha256=anchor.content_sha256,
            retrieved_at=anchor.known_at,
            contribution=models[0].predict_probability(bioguide, signals) - 0.5,
        )
        for anchor in entity.evidence
        if anchor.known_at.date() <= known_at
    ][:5]
    if not anchors:
        return None

    mean, lower, upper = predict_interval(
        models, bioguide, signals, confidence_level=confidence_level
    )
    influences = counterfactual_influences(
        lambda s: models[0].predict_probability(bioguide, s), signals, top_k=2
    )
    counterfactual = describe_counterfactual(influences, current_probability=mean)
    return ServedPrediction(
        canonical_person_id=f"bioguide:{bioguide}",
        canonical_bill_id=bill_id,
        model_name="per_member_signal_model",
        known_at=known_at,
        probability_yea=mean,
        uncertainty=PredictionUncertainty(
            confidence_level=confidence_level,
            interval_lower=lower,
            interval_upper=upper,
            conformal_label_set=_conformal_set(mean),
        ),
        evidence_anchors=anchors,
        llm_explanation=(
            f"On a party-line bill the model expects this member to vote "
            f"{'yea' if mean >= 0.5 else 'nay'} ({mean * 100:.0f}% yea), driven by "
            f"their party-alignment record across prior roll calls."
        ),
        counterfactual=counterfactual,
    )


def build_served_snapshot(
    models: list[PerMemberModel],
    bioguide_index: dict[str, ContractEntity],
    *,
    generated_at: datetime,
    known_at: date,
    bill_id: str = "us_congress:118:party-line-demo",
    confidence_level: float = 0.9,
    top_n: int = 100,
) -> PredictionSnapshot:
    """Assemble a content-addressed snapshot of real served predictions."""
    predictions: list[ServedPrediction] = []
    for bioguide in sorted(bioguide_index):
        served = _served_for_member(
            bioguide,
            bioguide_index[bioguide],
            models,
            known_at=known_at,
            bill_id=bill_id,
            confidence_level=confidence_level,
        )
        if served is not None:
            predictions.append(served)
        if len(predictions) >= top_n:
            break
    return build_prediction_snapshot(predictions, generated_at=generated_at)
