from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

import src.export.contracts  # noqa: F401  # Load contracts before ontology-backed models.
from src.evidence.source_anchor_policy import (
    SOURCE_TYPES_REQUIRING_URL,
    anchor_source_type,
    anchor_url,
    describe_missing_source_anchor_urls,
    is_official_source_url,
)
from src.export.contracts import SourceAnchor
from src.prediction.backtest import (
    PredictionBacktestMetricsPayload,
    PredictionBacktestPayload,
    PredictionBacktestPredictionPayload,
    VoteOption,
    _bill_semantic_available_at_or_before,
    _date_from_value,
    _distribution_with_binary_probability,
    _edge_available_at_or_before,
    _edge_has_known_availability,
    _predicted_vote_option,
    build_vote_baseline_backtest,
    build_vote_ontology_backtest,
)
from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.benchmark_slices import compute_benchmark_slices
from src.prediction.calibration_dashboard import CalibrationDashboard, build_calibration_dashboard
from src.prediction.dataset import (
    PredictionDatasetExamplePayload,
    PredictionDatasetSplitPayload,
    PredictionEvalDatasetPayload,
    build_prediction_eval_dataset_from_backtests,
    validate_eval_windows as _validate_eval_windows,
)
from src.prediction.llm_semantics import BillSemanticPayload
from src.prediction.per_member_model import (
    member_vote_examples_from_predictions,
    score_per_member_backtest,
    train_per_member_model,
)
from src.prediction.source_anchors import describe_missing_legislative_source_context

if TYPE_CHECKING:
    from src.ontology.contracts import OntologyEdgePayload


_LEARNED_MODEL_NAME = "learned_signal_logistic"
PredictionEvalReadinessStatus = Literal["ready", "partial", "blocked"]
PredictionEvalReadinessCheckStatus = Literal["pass", "warn", "fail"]


def _float_matches(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return abs(actual - expected) <= 1e-9


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _reject_boolean_int(value: object, field_name: str) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _require_plain_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _require_nonblank_string(value: str, *, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be nonblank")


def _ensure_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be unique and nonblank")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique and nonblank")


def _ensure_sorted_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")
    if values != sorted(set(values)):
        raise ValueError(f"{field_name} must be sorted, unique, and nonblank")


def _vote_source_type(jurisdiction_id: str | None) -> str:
    if jurisdiction_id in {None, "", "us_congress"}:
        return "vote_event"
    return "legislative_vote"


class PredictionEvalModelPayload(BaseModel):
    model_name: str
    metrics: PredictionBacktestMetricsPayload
    coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    skip_reason_counts: dict[str, int] = Field(default_factory=dict)
    calibration_bins: list[PredictionEvalCalibrationBinPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def model_summary_matches_metrics(self) -> Self:
        if not _float_matches(
            self.coverage_rate,
            _optional_rate(self.metrics.evaluated_count, self.metrics.label_count),
        ):
            raise ValueError("coverage_rate must match evaluated_count / label_count")
        if any(count < 0 for count in self.skip_reason_counts.values()):
            raise ValueError("skip_reason_counts values must be non-negative")
        if sum(self.skip_reason_counts.values()) != self.metrics.skipped_count:
            raise ValueError("skip_reason_counts total must match metrics.skipped_count")
        return self


class PredictionEvalCalibrationBinPayload(BaseModel):
    bin_start: float = Field(ge=0.0, le=1.0)
    bin_end: float = Field(ge=0.0, le=1.0)
    prediction_count: int = Field(ge=0)
    average_predicted_probability: float = Field(ge=0.0, le=1.0)
    actual_yea_rate: float = Field(ge=0.0, le=1.0)

    @field_validator("prediction_count", mode="before")
    @classmethod
    def prediction_count_is_plain_int(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def calibration_bin_shape_is_valid(self) -> Self:
        if self.bin_start >= self.bin_end:
            raise ValueError("bin_start must be before bin_end")
        if self.prediction_count == 0:
            raise ValueError("calibration bins must contain predictions")
        if (
            self.average_predicted_probability < self.bin_start
            or self.average_predicted_probability > self.bin_end
        ):
            raise ValueError("average_predicted_probability must fall inside bin")
        return self


class PredictionEvalReadinessCheckPayload(BaseModel):
    name: str
    status: PredictionEvalReadinessCheckStatus
    reason: str
    observed_count: int = Field(ge=0)
    minimum_required: int = Field(ge=0)

    @field_validator("name", "reason", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("observed_count", "minimum_required", mode="before")
    @classmethod
    def readiness_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def text_is_nonblank(self) -> Self:
        _require_nonblank_string(self.name, field_name="name")
        _require_nonblank_string(self.reason, field_name="reason")
        return self


class PredictionEvalReadinessPayload(BaseModel):
    status: PredictionEvalReadinessStatus
    ok: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    warning_reasons: list[str] = Field(default_factory=list)
    checks: list[PredictionEvalReadinessCheckPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def readiness_shape_matches_status(self) -> Self:
        _ensure_unique_nonblank(self.blocking_reasons, field_name="blocking_reasons")
        _ensure_unique_nonblank(self.warning_reasons, field_name="warning_reasons")
        if set(self.blocking_reasons) & set(self.warning_reasons):
            raise ValueError("blocking_reasons and warning_reasons must not overlap")
        if self.status == "blocked" and self.ok:
            raise ValueError("blocked readiness cannot be ok")
        if self.status != "blocked" and not self.ok:
            raise ValueError("non-blocked readiness must be ok")
        if self.status == "ready" and (self.blocking_reasons or self.warning_reasons):
            raise ValueError("ready readiness must not carry reasons")
        return self


class PredictionDatasetSplitQualityPayload(BaseModel):
    label_count: int = Field(ge=0)
    model_ready_example_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)
    binary_yea_count: int = Field(ge=0)
    binary_nay_count: int = Field(ge=0)
    non_binary_vote_count: int = Field(default=0, ge=0)
    source_url_count: int = Field(ge=0)
    model_ready_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    skipped_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    source_url_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_nonzero_feature_count: float | None = Field(default=None, ge=0.0)

    @field_validator(
        "label_count",
        "model_ready_example_count",
        "skipped_count",
        "feature_count",
        "binary_yea_count",
        "binary_nay_count",
        "non_binary_vote_count",
        "source_url_count",
        mode="before",
    )
    @classmethod
    def quality_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def quality_counts_and_rates_are_consistent(self) -> Self:
        binary_vote_count = self.binary_yea_count + self.binary_nay_count
        if binary_vote_count > self.label_count:
            raise ValueError("binary vote counts cannot exceed label_count")
        if binary_vote_count + self.non_binary_vote_count != self.label_count:
            raise ValueError("binary plus non-binary vote counts must equal label_count")
        if self.skipped_count > self.label_count:
            raise ValueError("skipped_count cannot exceed label_count")
        if binary_vote_count < self.model_ready_example_count:
            raise ValueError("binary vote counts cannot be below model_ready_example_count")
        if self.source_url_count > self.label_count:
            raise ValueError("source_url_count cannot exceed label_count")
        if not _float_matches(
            self.model_ready_rate,
            _optional_rate(self.model_ready_example_count, self.label_count),
        ):
            raise ValueError("model_ready_rate must match model_ready_example_count / label_count")
        if not _float_matches(
            self.skipped_rate,
            _optional_rate(self.skipped_count, self.label_count),
        ):
            raise ValueError("skipped_rate must match skipped_count / label_count")
        if not _float_matches(
            self.source_url_coverage_rate,
            _optional_rate(self.source_url_count, self.label_count),
        ):
            raise ValueError("source_url_coverage_rate must match source_url_count / label_count")
        return self


class PredictionEvalDataQualityPayload(BaseModel):
    training: PredictionDatasetSplitQualityPayload
    evaluation: PredictionDatasetSplitQualityPayload


class PredictionBillSemanticCoveragePayload(BaseModel):
    required_bill_count: int = Field(ge=0)
    covered_bill_count: int = Field(ge=0)
    missing_bill_count: int = Field(ge=0)
    cutoff_ineligible_bill_count: int = Field(default=0, ge=0)
    available_semantic_count: int = Field(ge=0)
    coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    required_bill_keys: list[str] = Field(default_factory=list)
    covered_bill_keys: list[str] = Field(default_factory=list)
    missing_bill_keys: list[str] = Field(default_factory=list)
    cutoff_ineligible_bill_keys: list[str] = Field(default_factory=list)

    @field_validator(
        "required_bill_count",
        "covered_bill_count",
        "missing_bill_count",
        "cutoff_ineligible_bill_count",
        "available_semantic_count",
        mode="before",
    )
    @classmethod
    def semantic_coverage_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def semantic_coverage_counts_match_keys(self) -> Self:
        _ensure_sorted_unique_nonblank(
            self.required_bill_keys,
            field_name="required_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.covered_bill_keys,
            field_name="covered_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.missing_bill_keys,
            field_name="missing_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.cutoff_ineligible_bill_keys,
            field_name="cutoff_ineligible_bill_keys",
        )
        if self.required_bill_count != len(self.required_bill_keys):
            raise ValueError("required_bill_count must match required_bill_keys")
        if self.covered_bill_count != len(self.covered_bill_keys):
            raise ValueError("covered_bill_count must match covered_bill_keys")
        if self.missing_bill_count != len(self.missing_bill_keys):
            raise ValueError("missing_bill_count must match missing_bill_keys")
        if self.cutoff_ineligible_bill_count != len(self.cutoff_ineligible_bill_keys):
            raise ValueError("cutoff_ineligible_bill_count must match cutoff_ineligible_bill_keys")
        if self.covered_bill_count + self.missing_bill_count != self.required_bill_count:
            raise ValueError("required bill count must equal covered plus missing")
        if set(self.covered_bill_keys) | set(self.missing_bill_keys) != set(
            self.required_bill_keys
        ):
            raise ValueError("covered and missing bill keys must partition required bill keys")
        if set(self.covered_bill_keys) & set(self.missing_bill_keys):
            raise ValueError("covered and missing bill keys must not overlap")
        if set(self.cutoff_ineligible_bill_keys) & set(self.required_bill_keys):
            raise ValueError("cutoff-ineligible bill keys must not overlap required bill keys")
        if self.covered_bill_count > self.available_semantic_count:
            raise ValueError("covered_bill_count cannot exceed available_semantic_count")
        if not _float_matches(
            self.coverage_rate,
            _optional_rate(self.covered_bill_count, self.required_bill_count),
        ):
            raise ValueError("coverage_rate must match covered_bill_count / required_bill_count")
        return self


class PredictionBillMetadataCoveragePayload(BaseModel):
    required_bill_count: int = Field(ge=0)
    loaded_bill_count: int = Field(ge=0)
    missing_bill_count: int = Field(ge=0)
    cutoff_ineligible_bill_count: int = Field(default=0, ge=0)
    coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    required_bill_keys: list[str] = Field(default_factory=list)
    loaded_bill_keys: list[str] = Field(default_factory=list)
    missing_bill_keys: list[str] = Field(default_factory=list)
    cutoff_ineligible_bill_keys: list[str] = Field(default_factory=list)

    @field_validator(
        "required_bill_count",
        "loaded_bill_count",
        "missing_bill_count",
        "cutoff_ineligible_bill_count",
        mode="before",
    )
    @classmethod
    def metadata_coverage_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def metadata_coverage_counts_match_keys(self) -> Self:
        _ensure_sorted_unique_nonblank(
            self.required_bill_keys,
            field_name="required_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.loaded_bill_keys,
            field_name="loaded_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.missing_bill_keys,
            field_name="missing_bill_keys",
        )
        _ensure_sorted_unique_nonblank(
            self.cutoff_ineligible_bill_keys,
            field_name="cutoff_ineligible_bill_keys",
        )
        if self.required_bill_count != len(self.required_bill_keys):
            raise ValueError("required_bill_count must match required_bill_keys")
        if self.loaded_bill_count != len(self.loaded_bill_keys):
            raise ValueError("loaded_bill_count must match loaded_bill_keys")
        if self.missing_bill_count != len(self.missing_bill_keys):
            raise ValueError("missing_bill_count must match missing_bill_keys")
        if self.cutoff_ineligible_bill_count != len(self.cutoff_ineligible_bill_keys):
            raise ValueError("cutoff_ineligible_bill_count must match cutoff_ineligible_bill_keys")
        if self.loaded_bill_count + self.missing_bill_count != self.required_bill_count:
            raise ValueError("required bill count must equal loaded plus missing")
        if set(self.loaded_bill_keys) | set(self.missing_bill_keys) != set(self.required_bill_keys):
            raise ValueError("loaded and missing bill keys must partition required bill keys")
        if set(self.loaded_bill_keys) & set(self.missing_bill_keys):
            raise ValueError("loaded and missing bill keys must not overlap")
        if set(self.cutoff_ineligible_bill_keys) & set(self.required_bill_keys):
            raise ValueError("cutoff-ineligible bill keys must not overlap required bill keys")
        if not _float_matches(
            self.coverage_rate,
            _optional_rate(self.loaded_bill_count, self.required_bill_count),
        ):
            raise ValueError("coverage_rate must match loaded_bill_count / required_bill_count")
        return self


class PredictionEvalModelLiftPayload(BaseModel):
    model_name: str
    baseline_model_name: str
    accuracy_delta: float | None = None
    brier_score_improvement: float | None = None
    log_loss_improvement: float | None = None
    coverage_rate_delta: float | None = None


class PredictionFeatureSourceCoveragePayload(BaseModel):
    model_name: str
    signal_name: str
    prediction_count: int = Field(ge=0)
    sourced_prediction_count: int = Field(ge=0)
    source_anchor_count: int = Field(ge=0)
    source_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    url_sourced_prediction_count: int = Field(default=0, ge=0)
    url_source_anchor_count: int = Field(default=0, ge=0)
    url_source_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    official_source_sourced_prediction_count: int = Field(default=0, ge=0)
    official_source_anchor_count: int = Field(default=0, ge=0)
    official_source_coverage_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    @field_validator(
        "prediction_count",
        "sourced_prediction_count",
        "source_anchor_count",
        "url_sourced_prediction_count",
        "url_source_anchor_count",
        "official_source_sourced_prediction_count",
        "official_source_anchor_count",
        mode="before",
    )
    @classmethod
    def source_coverage_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def source_coverage_counts_and_rates_are_consistent(self) -> Self:
        if self.sourced_prediction_count > self.prediction_count:
            raise ValueError("sourced predictions cannot exceed prediction_count")
        if self.url_sourced_prediction_count > self.sourced_prediction_count:
            raise ValueError("url sourced predictions cannot exceed sourced predictions")
        if self.official_source_sourced_prediction_count > self.sourced_prediction_count:
            raise ValueError("official sourced predictions cannot exceed sourced predictions")
        if self.url_source_anchor_count > self.source_anchor_count:
            raise ValueError("url source anchors cannot exceed source_anchor_count")
        if self.official_source_anchor_count > self.source_anchor_count:
            raise ValueError("official source anchors cannot exceed source_anchor_count")
        if self.sourced_prediction_count and self.source_anchor_count == 0:
            raise ValueError("sourced predictions require source anchors")
        if not _float_matches(
            self.source_coverage_rate,
            _optional_rate(self.sourced_prediction_count, self.prediction_count),
        ):
            raise ValueError("source_coverage_rate must match sourced predictions")
        if not _float_matches(
            self.url_source_coverage_rate,
            _optional_rate(self.url_sourced_prediction_count, self.prediction_count),
        ):
            raise ValueError("url_source_coverage_rate must match url sourced predictions")
        if not _float_matches(
            self.official_source_coverage_rate,
            _optional_rate(
                self.official_source_sourced_prediction_count,
                self.prediction_count,
            ),
        ):
            raise ValueError(
                "official_source_coverage_rate must match official sourced predictions"
            )
        return self


class LearnedSignalCoefficientPayload(BaseModel):
    signal_name: str
    coefficient: float


class LearnedSignalModelPayload(BaseModel):
    model_name: str = _LEARNED_MODEL_NAME
    training_example_count: int = Field(ge=0)
    signal_names: list[str] = Field(default_factory=list)
    intercept: float
    coefficients: list[LearnedSignalCoefficientPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def learned_model_shape_is_consistent(self) -> Self:
        if self.model_name != _LEARNED_MODEL_NAME:
            raise ValueError("learned model_name must be learned_signal_logistic")
        if self.signal_names != sorted(set(self.signal_names)):
            raise ValueError("learned signal_names must be sorted and unique")
        coefficient_signal_names = [coefficient.signal_name for coefficient in self.coefficients]
        if coefficient_signal_names != sorted(set(coefficient_signal_names)):
            raise ValueError("learned coefficients must be sorted and unique")
        if coefficient_signal_names != self.signal_names:
            raise ValueError("coefficient signal names must match signal_names")
        return self


class PredictionEvalComparisonPayload(BaseModel):
    vote_event_id: int
    event_key: str
    jurisdiction_id: str = "us_congress"
    legislative_body_id: str | None = None
    legislative_session_id: str | None = None
    vote_date: date
    chamber: str
    question: str
    source_url: str | None = None
    bill_key: str | None = None
    bill_context_key: str | None = None
    member_bioguide_id: str
    member_name: str | None = None
    actual_vote_option: VoteOption
    model_probabilities: dict[str, float | None] = Field(default_factory=dict)
    model_predicted_vote_options: dict[str, VoteOption | None] = Field(default_factory=dict)
    model_vote_probabilities: dict[str, dict[VoteOption, float]] = Field(default_factory=dict)
    model_correct: dict[str, bool | None] = Field(default_factory=dict)
    model_skipped_reasons: dict[str, str | None] = Field(default_factory=dict)
    feature_signals_by_model: dict[str, dict[str, float]] = Field(default_factory=dict)
    feature_source_anchors_by_model: dict[str, dict[str, list[SourceAnchor]]] = Field(
        default_factory=dict
    )
    unavailable_signals_by_model: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def comparison_model_maps_are_consistent(self) -> Self:
        if self.jurisdiction_id != "us_congress" and not self.legislative_body_id:
            raise ValueError("non-Congress comparisons require legislative_body_id")
        if self.jurisdiction_id != "us_congress" and not self.legislative_session_id:
            raise ValueError("non-Congress comparisons require legislative_session_id")
        if self.source_url is not None and not is_official_source_url(
            _vote_source_type(self.jurisdiction_id),
            self.source_url,
        ):
            raise ValueError("comparison source_url must be official")
        model_names = set(self.model_probabilities)
        if (
            set(self.model_predicted_vote_options) != model_names
            or set(self.model_vote_probabilities) != model_names
            or set(self.model_correct) != model_names
            or set(self.model_skipped_reasons) != model_names
            or set(self.feature_signals_by_model) != model_names
            or set(self.feature_source_anchors_by_model) != model_names
            or set(self.unavailable_signals_by_model) != model_names
        ):
            raise ValueError("comparison model maps must align")
        for model_name, distribution in self.model_vote_probabilities.items():
            if not distribution:
                if self.model_probabilities[model_name] is not None:
                    raise ValueError("empty model vote distributions require no probability")
                if self.model_predicted_vote_options[model_name] is not None:
                    raise ValueError("empty model vote distributions require no predicted option")
                continue
            if any(probability < 0.0 or probability > 1.0 for probability in distribution.values()):
                raise ValueError("model vote distributions must contain probabilities in [0, 1]")
            if abs(sum(distribution.values()) - 1.0) > 1e-9:
                raise ValueError("model vote distributions must sum to 1")
            if not _float_matches(
                self.model_probabilities[model_name],
                distribution.get("yea", 0.0),
            ):
                raise ValueError("model probabilities must match vote distributions")
            predicted_vote = self.model_predicted_vote_options[model_name]
            if predicted_vote is not None and predicted_vote != _predicted_vote_option(
                distribution
            ):
                raise ValueError("model predicted vote options must match vote distributions")
        for model_name, source_anchors_by_signal in self.feature_source_anchors_by_model.items():
            signal_names = set(self.feature_signals_by_model.get(model_name, {}))
            orphan_source_signals = sorted(set(source_anchors_by_signal) - signal_names)
            if orphan_source_signals:
                raise ValueError(
                    "feature source anchor signals must be present in feature signals: "
                    + ", ".join(orphan_source_signals)
                )
            if any(not anchors for anchors in source_anchors_by_signal.values()):
                raise ValueError("feature source anchor lists must not be empty")
            for signal_name, anchors in sorted(source_anchors_by_signal.items()):
                missing_source_urls = describe_missing_source_anchor_urls(anchors)
                if missing_source_urls:
                    raise ValueError(
                        "feature source anchors require official source URLs for "
                        f"{signal_name}: {missing_source_urls}"
                    )
                missing_source_context = describe_missing_legislative_source_context(anchors)
                if missing_source_context:
                    raise ValueError(
                        "feature source anchors require legislative source context for "
                        f"{signal_name}: {missing_source_context}"
                    )
        return self


class PredictionEvalFailureCasePayload(BaseModel):
    model_name: str
    failure_kind: str
    vote_event_id: int
    event_key: str
    jurisdiction_id: str = "us_congress"
    legislative_body_id: str | None = None
    legislative_session_id: str | None = None
    vote_date: date
    chamber: str
    question: str
    source_url: str | None = None
    member_bioguide_id: str
    member_name: str | None = None
    actual_vote_option: VoteOption
    predicted_probability_yea: float | None = None
    predicted_vote_option: VoteOption | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    skipped_reason: str | None = None


class PredictionEvalFailureGroupPayload(BaseModel):
    model_name: str
    failure_kind: str
    actual_vote_option: VoteOption
    predicted_vote_option: VoteOption | None = None
    skipped_reason: str | None = None
    strongest_signal_name: str | None = None
    has_source_url: bool
    case_count: int = Field(ge=0)
    average_brier_score: float | None = None
    average_log_loss: float | None = None
    unavailable_signal_counts: dict[str, int] = Field(default_factory=dict)
    sample_vote_event_ids: list[int] = Field(default_factory=list)
    sample_cases: list[PredictionEvalBackfillSampleCasePayload] = Field(default_factory=list)


class PredictionEvalBackfillSampleCasePayload(BaseModel):
    vote_event_id: int
    event_key: str | None = None
    jurisdiction_id: str | None = None
    legislative_body_id: str | None = None
    legislative_session_id: str | None = None
    bill_key: str | None = None
    bill_context_key: str | None = Field(default=None, exclude_if=lambda value: value is None)
    member_bioguide_id: str | None = None

    @field_validator("vote_event_id", mode="before")
    @classmethod
    def vote_event_id_is_plain_int(cls, value: object, info: object) -> object:
        return _require_plain_int(value, getattr(info, "field_name", "vote_event_id"))

    @field_validator(
        "event_key",
        "jurisdiction_id",
        "legislative_body_id",
        "legislative_session_id",
        "bill_key",
        "bill_context_key",
        "member_bioguide_id",
        mode="before",
    )
    @classmethod
    def strip_optional_scope_text(cls, value: object) -> object:
        return value

    @model_validator(mode="after")
    def sample_case_scope_is_canonical(self) -> Self:
        for field_name in (
            "event_key",
            "jurisdiction_id",
            "legislative_body_id",
            "legislative_session_id",
            "bill_key",
            "bill_context_key",
            "member_bioguide_id",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str) and not value:
                raise ValueError(f"{field_name} must be nonblank")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{field_name} must be nonblank")
            if isinstance(value, str) and value != value.strip():
                raise ValueError(f"{field_name} must not have surrounding whitespace")
        return self


class PredictionEvalBackfillRecommendationPayload(BaseModel):
    action: str
    priority_score: int = Field(ge=0)
    reason: str
    affected_case_count: int = Field(ge=0)
    missing_bill_keys: list[str] = Field(default_factory=list)
    unavailable_signal_counts: dict[str, int] = Field(default_factory=dict)
    sample_vote_event_ids: list[int] = Field(default_factory=list)
    sample_cases: list[PredictionEvalBackfillSampleCasePayload] = Field(default_factory=list)

    @field_validator("action", "reason", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator(
        "priority_score",
        "affected_case_count",
        mode="before",
    )
    @classmethod
    def recommendation_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _require_plain_int(value, getattr(info, "field_name", "count"))

    @field_validator("sample_vote_event_ids", mode="before")
    @classmethod
    def sample_vote_ids_are_plain_ints(cls, value: object) -> object:
        if isinstance(value, list):
            return [_require_plain_int(item, "sample_vote_event_ids") for item in value]
        return value

    @field_validator("unavailable_signal_counts", mode="before")
    @classmethod
    def unavailable_signal_counts_are_plain_ints(cls, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: _require_plain_int(count, "unavailable_signal_counts")
                for key, count in value.items()
            }
        return value

    @model_validator(mode="after")
    def recommendation_shape_is_canonical(self) -> Self:
        _require_nonblank_string(self.action, field_name="action")
        _require_nonblank_string(self.reason, field_name="reason")
        _ensure_sorted_unique_nonblank(self.missing_bill_keys, field_name="missing_bill_keys")
        if any(not key.strip() or key != key.strip() for key in self.unavailable_signal_counts):
            raise ValueError("unavailable_signal_counts keys must be nonblank")
        return self


class PredictionEvalCutoffAuditPayload(BaseModel):
    training_feature_row_count: int = Field(ge=0)
    evaluation_feature_row_count: int = Field(ge=0)
    training_label_row_count: int = Field(ge=0)
    evaluation_label_row_count: int = Field(ge=0)
    ontology_edge_count: int = Field(ge=0)
    cutoff_ontology_edge_count: int = Field(ge=0)
    unknown_availability_ontology_edge_count: int = Field(ge=0)
    excluded_future_ontology_edge_count: int = Field(ge=0)
    bill_signal_row_count: int = Field(ge=0)
    cutoff_bill_signal_row_count: int = Field(ge=0)
    unknown_availability_bill_signal_row_count: int = Field(default=0, ge=0)
    excluded_future_bill_signal_row_count: int = Field(ge=0)
    bill_semantic_count: int = Field(ge=0)
    cutoff_training_bill_semantic_count: int = Field(ge=0)
    excluded_future_training_bill_semantic_count: int = Field(ge=0)
    cutoff_evaluation_bill_semantic_count: int = Field(ge=0)
    excluded_future_evaluation_bill_semantic_count: int = Field(ge=0)
    unknown_availability_bill_semantic_count: int = Field(ge=0)
    training_contribution_signal_row_count: int = Field(ge=0)
    cutoff_training_contribution_signal_row_count: int = Field(ge=0)
    unknown_availability_training_contribution_signal_row_count: int = Field(ge=0)
    excluded_future_training_contribution_signal_row_count: int = Field(ge=0)
    evaluation_contribution_signal_row_count: int = Field(ge=0)
    cutoff_evaluation_contribution_signal_row_count: int = Field(ge=0)
    unknown_availability_evaluation_contribution_signal_row_count: int = Field(ge=0)
    excluded_future_evaluation_contribution_signal_row_count: int = Field(ge=0)
    training_statement_signal_row_count: int = Field(ge=0)
    cutoff_training_statement_signal_row_count: int = Field(ge=0)
    unknown_availability_training_statement_signal_row_count: int = Field(ge=0)
    excluded_future_training_statement_signal_row_count: int = Field(ge=0)
    evaluation_statement_signal_row_count: int = Field(ge=0)
    cutoff_evaluation_statement_signal_row_count: int = Field(ge=0)
    unknown_availability_evaluation_statement_signal_row_count: int = Field(ge=0)
    excluded_future_evaluation_statement_signal_row_count: int = Field(ge=0)

    @field_validator(
        "training_feature_row_count",
        "evaluation_feature_row_count",
        "training_label_row_count",
        "evaluation_label_row_count",
        "ontology_edge_count",
        "cutoff_ontology_edge_count",
        "unknown_availability_ontology_edge_count",
        "excluded_future_ontology_edge_count",
        "bill_signal_row_count",
        "cutoff_bill_signal_row_count",
        "unknown_availability_bill_signal_row_count",
        "excluded_future_bill_signal_row_count",
        "bill_semantic_count",
        "cutoff_training_bill_semantic_count",
        "excluded_future_training_bill_semantic_count",
        "cutoff_evaluation_bill_semantic_count",
        "excluded_future_evaluation_bill_semantic_count",
        "unknown_availability_bill_semantic_count",
        "training_contribution_signal_row_count",
        "cutoff_training_contribution_signal_row_count",
        "unknown_availability_training_contribution_signal_row_count",
        "excluded_future_training_contribution_signal_row_count",
        "evaluation_contribution_signal_row_count",
        "cutoff_evaluation_contribution_signal_row_count",
        "unknown_availability_evaluation_contribution_signal_row_count",
        "excluded_future_evaluation_contribution_signal_row_count",
        "training_statement_signal_row_count",
        "cutoff_training_statement_signal_row_count",
        "unknown_availability_training_statement_signal_row_count",
        "excluded_future_training_statement_signal_row_count",
        "evaluation_statement_signal_row_count",
        "cutoff_evaluation_statement_signal_row_count",
        "unknown_availability_evaluation_statement_signal_row_count",
        "excluded_future_evaluation_statement_signal_row_count",
        mode="before",
    )
    @classmethod
    def cutoff_audit_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def cutoff_counts_are_consistent(self) -> Self:
        _ensure_cutoff_partition(
            total=self.ontology_edge_count,
            cutoff=self.cutoff_ontology_edge_count,
            future=self.excluded_future_ontology_edge_count,
            unknown=self.unknown_availability_ontology_edge_count,
            field_name="ontology edge",
        )
        _ensure_cutoff_partition(
            total=self.bill_semantic_count,
            cutoff=self.cutoff_training_bill_semantic_count,
            future=self.excluded_future_training_bill_semantic_count,
            unknown=self.unknown_availability_bill_semantic_count,
            field_name="training bill semantic",
        )
        _ensure_cutoff_partition(
            total=self.bill_semantic_count,
            cutoff=self.cutoff_evaluation_bill_semantic_count,
            future=self.excluded_future_evaluation_bill_semantic_count,
            unknown=self.unknown_availability_bill_semantic_count,
            field_name="evaluation bill semantic",
        )
        _ensure_cutoff_partition(
            total=self.bill_signal_row_count,
            cutoff=self.cutoff_bill_signal_row_count,
            future=self.excluded_future_bill_signal_row_count,
            unknown=self.unknown_availability_bill_signal_row_count,
            field_name="bill signal row",
        )
        _ensure_cutoff_partition(
            total=self.training_contribution_signal_row_count,
            cutoff=self.cutoff_training_contribution_signal_row_count,
            future=self.excluded_future_training_contribution_signal_row_count,
            unknown=self.unknown_availability_training_contribution_signal_row_count,
            field_name="training contribution signal row",
        )
        _ensure_cutoff_partition(
            total=self.evaluation_contribution_signal_row_count,
            cutoff=self.cutoff_evaluation_contribution_signal_row_count,
            future=self.excluded_future_evaluation_contribution_signal_row_count,
            unknown=self.unknown_availability_evaluation_contribution_signal_row_count,
            field_name="evaluation contribution signal row",
        )
        _ensure_cutoff_partition(
            total=self.training_statement_signal_row_count,
            cutoff=self.cutoff_training_statement_signal_row_count,
            future=self.excluded_future_training_statement_signal_row_count,
            unknown=self.unknown_availability_training_statement_signal_row_count,
            field_name="training statement signal row",
        )
        _ensure_cutoff_partition(
            total=self.evaluation_statement_signal_row_count,
            cutoff=self.cutoff_evaluation_statement_signal_row_count,
            future=self.excluded_future_evaluation_statement_signal_row_count,
            unknown=self.unknown_availability_evaluation_statement_signal_row_count,
            field_name="evaluation statement signal row",
        )
        return self


def _ensure_cutoff_partition(
    *,
    total: int,
    cutoff: int,
    future: int,
    unknown: int,
    field_name: str,
) -> None:
    if cutoff + future + unknown != total:
        raise ValueError(f"{field_name} cutoff audit counts must partition total")


class PredictionEvalReportPayload(BaseModel):
    """Temporal benchmark comparing baseline, ontology, and learned signal models."""

    training_feature_cutoff: date
    train_start: date
    train_end: date
    feature_cutoff: date
    label_start: date
    label_end: date
    training_example_count: int = Field(ge=0)
    evaluation_label_count: int = Field(ge=0)
    readiness: PredictionEvalReadinessPayload
    models: list[PredictionEvalModelPayload] = Field(default_factory=list)
    learned_signal_coefficients: list[LearnedSignalCoefficientPayload] = Field(default_factory=list)
    learned_model: LearnedSignalModelPayload
    dataset: PredictionEvalDatasetPayload
    data_quality: PredictionEvalDataQualityPayload
    training_bill_semantic_coverage: PredictionBillSemanticCoveragePayload
    evaluation_bill_semantic_coverage: PredictionBillSemanticCoveragePayload
    bill_semantic_coverage: PredictionBillSemanticCoveragePayload
    bill_metadata_coverage: PredictionBillMetadataCoveragePayload
    model_lifts: list[PredictionEvalModelLiftPayload] = Field(default_factory=list)
    training_feature_source_coverage: list[PredictionFeatureSourceCoveragePayload] = Field(
        default_factory=list
    )
    evaluation_feature_source_coverage: list[PredictionFeatureSourceCoveragePayload] = Field(
        default_factory=list
    )
    feature_source_coverage: list[PredictionFeatureSourceCoveragePayload] = Field(
        default_factory=list
    )
    cutoff_audit: PredictionEvalCutoffAuditPayload
    unavailable_signal_counts: dict[str, int] = Field(default_factory=dict)
    top_failure_cases: list[PredictionEvalFailureCasePayload] = Field(default_factory=list)
    failure_groups: list[PredictionEvalFailureGroupPayload] = Field(default_factory=list)
    backfill_recommendations: list[PredictionEvalBackfillRecommendationPayload] = Field(
        default_factory=list
    )
    comparisons: list[PredictionEvalComparisonPayload] = Field(default_factory=list)
    benchmark_slices: list[BenchmarkSliceMetrics] = Field(default_factory=list)
    calibration_dashboard: CalibrationDashboard | None = None

    @model_validator(mode="after")
    def windows_are_temporal(self) -> Self:
        if self.training_feature_cutoff >= self.train_start:
            raise ValueError("training_feature_cutoff must be before train_start")
        if self.train_start > self.train_end:
            raise ValueError("train_start must be on or before train_end")
        if self.feature_cutoff >= self.label_start:
            raise ValueError("feature_cutoff must be before label_start")
        if self.label_start > self.label_end:
            raise ValueError("label_start must be on or before label_end")
        if self.train_end > self.feature_cutoff:
            raise ValueError("train_end must be on or before feature_cutoff")
        if self.evaluation_label_count != len(self.comparisons):
            raise ValueError("evaluation_label_count must match comparisons")
        if self.training_example_count != self.dataset.training.model_ready_example_count:
            raise ValueError("training_example_count must match dataset training examples")
        if self.evaluation_label_count != self.dataset.evaluation.label_count:
            raise ValueError("evaluation_label_count must match dataset evaluation labels")
        if [_vote_identity(comparison) for comparison in self.comparisons] != [
            _vote_identity(example) for example in self.dataset.evaluation.examples
        ]:
            raise ValueError("comparisons must match dataset evaluation examples")
        feature_count = len(self.dataset.feature_names)
        expected_training_quality = _split_quality_payload(
            self.dataset.training,
            feature_count=feature_count,
        )
        if self.data_quality.training.model_dump(
            mode="json"
        ) != expected_training_quality.model_dump(mode="json"):
            raise ValueError("data_quality.training must match dataset")
        expected_evaluation_quality = _split_quality_payload(
            self.dataset.evaluation,
            feature_count=feature_count,
        )
        if self.data_quality.evaluation.model_dump(
            mode="json"
        ) != expected_evaluation_quality.model_dump(mode="json"):
            raise ValueError("data_quality.evaluation must match dataset")
        if [
            coefficient.model_dump(mode="json") for coefficient in self.learned_signal_coefficients
        ] != [
            coefficient.model_dump(mode="json") for coefficient in self.learned_model.coefficients
        ]:
            raise ValueError("learned_signal_coefficients must match learned_model")
        if [lift.model_dump(mode="json") for lift in self.model_lifts] != [
            lift.model_dump(mode="json") for lift in _model_lifts(self.models)
        ]:
            raise ValueError("model_lifts must match models")
        if self.bill_semantic_coverage.model_dump(mode="json") != (
            self.evaluation_bill_semantic_coverage.model_dump(mode="json")
        ):
            raise ValueError("bill_semantic_coverage must match evaluation coverage")
        if [row.model_dump(mode="json") for row in self.feature_source_coverage] != [
            row.model_dump(mode="json") for row in self.evaluation_feature_source_coverage
        ]:
            raise ValueError("feature_source_coverage must match evaluation coverage")
        expected_model_names = {model.model_name for model in self.models}
        for comparison in self.comparisons:
            if (
                set(comparison.model_probabilities) != expected_model_names
                or set(comparison.model_predicted_vote_options) != expected_model_names
                or set(comparison.model_vote_probabilities) != expected_model_names
                or set(comparison.model_correct) != expected_model_names
                or set(comparison.model_skipped_reasons) != expected_model_names
                or set(comparison.feature_signals_by_model) != expected_model_names
                or set(comparison.feature_source_anchors_by_model) != expected_model_names
                or set(comparison.unavailable_signals_by_model) != expected_model_names
            ):
                raise ValueError("comparison model maps must match models")
        if [row.model_dump(mode="json") for row in self.evaluation_feature_source_coverage] != [
            row.model_dump(mode="json")
            for row in _feature_source_coverage_from_comparisons(
                self.comparisons,
                model_names=[model.model_name for model in self.models],
            )
        ]:
            raise ValueError("evaluation_feature_source_coverage must match comparisons")
        if self.unavailable_signal_counts != _unavailable_signal_counts_from_comparisons(
            self.comparisons
        ):
            raise ValueError("unavailable_signal_counts must match comparisons")
        if _failure_case_records(self.top_failure_cases) != _failure_case_records(
            _failure_cases_from_comparisons(self.comparisons)
        ):
            raise ValueError("top_failure_cases must match comparisons")
        if [group.model_dump(mode="json") for group in self.failure_groups] != [
            group.model_dump(mode="json")
            for group in _failure_groups_from_comparisons(self.comparisons)
        ]:
            raise ValueError("failure_groups must match comparisons")
        _validate_unavailable_signal_backfill_recommendations(
            self.unavailable_signal_counts,
            self.backfill_recommendations,
            self.comparisons,
        )
        _validate_source_url_backfill_recommendation(
            self.evaluation_feature_source_coverage,
            self.backfill_recommendations,
            self.comparisons,
        )
        return self


def _vote_identity(
    comparison: PredictionEvalComparisonPayload | PredictionDatasetExamplePayload,
) -> dict[str, object]:
    return {
        "vote_event_id": comparison.vote_event_id,
        "event_key": comparison.event_key,
        "jurisdiction_id": comparison.jurisdiction_id,
        "legislative_body_id": comparison.legislative_body_id,
        "legislative_session_id": comparison.legislative_session_id,
        "vote_date": comparison.vote_date.isoformat(),
        "chamber": comparison.chamber,
        "question": comparison.question,
        "source_url": comparison.source_url,
        "bill_key": comparison.bill_key,
        "bill_context_key": comparison.bill_context_key,
        "member_bioguide_id": comparison.member_bioguide_id,
        "member_name": comparison.member_name,
        "actual_vote_option": comparison.actual_vote_option,
    }


def _unavailable_signal_counts_from_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comparison in comparisons:
        for unavailable_signals in comparison.unavailable_signals_by_model.values():
            for signal_name in unavailable_signals:
                counts[signal_name] = counts.get(signal_name, 0) + 1
    return dict(sorted(counts.items()))


def _failure_cases_from_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
    *,
    limit: int = 25,
) -> list[PredictionEvalFailureCasePayload]:
    cases: list[PredictionEvalFailureCasePayload] = []
    for comparison in comparisons:
        for model_name in sorted(comparison.model_correct):
            skipped_reason = comparison.model_skipped_reasons[model_name]
            if skipped_reason is not None:
                cases.append(_failure_case_from_comparison(comparison, model_name, "skipped"))
            elif comparison.model_correct[model_name] is False:
                cases.append(
                    _failure_case_from_comparison(comparison, model_name, "wrong_prediction")
                )
    return sorted(
        cases,
        key=lambda item: (
            1 if item.failure_kind == "wrong_prediction" else 0,
            item.log_loss or item.brier_score or 0.0,
            -item.vote_event_id,
        ),
        reverse=True,
    )[:limit]


def _failure_case_records(cases: list[PredictionEvalFailureCasePayload]) -> list[dict[str, object]]:
    return sorted(
        (case.model_dump(mode="json") for case in cases),
        key=lambda item: (
            str(item["model_name"]),
            str(item["failure_kind"]),
            _plain_int(item["vote_event_id"]),
            str(item["member_bioguide_id"]),
        ),
    )


def _plain_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("failure case vote_event_id must be an integer")
    return value


def _failure_case_from_comparison(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
    failure_kind: str,
) -> PredictionEvalFailureCasePayload:
    probability = comparison.model_probabilities[model_name]
    actual_yea = 1.0 if comparison.actual_vote_option == "yea" else 0.0
    return PredictionEvalFailureCasePayload(
        model_name=model_name,
        failure_kind=failure_kind,
        vote_event_id=comparison.vote_event_id,
        event_key=comparison.event_key,
        jurisdiction_id=comparison.jurisdiction_id,
        legislative_body_id=comparison.legislative_body_id,
        legislative_session_id=comparison.legislative_session_id,
        vote_date=comparison.vote_date,
        chamber=comparison.chamber,
        question=comparison.question,
        source_url=comparison.source_url,
        member_bioguide_id=comparison.member_bioguide_id,
        member_name=comparison.member_name,
        actual_vote_option=comparison.actual_vote_option,
        predicted_probability_yea=probability,
        predicted_vote_option=comparison.model_predicted_vote_options[model_name],
        brier_score=(
            (probability - actual_yea) ** 2
            if probability is not None and comparison.actual_vote_option in {"yea", "nay"}
            else None
        ),
        log_loss=(
            _log_loss(probability, actual_yea)
            if probability is not None and comparison.actual_vote_option in {"yea", "nay"}
            else None
        ),
        skipped_reason=comparison.model_skipped_reasons[model_name],
    )


def _failure_groups_from_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
    *,
    limit: int = 50,
    sample_limit: int = 5,
) -> list[PredictionEvalFailureGroupPayload]:
    groups: dict[
        tuple[str, str, VoteOption, VoteOption | None, str | None, str | None, bool],
        list[tuple[PredictionEvalComparisonPayload, str]],
    ] = {}
    for comparison in comparisons:
        for model_name in sorted(comparison.model_correct):
            failure_kind = _comparison_failure_kind(comparison, model_name)
            if failure_kind is None:
                continue
            key = (
                model_name,
                failure_kind,
                comparison.actual_vote_option,
                comparison.model_predicted_vote_options[model_name],
                comparison.model_skipped_reasons[model_name],
                _strongest_comparison_signal_name(comparison, model_name),
                _comparison_has_source_url(comparison, model_name),
            )
            groups.setdefault(key, []).append((comparison, model_name))
    payloads = [
        _failure_group_from_comparisons(key, items, sample_limit=sample_limit)
        for key, items in groups.items()
    ]
    return sorted(
        payloads,
        key=lambda item: (
            item.case_count,
            item.average_log_loss or item.average_brier_score or 0.0,
            item.model_name,
            item.failure_kind,
        ),
        reverse=True,
    )[:limit]


def _comparison_failure_kind(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
) -> str | None:
    if comparison.model_skipped_reasons[model_name] is not None:
        return "skipped"
    if comparison.model_correct[model_name] is False:
        return "wrong_prediction"
    return None


def _strongest_comparison_signal_name(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
) -> str | None:
    signals = comparison.feature_signals_by_model[model_name]
    if not signals:
        return None
    return max(sorted(signals), key=lambda signal: abs(signals[signal]))


def _comparison_has_source_url(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
) -> bool:
    if comparison.source_url:
        return True
    return any(
        anchor.url
        for anchors in comparison.feature_source_anchors_by_model[model_name].values()
        for anchor in anchors
    )


def _failure_group_from_comparisons(
    key: tuple[str, str, VoteOption, VoteOption | None, str | None, str | None, bool],
    items: list[tuple[PredictionEvalComparisonPayload, str]],
    *,
    sample_limit: int,
) -> PredictionEvalFailureGroupPayload:
    model_name, failure_kind, actual_vote, predicted_vote, skipped_reason, signal, has_url = key
    brier_scores: list[float] = []
    log_losses: list[float] = []
    for comparison, item_model_name in items:
        brier_score = _comparison_brier_score(comparison, item_model_name)
        if brier_score is not None:
            brier_scores.append(brier_score)
        log_loss = _comparison_log_loss(comparison, item_model_name)
        if log_loss is not None:
            log_losses.append(log_loss)
    return PredictionEvalFailureGroupPayload(
        model_name=model_name,
        failure_kind=failure_kind,
        actual_vote_option=actual_vote,
        predicted_vote_option=predicted_vote,
        skipped_reason=skipped_reason,
        strongest_signal_name=signal,
        has_source_url=has_url,
        case_count=len(items),
        average_brier_score=(sum(brier_scores) / len(brier_scores) if brier_scores else None),
        average_log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
        unavailable_signal_counts=_group_unavailable_signal_counts_from_comparisons(items),
        sample_vote_event_ids=[
            comparison.vote_event_id for comparison, _model_name in items[:sample_limit]
        ],
        sample_cases=[
            _sample_case_from_comparison(comparison)
            for comparison, _model_name in items[:sample_limit]
        ],
    )


def _comparison_brier_score(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
) -> float | None:
    probability = comparison.model_probabilities[model_name]
    if probability is None or comparison.actual_vote_option not in {"yea", "nay"}:
        return None
    actual_yea = 1.0 if comparison.actual_vote_option == "yea" else 0.0
    return (probability - actual_yea) ** 2


def _comparison_log_loss(
    comparison: PredictionEvalComparisonPayload,
    model_name: str,
) -> float | None:
    probability = comparison.model_probabilities[model_name]
    if probability is None or comparison.actual_vote_option not in {"yea", "nay"}:
        return None
    actual_yea = 1.0 if comparison.actual_vote_option == "yea" else 0.0
    return _log_loss(probability, actual_yea)


def _group_unavailable_signal_counts_from_comparisons(
    items: list[tuple[PredictionEvalComparisonPayload, str]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comparison, model_name in items:
        for signal in comparison.unavailable_signals_by_model[model_name]:
            counts[signal] = counts.get(signal, 0) + 1
    return dict(sorted(counts.items()))


def _validate_unavailable_signal_backfill_recommendations(
    unavailable_signal_counts: dict[str, int],
    recommendations: list[PredictionEvalBackfillRecommendationPayload],
    comparisons: list[PredictionEvalComparisonPayload],
) -> None:
    by_action = {recommendation.action: recommendation for recommendation in recommendations}
    for signal_name, count in unavailable_signal_counts.items():
        if count <= 0:
            continue
        action, reason = _unavailable_signal_backfill_action(signal_name)
        recommendation = by_action.get(action)
        if (
            recommendation is None
            or recommendation.affected_case_count != count
            or recommendation.priority_score != count * 8
            or recommendation.reason != reason
            or recommendation.unavailable_signal_counts != {signal_name: count}
        ):
            raise ValueError("backfill_recommendations must include unavailable signal actions")
        expected_sample_cases = _sample_cases_for_unavailable_signal_comparisons(
            comparisons,
            signal_name,
        )
        if [case.model_dump(mode="json") for case in recommendation.sample_cases] != [
            case.model_dump(mode="json") for case in expected_sample_cases
        ]:
            raise ValueError(
                "backfill_recommendations must include unavailable signal sample cases"
            )
        if recommendation.sample_vote_event_ids != [
            case.vote_event_id for case in expected_sample_cases
        ]:
            raise ValueError(
                "backfill_recommendations must include unavailable signal sample cases"
            )


def _sample_cases_for_unavailable_signal_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
    signal_name: str,
    *,
    limit: int = 5,
) -> list[PredictionEvalBackfillSampleCasePayload]:
    samples: list[PredictionEvalBackfillSampleCasePayload] = []
    for comparison in comparisons:
        if not any(
            signal_name in unavailable_signals
            for unavailable_signals in comparison.unavailable_signals_by_model.values()
        ):
            continue
        _append_backfill_sample_case(
            samples,
            _sample_case_from_comparison(comparison),
            limit=limit,
        )
        if len(samples) >= limit:
            return samples
    return samples


def _unavailable_signal_backfill_action(signal_name: str) -> tuple[str, str]:
    signal_actions = {
        "member_vote_history": (
            "backfill_member_vote_history",
            "Some predictions cannot use pre-cutoff member vote history.",
        ),
        "donation_industry_alignment": (
            "load_fec_donations_and_member_crosswalks",
            "Donation/PAC industry-alignment signals are unavailable for prediction cases.",
        ),
        "public_statement_alignment": (
            "load_source_backed_public_statement_signals",
            "Public-statement alignment signals are unavailable for prediction cases.",
        ),
    }
    return signal_actions.get(
        signal_name,
        (
            f"backfill_{signal_name}",
            f"Signal {signal_name!r} is unavailable for prediction cases.",
        ),
    )


class _LearnedSignalModel(BaseModel):
    signal_names: list[str]
    coefficients: dict[str, float]
    intercept: float
    training_example_count: int


def build_prediction_eval_report(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    training_feature_rows: list[dict[str, object]],
    evaluation_feature_rows: list[dict[str, object]],
    training_label_rows: list[dict[str, object]],
    evaluation_label_rows: list[dict[str, object]],
    ontology_edges: list[OntologyEdgePayload],
    bill_signal_rows: list[dict[str, object]],
    bill_semantics: list[BillSemanticPayload] | None = None,
    contribution_signal_rows: list[dict[str, object]] | None = None,
    statement_signal_rows: list[dict[str, object]] | None = None,
    training_contribution_signal_rows: list[dict[str, object]] | None = None,
    evaluation_contribution_signal_rows: list[dict[str, object]] | None = None,
    training_statement_signal_rows: list[dict[str, object]] | None = None,
    evaluation_statement_signal_rows: list[dict[str, object]] | None = None,
) -> PredictionEvalReportPayload:
    """Build a cutoff-safe prediction benchmark over historical member-vote outcomes."""
    _validate_eval_windows(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
    )

    training_ontology = build_vote_ontology_backtest(
        feature_cutoff=training_feature_cutoff,
        label_start=train_start,
        label_end=train_end,
        feature_rows=training_feature_rows,
        label_rows=training_label_rows,
        ontology_edges=ontology_edges,
        bill_signal_rows=bill_signal_rows,
        bill_semantics=bill_semantics,
        contribution_signal_rows=(
            training_contribution_signal_rows
            if training_contribution_signal_rows is not None
            else contribution_signal_rows
        ),
        statement_signal_rows=(
            training_statement_signal_rows
            if training_statement_signal_rows is not None
            else statement_signal_rows
        ),
    )
    learned_model = _train_learned_signal_model(training_ontology.predictions)
    per_member_model = train_per_member_model(
        member_vote_examples_from_predictions(training_ontology.predictions)
    )

    baseline = build_vote_baseline_backtest(
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_rows=evaluation_feature_rows,
        label_rows=evaluation_label_rows,
    )
    ontology = build_vote_ontology_backtest(
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_rows=evaluation_feature_rows,
        label_rows=evaluation_label_rows,
        ontology_edges=ontology_edges,
        bill_signal_rows=bill_signal_rows,
        bill_semantics=bill_semantics,
        contribution_signal_rows=(
            evaluation_contribution_signal_rows
            if evaluation_contribution_signal_rows is not None
            else contribution_signal_rows
        ),
        statement_signal_rows=(
            evaluation_statement_signal_rows
            if evaluation_statement_signal_rows is not None
            else statement_signal_rows
        ),
    )
    learned = _score_learned_signal_backtest(ontology, learned_model)
    per_member = score_per_member_backtest(ontology, per_member_model)
    dataset = build_prediction_eval_dataset_from_backtests(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        training_backtest=training_ontology,
        evaluation_backtest=ontology,
    )
    model_payloads = [
        _model_summary_payload(baseline),
        _model_summary_payload(ontology),
        _model_summary_payload(learned),
        _model_summary_payload(per_member),
    ]
    training_source_coverage = _feature_source_coverage([training_ontology])
    evaluation_source_coverage = _feature_source_coverage([ontology, learned, per_member])
    source_coverage = evaluation_source_coverage
    bill_metadata_coverage = _bill_metadata_coverage_payload(
        training_split=dataset.training,
        evaluation_split=dataset.evaluation,
        bill_signal_rows=bill_signal_rows,
    )
    training_bill_semantic_coverage = _bill_semantic_coverage_payload(
        split=dataset.training,
        bill_semantics=bill_semantics or [],
        bill_signal_rows=bill_signal_rows,
    )
    evaluation_bill_semantic_coverage = _bill_semantic_coverage_payload(
        split=dataset.evaluation,
        bill_semantics=bill_semantics or [],
        bill_signal_rows=bill_signal_rows,
    )
    bill_semantic_coverage = evaluation_bill_semantic_coverage
    cutoff_audit = _cutoff_audit_payload(
        training_feature_cutoff=training_feature_cutoff,
        feature_cutoff=feature_cutoff,
        training_feature_rows=training_feature_rows,
        evaluation_feature_rows=evaluation_feature_rows,
        training_label_rows=training_label_rows,
        evaluation_label_rows=evaluation_label_rows,
        ontology_edges=ontology_edges,
        bill_signal_rows=bill_signal_rows,
        bill_semantics=bill_semantics or [],
        training_contribution_signal_rows=(
            training_contribution_signal_rows
            if training_contribution_signal_rows is not None
            else contribution_signal_rows or []
        ),
        evaluation_contribution_signal_rows=(
            evaluation_contribution_signal_rows
            if evaluation_contribution_signal_rows is not None
            else contribution_signal_rows or []
        ),
        training_statement_signal_rows=(
            training_statement_signal_rows
            if training_statement_signal_rows is not None
            else statement_signal_rows or []
        ),
        evaluation_statement_signal_rows=(
            evaluation_statement_signal_rows
            if evaluation_statement_signal_rows is not None
            else statement_signal_rows or []
        ),
    )
    unavailable_signal_counts = _unavailable_signal_counts([ontology, learned, per_member])
    failure_groups = _build_failure_groups([baseline, ontology, learned, per_member])
    return PredictionEvalReportPayload(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        training_example_count=training_ontology.metrics.evaluated_count,
        evaluation_label_count=baseline.metrics.label_count,
        readiness=_readiness_payload(
            dataset=dataset,
            learned_model=learned_model,
            cutoff_audit=cutoff_audit,
            training_source_coverage=training_source_coverage,
            source_coverage=source_coverage,
            training_bill_semantic_coverage=training_bill_semantic_coverage,
            bill_semantic_coverage=bill_semantic_coverage,
        ),
        models=model_payloads,
        learned_signal_coefficients=[
            LearnedSignalCoefficientPayload(signal_name=name, coefficient=value)
            for name, value in sorted(learned_model.coefficients.items())
        ],
        learned_model=_learned_model_payload(learned_model),
        dataset=dataset,
        data_quality=_data_quality_payload(dataset),
        training_bill_semantic_coverage=training_bill_semantic_coverage,
        evaluation_bill_semantic_coverage=evaluation_bill_semantic_coverage,
        bill_semantic_coverage=bill_semantic_coverage,
        bill_metadata_coverage=bill_metadata_coverage,
        model_lifts=_model_lifts(model_payloads),
        training_feature_source_coverage=training_source_coverage,
        evaluation_feature_source_coverage=evaluation_source_coverage,
        feature_source_coverage=source_coverage,
        cutoff_audit=cutoff_audit,
        unavailable_signal_counts=unavailable_signal_counts,
        top_failure_cases=_build_failure_cases([baseline, ontology, learned, per_member]),
        failure_groups=failure_groups,
        backfill_recommendations=_build_backfill_recommendations(
            bill_semantic_coverage=bill_semantic_coverage,
            training_bill_semantic_coverage=training_bill_semantic_coverage,
            bill_metadata_coverage=bill_metadata_coverage,
            dataset=dataset,
            unavailable_signal_counts=unavailable_signal_counts,
            failure_groups=failure_groups,
            cutoff_audit=cutoff_audit,
            source_coverage=source_coverage,
            source_coverage_backtests=[ontology, learned, per_member],
        ),
        comparisons=_build_comparisons([baseline, ontology, learned, per_member]),
        benchmark_slices=compute_benchmark_slices(per_member.predictions),
        calibration_dashboard=build_calibration_dashboard(
            per_member.model_name, compute_benchmark_slices(per_member.predictions)
        ),
    )


def _train_learned_signal_model(
    predictions: list[PredictionBacktestPredictionPayload],
) -> _LearnedSignalModel:
    examples = [
        prediction
        for prediction in predictions
        if prediction.skipped_reason is None
        and prediction.actual_vote_option in {"yea", "nay"}
        and prediction.feature_signals
    ]
    signal_names = sorted(
        {signal for prediction in examples for signal in prediction.feature_signals}
    )
    if not examples or not signal_names:
        return _LearnedSignalModel(
            signal_names=[],
            coefficients={},
            intercept=0.0,
            training_example_count=0,
        )

    coefficients = {signal: 0.0 for signal in signal_names}
    positive_rate = sum(1 for item in examples if item.actual_vote_option == "yea") / len(examples)
    intercept = _logit(min(0.95, max(0.05, positive_rate)))
    learning_rate = 0.35
    l2_penalty = 0.01
    for _ in range(400):
        intercept_gradient = 0.0
        coefficient_gradients = {signal: 0.0 for signal in signal_names}
        for prediction in examples:
            target = 1.0 if prediction.actual_vote_option == "yea" else 0.0
            predicted = _predict_probability(
                prediction.feature_signals,
                coefficients=coefficients,
                intercept=intercept,
            )
            error = predicted - target
            intercept_gradient += error
            for signal in signal_names:
                coefficient_gradients[signal] += error * prediction.feature_signals.get(signal, 0.0)
        scale = 1.0 / len(examples)
        intercept -= learning_rate * intercept_gradient * scale
        for signal in signal_names:
            gradient = coefficient_gradients[signal] * scale + l2_penalty * coefficients[signal]
            coefficients[signal] -= learning_rate * gradient
    return _LearnedSignalModel(
        signal_names=signal_names,
        coefficients=coefficients,
        intercept=intercept,
        training_example_count=len(examples),
    )


def _score_learned_signal_backtest(
    ontology: PredictionBacktestPayload,
    model: _LearnedSignalModel,
) -> PredictionBacktestPayload:
    predictions = [
        _score_learned_prediction(prediction, model) for prediction in ontology.predictions
    ]
    metrics = _metrics_for_predictions(predictions)
    return PredictionBacktestPayload(
        model_name=_LEARNED_MODEL_NAME,
        feature_cutoff=ontology.feature_cutoff,
        label_start=ontology.label_start,
        label_end=ontology.label_end,
        feature_vote_event_count=ontology.feature_vote_event_count,
        label_vote_event_count=ontology.label_vote_event_count,
        member_count=ontology.member_count,
        metrics=metrics,
        predictions=predictions,
    )


def _learned_model_payload(model: _LearnedSignalModel) -> LearnedSignalModelPayload:
    return LearnedSignalModelPayload(
        training_example_count=model.training_example_count,
        signal_names=list(model.signal_names),
        intercept=model.intercept,
        coefficients=[
            LearnedSignalCoefficientPayload(signal_name=name, coefficient=value)
            for name, value in sorted(model.coefficients.items())
        ],
    )


def _data_quality_payload(
    dataset: PredictionEvalDatasetPayload,
) -> PredictionEvalDataQualityPayload:
    return PredictionEvalDataQualityPayload(
        training=_split_quality_payload(
            dataset.training,
            feature_count=len(dataset.feature_names),
        ),
        evaluation=_split_quality_payload(
            dataset.evaluation,
            feature_count=len(dataset.feature_names),
        ),
    )


def _bill_semantic_coverage_payload(
    *,
    split: PredictionDatasetSplitPayload,
    bill_semantics: list[BillSemanticPayload],
    bill_signal_rows: list[dict[str, object]],
) -> PredictionBillSemanticCoveragePayload:
    candidate_bill_keys = sorted(
        {
            _bill_semantic_coverage_key(example)
            for example in split.examples
            if example.bill_key is not None
        }
    )
    cutoff_eligible_bill_keys = _cutoff_eligible_bill_signal_keys(
        bill_signal_rows,
        split.feature_cutoff,
    )
    loaded_bill_keys = _bill_signal_loaded_key_set(bill_signal_rows)
    cutoff_ineligible_bill_keys = [
        bill_key
        for bill_key in candidate_bill_keys
        if bill_key in loaded_bill_keys and bill_key not in cutoff_eligible_bill_keys
    ]
    required_bill_keys = [
        bill_key for bill_key in candidate_bill_keys if bill_key not in cutoff_ineligible_bill_keys
    ]
    available_bill_keys = {
        semantic.bill_key
        for semantic in bill_semantics
        if _bill_semantic_available_at_or_before(semantic, split.feature_cutoff)
    }
    covered_bill_keys = [
        bill_key for bill_key in required_bill_keys if bill_key in available_bill_keys
    ]
    missing_bill_keys = [
        bill_key for bill_key in required_bill_keys if bill_key not in available_bill_keys
    ]
    return PredictionBillSemanticCoveragePayload(
        required_bill_count=len(required_bill_keys),
        covered_bill_count=len(covered_bill_keys),
        missing_bill_count=len(missing_bill_keys),
        cutoff_ineligible_bill_count=len(cutoff_ineligible_bill_keys),
        available_semantic_count=len(available_bill_keys),
        coverage_rate=(
            len(covered_bill_keys) / len(required_bill_keys) if required_bill_keys else None
        ),
        required_bill_keys=required_bill_keys,
        covered_bill_keys=covered_bill_keys,
        missing_bill_keys=missing_bill_keys,
        cutoff_ineligible_bill_keys=cutoff_ineligible_bill_keys,
    )


def _bill_semantic_coverage_key(example: PredictionDatasetExamplePayload) -> str:
    if example.bill_context_key is not None:
        return example.bill_context_key
    bill_key = str(example.bill_key)
    jurisdiction_id = example.jurisdiction_id or "us_congress"
    if jurisdiction_id == "us_congress" or _bill_key_has_jurisdiction_prefix(
        bill_key,
        jurisdiction_id,
    ):
        return bill_key
    return f"{jurisdiction_id}:{bill_key}"


def _bill_metadata_coverage_payload(
    *,
    training_split: PredictionDatasetSplitPayload,
    evaluation_split: PredictionDatasetSplitPayload,
    bill_signal_rows: list[dict[str, object]],
) -> PredictionBillMetadataCoveragePayload:
    required_by_cutoff: dict[str, date] = {}
    for split in (training_split, evaluation_split):
        for example in split.examples:
            if example.bill_key is None:
                continue
            bill_key = _bill_semantic_coverage_key(example)
            previous = required_by_cutoff.get(bill_key)
            if previous is None or split.feature_cutoff > previous:
                required_by_cutoff[bill_key] = split.feature_cutoff
    loaded_by_key = _bill_signal_loaded_key_set(bill_signal_rows)
    cutoff_eligible_keys_by_cutoff = {
        cutoff: _cutoff_eligible_bill_signal_keys(bill_signal_rows, cutoff)
        for cutoff in set(required_by_cutoff.values())
    }
    cutoff_ineligible_bill_keys = sorted(
        bill_key
        for bill_key, cutoff in required_by_cutoff.items()
        if bill_key in loaded_by_key and bill_key not in cutoff_eligible_keys_by_cutoff[cutoff]
    )
    required_bill_keys = sorted(
        bill_key
        for bill_key in required_by_cutoff
        if bill_key not in set(cutoff_ineligible_bill_keys)
    )
    covered_bill_keys = [bill_key for bill_key in required_bill_keys if bill_key in loaded_by_key]
    missing_bill_keys = sorted(
        bill_key
        for bill_key, cutoff in required_by_cutoff.items()
        if bill_key not in loaded_by_key and bill_key not in cutoff_eligible_keys_by_cutoff[cutoff]
    )
    return PredictionBillMetadataCoveragePayload(
        required_bill_count=len(required_bill_keys),
        loaded_bill_count=len(covered_bill_keys),
        missing_bill_count=len(missing_bill_keys),
        cutoff_ineligible_bill_count=len(cutoff_ineligible_bill_keys),
        coverage_rate=(
            len(covered_bill_keys) / len(required_bill_keys) if required_bill_keys else None
        ),
        required_bill_keys=required_bill_keys,
        loaded_bill_keys=covered_bill_keys,
        missing_bill_keys=missing_bill_keys,
        cutoff_ineligible_bill_keys=cutoff_ineligible_bill_keys,
    )


def _bill_signal_loaded_key_set(bill_signal_rows: list[dict[str, object]]) -> set[str]:
    loaded: set[str] = set()
    for row in bill_signal_rows:
        if not _bill_signal_row_has_source_url(row):
            continue
        bill_key = _bill_signal_row_key(row)
        if bill_key is not None:
            loaded.add(bill_key)
    return loaded


def _cutoff_eligible_bill_signal_keys(
    bill_signal_rows: list[dict[str, object]],
    feature_cutoff: date,
) -> set[str]:
    eligible: set[str] = set()
    for row in bill_signal_rows:
        if not _bill_signal_row_available_at_or_before(row, feature_cutoff):
            continue
        if not _bill_signal_row_has_source_url(row):
            continue
        bill_key = _bill_signal_row_key(row)
        if bill_key is not None:
            eligible.add(bill_key)
    return eligible


def _split_quality_payload(
    split: PredictionDatasetSplitPayload,
    *,
    feature_count: int,
) -> PredictionDatasetSplitQualityPayload:
    label_count = split.label_count
    binary_yea_count = sum(1 for example in split.examples if example.binary_label == 1)
    binary_nay_count = sum(1 for example in split.examples if example.binary_label == 0)
    non_binary_vote_count = sum(1 for example in split.examples if example.binary_label is None)
    nonzero_feature_counts = [
        sum(1 for value in example.features.values() if value != 0.0) for example in split.examples
    ]
    return PredictionDatasetSplitQualityPayload(
        label_count=label_count,
        model_ready_example_count=split.model_ready_example_count,
        skipped_count=split.skipped_count,
        feature_count=feature_count,
        binary_yea_count=binary_yea_count,
        binary_nay_count=binary_nay_count,
        non_binary_vote_count=non_binary_vote_count,
        source_url_count=split.source_url_count,
        model_ready_rate=_optional_rate(split.model_ready_example_count, label_count),
        skipped_rate=_optional_rate(split.skipped_count, label_count),
        source_url_coverage_rate=_optional_rate(split.source_url_count, label_count),
        average_nonzero_feature_count=(
            sum(nonzero_feature_counts) / len(nonzero_feature_counts)
            if nonzero_feature_counts
            else None
        ),
    )


def _optional_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _score_learned_prediction(
    prediction: PredictionBacktestPredictionPayload,
    model: _LearnedSignalModel,
) -> PredictionBacktestPredictionPayload:
    data = prediction.model_dump()
    data.update(
        {
            "predicted_probability_yea": None,
            "predicted_vote_option": None,
            "predicted_vote_probabilities": {},
            "correct": None,
            "brier_score": None,
            "log_loss": None,
        }
    )
    if prediction.skipped_reason is not None:
        data["skipped_reason"] = prediction.skipped_reason
        return PredictionBacktestPredictionPayload.model_validate(data)
    if not model.coefficients:
        data["skipped_reason"] = "missing_learned_training_examples"
        return PredictionBacktestPredictionPayload.model_validate(data)
    if not prediction.feature_signals:
        data["skipped_reason"] = "missing_learned_feature_signals"
        return PredictionBacktestPredictionPayload.model_validate(data)

    probability = _predict_probability(
        prediction.feature_signals,
        coefficients=model.coefficients,
        intercept=model.intercept,
    )
    distribution = _distribution_with_binary_probability(
        prediction.predicted_vote_probabilities,
        probability_yea=probability,
    )
    probability_yea = distribution["yea"]
    predicted_vote = _predicted_vote_option(distribution)
    actual_yea = 1.0 if prediction.actual_vote_option == "yea" else 0.0
    data.update(
        {
            "predicted_probability_yea": probability_yea,
            "predicted_vote_option": predicted_vote,
            "predicted_vote_probabilities": distribution,
            "correct": predicted_vote == prediction.actual_vote_option,
            "brier_score": (
                (probability_yea - actual_yea) ** 2
                if prediction.actual_vote_option in {"yea", "nay"}
                else None
            ),
            "log_loss": (
                _log_loss(probability_yea, actual_yea)
                if prediction.actual_vote_option in {"yea", "nay"}
                else None
            ),
            "skipped_reason": None,
        }
    )
    return PredictionBacktestPredictionPayload.model_validate(data)


def _predict_probability(
    signals: dict[str, float],
    *,
    coefficients: dict[str, float],
    intercept: float,
) -> float:
    raw = intercept + sum(coefficients[name] * signals.get(name, 0.0) for name in coefficients)
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, raw))))


def _metrics_for_predictions(
    predictions: list[PredictionBacktestPredictionPayload],
) -> PredictionBacktestMetricsPayload:
    evaluated = [item for item in predictions if item.correct is not None]
    correct_count = sum(1 for item in evaluated if item.correct)
    brier_scores = [item.brier_score for item in evaluated if item.brier_score is not None]
    log_losses = [item.log_loss for item in evaluated if item.log_loss is not None]
    return PredictionBacktestMetricsPayload(
        label_count=len(predictions),
        evaluated_count=len(evaluated),
        correct_count=correct_count,
        skipped_count=sum(1 for item in predictions if item.skipped_reason is not None),
        accuracy=correct_count / len(evaluated) if evaluated else None,
        brier_score=sum(brier_scores) / len(brier_scores) if brier_scores else None,
        log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
    )


def _model_summary_payload(backtest: PredictionBacktestPayload) -> PredictionEvalModelPayload:
    label_count = backtest.metrics.label_count
    coverage_rate = backtest.metrics.evaluated_count / label_count if label_count else None
    return PredictionEvalModelPayload(
        model_name=backtest.model_name,
        metrics=backtest.metrics,
        coverage_rate=coverage_rate,
        skip_reason_counts=_skip_reason_counts(backtest.predictions),
        calibration_bins=_calibration_bins(backtest.predictions),
    )


def _calibration_bins(
    predictions: list[PredictionBacktestPredictionPayload],
    *,
    bin_count: int = 10,
) -> list[PredictionEvalCalibrationBinPayload]:
    by_bin: dict[int, list[PredictionBacktestPredictionPayload]] = {}
    for prediction in predictions:
        if prediction.predicted_probability_yea is None or prediction.actual_vote_option not in {
            "yea",
            "nay",
        }:
            continue
        index = min(
            bin_count - 1,
            int(prediction.predicted_probability_yea * bin_count),
        )
        by_bin.setdefault(index, []).append(prediction)
    return [
        _calibration_bin_payload(index, items, bin_count=bin_count)
        for index, items in sorted(by_bin.items())
    ]


def _calibration_bin_payload(
    index: int,
    predictions: list[PredictionBacktestPredictionPayload],
    *,
    bin_count: int,
) -> PredictionEvalCalibrationBinPayload:
    predicted_values = [
        prediction.predicted_probability_yea
        for prediction in predictions
        if prediction.predicted_probability_yea is not None
    ]
    return PredictionEvalCalibrationBinPayload(
        bin_start=index / bin_count,
        bin_end=(index + 1) / bin_count,
        prediction_count=len(predictions),
        average_predicted_probability=sum(predicted_values) / len(predicted_values),
        actual_yea_rate=sum(
            1 for prediction in predictions if prediction.actual_vote_option == "yea"
        )
        / len(predictions),
    )


def _readiness_payload(
    *,
    dataset: PredictionEvalDatasetPayload,
    learned_model: _LearnedSignalModel,
    cutoff_audit: PredictionEvalCutoffAuditPayload,
    training_source_coverage: list[PredictionFeatureSourceCoveragePayload],
    source_coverage: list[PredictionFeatureSourceCoveragePayload],
    training_bill_semantic_coverage: PredictionBillSemanticCoveragePayload,
    bill_semantic_coverage: PredictionBillSemanticCoveragePayload,
) -> PredictionEvalReadinessPayload:
    checks = [
        _readiness_check(
            "training_labels_present",
            "missing_training_labels",
            dataset.training.label_count,
            minimum_required=1,
            fail_when_missing=True,
        ),
        _readiness_check(
            "evaluation_labels_present",
            "missing_evaluation_labels",
            dataset.evaluation.label_count,
            minimum_required=1,
            fail_when_missing=True,
        ),
        _readiness_check(
            "learned_training_examples_present",
            "missing_learned_training_examples",
            learned_model.training_example_count,
            minimum_required=1,
            fail_when_missing=True,
        ),
        _readiness_check(
            "evaluation_model_ready_examples_present",
            "missing_evaluation_model_ready_examples",
            dataset.evaluation.model_ready_example_count,
            minimum_required=1,
            fail_when_missing=True,
        ),
        _readiness_check(
            "ontology_edges_present",
            "missing_ontology_edges",
            cutoff_audit.cutoff_ontology_edge_count,
            minimum_required=1,
            fail_when_missing=False,
        ),
        _readiness_absence_check(
            "ontology_edge_availability_known",
            "unknown_ontology_edge_availability",
            cutoff_audit.unknown_availability_ontology_edge_count,
            fail_when_present=False,
        ),
        _readiness_check(
            "bill_semantics_present",
            "missing_bill_semantics",
            cutoff_audit.bill_semantic_count,
            minimum_required=1,
            fail_when_missing=False,
        ),
        _readiness_absence_check(
            "bill_semantic_availability_known",
            "unknown_bill_semantic_availability",
            cutoff_audit.unknown_availability_bill_semantic_count,
            fail_when_present=False,
        ),
        _readiness_absence_check(
            "bill_signal_availability_known",
            "unknown_bill_signal_availability",
            cutoff_audit.unknown_availability_bill_signal_row_count,
            fail_when_present=False,
        ),
        _readiness_absence_check(
            "contribution_signal_availability_known",
            "unknown_contribution_signal_availability",
            (
                cutoff_audit.unknown_availability_training_contribution_signal_row_count
                + cutoff_audit.unknown_availability_evaluation_contribution_signal_row_count
            ),
            fail_when_present=False,
        ),
        _readiness_absence_check(
            "statement_signal_availability_known",
            "unknown_statement_signal_availability",
            (
                cutoff_audit.unknown_availability_training_statement_signal_row_count
                + cutoff_audit.unknown_availability_evaluation_statement_signal_row_count
            ),
            fail_when_present=False,
        ),
        _readiness_check(
            "bill_semantic_coverage_complete",
            "missing_bill_semantic_coverage",
            bill_semantic_coverage.covered_bill_count,
            minimum_required=bill_semantic_coverage.required_bill_count,
            fail_when_missing=False,
        ),
        _readiness_check(
            "training_bill_semantic_coverage_complete",
            "missing_training_bill_semantic_coverage",
            training_bill_semantic_coverage.covered_bill_count,
            minimum_required=training_bill_semantic_coverage.required_bill_count,
            fail_when_missing=False,
        ),
        _readiness_check(
            "feature_source_coverage_present",
            "missing_feature_source_coverage",
            sum(row.sourced_prediction_count for row in source_coverage),
            minimum_required=1,
            fail_when_missing=False,
        ),
        _readiness_check(
            "training_feature_source_coverage_present",
            "missing_training_feature_source_coverage",
            sum(row.sourced_prediction_count for row in training_source_coverage),
            minimum_required=1,
            fail_when_missing=False,
        ),
    ]
    blocking_reasons = [check.reason for check in checks if check.status == "fail"]
    warning_reasons = [check.reason for check in checks if check.status == "warn"]
    status: PredictionEvalReadinessStatus
    if blocking_reasons:
        status = "blocked"
    elif warning_reasons:
        status = "partial"
    else:
        status = "ready"
    return PredictionEvalReadinessPayload(
        status=status,
        ok=not blocking_reasons,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
        checks=checks,
    )


def _readiness_absence_check(
    name: str,
    reason: str,
    observed_count: int,
    *,
    fail_when_present: bool,
) -> PredictionEvalReadinessCheckPayload:
    if observed_count == 0:
        status: PredictionEvalReadinessCheckStatus = "pass"
    elif fail_when_present:
        status = "fail"
    else:
        status = "warn"
    return PredictionEvalReadinessCheckPayload(
        name=name,
        status=status,
        reason=reason,
        observed_count=observed_count,
        minimum_required=0,
    )


def _readiness_check(
    name: str,
    reason: str,
    observed_count: int,
    *,
    minimum_required: int,
    fail_when_missing: bool,
) -> PredictionEvalReadinessCheckPayload:
    if observed_count >= minimum_required:
        status: PredictionEvalReadinessCheckStatus = "pass"
    elif fail_when_missing:
        status = "fail"
    else:
        status = "warn"
    return PredictionEvalReadinessCheckPayload(
        name=name,
        status=status,
        reason=reason,
        observed_count=observed_count,
        minimum_required=minimum_required,
    )


def _model_lifts(models: list[PredictionEvalModelPayload]) -> list[PredictionEvalModelLiftPayload]:
    if not models:
        return []
    baseline = models[0]
    return [
        PredictionEvalModelLiftPayload(
            model_name=model.model_name,
            baseline_model_name=baseline.model_name,
            accuracy_delta=_delta(model.metrics.accuracy, baseline.metrics.accuracy),
            brier_score_improvement=_delta(
                baseline.metrics.brier_score,
                model.metrics.brier_score,
            ),
            log_loss_improvement=_delta(
                baseline.metrics.log_loss,
                model.metrics.log_loss,
            ),
            coverage_rate_delta=_delta(model.coverage_rate, baseline.coverage_rate),
        )
        for model in models[1:]
    ]


def _delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return value - baseline


def _feature_source_coverage(
    backtests: list[PredictionBacktestPayload],
) -> list[PredictionFeatureSourceCoveragePayload]:
    rows: list[PredictionFeatureSourceCoveragePayload] = []
    for backtest in backtests:
        signal_names = sorted(
            {signal for prediction in backtest.predictions for signal in prediction.feature_signals}
        )
        for signal_name in signal_names:
            predictions_with_signal = [
                prediction
                for prediction in backtest.predictions
                if signal_name in prediction.feature_signals
            ]
            sourced_predictions = [
                prediction
                for prediction in predictions_with_signal
                if prediction.feature_source_anchors.get(signal_name)
            ]
            url_sourced_predictions = [
                prediction
                for prediction in predictions_with_signal
                if any(
                    anchor.url for anchor in prediction.feature_source_anchors.get(signal_name, [])
                )
            ]
            official_source_sourced_predictions = [
                prediction
                for prediction in predictions_with_signal
                if _has_official_feature_source_anchor(
                    prediction.feature_source_anchors.get(signal_name, []),
                    jurisdiction_id=prediction.jurisdiction_id,
                )
            ]
            source_anchor_count = sum(
                len(prediction.feature_source_anchors.get(signal_name, []))
                for prediction in predictions_with_signal
            )
            url_source_anchor_count = sum(
                1
                for prediction in predictions_with_signal
                for anchor in prediction.feature_source_anchors.get(signal_name, [])
                if anchor.url
            )
            official_source_anchor_count = sum(
                _official_feature_source_anchor_count(
                    prediction.feature_source_anchors.get(signal_name, []),
                    jurisdiction_id=prediction.jurisdiction_id,
                )
                for prediction in predictions_with_signal
            )
            prediction_count = len(predictions_with_signal)
            rows.append(
                PredictionFeatureSourceCoveragePayload(
                    model_name=backtest.model_name,
                    signal_name=signal_name,
                    prediction_count=prediction_count,
                    sourced_prediction_count=len(sourced_predictions),
                    source_anchor_count=source_anchor_count,
                    source_coverage_rate=(
                        len(sourced_predictions) / prediction_count if prediction_count else None
                    ),
                    url_sourced_prediction_count=len(url_sourced_predictions),
                    url_source_anchor_count=url_source_anchor_count,
                    url_source_coverage_rate=(
                        len(url_sourced_predictions) / prediction_count
                        if prediction_count
                        else None
                    ),
                    official_source_sourced_prediction_count=len(
                        official_source_sourced_predictions
                    ),
                    official_source_anchor_count=official_source_anchor_count,
                    official_source_coverage_rate=(
                        len(official_source_sourced_predictions) / prediction_count
                        if prediction_count
                        else None
                    ),
                )
            )
    return rows


def _feature_source_coverage_from_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
    *,
    model_names: list[str],
) -> list[PredictionFeatureSourceCoveragePayload]:
    rows: list[PredictionFeatureSourceCoveragePayload] = []
    for model_name in model_names:
        signal_names = sorted(
            {
                signal_name
                for comparison in comparisons
                for signal_name in comparison.feature_signals_by_model.get(
                    model_name,
                    {},
                )
            }
        )
        for signal_name in signal_names:
            comparisons_with_signal = [
                comparison
                for comparison in comparisons
                if signal_name in comparison.feature_signals_by_model.get(model_name, {})
            ]
            sourced_comparisons = [
                comparison
                for comparison in comparisons_with_signal
                if comparison.feature_source_anchors_by_model.get(model_name, {}).get(signal_name)
            ]
            url_sourced_comparisons = [
                comparison
                for comparison in comparisons_with_signal
                if any(
                    anchor.url
                    for anchor in comparison.feature_source_anchors_by_model.get(
                        model_name,
                        {},
                    ).get(signal_name, [])
                )
            ]
            official_source_sourced_comparisons = [
                comparison
                for comparison in comparisons_with_signal
                if _has_official_feature_source_anchor(
                    comparison.feature_source_anchors_by_model.get(
                        model_name,
                        {},
                    ).get(signal_name, []),
                    jurisdiction_id=comparison.jurisdiction_id,
                )
            ]
            source_anchor_count = sum(
                len(
                    comparison.feature_source_anchors_by_model.get(model_name, {}).get(
                        signal_name,
                        [],
                    )
                )
                for comparison in comparisons_with_signal
            )
            url_source_anchor_count = sum(
                1
                for comparison in comparisons_with_signal
                for anchor in comparison.feature_source_anchors_by_model.get(
                    model_name,
                    {},
                ).get(signal_name, [])
                if anchor.url
            )
            official_source_anchor_count = sum(
                _official_feature_source_anchor_count(
                    comparison.feature_source_anchors_by_model.get(model_name, {}).get(
                        signal_name,
                        [],
                    ),
                    jurisdiction_id=comparison.jurisdiction_id,
                )
                for comparison in comparisons_with_signal
            )
            prediction_count = len(comparisons_with_signal)
            rows.append(
                PredictionFeatureSourceCoveragePayload(
                    model_name=model_name,
                    signal_name=signal_name,
                    prediction_count=prediction_count,
                    sourced_prediction_count=len(sourced_comparisons),
                    source_anchor_count=source_anchor_count,
                    source_coverage_rate=(
                        len(sourced_comparisons) / prediction_count if prediction_count else None
                    ),
                    url_sourced_prediction_count=len(url_sourced_comparisons),
                    url_source_anchor_count=url_source_anchor_count,
                    url_source_coverage_rate=(
                        len(url_sourced_comparisons) / prediction_count
                        if prediction_count
                        else None
                    ),
                    official_source_sourced_prediction_count=len(
                        official_source_sourced_comparisons
                    ),
                    official_source_anchor_count=official_source_anchor_count,
                    official_source_coverage_rate=(
                        len(official_source_sourced_comparisons) / prediction_count
                        if prediction_count
                        else None
                    ),
                )
            )
    return rows


def _has_official_feature_source_anchor(
    anchors: list[SourceAnchor],
    *,
    jurisdiction_id: str,
) -> bool:
    return any(
        _feature_source_anchor_has_official_url(anchor, jurisdiction_id=jurisdiction_id)
        for anchor in anchors
    )


def _official_feature_source_anchor_count(
    anchors: list[SourceAnchor],
    *,
    jurisdiction_id: str,
) -> int:
    return sum(
        1
        for anchor in anchors
        if _feature_source_anchor_has_official_url(anchor, jurisdiction_id=jurisdiction_id)
    )


def _feature_source_anchor_has_official_url(
    anchor: SourceAnchor,
    *,
    jurisdiction_id: str,
) -> bool:
    source_type = anchor_source_type(anchor)
    if source_type not in SOURCE_TYPES_REQUIRING_URL:
        return False
    normalized_source_type = _feature_source_family_id(source_type, jurisdiction_id=jurisdiction_id)
    return is_official_source_url(normalized_source_type, anchor_url(anchor))


def _feature_source_family_id(source_type: str, *, jurisdiction_id: str) -> str:
    source_type = source_type.strip()
    if source_type == "legislative_vote":
        return "legislative_vote"
    if source_type in {"vote_event", "congress_vote"}:
        return "congress_vote" if jurisdiction_id == "us_congress" else "legislative_vote"
    return source_type


def _log_loss(probability_yea: float, actual_yea: float) -> float:
    probability = max(1e-15, min(1.0 - 1e-15, probability_yea))
    return -(actual_yea * math.log(probability) + (1.0 - actual_yea) * math.log(1.0 - probability))


def _cutoff_audit_payload(
    *,
    training_feature_cutoff: date,
    feature_cutoff: date,
    training_feature_rows: list[dict[str, object]],
    evaluation_feature_rows: list[dict[str, object]],
    training_label_rows: list[dict[str, object]],
    evaluation_label_rows: list[dict[str, object]],
    ontology_edges: list[OntologyEdgePayload],
    bill_signal_rows: list[dict[str, object]],
    bill_semantics: list[BillSemanticPayload],
    training_contribution_signal_rows: list[dict[str, object]],
    evaluation_contribution_signal_rows: list[dict[str, object]],
    training_statement_signal_rows: list[dict[str, object]],
    evaluation_statement_signal_rows: list[dict[str, object]],
) -> PredictionEvalCutoffAuditPayload:
    cutoff_edges = [
        edge for edge in ontology_edges if _edge_available_at_or_before(edge, feature_cutoff)
    ]
    unknown_ontology_edges = [
        edge for edge in ontology_edges if not _edge_has_known_availability(edge)
    ]
    future_ontology_edges = [
        edge
        for edge in ontology_edges
        if _edge_has_known_availability(edge)
        and not _edge_available_at_or_before(edge, feature_cutoff)
    ]
    cutoff_bill_rows = [
        row
        for row in bill_signal_rows
        if _bill_signal_row_available_at_or_before(row, feature_cutoff)
    ]
    unknown_bill_signal_rows = [
        row for row in bill_signal_rows if _bill_signal_row_has_unknown_availability(row)
    ]
    cutoff_training_bill_semantics = [
        semantic
        for semantic in bill_semantics
        if _bill_semantic_available_at_or_before(semantic, training_feature_cutoff)
    ]
    cutoff_evaluation_bill_semantics = [
        semantic
        for semantic in bill_semantics
        if _bill_semantic_available_at_or_before(semantic, feature_cutoff)
    ]
    unknown_availability_bill_semantics = [
        semantic for semantic in bill_semantics if semantic.available_at is None
    ]
    future_training_bill_semantics = [
        semantic
        for semantic in bill_semantics
        if semantic.available_at is not None and semantic.available_at > training_feature_cutoff
    ]
    future_evaluation_bill_semantics = [
        semantic
        for semantic in bill_semantics
        if semantic.available_at is not None and semantic.available_at > feature_cutoff
    ]
    cutoff_training_contribution_rows = _signal_rows_available_at_or_before(
        training_contribution_signal_rows,
        training_feature_cutoff,
        ("contribution_date", "date", "as_of_date"),
    )
    unknown_training_contribution_rows = _signal_rows_with_unknown_availability(
        training_contribution_signal_rows,
        ("contribution_date", "date", "as_of_date"),
    )
    cutoff_evaluation_contribution_rows = _signal_rows_available_at_or_before(
        evaluation_contribution_signal_rows,
        feature_cutoff,
        ("contribution_date", "date", "as_of_date"),
    )
    unknown_evaluation_contribution_rows = _signal_rows_with_unknown_availability(
        evaluation_contribution_signal_rows,
        ("contribution_date", "date", "as_of_date"),
    )
    cutoff_training_statement_rows = _signal_rows_available_at_or_before(
        training_statement_signal_rows,
        training_feature_cutoff,
        ("statement_date", "date", "as_of_date"),
    )
    unknown_training_statement_rows = _signal_rows_with_unknown_availability(
        training_statement_signal_rows,
        ("statement_date", "date", "as_of_date"),
    )
    cutoff_evaluation_statement_rows = _signal_rows_available_at_or_before(
        evaluation_statement_signal_rows,
        feature_cutoff,
        ("statement_date", "date", "as_of_date"),
    )
    unknown_evaluation_statement_rows = _signal_rows_with_unknown_availability(
        evaluation_statement_signal_rows,
        ("statement_date", "date", "as_of_date"),
    )
    return PredictionEvalCutoffAuditPayload(
        training_feature_row_count=len(training_feature_rows),
        evaluation_feature_row_count=len(evaluation_feature_rows),
        training_label_row_count=len(training_label_rows),
        evaluation_label_row_count=len(evaluation_label_rows),
        ontology_edge_count=len(ontology_edges),
        cutoff_ontology_edge_count=len(cutoff_edges),
        unknown_availability_ontology_edge_count=len(unknown_ontology_edges),
        excluded_future_ontology_edge_count=len(future_ontology_edges),
        bill_signal_row_count=len(bill_signal_rows),
        cutoff_bill_signal_row_count=len(cutoff_bill_rows),
        unknown_availability_bill_signal_row_count=len(unknown_bill_signal_rows),
        excluded_future_bill_signal_row_count=(
            len(bill_signal_rows) - len(cutoff_bill_rows) - len(unknown_bill_signal_rows)
        ),
        bill_semantic_count=len(bill_semantics),
        cutoff_training_bill_semantic_count=len(cutoff_training_bill_semantics),
        excluded_future_training_bill_semantic_count=len(future_training_bill_semantics),
        cutoff_evaluation_bill_semantic_count=len(cutoff_evaluation_bill_semantics),
        excluded_future_evaluation_bill_semantic_count=len(future_evaluation_bill_semantics),
        unknown_availability_bill_semantic_count=len(unknown_availability_bill_semantics),
        training_contribution_signal_row_count=len(training_contribution_signal_rows),
        cutoff_training_contribution_signal_row_count=len(cutoff_training_contribution_rows),
        unknown_availability_training_contribution_signal_row_count=(
            len(unknown_training_contribution_rows)
        ),
        excluded_future_training_contribution_signal_row_count=(
            len(training_contribution_signal_rows)
            - len(cutoff_training_contribution_rows)
            - len(unknown_training_contribution_rows)
        ),
        evaluation_contribution_signal_row_count=len(evaluation_contribution_signal_rows),
        cutoff_evaluation_contribution_signal_row_count=len(cutoff_evaluation_contribution_rows),
        unknown_availability_evaluation_contribution_signal_row_count=(
            len(unknown_evaluation_contribution_rows)
        ),
        excluded_future_evaluation_contribution_signal_row_count=(
            len(evaluation_contribution_signal_rows)
            - len(cutoff_evaluation_contribution_rows)
            - len(unknown_evaluation_contribution_rows)
        ),
        training_statement_signal_row_count=len(training_statement_signal_rows),
        cutoff_training_statement_signal_row_count=len(cutoff_training_statement_rows),
        unknown_availability_training_statement_signal_row_count=(
            len(unknown_training_statement_rows)
        ),
        excluded_future_training_statement_signal_row_count=(
            len(training_statement_signal_rows)
            - len(cutoff_training_statement_rows)
            - len(unknown_training_statement_rows)
        ),
        evaluation_statement_signal_row_count=len(evaluation_statement_signal_rows),
        cutoff_evaluation_statement_signal_row_count=len(cutoff_evaluation_statement_rows),
        unknown_availability_evaluation_statement_signal_row_count=(
            len(unknown_evaluation_statement_rows)
        ),
        excluded_future_evaluation_statement_signal_row_count=(
            len(evaluation_statement_signal_rows)
            - len(cutoff_evaluation_statement_rows)
            - len(unknown_evaluation_statement_rows)
        ),
    )


def _bill_signal_row_available_at_or_before(row: dict[str, object], cutoff: date) -> bool:
    introduced_date = _date_from_value(row.get("introduced_date"))
    latest_action_date = _date_from_value(row.get("latest_action_date"))
    bill_available_at = introduced_date or latest_action_date
    if bill_available_at is None or bill_available_at > cutoff:
        return False
    sponsor_date = _date_from_value(row.get("sponsor_date"))
    return row.get("sponsor_bioguide_id") is None or (
        sponsor_date is not None and sponsor_date <= cutoff
    )


def _bill_signal_row_has_unknown_availability(row: dict[str, object]) -> bool:
    introduced_date = _date_from_value(row.get("introduced_date"))
    latest_action_date = _date_from_value(row.get("latest_action_date"))
    if introduced_date is None and latest_action_date is None:
        return True
    return (
        row.get("sponsor_bioguide_id") is not None
        and _date_from_value(row.get("sponsor_date")) is None
    )


def _bill_signal_row_has_source_url(row: dict[str, object]) -> bool:
    return _bill_signal_row_source_url(row) is not None


def _bill_signal_row_source_url(row: dict[str, object]) -> str | None:
    source_type = _bill_signal_row_source_type(row)
    source_url = row.get("bill_source_url")
    if (
        isinstance(source_url, str)
        and source_url.strip()
        and is_official_source_url(source_type, source_url.strip())
    ):
        return source_url.strip()
    if source_type == "congress_bill":
        return _congress_bill_signal_row_source_url(row)
    return None


def _congress_bill_signal_row_source_url(row: dict[str, object]) -> str | None:
    congress = _plain_positive_int(row.get("congress"))
    bill_number = _plain_positive_int(row.get("bill_number"))
    bill_type = row.get("bill_type")
    if congress is None or bill_number is None:
        return None
    if not isinstance(bill_type, str) or not bill_type.strip():
        return None
    return (
        "https://api.congress.gov/v3/bill/"
        f"{congress}/{bill_type.strip().lower()}/{bill_number}?format=json"
    )


def _bill_signal_row_source_type(row: dict[str, object]) -> str:
    value = row.get("bill_source_type")
    if isinstance(value, str) and value.strip():
        source_type = value.strip()
        if source_type in {"congress_bill", "legislative_bill"}:
            return (
                "congress_bill"
                if _row_jurisdiction_id(row) in (None, "", "us_congress")
                else "legislative_bill"
            )
        return source_type
    return (
        "congress_bill"
        if row.get("jurisdiction_id") in (None, "us_congress")
        else "legislative_bill"
    )


def _row_jurisdiction_id(row: dict[str, object]) -> object:
    value = row.get("jurisdiction_id")
    if isinstance(value, str):
        return value.strip()
    return value


def _plain_positive_int(value: object) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _bill_signal_row_key(row: dict[str, object]) -> str | None:
    congress = row.get("congress")
    bill_type = row.get("bill_type")
    bill_number = row.get("bill_number")
    if congress is None or bill_type is None or bill_number is None:
        return None
    try:
        bill_key = (
            f"{int(str(congress))}-{_normalized_bill_type(str(bill_type))}-{int(str(bill_number))}"
        )
    except ValueError:
        return None
    jurisdiction_id = str(row.get("jurisdiction_id") or "us_congress")
    if jurisdiction_id == "us_congress" or _bill_key_has_jurisdiction_prefix(
        bill_key,
        jurisdiction_id,
    ):
        return bill_key
    return f"{jurisdiction_id}:{bill_key}"


def _bill_key_has_jurisdiction_prefix(bill_key: str, jurisdiction_id: str) -> bool:
    return bill_key.startswith((f"{jurisdiction_id}-", f"{jurisdiction_id}:"))


def _normalized_bill_type(raw: str) -> str:
    normalized = "".join(char for char in raw.lower() if char.isalpha())
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


def _signal_rows_available_at_or_before(
    rows: list[dict[str, object]],
    cutoff: date,
    date_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    return [row for row in rows if _signal_row_available_at_or_before(row, cutoff, date_keys)]


def _signal_rows_with_unknown_availability(
    rows: list[dict[str, object]],
    date_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    return [row for row in rows if not _signal_row_has_known_availability(row, date_keys)]


def _signal_row_available_at_or_before(
    row: dict[str, object],
    cutoff: date,
    date_keys: tuple[str, ...],
) -> bool:
    for key in date_keys:
        value = _date_from_value(row.get(key))
        if value is not None:
            return value <= cutoff
    return False


def _signal_row_has_known_availability(
    row: dict[str, object],
    date_keys: tuple[str, ...],
) -> bool:
    return any(_date_from_value(row.get(key)) is not None for key in date_keys)


def _skip_reason_counts(
    predictions: list[PredictionBacktestPredictionPayload],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for prediction in predictions:
        if prediction.skipped_reason is None:
            continue
        counts[prediction.skipped_reason] = counts.get(prediction.skipped_reason, 0) + 1
    return dict(sorted(counts.items()))


def _unavailable_signal_counts(backtests: list[PredictionBacktestPayload]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for backtest in backtests:
        for prediction in backtest.predictions:
            for signal in prediction.unavailable_signals:
                counts[signal] = counts.get(signal, 0) + 1
    return dict(sorted(counts.items()))


def _build_failure_cases(
    backtests: list[PredictionBacktestPayload],
    *,
    limit: int = 25,
) -> list[PredictionEvalFailureCasePayload]:
    cases: list[PredictionEvalFailureCasePayload] = []
    for backtest in backtests:
        for prediction in backtest.predictions:
            if prediction.skipped_reason is not None:
                cases.append(_failure_case(backtest.model_name, prediction, "skipped"))
            elif prediction.correct is False:
                cases.append(_failure_case(backtest.model_name, prediction, "wrong_prediction"))
    return sorted(
        cases,
        key=lambda item: (
            1 if item.failure_kind == "wrong_prediction" else 0,
            item.log_loss or item.brier_score or 0.0,
            -item.vote_event_id,
        ),
        reverse=True,
    )[:limit]


def _build_failure_groups(
    backtests: list[PredictionBacktestPayload],
    *,
    limit: int = 50,
    sample_limit: int = 5,
) -> list[PredictionEvalFailureGroupPayload]:
    groups: dict[
        tuple[str, str, VoteOption, VoteOption | None, str | None, str | None, bool],
        list[PredictionBacktestPredictionPayload],
    ] = {}
    for backtest in backtests:
        for prediction in backtest.predictions:
            failure_kind = _prediction_failure_kind(prediction)
            if failure_kind is None:
                continue
            key = (
                backtest.model_name,
                failure_kind,
                prediction.actual_vote_option,
                prediction.predicted_vote_option,
                prediction.skipped_reason,
                _strongest_signal_name(prediction),
                _prediction_has_source_url(prediction),
            )
            groups.setdefault(key, []).append(prediction)

    payloads = [
        _failure_group_payload(key, predictions, sample_limit=sample_limit)
        for key, predictions in groups.items()
    ]
    return sorted(
        payloads,
        key=lambda item: (
            item.case_count,
            item.average_log_loss or item.average_brier_score or 0.0,
            item.model_name,
            item.failure_kind,
        ),
        reverse=True,
    )[:limit]


def _build_backfill_recommendations(
    *,
    bill_semantic_coverage: PredictionBillSemanticCoveragePayload,
    training_bill_semantic_coverage: PredictionBillSemanticCoveragePayload,
    bill_metadata_coverage: PredictionBillMetadataCoveragePayload,
    dataset: PredictionEvalDatasetPayload,
    unavailable_signal_counts: dict[str, int],
    failure_groups: list[PredictionEvalFailureGroupPayload],
    cutoff_audit: PredictionEvalCutoffAuditPayload,
    source_coverage: list[PredictionFeatureSourceCoveragePayload],
    source_coverage_backtests: list[PredictionBacktestPayload],
) -> list[PredictionEvalBackfillRecommendationPayload]:
    recommendations: list[PredictionEvalBackfillRecommendationPayload] = []
    if bill_metadata_coverage.missing_bill_keys:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="load_missing_bill_metadata",
                priority_score=len(bill_metadata_coverage.missing_bill_keys) * 40,
                reason=(
                    "Some train/eval bills are not loaded as bill metadata, so semantic "
                    "materialization cannot target them yet."
                ),
                affected_case_count=len(bill_metadata_coverage.missing_bill_keys),
                missing_bill_keys=list(bill_metadata_coverage.missing_bill_keys),
                sample_vote_event_ids=_sample_vote_event_ids_for_bill_keys(
                    dataset,
                    set(bill_metadata_coverage.missing_bill_keys),
                ),
                sample_cases=_sample_cases_for_bill_keys(
                    dataset,
                    set(bill_metadata_coverage.missing_bill_keys),
                ),
            )
        )
    missing_bill_keys = sorted(
        set(bill_semantic_coverage.missing_bill_keys)
        | set(training_bill_semantic_coverage.missing_bill_keys)
    )
    if missing_bill_keys:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="materialize_missing_bill_semantics",
                priority_score=len(missing_bill_keys) * 10,
                reason="LLM bill semantics are missing for bills used by the train/eval windows.",
                affected_case_count=len(missing_bill_keys),
                missing_bill_keys=missing_bill_keys,
                sample_vote_event_ids=_sample_vote_event_ids_for_bill_keys(
                    dataset,
                    set(missing_bill_keys),
                ),
                sample_cases=_sample_cases_for_bill_keys(
                    dataset,
                    set(missing_bill_keys),
                ),
            )
        )

    recommendations.extend(
        _signal_backfill_recommendations(
            unavailable_signal_counts=unavailable_signal_counts,
            failure_groups=failure_groups,
            source_coverage_backtests=source_coverage_backtests,
        )
    )
    recommendations.extend(
        _coverage_backfill_recommendations(
            source_coverage,
            source_coverage_backtests=source_coverage_backtests,
        )
    )

    if cutoff_audit.unknown_availability_bill_semantic_count:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="timestamp_bill_semantic_availability",
                priority_score=cutoff_audit.unknown_availability_bill_semantic_count * 5,
                reason="Some bill semantic artifacts lack available_at timestamps, weakening no-leakage audits.",
                affected_case_count=cutoff_audit.unknown_availability_bill_semantic_count,
            )
        )
    if cutoff_audit.unknown_availability_bill_signal_row_count:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="timestamp_bill_signal_availability",
                priority_score=cutoff_audit.unknown_availability_bill_signal_row_count * 5,
                reason=(
                    "Some bill signal rows lack introduced/latest-action or sponsor_date "
                    "timestamps, weakening no-leakage audits."
                ),
                affected_case_count=cutoff_audit.unknown_availability_bill_signal_row_count,
            )
        )
    if cutoff_audit.unknown_availability_ontology_edge_count:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="timestamp_ontology_edge_availability",
                priority_score=cutoff_audit.unknown_availability_ontology_edge_count * 5,
                reason="Some ontology edges lack availability timestamps, weakening no-leakage audits.",
                affected_case_count=cutoff_audit.unknown_availability_ontology_edge_count,
            )
        )
    unknown_contribution_signal_count = (
        cutoff_audit.unknown_availability_training_contribution_signal_row_count
        + cutoff_audit.unknown_availability_evaluation_contribution_signal_row_count
    )
    if unknown_contribution_signal_count:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="timestamp_contribution_signal_availability",
                priority_score=unknown_contribution_signal_count * 5,
                reason=(
                    "Some contribution signal rows lack contribution_date/date/as_of_date "
                    "timestamps, weakening no-leakage audits."
                ),
                affected_case_count=unknown_contribution_signal_count,
            )
        )
    unknown_statement_signal_count = (
        cutoff_audit.unknown_availability_training_statement_signal_row_count
        + cutoff_audit.unknown_availability_evaluation_statement_signal_row_count
    )
    if unknown_statement_signal_count:
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action="timestamp_statement_signal_availability",
                priority_score=unknown_statement_signal_count * 5,
                reason=(
                    "Some public-statement signal rows lack statement_date/date/as_of_date "
                    "timestamps, weakening no-leakage audits."
                ),
                affected_case_count=unknown_statement_signal_count,
            )
        )

    return sorted(
        recommendations,
        key=lambda item: (
            item.priority_score,
            item.affected_case_count,
            item.action,
        ),
        reverse=True,
    )


def _sample_vote_event_ids_for_bill_keys(
    dataset: PredictionEvalDatasetPayload,
    bill_keys: set[str],
    *,
    limit: int = 5,
) -> list[int]:
    samples: list[int] = []
    for split in (dataset.training, dataset.evaluation):
        for example in split.examples:
            if not _example_matches_bill_key(example, bill_keys):
                continue
            if example.vote_event_id not in samples:
                samples.append(example.vote_event_id)
            if len(samples) >= limit:
                return samples
    return samples


def _sample_cases_for_bill_keys(
    dataset: PredictionEvalDatasetPayload,
    bill_keys: set[str],
    *,
    limit: int = 5,
) -> list[PredictionEvalBackfillSampleCasePayload]:
    samples: list[PredictionEvalBackfillSampleCasePayload] = []
    for split in (dataset.training, dataset.evaluation):
        for example in split.examples:
            if not _example_matches_bill_key(example, bill_keys):
                continue
            _append_backfill_sample_case(
                samples,
                PredictionEvalBackfillSampleCasePayload(
                    vote_event_id=example.vote_event_id,
                    event_key=example.event_key,
                    jurisdiction_id=example.jurisdiction_id,
                    legislative_body_id=example.legislative_body_id,
                    legislative_session_id=example.legislative_session_id,
                    bill_key=example.bill_key,
                    bill_context_key=_scoped_sample_bill_context_key(
                        example.bill_key,
                        example.bill_context_key,
                    ),
                    member_bioguide_id=example.member_bioguide_id,
                ),
                limit=limit,
            )
            if len(samples) >= limit:
                return samples
    return samples


def _example_matches_bill_key(
    example: PredictionDatasetExamplePayload,
    bill_keys: set[str],
) -> bool:
    if example.bill_key is None:
        return False
    return example.bill_key in bill_keys or _bill_semantic_coverage_key(example) in bill_keys


def _signal_backfill_recommendations(
    *,
    unavailable_signal_counts: dict[str, int],
    failure_groups: list[PredictionEvalFailureGroupPayload],
    source_coverage_backtests: list[PredictionBacktestPayload],
) -> list[PredictionEvalBackfillRecommendationPayload]:
    recommendations: list[PredictionEvalBackfillRecommendationPayload] = []
    for signal_name, count in sorted(unavailable_signal_counts.items()):
        if count == 0:
            continue
        action, reason = _unavailable_signal_backfill_action(signal_name)
        sample_cases = _sample_cases_for_unavailable_signal_predictions(
            source_coverage_backtests,
            signal_name,
        )
        sample_vote_event_ids = [case.vote_event_id for case in sample_cases]
        if not sample_vote_event_ids:
            sample_vote_event_ids = _sample_vote_event_ids_for_unavailable_signal(
                failure_groups,
                signal_name,
            )
            sample_cases = [
                PredictionEvalBackfillSampleCasePayload(vote_event_id=vote_event_id)
                for vote_event_id in sample_vote_event_ids
            ]
        recommendations.append(
            PredictionEvalBackfillRecommendationPayload(
                action=action,
                priority_score=count * 8,
                reason=reason,
                affected_case_count=count,
                unavailable_signal_counts={signal_name: count},
                sample_vote_event_ids=sample_vote_event_ids,
                sample_cases=sample_cases,
            )
        )
    return recommendations


def _sample_cases_for_unavailable_signal_predictions(
    backtests: list[PredictionBacktestPayload],
    signal_name: str,
    *,
    limit: int = 5,
) -> list[PredictionEvalBackfillSampleCasePayload]:
    samples: list[PredictionEvalBackfillSampleCasePayload] = []
    for backtest in backtests:
        for prediction in backtest.predictions:
            if signal_name not in prediction.unavailable_signals:
                continue
            _append_backfill_sample_case(
                samples,
                _sample_case_from_prediction(prediction),
                limit=limit,
            )
            if len(samples) >= limit:
                return samples
    return samples


def _coverage_backfill_recommendations(
    source_coverage: list[PredictionFeatureSourceCoveragePayload],
    *,
    source_coverage_backtests: list[PredictionBacktestPayload],
) -> list[PredictionEvalBackfillRecommendationPayload]:
    missing_source_count = sum(
        max(0, row.prediction_count - row.url_sourced_prediction_count) for row in source_coverage
    )
    if missing_source_count == 0:
        return []
    return [
        PredictionEvalBackfillRecommendationPayload(
            action="backfill_feature_source_urls",
            priority_score=missing_source_count * 3,
            reason="Some prediction feature signals lack URL-backed source anchors.",
            affected_case_count=missing_source_count,
            sample_vote_event_ids=_sample_vote_event_ids_for_source_url_gaps(
                source_coverage_backtests
            ),
            sample_cases=_sample_cases_for_source_url_gaps(source_coverage_backtests),
        )
    ]


def _sample_vote_event_ids_for_source_url_gaps(
    backtests: list[PredictionBacktestPayload],
    *,
    limit: int = 5,
) -> list[int]:
    samples: list[int] = []
    for backtest in backtests:
        for prediction in backtest.predictions:
            if _prediction_has_source_url_gap(prediction):
                if prediction.vote_event_id not in samples:
                    samples.append(prediction.vote_event_id)
                if len(samples) >= limit:
                    return samples
    return samples


def _sample_cases_for_source_url_gaps(
    backtests: list[PredictionBacktestPayload],
    *,
    limit: int = 5,
) -> list[PredictionEvalBackfillSampleCasePayload]:
    samples: list[PredictionEvalBackfillSampleCasePayload] = []
    for backtest in backtests:
        for prediction in backtest.predictions:
            if _prediction_has_source_url_gap(prediction):
                _append_backfill_sample_case(
                    samples,
                    _sample_case_from_prediction(prediction),
                    limit=limit,
                )
                if len(samples) >= limit:
                    return samples
    return samples


def _sample_case_from_prediction(
    prediction: PredictionBacktestPredictionPayload,
) -> PredictionEvalBackfillSampleCasePayload:
    return PredictionEvalBackfillSampleCasePayload(
        vote_event_id=prediction.vote_event_id,
        event_key=prediction.event_key,
        jurisdiction_id=prediction.jurisdiction_id,
        legislative_body_id=prediction.legislative_body_id,
        legislative_session_id=prediction.legislative_session_id,
        bill_key=prediction.bill_key,
        bill_context_key=_scoped_sample_bill_context_key(
            prediction.bill_key,
            prediction.bill_context_key,
        ),
        member_bioguide_id=prediction.member_bioguide_id,
    )


def _sample_case_from_comparison(
    comparison: PredictionEvalComparisonPayload,
) -> PredictionEvalBackfillSampleCasePayload:
    return PredictionEvalBackfillSampleCasePayload(
        vote_event_id=comparison.vote_event_id,
        event_key=comparison.event_key,
        jurisdiction_id=comparison.jurisdiction_id,
        legislative_body_id=comparison.legislative_body_id,
        legislative_session_id=comparison.legislative_session_id,
        bill_key=comparison.bill_key,
        bill_context_key=_scoped_sample_bill_context_key(
            comparison.bill_key,
            comparison.bill_context_key,
        ),
        member_bioguide_id=comparison.member_bioguide_id,
    )


def _scoped_sample_bill_context_key(
    bill_key: str | None,
    bill_context_key: str | None,
) -> str | None:
    if bill_context_key is None or bill_context_key == bill_key:
        return None
    return bill_context_key


def _append_backfill_sample_case(
    samples: list[PredictionEvalBackfillSampleCasePayload],
    case: PredictionEvalBackfillSampleCasePayload,
    *,
    limit: int,
) -> None:
    if len(samples) >= limit:
        return
    key = (
        case.jurisdiction_id,
        case.legislative_body_id,
        case.legislative_session_id,
        case.event_key,
        case.vote_event_id,
        case.bill_key,
        case.bill_context_key,
        case.member_bioguide_id,
    )
    if any(
        (
            sample.jurisdiction_id,
            sample.legislative_body_id,
            sample.legislative_session_id,
            sample.event_key,
            sample.vote_event_id,
            sample.bill_key,
            sample.bill_context_key,
            sample.member_bioguide_id,
        )
        == key
        for sample in samples
    ):
        return
    samples.append(case)


def _prediction_has_source_url_gap(
    prediction: PredictionBacktestPredictionPayload,
) -> bool:
    for signal_name in prediction.feature_signals:
        anchors = prediction.feature_source_anchors.get(signal_name, [])
        if not anchors:
            return True
        if not any(anchor.url for anchor in anchors):
            return True
    return False


def _sample_vote_event_ids_for_unavailable_signal(
    failure_groups: list[PredictionEvalFailureGroupPayload],
    signal_name: str,
    *,
    limit: int = 5,
) -> list[int]:
    samples: list[int] = []
    for group in failure_groups:
        if signal_name not in group.unavailable_signal_counts:
            continue
        for vote_event_id in group.sample_vote_event_ids:
            if vote_event_id not in samples:
                samples.append(vote_event_id)
            if len(samples) >= limit:
                return samples
    return samples


def _validate_source_url_backfill_recommendation(
    source_coverage: list[PredictionFeatureSourceCoveragePayload],
    recommendations: list[PredictionEvalBackfillRecommendationPayload],
    comparisons: list[PredictionEvalComparisonPayload],
) -> None:
    missing_source_count = sum(
        max(0, row.prediction_count - row.url_sourced_prediction_count) for row in source_coverage
    )
    by_action = {recommendation.action: recommendation for recommendation in recommendations}
    recommendation = by_action.get("backfill_feature_source_urls")
    if missing_source_count == 0:
        return
    if (
        recommendation is None
        or recommendation.affected_case_count != missing_source_count
        or recommendation.priority_score != missing_source_count * 3
        or recommendation.reason
        != "Some prediction feature signals lack URL-backed source anchors."
    ):
        raise ValueError("backfill_recommendations must include source URL action")
    expected_sample_cases = _sample_cases_for_source_url_gap_comparisons(comparisons)
    if [case.model_dump(mode="json") for case in recommendation.sample_cases] != [
        case.model_dump(mode="json") for case in expected_sample_cases
    ]:
        raise ValueError("backfill_recommendations must include source URL sample cases")
    expected_vote_event_ids: list[int] = []
    for case in expected_sample_cases:
        if case.vote_event_id not in expected_vote_event_ids:
            expected_vote_event_ids.append(case.vote_event_id)
    if recommendation.sample_vote_event_ids != expected_vote_event_ids:
        raise ValueError("backfill_recommendations must include source URL sample cases")


def _sample_cases_for_source_url_gap_comparisons(
    comparisons: list[PredictionEvalComparisonPayload],
    *,
    limit: int = 5,
) -> list[PredictionEvalBackfillSampleCasePayload]:
    samples: list[PredictionEvalBackfillSampleCasePayload] = []
    for comparison in comparisons:
        if not _comparison_has_source_url_gap(comparison):
            continue
        _append_backfill_sample_case(
            samples,
            _sample_case_from_comparison(comparison),
            limit=limit,
        )
        if len(samples) >= limit:
            return samples
    return samples


def _comparison_has_source_url_gap(comparison: PredictionEvalComparisonPayload) -> bool:
    for model_name, signals in comparison.feature_signals_by_model.items():
        anchors_by_signal = comparison.feature_source_anchors_by_model.get(model_name, {})
        for signal_name in signals:
            anchors = anchors_by_signal.get(signal_name, [])
            if not anchors:
                return True
            if not any(anchor.url for anchor in anchors):
                return True
    return False


def _prediction_failure_kind(
    prediction: PredictionBacktestPredictionPayload,
) -> str | None:
    if prediction.skipped_reason is not None:
        return "skipped"
    if prediction.correct is False:
        return "wrong_prediction"
    return None


def _failure_group_payload(
    key: tuple[str, str, VoteOption, VoteOption | None, str | None, str | None, bool],
    predictions: list[PredictionBacktestPredictionPayload],
    *,
    sample_limit: int,
) -> PredictionEvalFailureGroupPayload:
    model_name, failure_kind, actual_vote, predicted_vote, skipped_reason, signal, has_url = key
    brier_scores = [
        prediction.brier_score for prediction in predictions if prediction.brier_score is not None
    ]
    log_losses = [
        prediction.log_loss for prediction in predictions if prediction.log_loss is not None
    ]
    return PredictionEvalFailureGroupPayload(
        model_name=model_name,
        failure_kind=failure_kind,
        actual_vote_option=actual_vote,
        predicted_vote_option=predicted_vote,
        skipped_reason=skipped_reason,
        strongest_signal_name=signal,
        has_source_url=has_url,
        case_count=len(predictions),
        average_brier_score=(sum(brier_scores) / len(brier_scores) if brier_scores else None),
        average_log_loss=sum(log_losses) / len(log_losses) if log_losses else None,
        unavailable_signal_counts=_group_unavailable_signal_counts(predictions),
        sample_vote_event_ids=[
            prediction.vote_event_id for prediction in predictions[:sample_limit]
        ],
        sample_cases=[
            _sample_case_from_prediction(prediction) for prediction in predictions[:sample_limit]
        ],
    )


def _group_unavailable_signal_counts(
    predictions: list[PredictionBacktestPredictionPayload],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for prediction in predictions:
        for signal in prediction.unavailable_signals:
            counts[signal] = counts.get(signal, 0) + 1
    return dict(sorted(counts.items()))


def _strongest_signal_name(prediction: PredictionBacktestPredictionPayload) -> str | None:
    if not prediction.feature_signals:
        return None
    return max(
        sorted(prediction.feature_signals),
        key=lambda signal: abs(prediction.feature_signals[signal]),
    )


def _prediction_has_source_url(prediction: PredictionBacktestPredictionPayload) -> bool:
    if prediction.source_url:
        return True
    return any(
        anchor.url for anchors in prediction.feature_source_anchors.values() for anchor in anchors
    )


def _failure_case(
    model_name: str,
    prediction: PredictionBacktestPredictionPayload,
    failure_kind: str,
) -> PredictionEvalFailureCasePayload:
    return PredictionEvalFailureCasePayload(
        model_name=model_name,
        failure_kind=failure_kind,
        vote_event_id=prediction.vote_event_id,
        event_key=prediction.event_key,
        jurisdiction_id=prediction.jurisdiction_id,
        legislative_body_id=prediction.legislative_body_id,
        legislative_session_id=prediction.legislative_session_id,
        vote_date=prediction.vote_date,
        chamber=prediction.chamber,
        question=prediction.question,
        source_url=prediction.source_url,
        member_bioguide_id=prediction.member_bioguide_id,
        member_name=prediction.member_name,
        actual_vote_option=prediction.actual_vote_option,
        predicted_probability_yea=prediction.predicted_probability_yea,
        predicted_vote_option=prediction.predicted_vote_option,
        brier_score=prediction.brier_score,
        log_loss=prediction.log_loss,
        skipped_reason=prediction.skipped_reason,
    )


def _build_comparisons(
    backtests: list[PredictionBacktestPayload],
) -> list[PredictionEvalComparisonPayload]:
    if not backtests:
        return []
    comparison_count = len(backtests[0].predictions)
    comparisons: list[PredictionEvalComparisonPayload] = []
    for index in range(comparison_count):
        first = backtests[0].predictions[index]
        model_probabilities: dict[str, float | None] = {}
        model_predicted_vote_options: dict[str, VoteOption | None] = {}
        model_vote_probabilities: dict[str, dict[VoteOption, float]] = {}
        model_correct: dict[str, bool | None] = {}
        model_skipped_reasons: dict[str, str | None] = {}
        feature_signals_by_model: dict[str, dict[str, float]] = {}
        feature_source_anchors_by_model: dict[str, dict[str, list[SourceAnchor]]] = {}
        unavailable_signals_by_model: dict[str, list[str]] = {}
        for backtest in backtests:
            prediction = backtest.predictions[index]
            model_probabilities[backtest.model_name] = prediction.predicted_probability_yea
            model_predicted_vote_options[backtest.model_name] = prediction.predicted_vote_option
            model_vote_probabilities[backtest.model_name] = dict(
                prediction.predicted_vote_probabilities
            )
            model_correct[backtest.model_name] = prediction.correct
            model_skipped_reasons[backtest.model_name] = prediction.skipped_reason
            feature_signals_by_model[backtest.model_name] = dict(prediction.feature_signals)
            feature_source_anchors_by_model[backtest.model_name] = {
                signal_name: list(anchors)
                for signal_name, anchors in prediction.feature_source_anchors.items()
            }
            unavailable_signals_by_model[backtest.model_name] = list(prediction.unavailable_signals)
        comparisons.append(
            PredictionEvalComparisonPayload(
                vote_event_id=first.vote_event_id,
                event_key=first.event_key,
                jurisdiction_id=first.jurisdiction_id,
                legislative_body_id=first.legislative_body_id,
                legislative_session_id=first.legislative_session_id,
                vote_date=first.vote_date,
                chamber=first.chamber,
                question=first.question,
                source_url=first.source_url,
                bill_key=first.bill_key,
                bill_context_key=first.bill_context_key,
                member_bioguide_id=first.member_bioguide_id,
                member_name=first.member_name,
                actual_vote_option=first.actual_vote_option,
                model_probabilities=model_probabilities,
                model_predicted_vote_options=model_predicted_vote_options,
                model_vote_probabilities=model_vote_probabilities,
                model_correct=model_correct,
                model_skipped_reasons=model_skipped_reasons,
                feature_signals_by_model=feature_signals_by_model,
                feature_source_anchors_by_model=feature_source_anchors_by_model,
                unavailable_signals_by_model=unavailable_signals_by_model,
            )
        )
    return comparisons


def _logit(value: float) -> float:
    return math.log(value / (1.0 - value))
