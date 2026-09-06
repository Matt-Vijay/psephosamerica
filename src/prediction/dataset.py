from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from src.evidence.source_anchor_policy import (
    describe_missing_source_anchor_urls,
    is_official_source_url,
)
from src.export.contracts import SourceAnchor
from src.prediction.backtest import (
    PredictionBacktestPayload,
    PredictionBacktestPredictionPayload,
    VoteOption,
    build_vote_ontology_backtest,
)
from src.prediction.llm_semantics import BillSemanticPayload
from src.prediction.source_anchors import describe_missing_legislative_source_context

if TYPE_CHECKING:
    from src.ontology.contracts import OntologyEdgePayload


PredictionDatasetSplit = Literal["training", "evaluation"]


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


class PredictionDatasetExamplePayload(BaseModel):
    """One model-ready or skipped member-vote row in a temporal prediction dataset."""

    split: PredictionDatasetSplit
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
    binary_label: int | None = Field(default=None, ge=0, le=1)
    features: dict[str, float] = Field(default_factory=dict)
    feature_source_anchors: dict[str, list[SourceAnchor]] = Field(default_factory=dict)
    unavailable_signals: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None

    @field_validator("source_url", "skipped_reason", mode="before")
    @classmethod
    def strip_optional_text_fields(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("vote_event_id", "binary_label", mode="before")
    @classmethod
    def example_int_fields_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def feature_sources_match_dense_features(self) -> Self:
        if self.jurisdiction_id != "us_congress" and not self.legislative_body_id:
            raise ValueError("non-Congress examples require legislative_body_id")
        if self.jurisdiction_id != "us_congress" and not self.legislative_session_id:
            raise ValueError("non-Congress examples require legislative_session_id")
        _ensure_sorted_unique_nonblank(
            self.unavailable_signals,
            field_name="unavailable_signals",
        )
        if self.skipped_reason == "":
            raise ValueError("skipped examples require skipped_reason")
        if self.source_url is not None and not is_official_source_url(
            _vote_source_type(self.jurisdiction_id),
            self.source_url,
        ):
            raise ValueError("source_url must be an official vote source URL")
        if any(not anchors for anchors in self.feature_source_anchors.values()):
            raise ValueError("feature_source_anchors entries must not be empty")
        orphan_source_signals = sorted(set(self.feature_source_anchors) - set(self.features))
        if orphan_source_signals:
            raise ValueError(
                "feature_source_anchors keys must be present in features: "
                + ", ".join(orphan_source_signals)
            )
        for signal_name, anchors in sorted(self.feature_source_anchors.items()):
            missing_source_urls = describe_missing_source_anchor_urls(anchors)
            if missing_source_urls:
                raise ValueError(
                    "feature_source_anchors require official source URLs "
                    f"for {signal_name}: {missing_source_urls}"
                )
            missing_source_context = describe_missing_legislative_source_context(anchors)
            if missing_source_context:
                raise ValueError(
                    "feature_source_anchors require legislative source context "
                    f"for {signal_name}: {missing_source_context}"
                )
        return self


class PredictionDatasetSplitPayload(BaseModel):
    """Dense feature matrix for one temporal split."""

    split: PredictionDatasetSplit
    feature_cutoff: date
    label_start: date
    label_end: date
    label_count: int = Field(ge=0)
    model_ready_example_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    non_binary_label_count: int = Field(default=0, ge=0)
    source_url_count: int = Field(ge=0)
    examples: list[PredictionDatasetExamplePayload] = Field(default_factory=list)

    @field_validator(
        "label_count",
        "model_ready_example_count",
        "skipped_count",
        "non_binary_label_count",
        "source_url_count",
        mode="before",
    )
    @classmethod
    def split_counts_are_plain_ints(cls, value: object, info: object) -> object:
        return _reject_boolean_int(value, getattr(info, "field_name", "count"))

    @model_validator(mode="after")
    def counts_match_examples(self) -> Self:
        if self.feature_cutoff >= self.label_start:
            raise ValueError("feature_cutoff must be before label_start")
        if self.label_start > self.label_end:
            raise ValueError("label_start must be on or before label_end")
        if self.label_count != len(self.examples):
            raise ValueError("label_count must match examples length")
        ready_count = sum(
            1
            for example in self.examples
            if example.skipped_reason is None and example.binary_label is not None
        )
        if self.model_ready_example_count != ready_count:
            raise ValueError("model_ready_example_count must match examples")
        skipped_count = sum(1 for example in self.examples if example.skipped_reason is not None)
        if self.skipped_count != skipped_count:
            raise ValueError("skipped_count must match examples")
        non_binary_label_count = sum(1 for example in self.examples if example.binary_label is None)
        if self.non_binary_label_count != non_binary_label_count:
            raise ValueError("non_binary_label_count must match examples")
        source_url_count = sum(1 for example in self.examples if example.source_url is not None)
        if self.source_url_count != source_url_count:
            raise ValueError("source_url_count must match examples")
        if any(example.split != self.split for example in self.examples):
            raise ValueError("all examples must match split")
        if any(
            example.vote_date < self.label_start or example.vote_date > self.label_end
            for example in self.examples
        ):
            raise ValueError("examples must stay inside the split label window")
        return self


class PredictionEvalDatasetPayload(BaseModel):
    """Cutoff-safe train/evaluation matrices used by prediction evaluation."""

    training_feature_cutoff: date
    train_start: date
    train_end: date
    feature_cutoff: date
    label_start: date
    label_end: date
    feature_names: list[str] = Field(default_factory=list)
    training: PredictionDatasetSplitPayload
    evaluation: PredictionDatasetSplitPayload

    @model_validator(mode="after")
    def feature_matrix_is_dense(self) -> Self:
        validate_eval_windows(
            training_feature_cutoff=self.training_feature_cutoff,
            train_start=self.train_start,
            train_end=self.train_end,
            feature_cutoff=self.feature_cutoff,
            label_start=self.label_start,
            label_end=self.label_end,
        )
        if (
            self.training.feature_cutoff != self.training_feature_cutoff
            or self.training.label_start != self.train_start
            or self.training.label_end != self.train_end
        ):
            raise ValueError("training split window must match dataset")
        if (
            self.evaluation.feature_cutoff != self.feature_cutoff
            or self.evaluation.label_start != self.label_start
            or self.evaluation.label_end != self.label_end
        ):
            raise ValueError("evaluation split window must match dataset")
        _ensure_sorted_unique_nonblank(self.feature_names, field_name="feature_names")
        expected = set(self.feature_names)
        for split in (self.training, self.evaluation):
            for example in split.examples:
                if set(example.features) != expected:
                    raise ValueError("all examples must include the full dense feature set")
        return self


def build_prediction_eval_dataset(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    training_feature_rows: list[dict[str, Any]],
    evaluation_feature_rows: list[dict[str, Any]],
    training_label_rows: list[dict[str, Any]],
    evaluation_label_rows: list[dict[str, Any]],
    ontology_edges: list[OntologyEdgePayload],
    bill_signal_rows: list[dict[str, Any]],
    bill_semantics: list[BillSemanticPayload] | None = None,
    contribution_signal_rows: list[dict[str, Any]] | None = None,
    statement_signal_rows: list[dict[str, Any]] | None = None,
    training_contribution_signal_rows: list[dict[str, Any]] | None = None,
    evaluation_contribution_signal_rows: list[dict[str, Any]] | None = None,
    training_statement_signal_rows: list[dict[str, Any]] | None = None,
    evaluation_statement_signal_rows: list[dict[str, Any]] | None = None,
) -> PredictionEvalDatasetPayload:
    """Build dense train/eval feature matrices from the same cutoff-safe scorer inputs."""
    validate_eval_windows(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
    )
    training_backtest = build_vote_ontology_backtest(
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
    evaluation_backtest = build_vote_ontology_backtest(
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
    feature_names = _feature_names(training_backtest, evaluation_backtest)
    return PredictionEvalDatasetPayload(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_names=feature_names,
        training=_split_payload(
            "training",
            backtest=training_backtest,
            feature_names=feature_names,
        ),
        evaluation=_split_payload(
            "evaluation",
            backtest=evaluation_backtest,
            feature_names=feature_names,
        ),
    )


def build_prediction_eval_dataset_from_backtests(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    training_backtest: PredictionBacktestPayload,
    evaluation_backtest: PredictionBacktestPayload,
) -> PredictionEvalDatasetPayload:
    """Build the dataset artifact when ontology backtests were already computed."""
    validate_eval_windows(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
    )
    feature_names = _feature_names(training_backtest, evaluation_backtest)
    return PredictionEvalDatasetPayload(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_names=feature_names,
        training=_split_payload(
            "training",
            backtest=training_backtest,
            feature_names=feature_names,
        ),
        evaluation=_split_payload(
            "evaluation",
            backtest=evaluation_backtest,
            feature_names=feature_names,
        ),
    )


def validate_eval_windows(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
) -> None:
    if training_feature_cutoff >= train_start:
        raise ValueError("training_feature_cutoff must be before train_start")
    if train_start > train_end:
        raise ValueError("train_start must be on or before train_end")
    if feature_cutoff >= label_start:
        raise ValueError("feature_cutoff must be before label_start")
    if label_start > label_end:
        raise ValueError("label_start must be on or before label_end")
    if train_end > feature_cutoff:
        raise ValueError("train_end must be on or before feature_cutoff")


def _feature_names(*backtests: PredictionBacktestPayload) -> list[str]:
    return sorted(
        {
            signal
            for backtest in backtests
            for prediction in backtest.predictions
            for signal in prediction.feature_signals
        }
    )


def _split_payload(
    split: PredictionDatasetSplit,
    *,
    backtest: PredictionBacktestPayload,
    feature_names: list[str],
) -> PredictionDatasetSplitPayload:
    examples = [
        _example_payload(split, prediction, feature_names=feature_names)
        for prediction in backtest.predictions
    ]
    return PredictionDatasetSplitPayload(
        split=split,
        feature_cutoff=backtest.feature_cutoff,
        label_start=backtest.label_start,
        label_end=backtest.label_end,
        label_count=len(examples),
        model_ready_example_count=sum(
            1
            for example in examples
            if example.skipped_reason is None and example.binary_label is not None
        ),
        skipped_count=sum(1 for example in examples if example.skipped_reason is not None),
        non_binary_label_count=sum(1 for example in examples if example.binary_label is None),
        source_url_count=sum(1 for example in examples if example.source_url is not None),
        examples=examples,
    )


def _example_payload(
    split: PredictionDatasetSplit,
    prediction: PredictionBacktestPredictionPayload,
    *,
    feature_names: list[str],
) -> PredictionDatasetExamplePayload:
    binary_label = _binary_label(prediction.actual_vote_option)
    return PredictionDatasetExamplePayload(
        split=split,
        vote_event_id=prediction.vote_event_id,
        event_key=prediction.event_key,
        jurisdiction_id=prediction.jurisdiction_id,
        legislative_body_id=prediction.legislative_body_id,
        legislative_session_id=prediction.legislative_session_id,
        vote_date=prediction.vote_date,
        chamber=prediction.chamber,
        question=prediction.question,
        source_url=prediction.source_url,
        bill_key=prediction.bill_key,
        bill_context_key=prediction.bill_context_key,
        member_bioguide_id=prediction.member_bioguide_id,
        member_name=prediction.member_name,
        actual_vote_option=prediction.actual_vote_option,
        binary_label=binary_label,
        features={name: float(prediction.feature_signals.get(name, 0.0)) for name in feature_names},
        feature_source_anchors={
            signal_name: list(anchors)
            for signal_name, anchors in prediction.feature_source_anchors.items()
        },
        unavailable_signals=list(prediction.unavailable_signals),
        skipped_reason=prediction.skipped_reason,
    )


def _binary_label(vote_option: str) -> int | None:
    if vote_option == "yea":
        return 1
    if vote_option == "nay":
        return 0
    return None


def _vote_source_type(jurisdiction_id: str) -> str:
    return "vote_event" if jurisdiction_id == "us_congress" else "legislative_vote"
