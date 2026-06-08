"""The served prediction contract.

OVERALL_GOAL.md's auditability requirement: every prediction the public read
API emits renders with a calibrated yea probability, a calibrated uncertainty
interval, the top-5 evidence sources that drove it (each with URL + content
sha256 + retrieval timestamp), an LLM explanation, and a
"what-would-change-this" counterfactual.

This module is the typed boundary object for that. It composes the calibrated
probabilities (``calibration.py``), an uncertainty interval derived from the
Bayesian deep ensemble the blueprint calls for (multiple seed probabilities),
and the conformal label set for distribution-free coverage. It enforces the
no-leakage discipline at the serving boundary: no cited evidence may post-date
the prediction's information cutoff (``known_at``).

Pure data + assembly; the FastAPI / Ray Serve transport and Postgres snapshot
store are separate and not built here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from src.prediction.calibration import conformal_label_set

_MAX_EVIDENCE_ANCHORS = 5
_VOTE_OPTIONS = ("nay", "yea")


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


class PredictionEvidenceAnchor(BaseModel, frozen=True):
    """One audited source that drove a prediction (URL + sha256 + timestamp)."""

    label: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    content_sha256: str
    retrieved_at: datetime
    contribution: float

    @field_validator("label", "source_url", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _anchor_is_well_formed(self) -> Self:
        if not self.label:
            raise ValueError("evidence anchor label must not be blank")
        if not self.source_url:
            raise ValueError("evidence anchor source_url must not be blank")
        if not _is_sha256_hex(self.content_sha256):
            raise ValueError("evidence anchor content_sha256 must be 64 hex characters")
        return self


class PredictionUncertainty(BaseModel):
    """A calibrated uncertainty interval plus its distribution-free conformal set."""

    confidence_level: float = Field(gt=0.0, lt=1.0)
    interval_lower: float = Field(ge=0.0, le=1.0)
    interval_upper: float = Field(ge=0.0, le=1.0)
    conformal_label_set: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _uncertainty_is_consistent(self) -> Self:
        if self.interval_lower > self.interval_upper:
            raise ValueError("interval_lower must not exceed interval_upper")
        unknown = [option for option in self.conformal_label_set if option not in _VOTE_OPTIONS]
        if unknown:
            raise ValueError(f"conformal_label_set has unknown options: {sorted(unknown)}")
        if len(set(self.conformal_label_set)) != len(self.conformal_label_set):
            raise ValueError("conformal_label_set must not repeat options")
        if self.conformal_label_set != sorted(self.conformal_label_set):
            raise ValueError("conformal_label_set must be sorted")
        return self


class ServedPrediction(BaseModel):
    """The audited, calibrated prediction object served per (person, bill) pair."""

    canonical_person_id: str = Field(min_length=1)
    canonical_bill_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    known_at: date
    probability_yea: float = Field(ge=0.0, le=1.0)
    uncertainty: PredictionUncertainty
    evidence_anchors: list[PredictionEvidenceAnchor] = Field(min_length=1)
    llm_explanation: str = Field(min_length=1)
    counterfactual: str = Field(min_length=1)

    @field_validator("llm_explanation", "counterfactual", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _served_prediction_is_well_formed(self) -> Self:
        if not self.llm_explanation:
            raise ValueError("llm_explanation must not be blank")
        if not self.counterfactual:
            raise ValueError("counterfactual must not be blank")
        if len(self.evidence_anchors) > _MAX_EVIDENCE_ANCHORS:
            raise ValueError(f"at most {_MAX_EVIDENCE_ANCHORS} evidence anchors may be served")
        if not (
            self.uncertainty.interval_lower
            <= self.probability_yea
            <= self.uncertainty.interval_upper
        ):
            raise ValueError("probability_yea must lie within the uncertainty interval")
        magnitudes = [abs(anchor.contribution) for anchor in self.evidence_anchors]
        if magnitudes != sorted(magnitudes, reverse=True):
            raise ValueError("evidence_anchors must be ordered by descending |contribution|")
        # No-leakage at the serving boundary: cited evidence cannot post-date the cutoff.
        late = [
            anchor.source_url
            for anchor in self.evidence_anchors
            if anchor.retrieved_at.date() > self.known_at
        ]
        if late:
            raise ValueError(f"evidence retrieved after known_at must not be cited: {late}")
        return self


def ensemble_probability_interval(
    probabilities: Sequence[float],
    *,
    confidence_level: float,
) -> tuple[float, float]:
    """Empirical central interval over a Bayesian deep-ensemble's probabilities.

    Trims ``(1 - confidence_level) / 2`` from each tail of the sorted member
    probabilities. A single-member ensemble yields a degenerate point interval.
    """
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be in (0, 1)")
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    ordered = sorted(probabilities)
    count = len(ordered)
    if count == 1:
        return (ordered[0], ordered[0])
    tail = (1.0 - confidence_level) / 2.0
    lower_index = max(0, math.floor(tail * (count - 1)))
    upper_index = min(count - 1, math.ceil((1.0 - tail) * (count - 1)))
    return (ordered[lower_index], ordered[upper_index])


def build_served_prediction(
    *,
    canonical_person_id: str,
    canonical_bill_id: str,
    model_name: str,
    known_at: date,
    ensemble_probabilities: Sequence[float],
    conformal_threshold: float,
    confidence_level: float,
    evidence_anchors: Sequence[PredictionEvidenceAnchor],
    llm_explanation: str,
    counterfactual: str,
) -> ServedPrediction:
    """Assemble a served prediction from calibrated ensemble outputs and evidence.

    The point estimate is the ensemble mean; the interval is the ensemble's
    central ``confidence_level`` band; the conformal set is derived from the
    point estimate and the held-out conformal threshold. Evidence anchors are
    ordered by descending influence and capped at the top five.
    """
    if not ensemble_probabilities:
        raise ValueError("ensemble_probabilities must not be empty")
    point = sum(ensemble_probabilities) / len(ensemble_probabilities)
    lower, upper = ensemble_probability_interval(
        ensemble_probabilities, confidence_level=confidence_level
    )
    # The point estimate must remain inside the served interval even when the
    # ensemble band is narrower than the mean's rounding (single-member case).
    lower = min(lower, point)
    upper = max(upper, point)
    label_set = sorted(conformal_label_set(point, threshold=conformal_threshold))
    ordered_anchors = sorted(
        evidence_anchors, key=lambda anchor: abs(anchor.contribution), reverse=True
    )[:_MAX_EVIDENCE_ANCHORS]
    return ServedPrediction(
        canonical_person_id=canonical_person_id,
        canonical_bill_id=canonical_bill_id,
        model_name=model_name,
        known_at=known_at,
        probability_yea=point,
        uncertainty=PredictionUncertainty(
            confidence_level=confidence_level,
            interval_lower=lower,
            interval_upper=upper,
            conformal_label_set=label_set,
        ),
        evidence_anchors=list(ordered_anchors),
        llm_explanation=llm_explanation,
        counterfactual=counterfactual,
    )
