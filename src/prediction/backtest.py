from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from src.evidence.source_anchor_policy import (
    describe_missing_source_anchor_urls,
    is_official_source_url,
)
from src.export.contracts import SourceAnchor
from src.ingest.congress.senate_votes import roll_call_url as senate_roll_call_url
from src.ontology.contracts import OntologyEdgePayload
from src.ontology.member_features import build_member_feature_slices, slice_member_ontology_edges
from src.prediction.llm_semantics import BillSemanticPayload
from src.prediction.source_anchors import (
    dedupe_prediction_source_anchors,
    describe_missing_legislative_source_context,
)

VoteOption = Literal["yea", "nay", "present", "not_voting", "paired", "abstain"]
_VOTE_OPTION_ORDER: tuple[VoteOption, ...] = (
    "yea",
    "nay",
    "present",
    "not_voting",
    "paired",
    "abstain",
)
_VOTE_OPTIONS: set[str] = {"yea", "nay", "present", "not_voting", "paired", "abstain"}
_BILL_REF_RE = re.compile(
    r"\b(?P<type>H\.?\s*R\.?|S\.?|H\.?\s*J\.?\s*RES\.?|S\.?\s*J\.?\s*RES\.?|"
    r"H\.?\s*CON\.?\s*RES\.?|S\.?\s*CON\.?\s*RES\.?|H\.?\s*RES\.?|S\.?\s*RES\.?)"
    r"\s*(?P<number>\d+)\b",
    re.IGNORECASE,
)
_SECTOR_KEYWORDS = {
    "agriculture": ("agriculture", "farm", "crop", "food"),
    "defense": ("defense", "military", "armed services", "national security"),
    "energy": ("energy", "oil", "gas", "nuclear", "permitting", "pipeline", "utility"),
    "finance": ("bank", "finance", "financial", "capital", "securities", "tax"),
    "health": ("health", "medicare", "medicaid", "drug", "hospital", "pharma"),
    "technology": ("technology", "ai", "artificial intelligence", "data", "cyber", "chip"),
    "transportation": ("transportation", "rail", "aviation", "highway", "transit"),
}
_EDGE_AVAILABILITY_DATE_KEYS = (
    "transaction_date",
    "contribution_date",
    "start_date",
    "committee_start_date",
    "filing_date",
    "filed_at",
    "report_date",
    "statement_date",
    "effective_date",
    "as_of_date",
    "date",
)
REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES = (
    "bill_coalition_signal",
    "bill_issue_sector_overlap",
    "committee_jurisdiction_overlap",
    "contribution_sector_overlap",
    "financial_sector_overlap",
    "holding_recency",
    "member_vote_history",
    "party_chamber_baseline",
    "source_anchor_strength",
    "sponsor_cosponsor_alignment",
    "transaction_recency",
)
OPTIONAL_ONTOLOGY_FEATURE_SIGNAL_NAMES = (
    "donation_industry_alignment",
    "public_statement_alignment",
)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _float_matches(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(actual - expected) <= 1e-9


def _strip_optional_string(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def _reject_boolean_int(value: object, field_name: str) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _ensure_sorted_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")
    if values != sorted(set(values)):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")


def _log_loss(probability_yea: float, actual_yea: float) -> float:
    probability = max(1e-15, min(1.0 - 1e-15, probability_yea))
    return -(actual_yea * math.log(probability) + (1.0 - actual_yea) * math.log(1.0 - probability))


def _prediction_vote_source_type(jurisdiction_id: str | None) -> str:
    if jurisdiction_id in {None, "", "us_congress"}:
        return "vote_event"
    return "legislative_vote"


class PredictionBacktestPredictionPayload(BaseModel):
    """One member-vote prediction and realized label."""

    vote_event_id: int
    event_key: str
    jurisdiction_id: str = "us_congress"
    legislative_body_id: str | None = None
    legislative_session_id: str | None = None
    chamber: str
    congress: int
    session_number: int
    roll_call_number: int
    vote_date: date
    question: str
    result: str | None = None
    source_url: str | None = None
    bill_key: str | None = None
    bill_context_key: str | None = None
    member_bioguide_id: str
    member_slug: str | None = None
    member_name: str | None = None
    party: str | None = None
    state: str | None = None
    actual_vote_option: VoteOption
    feature_vote_count: int = Field(ge=0)
    predicted_probability_yea: float | None = Field(default=None, ge=0.0, le=1.0)
    predicted_vote_option: VoteOption | None = None
    predicted_vote_probabilities: dict[VoteOption, float] = Field(default_factory=dict)
    correct: bool | None = None
    brier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    log_loss: float | None = Field(default=None, ge=0.0)
    skipped_reason: str | None = None
    feature_signals: dict[str, float] = Field(default_factory=dict)
    feature_source_anchors: dict[str, list[SourceAnchor]] = Field(default_factory=dict)
    unavailable_signals: list[str] = Field(default_factory=list)

    @field_validator("skipped_reason", mode="before")
    @classmethod
    def strip_skipped_reason(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator(
        "vote_event_id",
        "congress",
        "session_number",
        "roll_call_number",
        "feature_vote_count",
        mode="before",
    )
    @classmethod
    def prediction_identity_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def prediction_shape_matches_skip_status(self) -> Self:
        if self.jurisdiction_id != "us_congress" and not self.legislative_body_id:
            raise ValueError("non-Congress predictions require legislative_body_id")
        if self.jurisdiction_id != "us_congress" and not self.legislative_session_id:
            raise ValueError("non-Congress predictions require legislative_session_id")
        if self.event_key != _event_key(
            jurisdiction_id=self.jurisdiction_id,
            legislative_body_id=str(self.legislative_body_id or f"us_congress_{self.chamber}"),
            legislative_session_id=self.legislative_session_id,
            chamber=self.chamber,
            congress=self.congress,
            session_number=self.session_number,
            roll_call_number=self.roll_call_number,
        ):
            raise ValueError("event_key must match jurisdiction and roll call")
        _ensure_sorted_unique_nonblank(
            self.unavailable_signals,
            field_name="unavailable_signals",
        )
        if self.source_url is not None and not is_official_source_url(
            _prediction_vote_source_type(self.jurisdiction_id),
            self.source_url,
        ):
            raise ValueError("source_url must be an official vote source URL")
        if any(not anchors for anchors in self.feature_source_anchors.values()):
            raise ValueError("feature_source_anchors entries must not be empty")
        orphan_source_signals = sorted(set(self.feature_source_anchors) - set(self.feature_signals))
        if orphan_source_signals:
            raise ValueError(
                "feature_source_anchors keys must be present in feature_signals: "
                + ", ".join(orphan_source_signals)
            )
        for signal_name, anchors in sorted(self.feature_source_anchors.items()):
            missing_source_urls = describe_missing_source_anchor_urls(anchors)
            if missing_source_urls:
                raise ValueError(
                    "feature_source_anchors require official source URLs for "
                    f"{signal_name}: {missing_source_urls}"
                )
            missing_source_context = describe_missing_legislative_source_context(anchors)
            if missing_source_context:
                raise ValueError(
                    "feature_source_anchors require legislative source context for "
                    f"{signal_name}: {missing_source_context}"
                )
        if self.predicted_vote_probabilities:
            probability_sum = sum(self.predicted_vote_probabilities.values())
            if any(
                probability < 0.0 or probability > 1.0
                for probability in self.predicted_vote_probabilities.values()
            ):
                raise ValueError("predicted_vote_probabilities values must be in [0, 1]")
            if abs(probability_sum - 1.0) > 1e-9:
                raise ValueError("predicted_vote_probabilities must sum to 1")
            if self.predicted_probability_yea is not None and not _float_matches(
                self.predicted_probability_yea,
                self.predicted_vote_probabilities.get("yea", 0.0),
            ):
                raise ValueError("predicted_probability_yea must match vote distribution")
            if self.predicted_vote_option is not None:
                expected_option = _predicted_vote_option(self.predicted_vote_probabilities)
                if self.predicted_vote_option != expected_option:
                    raise ValueError("predicted_vote_option must match vote distribution")
        if self.skipped_reason is None:
            if self.predicted_probability_yea is None or self.predicted_vote_option is None:
                raise ValueError("scored predictions require probability and predicted option")
            if not self.predicted_vote_probabilities:
                raise ValueError("scored predictions require vote option probabilities")
            if self.actual_vote_option in {"yea", "nay"}:
                if self.correct is None or self.brier_score is None or self.log_loss is None:
                    raise ValueError(
                        "binary scored predictions require correctness, brier score, and log loss"
                    )
            elif self.correct is None:
                raise ValueError("non-binary scored predictions require correctness")
            return self
        if not self.skipped_reason:
            raise ValueError("skipped predictions require skipped_reason")
        if (
            self.predicted_probability_yea is not None
            or self.predicted_vote_option is not None
            or self.predicted_vote_probabilities
        ):
            raise ValueError("skipped predictions must not carry predicted values")
        if self.correct is not None or self.brier_score is not None or self.log_loss is not None:
            raise ValueError("skipped predictions must not carry evaluation values")
        return self


class PredictionBacktestMetricsPayload(BaseModel):
    """Aggregate evaluation metrics for one backtest run."""

    label_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    brier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    log_loss: float | None = Field(default=None, ge=0.0)

    @field_validator(
        "label_count",
        "evaluated_count",
        "correct_count",
        "skipped_count",
        mode="before",
    )
    @classmethod
    def metric_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def metrics_are_consistent(self) -> Self:
        if self.evaluated_count + self.skipped_count != self.label_count:
            raise ValueError("evaluated_count plus skipped_count must equal label_count")
        expected_accuracy = (
            _rate(self.correct_count, self.evaluated_count) if self.evaluated_count else None
        )
        if not _float_matches(self.accuracy, expected_accuracy):
            raise ValueError("accuracy must match correct_count / evaluated_count")
        return self


class PredictionBacktestPayload(BaseModel):
    """No-leakage vote prediction backtest over a feature cutoff and future label window."""

    model_name: str = "member_vote_rate_baseline"
    feature_cutoff: date
    label_start: date
    label_end: date
    feature_vote_event_count: int = Field(ge=0)
    label_vote_event_count: int = Field(ge=0)
    member_count: int = Field(ge=0)
    metrics: PredictionBacktestMetricsPayload
    predictions: list[PredictionBacktestPredictionPayload] = Field(default_factory=list)

    @field_validator(
        "feature_vote_event_count",
        "label_vote_event_count",
        "member_count",
        mode="before",
    )
    @classmethod
    def backtest_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def backtest_window_is_temporal(self) -> Self:
        if self.feature_cutoff >= self.label_start:
            raise ValueError("feature_cutoff must be before label_start")
        if self.label_start > self.label_end:
            raise ValueError("label_start must be on or before label_end")
        if self.metrics.label_count != len(self.predictions):
            raise ValueError("metrics.label_count must match predictions length")
        if self.label_vote_event_count != len(
            {prediction.vote_event_id for prediction in self.predictions}
        ):
            raise ValueError("label_vote_event_count must match predictions")
        if any(
            prediction.vote_date < self.label_start or prediction.vote_date > self.label_end
            for prediction in self.predictions
        ):
            raise ValueError("predictions must stay inside the label window")
        evaluated_count = sum(
            1 for prediction in self.predictions if prediction.correct is not None
        )
        skipped_count = sum(
            1 for prediction in self.predictions if prediction.skipped_reason is not None
        )
        correct_count = sum(1 for prediction in self.predictions if prediction.correct is True)
        if self.metrics.evaluated_count != evaluated_count:
            raise ValueError("metrics.evaluated_count must match predictions")
        if self.metrics.skipped_count != skipped_count:
            raise ValueError("metrics.skipped_count must match predictions")
        if self.metrics.correct_count != correct_count:
            raise ValueError("metrics.correct_count must match predictions")
        brier_scores = [
            prediction.brier_score
            for prediction in self.predictions
            if prediction.brier_score is not None
        ]
        expected_brier_score = sum(brier_scores) / len(brier_scores) if brier_scores else None
        if not _float_matches(self.metrics.brier_score, expected_brier_score):
            raise ValueError("metrics.brier_score must match predictions")
        log_losses = [
            prediction.log_loss
            for prediction in self.predictions
            if prediction.log_loss is not None
        ]
        expected_log_loss = sum(log_losses) / len(log_losses) if log_losses else None
        if not _float_matches(self.metrics.log_loss, expected_log_loss):
            raise ValueError("metrics.log_loss must match predictions")
        return self


def build_vote_baseline_backtest(
    *,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    feature_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> PredictionBacktestPayload:
    """Score future votes with a deterministic pre-cutoff member yea-rate baseline."""
    if feature_cutoff >= label_start:
        raise ValueError("feature_cutoff must be before label_start")
    if label_start > label_end:
        raise ValueError("label_start must be on or before label_end")
    _validate_no_leakage(feature_cutoff, label_start, label_end, feature_rows, label_rows)
    label_vote_event_count = _label_vote_event_count(label_rows)

    features = _member_feature_index(feature_rows)
    predictions = [
        _prediction_for_label(
            row, _first_member_feature_index_value(features, row), row_index=index
        )
        for index, row in enumerate(label_rows)
    ]
    evaluated = [item for item in predictions if item.correct is not None]
    brier_scores = [item.brier_score for item in evaluated if item.brier_score is not None]
    log_losses = [item.log_loss for item in evaluated if item.log_loss is not None]
    metrics = PredictionBacktestMetricsPayload(
        label_count=len(predictions),
        evaluated_count=len(evaluated),
        correct_count=sum(1 for item in evaluated if item.correct),
        skipped_count=sum(1 for item in predictions if item.skipped_reason is not None),
        accuracy=_rate(sum(1 for item in evaluated if item.correct), len(evaluated))
        if evaluated
        else None,
        brier_score=sum(brier_scores) / len(brier_scores) if brier_scores else None,
        log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
    )
    return PredictionBacktestPayload(
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_vote_event_count=max(
            (_int_value(row.get("vote_event_count")) for row in feature_rows), default=0
        ),
        label_vote_event_count=label_vote_event_count,
        member_count=_member_feature_count(feature_rows),
        metrics=metrics,
        predictions=predictions,
    )


def build_vote_ontology_backtest(
    *,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    feature_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
    ontology_edges: list[OntologyEdgePayload],
    bill_signal_rows: list[dict[str, Any]],
    bill_semantics: list[BillSemanticPayload] | None = None,
    contribution_signal_rows: list[dict[str, Any]] | None = None,
    statement_signal_rows: list[dict[str, Any]] | None = None,
) -> PredictionBacktestPayload:
    """Score future votes with vote history plus source-backed ontology/product signals."""
    if feature_cutoff >= label_start:
        raise ValueError("feature_cutoff must be before label_start")
    if label_start > label_end:
        raise ValueError("label_start must be on or before label_end")
    _validate_no_leakage(feature_cutoff, label_start, label_end, feature_rows, label_rows)
    label_vote_event_count = _label_vote_event_count(label_rows)

    features = _member_feature_index(feature_rows)
    cutoff_ontology_edges = _ontology_edges_available_at_or_before(
        ontology_edges,
        feature_cutoff,
    )
    ontology_features = build_member_feature_slices(
        feature_cutoff.isoformat(),
        cutoff_ontology_edges,
    )
    bill_signals = _bill_signal_index(bill_signal_rows, feature_cutoff)
    bill_semantics_by_key = {
        semantic.bill_key: semantic
        for semantic in bill_semantics or []
        if _bill_semantic_available_at_or_before(semantic, feature_cutoff)
    }
    party_baselines = _party_chamber_baselines(feature_rows)
    transaction_recency = _transaction_recency_index(cutoff_ontology_edges, feature_cutoff)
    holding_recency = _holding_recency_index(cutoff_ontology_edges, feature_cutoff)
    contribution_rows = contribution_signal_rows or []
    statement_rows = statement_signal_rows or []
    feature_source_anchors = _feature_source_anchor_index(cutoff_ontology_edges)
    feature_source_anchors = _merge_feature_source_anchor_indexes(
        feature_source_anchors,
        _signal_row_source_anchor_index(
            contribution_rows,
            signal_name="donation_industry_alignment",
            feature_cutoff=feature_cutoff,
            date_keys=("contribution_date", "date", "as_of_date"),
        ),
    )
    feature_source_anchors = _merge_feature_source_anchor_indexes(
        feature_source_anchors,
        _signal_row_source_anchor_index(
            statement_rows,
            signal_name="public_statement_alignment",
            feature_cutoff=feature_cutoff,
            date_keys=("statement_date", "date", "as_of_date"),
        ),
    )
    contribution_signals = _member_sector_signal_index(
        contribution_rows,
        feature_cutoff=feature_cutoff,
        date_keys=("contribution_date", "date", "as_of_date"),
    )
    statement_signals = _member_sector_signal_index(
        statement_rows,
        feature_cutoff=feature_cutoff,
        date_keys=("statement_date", "date", "as_of_date"),
    )
    predictions = [
        _ontology_prediction_for_label(
            row,
            _first_member_feature_index_value(features, row),
            row_index=index,
            party_baselines=party_baselines,
            ontology_features=_first_member_index_value(ontology_features, row),
            bill_signals=bill_signals,
            bill_semantics=bill_semantics_by_key,
            contribution_signals=_merge_member_float_indexes(contribution_signals, row),
            statement_signals=_merge_member_float_indexes(statement_signals, row),
            transaction_recency=_merge_member_float_indexes(transaction_recency, row),
            holding_recency=_merge_member_float_indexes(holding_recency, row),
            source_anchors_by_signal=_merge_member_signal_source_anchor_lookups(
                feature_source_anchors,
                row,
            ),
            feature_cutoff=feature_cutoff,
        )
        for index, row in enumerate(label_rows)
    ]
    evaluated = [item for item in predictions if item.correct is not None]
    brier_scores = [item.brier_score for item in evaluated if item.brier_score is not None]
    log_losses = [item.log_loss for item in evaluated if item.log_loss is not None]
    return PredictionBacktestPayload(
        model_name="ontology_signal_model",
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_vote_event_count=max(
            (_int_value(row.get("vote_event_count")) for row in feature_rows), default=0
        ),
        label_vote_event_count=label_vote_event_count,
        member_count=_member_feature_count(feature_rows),
        metrics=PredictionBacktestMetricsPayload(
            label_count=len(predictions),
            evaluated_count=len(evaluated),
            correct_count=sum(1 for item in evaluated if item.correct),
            skipped_count=sum(1 for item in predictions if item.skipped_reason is not None),
            accuracy=_rate(sum(1 for item in evaluated if item.correct), len(evaluated))
            if evaluated
            else None,
            brier_score=sum(brier_scores) / len(brier_scores) if brier_scores else None,
            log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
        ),
        predictions=predictions,
    )


def _validate_no_leakage(
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    feature_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> None:
    for row in feature_rows:
        latest_vote_date = row.get("latest_vote_date")
        if latest_vote_date is not None and latest_vote_date > feature_cutoff:
            raise ValueError("feature rows must not include votes after cutoff")
    for row in label_rows:
        vote_date = row["vote_date"]
        if vote_date < label_start or vote_date > label_end:
            raise ValueError("label rows must stay inside the evaluation window")


def _prediction_for_label(
    row: dict[str, Any],
    feature: dict[str, Any] | None,
    *,
    row_index: int,
) -> PredictionBacktestPredictionPayload:
    vote_count = _int_value(feature.get("vote_count")) if feature is not None else 0
    actual = _vote_option(row["vote_option"])
    base = _base_prediction_fields(row, vote_count, row_index=row_index)
    if feature is None or vote_count == 0:
        return PredictionBacktestPredictionPayload(
            **base,
            actual_vote_option=actual,
            skipped_reason="missing_pre_cutoff_vote_history",
        )

    distribution = _historical_vote_distribution(feature)
    probability_yea = distribution["yea"]
    predicted = _predicted_vote_option(distribution)
    actual_yea = 1.0 if actual == "yea" else 0.0
    brier_score = (probability_yea - actual_yea) ** 2 if actual in {"yea", "nay"} else None
    log_loss = _log_loss(probability_yea, actual_yea) if actual in {"yea", "nay"} else None
    return PredictionBacktestPredictionPayload(
        **base,
        actual_vote_option=actual,
        predicted_probability_yea=probability_yea,
        predicted_vote_option=predicted,
        predicted_vote_probabilities=distribution,
        correct=predicted == actual,
        brier_score=brier_score,
        log_loss=log_loss,
    )


def _ontology_prediction_for_label(
    row: dict[str, Any],
    feature: dict[str, Any] | None,
    *,
    row_index: int,
    party_baselines: dict[tuple[str, str], float],
    ontology_features: Any | None,
    bill_signals: dict[tuple[str, str | None, str | None, int, str, int], dict[str, Any]],
    bill_semantics: dict[str, BillSemanticPayload],
    contribution_signals: dict[str, float],
    statement_signals: dict[str, float],
    transaction_recency: dict[str, float],
    holding_recency: dict[str, float],
    source_anchors_by_signal: dict[str, dict[str, list[SourceAnchor]]],
    feature_cutoff: date,
) -> PredictionBacktestPredictionPayload:
    base_vote_count = _int_value(feature.get("vote_count")) if feature is not None else 0
    actual = _vote_option(row["vote_option"])
    base = _base_prediction_fields(row, base_vote_count, row_index=row_index)
    if feature is None or base_vote_count == 0:
        return PredictionBacktestPredictionPayload(
            **base,
            actual_vote_option=actual,
            skipped_reason="missing_pre_cutoff_vote_history",
            unavailable_signals=sorted(
                [
                    "member_vote_history",
                    "donation_industry_alignment",
                    "public_statement_alignment",
                ]
            ),
        )
    bill_ref = _bill_ref_from_label(row)
    bill = _bill_signal_for_label(row, bill_ref, bill_signals)
    semantic = _bill_semantic_for_label(row, bill_ref, bill_semantics)
    bill_sectors = _bill_sectors(bill, semantic)
    member_sectors = _member_sector_stats(ontology_features)
    signals, unavailable = _ontology_feature_signals(
        row,
        feature,
        bill=bill,
        semantic=semantic,
        bill_sectors=bill_sectors,
        member_sectors=member_sectors,
        party_baselines=party_baselines,
        contribution_signals=contribution_signals,
        statement_signals=statement_signals,
        transaction_recency=transaction_recency,
        holding_recency=holding_recency,
        feature_cutoff=feature_cutoff,
    )
    sources = _ontology_feature_source_anchors(
        row=row,
        feature=feature,
        bill=bill,
        semantic=semantic,
        bill_sectors=bill_sectors,
        source_anchors_by_signal=source_anchors_by_signal,
        signals=signals,
    )
    binary_probability_yea = _weighted_signal_probability(signals)
    distribution = _distribution_with_binary_probability(
        _historical_vote_distribution(feature),
        probability_yea=binary_probability_yea,
    )
    probability_yea = distribution["yea"]
    predicted = _predicted_vote_option(distribution)
    actual_yea = 1.0 if actual == "yea" else 0.0
    brier_score = (probability_yea - actual_yea) ** 2 if actual in {"yea", "nay"} else None
    log_loss = _log_loss(probability_yea, actual_yea) if actual in {"yea", "nay"} else None
    return PredictionBacktestPredictionPayload(
        **base,
        actual_vote_option=actual,
        predicted_probability_yea=probability_yea,
        predicted_vote_option=predicted,
        predicted_vote_probabilities=distribution,
        correct=predicted == actual,
        feature_signals=signals,
        feature_source_anchors=sources,
        unavailable_signals=unavailable,
        brier_score=brier_score,
        log_loss=log_loss,
    )


def _ontology_feature_signals(
    row: dict[str, Any],
    feature: dict[str, Any],
    *,
    bill: dict[str, Any] | None,
    semantic: BillSemanticPayload | None,
    bill_sectors: set[str],
    member_sectors: dict[str, dict[str, float]],
    party_baselines: dict[tuple[str, str], float],
    contribution_signals: dict[str, float],
    statement_signals: dict[str, float],
    transaction_recency: dict[str, float],
    holding_recency: dict[str, float],
    feature_cutoff: date,
) -> tuple[dict[str, float], list[str]]:
    member_vote_rate = _rate(
        _int_value(feature.get("yea_count")), _int_value(feature["vote_count"])
    )
    chamber = str(feature.get("chamber") or row.get("chamber") or "")
    party = str(feature.get("party") or row.get("party") or "")
    party_baseline = party_baselines.get((chamber, party), member_vote_rate)
    sector_overlap = _sector_overlap_strength(bill_sectors, member_sectors)
    signals = {
        "member_vote_history": member_vote_rate,
        "party_chamber_baseline": party_baseline,
        "sponsor_cosponsor_alignment": _sponsor_alignment(row, bill, party=party),
        "bill_coalition_signal": _bill_coalition_signal(semantic),
        "bill_issue_sector_overlap": 1.0 if bill_sectors else 0.0,
        "committee_jurisdiction_overlap": sector_overlap["committee"],
        "financial_sector_overlap": max(sector_overlap["holding"], sector_overlap["transaction"]),
        "contribution_sector_overlap": sector_overlap["contribution"],
        "holding_recency": _holding_recency_signal(
            member_sectors,
            bill_sectors,
            holding_recency,
        ),
        "transaction_recency": _transaction_recency_signal(
            member_sectors,
            bill_sectors,
            transaction_recency,
        ),
        "source_anchor_strength": _source_anchor_strength(member_sectors, bill_sectors),
    }
    expected_required = set(REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES)
    missing_required = sorted(expected_required - set(signals))
    if missing_required:
        raise RuntimeError(
            "ontology scorer omitted required feature signals: " + ", ".join(missing_required)
        )
    unavailable: list[str] = []
    donation_signal = _sector_signal(contribution_signals, bill_sectors)
    if donation_signal is None:
        unavailable.append("donation_industry_alignment")
    else:
        signals["donation_industry_alignment"] = donation_signal
    statement_signal = _sector_signal(statement_signals, bill_sectors)
    if statement_signal is None:
        unavailable.append("public_statement_alignment")
    else:
        signals["public_statement_alignment"] = statement_signal
    return signals, sorted(unavailable)


def _ontology_feature_source_anchors(
    *,
    row: dict[str, Any],
    feature: dict[str, Any],
    bill: dict[str, Any] | None,
    semantic: BillSemanticPayload | None,
    bill_sectors: set[str],
    source_anchors_by_signal: dict[str, dict[str, list[SourceAnchor]]],
    signals: dict[str, float],
) -> dict[str, list[SourceAnchor]]:
    sources: dict[str, list[SourceAnchor]] = {}
    vote_history_source = _vote_history_source_anchor(row, feature)
    if vote_history_source is not None:
        for signal_name in ("member_vote_history", "party_chamber_baseline"):
            if signal_name in signals:
                sources[signal_name] = [vote_history_source]

    bill_sources = _bill_source_anchors(bill, semantic)
    for signal_name in (
        "bill_issue_sector_overlap",
        "sponsor_cosponsor_alignment",
        "bill_coalition_signal",
    ):
        if signal_name in signals and bill_sources:
            sources[signal_name] = bill_sources

    for signal_name in (
        "committee_jurisdiction_overlap",
        "financial_sector_overlap",
        "contribution_sector_overlap",
        "holding_recency",
        "transaction_recency",
        "source_anchor_strength",
        "donation_industry_alignment",
        "public_statement_alignment",
    ):
        if signal_name not in signals:
            continue
        anchors = _source_anchors_for_signal(
            source_anchors_by_signal,
            signal_name,
            bill_sectors,
        )
        if anchors:
            sources[signal_name] = anchors
    return sources


def _vote_history_source_anchor(
    row: dict[str, Any],
    feature: dict[str, Any],
) -> SourceAnchor | None:
    vote_count = _int_value(feature.get("vote_count"))
    if vote_count == 0:
        return None
    latest_vote_date = feature.get("latest_vote_date")
    source_id = _vote_history_source_id(row, feature, latest_vote_date, vote_count)
    source_type = _vote_anchor_source_type(row, feature)
    url = _latest_vote_source_url(feature, source_type)
    if url is None:
        source_type = "vote_history"
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        **_legislative_source_context(_vote_history_source_context(feature), row),
        url=url,
        label=f"{vote_count} pre-cutoff vote records",
    )


def _vote_history_source_context(feature: dict[str, Any]) -> dict[str, Any]:
    if _optional_stripped_string(feature.get("legislative_session_id")) is not None:
        return feature
    latest_session_id = _optional_stripped_string(feature.get("latest_legislative_session_id"))
    if latest_session_id is None:
        return feature
    return {**feature, "legislative_session_id": latest_session_id}


def _vote_history_source_id(
    row: dict[str, Any],
    feature: dict[str, Any],
    latest_vote_date: Any,
    vote_count: int,
) -> str:
    latest_vote_event_key = feature.get("latest_vote_event_key")
    if isinstance(latest_vote_event_key, str) and latest_vote_event_key.strip():
        return latest_vote_event_key.strip()
    return "|".join(
        (
            str(row["bioguide_id"]),
            str(latest_vote_date or "unknown"),
            str(vote_count),
        )
    )


def _vote_anchor_source_type(row: dict[str, Any], feature: dict[str, Any]) -> str:
    if (
        not feature.get("latest_vote_source_url")
        and _canonical_latest_vote_source_url(feature) is None
    ):
        return "vote_history"
    value = row.get("source_type")
    if isinstance(value, str) and value.strip():
        source_type = value.strip()
        if source_type in {"vote_event", "congress_vote", "legislative_vote"}:
            return (
                "vote_event"
                if row.get("jurisdiction_id") in (None, "", "us_congress")
                else "legislative_vote"
            )
        return source_type
    return (
        "vote_event"
        if row.get("jurisdiction_id") in (None, "", "us_congress")
        else "legislative_vote"
    )


def _latest_vote_source_url(feature: dict[str, Any], source_type: str) -> str | None:
    raw_url = feature.get("latest_vote_source_url")
    if isinstance(raw_url, str):
        url = raw_url.strip()
        if url and is_official_source_url(source_type, url):
            return url
    if source_type == "vote_event":
        return _canonical_latest_vote_source_url(feature)
    return None


def _canonical_latest_vote_source_url(feature: dict[str, Any]) -> str | None:
    event_key = feature.get("latest_vote_event_key")
    if not isinstance(event_key, str) or not event_key.strip():
        return None
    parts = event_key.strip().split("-")
    if len(parts) != 4:
        return None
    chamber, congress_raw, session_raw, roll_call_raw = parts
    congress = _plain_positive_int_string(congress_raw)
    session_number = _plain_positive_int_string(session_raw)
    roll_call_number = _plain_positive_int_string(roll_call_raw)
    if congress is None or session_number is None or roll_call_number is None:
        return None
    normalized_chamber = chamber.strip().lower()
    if normalized_chamber == "senate":
        return senate_roll_call_url(congress, session_number, roll_call_number)
    if normalized_chamber != "house":
        return None
    latest_vote_date = _date_from_value(feature.get("latest_vote_date"))
    if latest_vote_date is None:
        return None
    return f"https://clerk.house.gov/Votes/{latest_vote_date.year}{roll_call_number:03d}"


def _feature_source_anchor_index(
    ontology_edges: list[OntologyEdgePayload],
) -> dict[str, dict[str, dict[str, list[SourceAnchor]]]]:
    index: dict[str, dict[str, dict[str, list[SourceAnchor]]]] = {}
    for member_id, edges in slice_member_ontology_edges(ontology_edges).items():
        member_index = index.setdefault(member_id, {})
        for edge in edges:
            sector = _node_id_of_type(edge, "sector")
            if sector is None:
                continue
            for signal_name in _source_signals_for_edge_type(edge.edge_type):
                sector_index = member_index.setdefault(signal_name, {})
                sector_index.setdefault(sector, []).extend(edge.source_anchors)
    return {
        member_id: {
            signal_name: {
                sector: dedupe_prediction_source_anchors(anchors)
                for sector, anchors in sectors.items()
            }
            for signal_name, sectors in signals.items()
        }
        for member_id, signals in index.items()
    }


def _signal_row_source_anchor_index(
    rows: list[dict[str, Any]],
    *,
    signal_name: str,
    feature_cutoff: date,
    date_keys: tuple[str, ...],
) -> dict[str, dict[str, dict[str, list[SourceAnchor]]]]:
    index: dict[str, dict[str, dict[str, list[SourceAnchor]]]] = {}
    for row in rows:
        if not _signal_row_available_at_or_before(row, feature_cutoff, date_keys):
            continue
        anchors = _source_anchors_from_row(row)
        if not anchors:
            continue
        sector = str(row["sector"])
        for member_id in _member_index_keys(row):
            sector_index = index.setdefault(member_id, {}).setdefault(signal_name, {})
            sector_index.setdefault(sector, []).extend(anchors)
    return {
        member_id: {
            signal: {
                sector: dedupe_prediction_source_anchors(anchors)
                for sector, anchors in sectors.items()
            }
            for signal, sectors in signals.items()
        }
        for member_id, signals in index.items()
    }


def _merge_feature_source_anchor_indexes(
    base: dict[str, dict[str, dict[str, list[SourceAnchor]]]],
    extra: dict[str, dict[str, dict[str, list[SourceAnchor]]]],
) -> dict[str, dict[str, dict[str, list[SourceAnchor]]]]:
    merged = {
        member_id: {
            signal_name: {sector: list(anchors) for sector, anchors in sector_index.items()}
            for signal_name, sector_index in signal_index.items()
        }
        for member_id, signal_index in base.items()
    }
    for member_id, signal_index in extra.items():
        member_index = merged.setdefault(member_id, {})
        for signal_name, sector_index in signal_index.items():
            signal_index_target = member_index.setdefault(signal_name, {})
            for sector, anchors in sector_index.items():
                signal_index_target.setdefault(sector, []).extend(anchors)
    return {
        member_id: {
            signal_name: {
                sector: dedupe_prediction_source_anchors(anchors)
                for sector, anchors in sector_index.items()
            }
            for signal_name, sector_index in signal_index.items()
        }
        for member_id, signal_index in merged.items()
    }


def _merge_member_signal_source_anchors(
    base: dict[str, dict[str, list[SourceAnchor]]],
    extra: dict[str, dict[str, list[SourceAnchor]]],
) -> dict[str, dict[str, list[SourceAnchor]]]:
    merged = {
        signal_name: {sector: list(anchors) for sector, anchors in sector_index.items()}
        for signal_name, sector_index in base.items()
    }
    for signal_name, sector_index in extra.items():
        target = merged.setdefault(signal_name, {})
        for sector, anchors in sector_index.items():
            target.setdefault(sector, []).extend(anchors)
    return {
        signal_name: {
            sector: dedupe_prediction_source_anchors(anchors)
            for sector, anchors in sector_index.items()
        }
        for signal_name, sector_index in merged.items()
    }


def _source_anchors_from_row(row: dict[str, Any]) -> list[SourceAnchor]:
    raw = row.get("source_anchors")
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    return dedupe_prediction_source_anchors(
        [_source_anchor_from_row_value(value, row=row) for value in values]
    )


def _source_anchor_from_row_value(value: object, *, row: dict[str, Any]) -> SourceAnchor:
    anchor = value if isinstance(value, SourceAnchor) else SourceAnchor.model_validate(value)
    if anchor.source_type not in {"legislative_bill", "legislative_vote"}:
        return anchor
    updates: dict[str, str] = {}
    for field_name in (
        "jurisdiction_id",
        "legislative_body_id",
        "legislative_session_id",
    ):
        if getattr(anchor, field_name) is not None:
            continue
        row_value = row.get(field_name)
        if isinstance(row_value, str) and row_value.strip():
            updates[field_name] = row_value.strip()
    if not updates:
        return anchor
    return anchor.model_copy(update=updates)


def _source_signals_for_edge_type(edge_type: str) -> tuple[str, ...]:
    if edge_type == "committee_sector_jurisdiction":
        return ("committee_jurisdiction_overlap", "source_anchor_strength")
    if edge_type == "member_sector_holding_exposure":
        return ("financial_sector_overlap", "holding_recency", "source_anchor_strength")
    if edge_type == "member_sector_transaction_exposure":
        return (
            "financial_sector_overlap",
            "transaction_recency",
            "source_anchor_strength",
        )
    if edge_type == "member_sector_contribution_exposure":
        return (
            "contribution_sector_overlap",
            "donation_industry_alignment",
            "source_anchor_strength",
        )
    if edge_type in {
        "member_sector_statement_alignment",
        "member_sector_public_statement_alignment",
    }:
        return ("public_statement_alignment", "source_anchor_strength")
    return ()


def _source_anchors_for_signal(
    source_anchors_by_signal: dict[str, dict[str, list[SourceAnchor]]],
    signal_name: str,
    bill_sectors: set[str],
) -> list[SourceAnchor]:
    sector_sources = source_anchors_by_signal.get(signal_name, {})
    if not sector_sources:
        return []
    if bill_sectors:
        anchors = [
            anchor for sector in sorted(bill_sectors) for anchor in sector_sources.get(sector, [])
        ]
    else:
        anchors = [anchor for sector in sorted(sector_sources) for anchor in sector_sources[sector]]
    return dedupe_prediction_source_anchors(anchors)


def _bill_source_anchors(
    bill: dict[str, Any] | None,
    semantic: BillSemanticPayload | None,
) -> list[SourceAnchor]:
    anchors: list[SourceAnchor] = []
    if bill is not None:
        raw_bill_key = _bill_key_from_ref(
            (
                _int_value(bill["congress"]),
                str(bill["bill_type"]),
                _int_value(bill["bill_number"]),
            )
        )
        bill_key = _bill_context_key_from_bill(bill, raw_bill_key)
        anchors.append(
            SourceAnchor(
                source_type=_bill_anchor_source_type(bill),
                source_id=bill_key,
                **_legislative_source_context(bill),
                url=_bill_anchor_url(bill),
                label=_bill_anchor_label(bill_key, bill),
            )
        )
        anchors.extend(_bill_sponsor_source_anchors(bill_key, bill))
    if semantic is not None:
        anchors.extend(semantic.source_anchors)
    return dedupe_prediction_source_anchors(anchors)


def _bill_sponsor_source_anchors(bill_key: str, bill: dict[str, Any]) -> list[SourceAnchor]:
    anchors: list[SourceAnchor] = []
    source_type = _bill_anchor_source_type(bill)
    for sponsor in bill.get("sponsors", []):
        if not isinstance(sponsor, dict):
            continue
        url = _optional_stripped_string(sponsor.get("sponsor_source_url"))
        if url is None or not is_official_source_url(source_type, url):
            continue
        sponsor_id = _optional_stripped_string(sponsor.get("sponsor_bioguide_id"))
        sponsor_role = _optional_stripped_string(sponsor.get("sponsor_role"))
        if sponsor_id is None or sponsor_role is None:
            continue
        anchors.append(
            SourceAnchor(
                source_type=source_type,
                source_id=f"{bill_key}:sponsor:{sponsor_id}:{sponsor_role}",
                **_legislative_source_context(bill, sponsor),
                url=url,
                label=f"{_bill_anchor_label(bill_key, bill)} sponsor {sponsor_id}",
            )
        )
    return anchors


def _bill_anchor_source_type(bill: dict[str, Any]) -> str:
    return (
        "congress_bill"
        if bill.get("jurisdiction_id") in (None, "us_congress")
        else "legislative_bill"
    )


def _bill_context_key_from_bill(bill: dict[str, Any], bill_key: str) -> str:
    jurisdiction_id = str(bill.get("jurisdiction_id") or "us_congress")
    if jurisdiction_id == "us_congress" or bill_key.startswith(f"{jurisdiction_id}-"):
        return bill_key
    return f"{jurisdiction_id}:{bill_key}"


def _member_context_key(row: dict[str, Any]) -> str:
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    body_id = _optional_stripped_string(row.get("legislative_body_id"))
    if body_id is None and jurisdiction_id == "us_congress":
        body_id = f"us_congress_{str(row.get('chamber') or '').lower()}"
    return f"{jurisdiction_id}:{body_id or ''}:{str(row['bioguide_id'])}"


def _member_feature_index(feature_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for row in feature_rows:
        for key in _member_feature_index_keys(row):
            features[key] = row
    return features


def _member_feature_count(feature_rows: list[dict[str, Any]]) -> int:
    return len({_member_context_key(row) for row in feature_rows})


def _member_lookup_keys(row: dict[str, Any]) -> tuple[str, ...]:
    context_key = _member_context_key(row)
    raw_key = str(row["bioguide_id"])
    if str(row.get("jurisdiction_id") or "us_congress") == "us_congress":
        return (context_key, raw_key)
    return (context_key,)


def _member_index_keys(row: dict[str, Any]) -> tuple[str, ...]:
    context_key = _member_context_key(row)
    if str(row.get("jurisdiction_id") or "us_congress") == "us_congress":
        return (context_key, str(row["bioguide_id"]))
    return (context_key,)


def _member_feature_lookup_keys(row: dict[str, Any]) -> tuple[str, ...]:
    context_key = _member_context_key(row)
    if str(row.get("jurisdiction_id") or "us_congress") == "us_congress":
        return (context_key, str(row["bioguide_id"]))
    return (context_key,)


def _member_feature_index_keys(row: dict[str, Any]) -> tuple[str, ...]:
    context_key = _member_context_key(row)
    if (
        str(row.get("jurisdiction_id") or "us_congress") == "us_congress"
        and _optional_stripped_string(row.get("legislative_body_id")) is None
        and _optional_stripped_string(row.get("chamber")) is None
    ):
        return (context_key, str(row["bioguide_id"]))
    return (context_key,)


def _first_member_index_value(
    index: dict[str, Any],
    row: dict[str, Any],
) -> Any:
    for key in _member_lookup_keys(row):
        value = index.get(key)
        if value is not None:
            return value
    return None


def _first_member_feature_index_value(
    index: dict[str, Any],
    row: dict[str, Any],
) -> Any:
    for key in _member_feature_lookup_keys(row):
        value = index.get(key)
        if value is not None:
            return value
    return None


def _merge_member_float_indexes(
    index: dict[str, dict[str, float]],
    row: dict[str, Any],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for key in _member_lookup_keys(row):
        for sector, value in index.get(key, {}).items():
            merged[sector] = max(merged.get(sector, 0.0), value)
    return merged


def _merge_member_signal_source_anchor_lookups(
    index: dict[str, dict[str, dict[str, list[SourceAnchor]]]],
    row: dict[str, Any],
) -> dict[str, dict[str, list[SourceAnchor]]]:
    merged: dict[str, dict[str, list[SourceAnchor]]] = {}
    for key in _member_lookup_keys(row):
        merged = _merge_member_signal_source_anchors(merged, index.get(key, {}))
    return merged


def _sponsor_lookup_keys(row: dict[str, Any]) -> tuple[str, ...]:
    sponsor_id = _optional_stripped_string(row.get("sponsor_bioguide_id"))
    if sponsor_id is None:
        return ()
    context_key = _member_context_key({**row, "bioguide_id": sponsor_id})
    if str(row.get("jurisdiction_id") or "us_congress") == "us_congress":
        return (context_key, sponsor_id)
    return (context_key,)


def _optional_stripped_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _legislative_source_context(*rows: dict[str, Any] | None) -> dict[str, str]:
    context: dict[str, str] = {}
    for field_name in (
        "jurisdiction_id",
        "legislative_body_id",
        "legislative_session_id",
    ):
        value = next(
            (
                _optional_stripped_string(row.get(field_name))
                for row in rows
                if isinstance(row, dict)
                and _optional_stripped_string(row.get(field_name)) is not None
            ),
            None,
        )
        if value is not None:
            context[field_name] = value
    return context


def _bill_anchor_url(bill: dict[str, Any]) -> str | None:
    source_type = _bill_anchor_source_type(bill)
    raw_url = bill.get("bill_source_url")
    if (
        isinstance(raw_url, str)
        and raw_url.strip()
        and is_official_source_url(
            source_type,
            raw_url,
        )
    ):
        return raw_url.strip()
    if source_type == "congress_bill":
        return _congress_bill_url(
            congress=_int_value(bill["congress"]),
            bill_type=str(bill["bill_type"]),
            bill_number=_int_value(bill["bill_number"]),
        )
    return None


def _congress_bill_url(*, congress: int, bill_type: str, bill_number: int) -> str:
    return (
        f"https://api.congress.gov/v3/bill/{congress}/{bill_type.lower()}/{bill_number}?format=json"
    )


def _bill_anchor_label(bill_key: str, bill: dict[str, Any]) -> str:
    if _bill_anchor_source_type(bill) == "congress_bill":
        return f"Congress.gov bill {bill_key}"
    return f"Legislative bill {bill_key}"


def _node_id_of_type(edge: OntologyEdgePayload, node_type: str) -> str | None:
    for node in (edge.subject, edge.object):
        if node.node_type == node_type:
            return node.node_id
    return None


def _weighted_signal_probability(signals: dict[str, float]) -> float:
    weights = {
        "member_vote_history": 0.34,
        "party_chamber_baseline": 0.18,
        "sponsor_cosponsor_alignment": 0.16,
        "bill_coalition_signal": 0.03,
        "bill_issue_sector_overlap": 0.04,
        "committee_jurisdiction_overlap": 0.08,
        "financial_sector_overlap": 0.08,
        "contribution_sector_overlap": 0.04,
        "holding_recency": 0.04,
        "transaction_recency": 0.06,
        "source_anchor_strength": 0.03,
        "donation_industry_alignment": 0.02,
        "public_statement_alignment": 0.01,
    }
    total_weight = sum(weights[key] for key in signals if key in weights)
    if total_weight == 0:
        return 0.5
    probability = sum(signals[key] * weights[key] for key in signals if key in weights)
    return max(0.0, min(1.0, probability / total_weight))


def _historical_vote_distribution(feature: dict[str, Any]) -> dict[VoteOption, float]:
    vote_count = _int_value(feature.get("vote_count"))
    if vote_count <= 0:
        raise ValueError("vote distribution requires positive vote_count")
    counts = {
        "yea": _int_value(feature.get("yea_count")),
        "nay": _int_value(feature.get("nay_count")),
        "present": _int_value(feature.get("present_count")),
        "not_voting": _int_value(feature.get("not_voting_count")),
        "paired": _int_value(feature.get("paired_count")),
        "abstain": _int_value(feature.get("abstain_count")),
    }
    known_total = sum(counts.values())
    if known_total < vote_count:
        counts["not_voting"] += vote_count - known_total
    denominator = sum(counts.values())
    if denominator <= 0:
        raise ValueError("vote distribution requires at least one observed vote")
    return {cast(VoteOption, option): count / denominator for option, count in counts.items()}


def _distribution_with_binary_probability(
    historical_distribution: dict[VoteOption, float],
    *,
    probability_yea: float,
) -> dict[VoteOption, float]:
    non_binary_mass = sum(
        historical_distribution.get(option, 0.0)
        for option in cast(tuple[VoteOption, ...], ("present", "not_voting", "paired", "abstain"))
    )
    binary_mass = max(0.0, 1.0 - non_binary_mass)
    probability_yea = max(0.0, min(1.0, probability_yea))
    distribution = {
        "yea": binary_mass * probability_yea,
        "nay": binary_mass * (1.0 - probability_yea),
        "present": historical_distribution.get("present", 0.0),
        "not_voting": historical_distribution.get("not_voting", 0.0),
        "paired": historical_distribution.get("paired", 0.0),
        "abstain": historical_distribution.get("abstain", 0.0),
    }
    total = sum(distribution.values())
    if total <= 0:
        return {option: 1.0 / len(_VOTE_OPTIONS) for option in _VOTE_OPTION_ORDER}
    return {
        cast(VoteOption, option): probability / total
        for option, probability in distribution.items()
    }


def _predicted_vote_option(distribution: dict[VoteOption, float]) -> VoteOption:
    return max(_VOTE_OPTION_ORDER, key=lambda option: distribution.get(option, 0.0))


def _party_chamber_baselines(feature_rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for row in feature_rows:
        chamber = row.get("chamber")
        party = row.get("party")
        if chamber is None or party is None:
            continue
        key = (str(chamber), str(party))
        yea, total = totals.get(key, (0, 0))
        totals[key] = (
            yea + _int_value(row.get("yea_count")),
            total + _int_value(row.get("vote_count")),
        )
    return {key: _rate(yea, total) for key, (yea, total) in totals.items()}


def _ontology_edges_available_at_or_before(
    edges: list[OntologyEdgePayload],
    cutoff: date,
) -> list[OntologyEdgePayload]:
    return [edge for edge in edges if _edge_available_at_or_before(edge, cutoff)]


def _edge_available_at_or_before(edge: OntologyEdgePayload, cutoff: date) -> bool:
    if not _edge_has_known_availability(edge):
        return False
    for key in _EDGE_AVAILABILITY_DATE_KEYS:
        value = _date_from_value(edge.attributes.get(key))
        if value is not None and value > cutoff:
            return False
    return True


def _edge_has_known_availability(edge: OntologyEdgePayload) -> bool:
    return any(
        _date_from_value(edge.attributes.get(key)) is not None
        for key in _EDGE_AVAILABILITY_DATE_KEYS
    )


def _bill_signal_index(
    rows: list[dict[str, Any]],
    feature_cutoff: date,
) -> dict[tuple[str, str | None, str | None, int, str, int], dict[str, Any]]:
    index: dict[tuple[str, str | None, str | None, int, str, int], dict[str, Any]] = {}
    for row in rows:
        if not _bill_metadata_available_at_or_before(row, feature_cutoff):
            continue
        key = (
            str(row.get("jurisdiction_id") or "us_congress"),
            _optional_stripped_string(row.get("legislative_body_id")),
            _optional_stripped_string(row.get("legislative_session_id")),
            _int_value(row["congress"]),
            _bill_type(str(row["bill_type"])),
            _int_value(row["bill_number"]),
        )
        entry = index.setdefault(
            key,
            {
                "congress": key[3],
                "bill_type": key[4],
                "bill_number": key[5],
                "jurisdiction_id": row.get("jurisdiction_id"),
                "legislative_body_id": row.get("legislative_body_id"),
                "legislative_session_id": row.get("legislative_session_id"),
                "title": row.get("title"),
                "short_title": row.get("short_title"),
                "bill_source_url": row.get("bill_source_url"),
                "sponsors": [],
            },
        )
        sponsor_date = _sponsor_available_date(row)
        if sponsor_date is None or sponsor_date > feature_cutoff:
            continue
        if row.get("sponsor_bioguide_id") is not None:
            entry["sponsors"].append(row)
    return index


def _bill_signal_for_label(
    row: dict[str, Any],
    bill_ref: tuple[int, str, int] | None,
    bill_signals: dict[tuple[str, str | None, str | None, int, str, int], dict[str, Any]],
) -> dict[str, Any] | None:
    if bill_ref is None:
        return None
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    body_id = _optional_stripped_string(row.get("legislative_body_id"))
    session_id = _optional_stripped_string(row.get("legislative_session_id"))
    candidates = (
        (jurisdiction_id, body_id, session_id, *bill_ref),
        (jurisdiction_id, body_id, None, *bill_ref),
        (jurisdiction_id, None, None, *bill_ref),
    )
    for key in candidates:
        bill = bill_signals.get(key)
        if bill is not None:
            return bill
    return None


def _bill_metadata_available_at_or_before(row: dict[str, Any], feature_cutoff: date) -> bool:
    introduced_date = _date_from_value(row.get("introduced_date"))
    if introduced_date is not None:
        return introduced_date <= feature_cutoff
    latest_action_date = _date_from_value(row.get("latest_action_date"))
    return latest_action_date is not None and latest_action_date <= feature_cutoff


def _sponsor_available_date(row: dict[str, Any]) -> date | None:
    sponsor_date = _date_from_value(row.get("sponsor_date"))
    if sponsor_date is not None:
        return sponsor_date
    sponsor_role = _optional_stripped_string(row.get("sponsor_role"))
    if row.get("is_primary") is True or sponsor_role == "primary":
        return _date_from_value(row.get("introduced_date"))
    return None


def _bill_ref_from_label(row: dict[str, Any]) -> tuple[int, str, int] | None:
    text = " ".join(str(row.get(key) or "") for key in ("question", "result"))
    match = _BILL_REF_RE.search(text)
    if match is None:
        return None
    return (
        _int_value(row["congress"]),
        _bill_type(match.group("type")),
        int(match.group("number")),
    )


def _bill_type(raw: str) -> str:
    normalized = re.sub(r"[^A-Za-z]", "", raw).lower()
    mapping = {
        "hr": "hr",
        "hres": "hres",
        "hjres": "hjres",
        "hconres": "hconres",
        "s": "s",
        "sres": "sres",
        "sjres": "sjres",
        "sconres": "sconres",
    }
    return mapping.get(normalized, normalized)


def _bill_key_from_ref(ref: tuple[int, str, int]) -> str:
    return f"{ref[0]}-{ref[1]}-{ref[2]}"


def _bill_context_key_from_ref(
    row: dict[str, Any],
    ref: tuple[int, str, int] | None,
) -> str | None:
    if ref is None:
        return None
    bill_key = _bill_key_from_ref(ref)
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    if jurisdiction_id == "us_congress" or bill_key.startswith(f"{jurisdiction_id}-"):
        return bill_key
    return f"{jurisdiction_id}:{bill_key}"


def _bill_semantic_for_label(
    row: dict[str, Any],
    ref: tuple[int, str, int] | None,
    bill_semantics: dict[str, BillSemanticPayload],
) -> BillSemanticPayload | None:
    if ref is None:
        return None
    # Current LLM bill semantics are keyed only by congressional bill key. Do
    # not reuse them for state/local rows until the payload carries a matching
    # jurisdiction/body/session context.
    if str(row.get("jurisdiction_id") or "us_congress") != "us_congress":
        return None
    return bill_semantics.get(_bill_key_from_ref(ref))


def _bill_sectors(bill: dict[str, Any] | None, semantic: BillSemanticPayload | None) -> set[str]:
    text = " ".join(
        str(value or "")
        for value in (
            bill.get("title") if bill is not None else None,
            bill.get("short_title") if bill is not None else None,
        )
    ).lower()
    sectors = {
        sector
        for sector, keywords in _SECTOR_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    }
    if semantic is not None:
        sectors.update(sector.sector_id for sector in semantic.sectors if sector.confidence >= 0.5)
    return sectors


def _bill_semantic_available_at_or_before(
    semantic: BillSemanticPayload,
    feature_cutoff: date,
) -> bool:
    return semantic.available_at is not None and semantic.available_at <= feature_cutoff


def _sponsor_alignment(
    row: dict[str, Any],
    bill: dict[str, Any] | None,
    *,
    party: str,
) -> float:
    if bill is None:
        return 0.5
    member_ids = set(_member_lookup_keys(row))
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    same_party = 0
    opposite_party = 0
    for sponsor in bill.get("sponsors", []):
        if str(sponsor.get("jurisdiction_id") or "us_congress") != jurisdiction_id:
            continue
        if member_ids.intersection(_sponsor_lookup_keys(sponsor)):
            return 1.0
        if party and sponsor.get("sponsor_party") == party:
            same_party += 1
        elif sponsor.get("sponsor_party") is not None:
            opposite_party += 1
    if same_party == 0 and opposite_party == 0:
        return 0.5
    return (same_party + 1) / (same_party + opposite_party + 2)


def _bill_coalition_signal(semantic: BillSemanticPayload | None) -> float:
    if semantic is None:
        return 0.0
    signal_parts = 0
    if semantic.coalition_summary:
        signal_parts += 1
    if semantic.industries:
        signal_parts += 1
    if semantic.affected_entities:
        signal_parts += 1
    if semantic.ideological_valence not in {"unknown", "procedural"}:
        signal_parts += 1
    return min(1.0, signal_parts / 3.0)


def _member_sector_stats(ontology_features: Any | None) -> dict[str, dict[str, float]]:
    if ontology_features is None:
        return {}
    stats: dict[str, dict[str, float]] = {}
    for exposure in ontology_features.sector_exposures:
        stats[exposure.sector_id] = {
            "committee": float(exposure.committee_jurisdiction_edge_count > 0),
            "holding": float(exposure.holding_edge_count > 0),
            "transaction": float(exposure.transaction_edge_count > 0),
            "contribution": float(exposure.contribution_edge_count > 0),
            "sources": float(exposure.source_count),
        }
    return stats


def _sector_overlap_strength(
    bill_sectors: set[str],
    member_sectors: dict[str, dict[str, float]],
) -> dict[str, float]:
    if not bill_sectors:
        return {"committee": 0.0, "holding": 0.0, "transaction": 0.0, "contribution": 0.0}
    overlapping = [member_sectors[sector] for sector in bill_sectors if sector in member_sectors]
    if not overlapping:
        return {"committee": 0.0, "holding": 0.0, "transaction": 0.0, "contribution": 0.0}
    return {
        key: max(item.get(key, 0.0) for item in overlapping)
        for key in ("committee", "holding", "transaction", "contribution")
    }


def _transaction_recency_signal(
    member_sectors: dict[str, dict[str, float]],
    bill_sectors: set[str],
    transaction_recency: dict[str, float],
) -> float:
    if not bill_sectors:
        return 0.0
    if transaction_recency:
        return max((transaction_recency.get(sector, 0.0) for sector in bill_sectors), default=0.0)
    return max(
        (member_sectors.get(sector, {}).get("transaction", 0.0) for sector in bill_sectors),
        default=0.0,
    )


def _holding_recency_signal(
    member_sectors: dict[str, dict[str, float]],
    bill_sectors: set[str],
    holding_recency: dict[str, float],
) -> float:
    if not bill_sectors:
        return 0.0
    if holding_recency:
        return max((holding_recency.get(sector, 0.0) for sector in bill_sectors), default=0.0)
    return max(
        (member_sectors.get(sector, {}).get("holding", 0.0) for sector in bill_sectors),
        default=0.0,
    )


def _holding_recency_index(
    ontology_edges: list[OntologyEdgePayload],
    feature_cutoff: date,
) -> dict[str, dict[str, float]]:
    return _financial_recency_index(
        ontology_edges,
        feature_cutoff,
        edge_type="member_sector_holding_exposure",
        date_keys=("filing_date", "filed_at", "report_date", "as_of_date", "date"),
    )


def _transaction_recency_index(
    ontology_edges: list[OntologyEdgePayload],
    feature_cutoff: date,
) -> dict[str, dict[str, float]]:
    return _financial_recency_index(
        ontology_edges,
        feature_cutoff,
        edge_type="member_sector_transaction_exposure",
        date_keys=("transaction_date", "filing_date", "filed_at", "as_of_date", "date"),
    )


def _financial_recency_index(
    ontology_edges: list[OntologyEdgePayload],
    feature_cutoff: date,
    *,
    edge_type: str,
    date_keys: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    index: dict[str, dict[str, float]] = {}
    for edge in ontology_edges:
        if edge.edge_type != edge_type:
            continue
        event_date = next(
            (
                parsed
                for parsed in (_date_from_value(edge.attributes.get(key)) for key in date_keys)
                if parsed is not None
            ),
            None,
        )
        if event_date is None or event_date > feature_cutoff:
            continue
        age_days = (feature_cutoff - event_date).days
        if age_days > 365:
            score = 0.25
        elif age_days > 180:
            score = 0.5
        else:
            score = 1.0
        member_id = edge.subject.node_id
        sector_id = edge.object.node_id
        index.setdefault(member_id, {})[sector_id] = max(
            index.get(member_id, {}).get(sector_id, 0.0),
            score,
        )
    return index


def _source_anchor_strength(
    member_sectors: dict[str, dict[str, float]],
    bill_sectors: set[str],
) -> float:
    if not bill_sectors:
        return 0.0
    source_count = max(
        (member_sectors.get(sector, {}).get("sources", 0.0) for sector in bill_sectors),
        default=0.0,
    )
    return min(1.0, source_count / 2.0)


def _member_sector_signal_index(
    rows: list[dict[str, Any]],
    *,
    feature_cutoff: date,
    date_keys: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    signals: dict[str, dict[str, float]] = {}
    for row in rows:
        if not _signal_row_available_at_or_before(row, feature_cutoff, date_keys):
            continue
        if not _source_anchors_from_row(row):
            continue
        sector = str(row["sector"])
        value = float(row.get("alignment_score", 1.0))
        for bioguide_id in _member_index_keys(row):
            signals.setdefault(bioguide_id, {})[sector] = max(
                signals.get(bioguide_id, {}).get(sector, 0.0),
                max(0.0, min(1.0, value)),
            )
    return signals


def _signal_row_available_at_or_before(
    row: dict[str, Any],
    feature_cutoff: date,
    date_keys: tuple[str, ...],
) -> bool:
    for key in date_keys:
        value = _date_from_value(row.get(key))
        if value is not None:
            return value <= feature_cutoff
    return False


def _sector_signal(signals: dict[str, float], bill_sectors: set[str]) -> float | None:
    if not signals:
        return None
    if not bill_sectors:
        return None
    values = [signals[sector] for sector in bill_sectors if sector in signals]
    return max(values) if values else None


def _date_from_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _base_prediction_fields(
    row: dict[str, Any],
    vote_count: int,
    *,
    row_index: int,
) -> dict[str, Any]:
    chamber = str(row["chamber"])
    congress = _required_label_int(row["congress"], "congress", row_index)
    session_number = _required_label_int(row["session_number"], "session_number", row_index)
    roll_call_number = _required_label_int(row["roll_call_number"], "roll_call_number", row_index)
    bill_ref = _bill_ref_from_label(row)
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    if jurisdiction_id != "us_congress" and not row.get("legislative_body_id"):
        raise ValueError("non-Congress predictions require legislative_body_id")
    legislative_body_id = row.get("legislative_body_id") or f"us_congress_{chamber}"
    if jurisdiction_id != "us_congress" and not row.get("legislative_session_id"):
        raise ValueError("non-Congress predictions require legislative_session_id")
    legislative_session_id = row.get("legislative_session_id") or (
        f"congress_{congress}_session_{session_number}"
    )
    source_url = _label_vote_source_url(row)
    return {
        "vote_event_id": _required_label_int(row["vote_event_id"], "vote_event_id", row_index),
        "event_key": _event_key(
            jurisdiction_id=jurisdiction_id,
            legislative_body_id=str(legislative_body_id),
            legislative_session_id=str(legislative_session_id),
            chamber=chamber,
            congress=congress,
            session_number=session_number,
            roll_call_number=roll_call_number,
        ),
        "jurisdiction_id": jurisdiction_id,
        "legislative_body_id": legislative_body_id,
        "legislative_session_id": legislative_session_id,
        "chamber": chamber,
        "congress": congress,
        "session_number": session_number,
        "roll_call_number": roll_call_number,
        "vote_date": row["vote_date"],
        "question": str(row["question"]),
        "result": row.get("result"),
        "source_url": source_url,
        "bill_key": _bill_key_from_ref(bill_ref) if bill_ref is not None else None,
        "bill_context_key": _bill_context_key_from_ref(row, bill_ref),
        "member_bioguide_id": str(row["bioguide_id"]),
        "member_slug": row.get("member_slug"),
        "member_name": row.get("member_name"),
        "party": row.get("party"),
        "state": row.get("state"),
        "feature_vote_count": vote_count,
    }


def _label_vote_source_url(row: dict[str, Any]) -> str | None:
    source_type = _prediction_vote_source_type(str(row.get("jurisdiction_id") or "us_congress"))
    raw_url = row.get("source_url")
    if (
        isinstance(raw_url, str)
        and raw_url.strip()
        and is_official_source_url(
            source_type,
            raw_url.strip(),
        )
    ):
        return raw_url.strip()
    if source_type == "vote_event":
        return _congress_vote_source_url(row)
    return None


def _congress_vote_source_url(row: dict[str, Any]) -> str | None:
    return _house_vote_source_url(row) or _senate_vote_source_url(row)


def _house_vote_source_url(row: dict[str, Any]) -> str | None:
    chamber = row.get("chamber")
    if not isinstance(chamber, str) or chamber.strip().lower() != "house":
        return None
    vote_date = _date_from_value(row.get("vote_date"))
    roll_call_number = _plain_positive_int(row.get("roll_call_number"))
    if vote_date is None or roll_call_number is None:
        return None
    return f"https://clerk.house.gov/Votes/{vote_date.year}{roll_call_number:03d}"


def _senate_vote_source_url(row: dict[str, Any]) -> str | None:
    chamber = row.get("chamber")
    if not isinstance(chamber, str) or chamber.strip().lower() != "senate":
        return None
    congress = _plain_positive_int(row.get("congress"))
    session_number = _plain_positive_int(row.get("session_number"))
    roll_call_number = _plain_positive_int(row.get("roll_call_number"))
    if congress is None or session_number is None or roll_call_number is None:
        return None
    return senate_roll_call_url(congress, session_number, roll_call_number)


def _plain_positive_int(value: object) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _plain_positive_int_string(value: str) -> int | None:
    if not value.isdecimal():
        return None
    parsed = int(value)
    if parsed <= 0:
        return None
    return parsed


def _event_key(
    *,
    jurisdiction_id: str,
    legislative_body_id: str,
    legislative_session_id: str | None,
    chamber: str,
    congress: int,
    session_number: int,
    roll_call_number: int,
) -> str:
    if jurisdiction_id == "us_congress":
        suffix = f"{congress}-{session_number}-{roll_call_number}"
        return f"{chamber}-{suffix}"
    session_id = legislative_session_id or f"{congress}-{session_number}"
    return f"{jurisdiction_id}-{legislative_body_id}-{session_id}-{roll_call_number}"


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    return int(value or 0)


def _label_vote_event_count(label_rows: list[dict[str, Any]]) -> int:
    vote_event_ids: set[int] = set()
    for index, row in enumerate(label_rows):
        vote_event_ids.add(_required_label_int(row["vote_event_id"], "vote_event_id", index))
    return len(vote_event_ids)


def _required_label_int(value: Any, field_name: str, row_index: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"label_rows[{row_index}].{field_name} must be an integer")
    return int(value)


def _vote_option(value: Any) -> VoteOption:
    option = str(value)
    if option not in _VOTE_OPTIONS:
        raise ValueError(f"unsupported vote option: {option}")
    return cast(VoteOption, option)
