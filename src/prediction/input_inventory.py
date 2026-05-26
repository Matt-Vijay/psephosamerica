from __future__ import annotations

from datetime import date
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from src.evidence.source_anchor_policy import (
    SOURCE_TYPES_REQUIRING_URL,
    anchor_source_type,
    anchor_url,
    is_official_source_url,
)
from src.ingest.congress.senate_votes import roll_call_url as senate_roll_call_url


class PredictionInputInventoryPayload(BaseModel):
    training_feature_cutoff: date
    train_start: date
    train_end: date
    feature_cutoff: date
    label_start: date
    label_end: date
    training_feature_member_count: int = Field(ge=0)
    evaluation_feature_member_count: int = Field(ge=0)
    training_feature_vote_history_member_count: int | None = Field(default=None, ge=0)
    evaluation_feature_vote_history_member_count: int | None = Field(default=None, ge=0)
    training_feature_vote_history_source_member_count: int | None = Field(default=None, ge=0)
    evaluation_feature_vote_history_source_member_count: int | None = Field(default=None, ge=0)
    training_feature_vote_history_source_coverage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    evaluation_feature_vote_history_source_coverage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    training_label_count: int = Field(ge=0)
    evaluation_label_count: int = Field(ge=0)
    training_labels_missing_feature_member_count: int = Field(default=0, ge=0)
    evaluation_labels_missing_feature_member_count: int = Field(default=0, ge=0)
    training_label_source_url_count: int = Field(ge=0)
    evaluation_label_source_url_count: int = Field(ge=0)
    training_label_source_url_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluation_label_source_url_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    official_training_label_source_url_count: int | None = Field(default=None, ge=0)
    official_evaluation_label_source_url_count: int | None = Field(default=None, ge=0)
    training_label_official_source_url_coverage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    evaluation_label_official_source_url_coverage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    bill_count: int = Field(ge=0)
    bill_source_url_count: int = Field(ge=0)
    bill_source_url_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    official_bill_source_url_count: int | None = Field(default=None, ge=0)
    bill_official_source_url_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    bill_sponsor_count: int = Field(default=0, ge=0)
    bill_available_sponsor_count: int = Field(default=0, ge=0)
    bill_sponsor_availability_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    bill_primary_sponsor_introduced_date_fallback_count: int = Field(default=0, ge=0)
    ontology_edge_count: int = Field(ge=0)
    sourced_ontology_edge_count: int = Field(ge=0)
    ontology_source_anchor_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    official_sourced_ontology_edge_count: int | None = Field(default=None, ge=0)
    ontology_official_source_anchor_coverage_rate: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    fec_contribution_count: int = Field(ge=0)
    member_attributed_fec_contribution_count: int = Field(ge=0)
    fec_member_attribution_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    members_with_fec_candidate_id_count: int = Field(ge=0)
    public_statement_signal_count: int = Field(ge=0)
    members_with_public_statement_signal_count: int = Field(ge=0)
    public_statement_ontology_edge_count: int = Field(default=0, ge=0)
    public_statement_prediction_member_overlap_count: int = Field(default=0, ge=0)
    jurisdiction_count: int = Field(default=0, ge=0)
    implemented_jurisdiction_count: int = Field(default=0, ge=0)
    portable_jurisdiction_count: int = Field(default=0, ge=0)
    portable_rows_missing_jurisdiction_id_count: int = Field(default=0, ge=0)
    portable_rows_missing_body_id_count: int = Field(default=0, ge=0)
    portable_rows_missing_session_id_count: int = Field(default=0, ge=0)
    legislative_body_count: int = Field(default=0, ge=0)
    legislative_session_count: int = Field(default=0, ge=0)
    source_family_count: int = Field(default=0, ge=0)
    jurisdiction_ids: list[str] = Field(default_factory=list)
    implemented_jurisdiction_ids: list[str] = Field(default_factory=list)
    portable_jurisdiction_ids: list[str] = Field(default_factory=list)
    legislative_body_ids: list[str] = Field(default_factory=list)
    legislative_session_ids: list[str] = Field(default_factory=list)
    source_family_ids: list[str] = Field(default_factory=list)
    ok: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    warning_reasons: list[str] = Field(default_factory=list)

    @field_validator(
        "training_feature_member_count",
        "evaluation_feature_member_count",
        "training_feature_vote_history_member_count",
        "evaluation_feature_vote_history_member_count",
        "training_feature_vote_history_source_member_count",
        "evaluation_feature_vote_history_source_member_count",
        "training_label_count",
        "evaluation_label_count",
        "training_labels_missing_feature_member_count",
        "evaluation_labels_missing_feature_member_count",
        "training_label_source_url_count",
        "evaluation_label_source_url_count",
        "official_training_label_source_url_count",
        "official_evaluation_label_source_url_count",
        "bill_count",
        "bill_source_url_count",
        "official_bill_source_url_count",
        "bill_sponsor_count",
        "bill_available_sponsor_count",
        "bill_primary_sponsor_introduced_date_fallback_count",
        "ontology_edge_count",
        "sourced_ontology_edge_count",
        "official_sourced_ontology_edge_count",
        "fec_contribution_count",
        "member_attributed_fec_contribution_count",
        "members_with_fec_candidate_id_count",
        "public_statement_signal_count",
        "members_with_public_statement_signal_count",
        "public_statement_ontology_edge_count",
        "public_statement_prediction_member_overlap_count",
        "jurisdiction_count",
        "implemented_jurisdiction_count",
        "portable_jurisdiction_count",
        "portable_rows_missing_jurisdiction_id_count",
        "portable_rows_missing_body_id_count",
        "portable_rows_missing_session_id_count",
        "legislative_body_count",
        "legislative_session_count",
        "source_family_count",
        mode="before",
    )
    @classmethod
    def count_fields_are_plain_ints(cls, value: object, info: object) -> object:
        if isinstance(value, bool):
            raise ValueError(f"{getattr(info, 'field_name', 'count')} must be an integer")
        return value

    @model_validator(mode="after")
    def payload_invariants_hold(self) -> Self:
        _ensure_unique_nonblank(self.blocking_reasons, field_name="blocking_reasons")
        _ensure_unique_nonblank(self.warning_reasons, field_name="warning_reasons")
        if set(self.blocking_reasons) & set(self.warning_reasons):
            raise ValueError("blocking_reasons and warning_reasons must not overlap")
        if (
            self.implemented_jurisdiction_count + self.portable_jurisdiction_count
            != self.jurisdiction_count
        ):
            raise ValueError("jurisdiction status counts must sum to jurisdiction_count")
        self._validate_legislative_context_ids()
        if self.training_feature_cutoff >= self.train_start:
            raise ValueError("training_feature_cutoff must be before train_start")
        if self.train_start > self.train_end:
            raise ValueError("train_start must be on or before train_end")
        if self.feature_cutoff >= self.label_start:
            raise ValueError("feature_cutoff must be before label_start")
        if self.label_start > self.label_end:
            raise ValueError("label_start must be on or before label_end")
        if self.ok and self.blocking_reasons:
            raise ValueError("ok inventory cannot carry blocking reasons")
        if not self.ok and not self.blocking_reasons:
            raise ValueError("non-ok inventory must carry blocking reasons")
        required_blocking_reasons = {
            reason
            for count, reason in (
                (
                    self.portable_rows_missing_jurisdiction_id_count,
                    "portable_source_rows_missing_jurisdiction_ids",
                ),
                (
                    self.portable_rows_missing_body_id_count,
                    "portable_jurisdiction_rows_missing_body_ids",
                ),
                (
                    self.portable_rows_missing_session_id_count,
                    "portable_jurisdiction_rows_missing_session_ids",
                ),
            )
            if count
        }
        missing_blocking_reasons = sorted(required_blocking_reasons - set(self.blocking_reasons))
        if missing_blocking_reasons:
            raise ValueError(
                "portable missing-context counts require blocking reasons: "
                + ", ".join(missing_blocking_reasons)
            )
        self._validate_coverage_counts_and_rates()
        return self

    def _validate_legislative_context_ids(self) -> None:
        list_fields = (
            ("jurisdiction_ids", self.jurisdiction_ids),
            ("implemented_jurisdiction_ids", self.implemented_jurisdiction_ids),
            ("portable_jurisdiction_ids", self.portable_jurisdiction_ids),
            ("legislative_body_ids", self.legislative_body_ids),
            ("legislative_session_ids", self.legislative_session_ids),
            ("source_family_ids", self.source_family_ids),
        )
        for field_name, values in list_fields:
            _ensure_sorted_unique_nonblank(values, field_name=field_name)
        if self.jurisdiction_count != len(self.jurisdiction_ids):
            raise ValueError("jurisdiction_count must match jurisdiction_ids")
        if self.implemented_jurisdiction_count != len(self.implemented_jurisdiction_ids):
            raise ValueError(
                "implemented_jurisdiction_count must match implemented_jurisdiction_ids"
            )
        if self.portable_jurisdiction_count != len(self.portable_jurisdiction_ids):
            raise ValueError("portable_jurisdiction_count must match portable_jurisdiction_ids")
        if self.legislative_body_count != len(self.legislative_body_ids):
            raise ValueError("legislative_body_count must match legislative_body_ids")
        if self.legislative_session_count != len(self.legislative_session_ids):
            raise ValueError("legislative_session_count must match legislative_session_ids")
        if self.source_family_count != len(self.source_family_ids):
            raise ValueError("source_family_count must match source_family_ids")
        if set(self.implemented_jurisdiction_ids) | set(self.portable_jurisdiction_ids) != set(
            self.jurisdiction_ids
        ):
            raise ValueError("jurisdiction status ids must cover jurisdiction_ids")
        if set(self.implemented_jurisdiction_ids) & set(self.portable_jurisdiction_ids):
            raise ValueError("jurisdiction status ids must not overlap")
        jurisdiction_ids = set(self.jurisdiction_ids)
        for body_id in self.legislative_body_ids:
            parts = body_id.split(":")
            if len(parts) != 2 or parts[0] not in jurisdiction_ids or not parts[1]:
                raise ValueError("legislative_body_ids must be jurisdiction scoped")
        body_ids = set(self.legislative_body_ids)
        for session_id in self.legislative_session_ids:
            parts = session_id.split(":")
            if (
                len(parts) != 3
                or parts[0] not in jurisdiction_ids
                or f"{parts[0]}:{parts[1]}" not in body_ids
                or not parts[2]
            ):
                raise ValueError("legislative_session_ids must be body scoped")

    def _validate_coverage_counts_and_rates(self) -> None:
        checks = (
            (
                "training_feature_vote_history_source_member_count",
                self.training_feature_vote_history_source_member_count,
                self.training_feature_vote_history_member_count,
                "training_feature_vote_history_source_coverage_rate",
                self.training_feature_vote_history_source_coverage_rate,
            ),
            (
                "evaluation_feature_vote_history_source_member_count",
                self.evaluation_feature_vote_history_source_member_count,
                self.evaluation_feature_vote_history_member_count,
                "evaluation_feature_vote_history_source_coverage_rate",
                self.evaluation_feature_vote_history_source_coverage_rate,
            ),
            (
                "training_label_source_url_count",
                self.training_label_source_url_count,
                self.training_label_count,
                "training_label_source_url_coverage_rate",
                self.training_label_source_url_coverage_rate,
            ),
            (
                "evaluation_label_source_url_count",
                self.evaluation_label_source_url_count,
                self.evaluation_label_count,
                "evaluation_label_source_url_coverage_rate",
                self.evaluation_label_source_url_coverage_rate,
            ),
            (
                "bill_source_url_count",
                self.bill_source_url_count,
                self.bill_count,
                "bill_source_url_coverage_rate",
                self.bill_source_url_coverage_rate,
            ),
            (
                "official_training_label_source_url_count",
                self.official_training_label_source_url_count,
                self.training_label_count,
                "training_label_official_source_url_coverage_rate",
                self.training_label_official_source_url_coverage_rate,
            ),
            (
                "official_evaluation_label_source_url_count",
                self.official_evaluation_label_source_url_count,
                self.evaluation_label_count,
                "evaluation_label_official_source_url_coverage_rate",
                self.evaluation_label_official_source_url_coverage_rate,
            ),
            (
                "official_bill_source_url_count",
                self.official_bill_source_url_count,
                self.bill_count,
                "bill_official_source_url_coverage_rate",
                self.bill_official_source_url_coverage_rate,
            ),
            (
                "bill_available_sponsor_count",
                self.bill_available_sponsor_count,
                self.bill_sponsor_count,
                "bill_sponsor_availability_rate",
                self.bill_sponsor_availability_rate,
            ),
            (
                "sourced_ontology_edge_count",
                self.sourced_ontology_edge_count,
                self.ontology_edge_count,
                "ontology_source_anchor_coverage_rate",
                self.ontology_source_anchor_coverage_rate,
            ),
            (
                "official_sourced_ontology_edge_count",
                self.official_sourced_ontology_edge_count,
                self.ontology_edge_count,
                "ontology_official_source_anchor_coverage_rate",
                self.ontology_official_source_anchor_coverage_rate,
            ),
            (
                "member_attributed_fec_contribution_count",
                self.member_attributed_fec_contribution_count,
                self.fec_contribution_count,
                "fec_member_attribution_rate",
                self.fec_member_attribution_rate,
            ),
        )
        for count_key, numerator, denominator, rate_key, actual_rate in checks:
            if numerator is None or denominator is None:
                continue
            if numerator > denominator:
                raise ValueError(f"coverage count exceeds total: {count_key}")
            expected_rate = _rate(numerator, denominator)
            if actual_rate != expected_rate:
                raise ValueError(f"coverage rate mismatch: {rate_key}")
        official_source_count_checks = (
            (
                "official_training_label_source_url_count",
                self.official_training_label_source_url_count,
                self.training_label_source_url_count,
            ),
            (
                "official_evaluation_label_source_url_count",
                self.official_evaluation_label_source_url_count,
                self.evaluation_label_source_url_count,
            ),
            (
                "official_bill_source_url_count",
                self.official_bill_source_url_count,
                self.bill_source_url_count,
            ),
            (
                "official_sourced_ontology_edge_count",
                self.official_sourced_ontology_edge_count,
                self.sourced_ontology_edge_count,
            ),
        )
        for count_key, official_count, source_count in official_source_count_checks:
            if official_count is not None and official_count > source_count:
                raise ValueError(f"official coverage count exceeds source count: {count_key}")
        if (
            self.bill_primary_sponsor_introduced_date_fallback_count
            > self.bill_available_sponsor_count
        ):
            raise ValueError(
                "primary sponsor introduced-date fallback count exceeds available sponsors"
            )


def build_prediction_input_inventory(
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
    bill_rows: list[dict[str, Any]],
    ontology_edge_rows: list[dict[str, Any]],
    fec_inventory: dict[str, Any],
) -> PredictionInputInventoryPayload:
    blocking: list[str] = []
    warnings: list[str] = []
    if not training_feature_rows:
        blocking.append("missing_training_feature_members")
        if training_label_rows and evaluation_feature_rows:
            warnings.append("training_feature_members_excluded_by_cutoff_or_term_window")
    if not evaluation_feature_rows:
        blocking.append("missing_evaluation_feature_members")
    if not training_label_rows:
        blocking.append("missing_training_labels")
    if not evaluation_label_rows:
        blocking.append("missing_evaluation_labels")
    training_missing_feature_member_count = _label_members_missing_from_features(
        training_label_rows,
        training_feature_rows,
    )
    evaluation_missing_feature_member_count = _label_members_missing_from_features(
        evaluation_label_rows,
        evaluation_feature_rows,
    )
    if training_missing_feature_member_count:
        blocking.append("training_labels_missing_feature_members")
    if evaluation_missing_feature_member_count:
        blocking.append("evaluation_labels_missing_feature_members")
    if not ontology_edge_rows:
        warnings.append("missing_ontology_edges")
    if not bill_rows:
        warnings.append("missing_bills")

    training_feature_vote_history_count = _feature_vote_history_member_count(training_feature_rows)
    evaluation_feature_vote_history_count = _feature_vote_history_member_count(
        evaluation_feature_rows
    )
    training_feature_vote_history_source_count = _feature_vote_history_source_member_count(
        training_feature_rows
    )
    evaluation_feature_vote_history_source_count = _feature_vote_history_source_member_count(
        evaluation_feature_rows
    )
    training_source_count = _source_url_count(training_label_rows)
    evaluation_source_count = _source_url_count(evaluation_label_rows)
    bill_source_count = _bill_source_url_count(bill_rows)
    official_training_source_count = _official_label_source_url_count(training_label_rows)
    official_evaluation_source_count = _official_label_source_url_count(evaluation_label_rows)
    official_bill_source_count = _official_bill_source_url_count(bill_rows)
    bill_sponsor_count = _bill_sponsor_count(bill_rows)
    bill_available_sponsor_count = _bill_available_sponsor_count(
        bill_rows,
        feature_cutoff=feature_cutoff,
    )
    bill_primary_sponsor_introduced_date_fallback_count = (
        _bill_primary_sponsor_introduced_date_fallback_count(
            bill_rows,
            feature_cutoff=feature_cutoff,
        )
    )
    sourced_ontology_edges = sum(1 for row in ontology_edge_rows if row.get("source_anchors"))
    official_sourced_ontology_edges = _official_source_anchor_edge_count(ontology_edge_rows)
    fec_count = _int_value(fec_inventory.get("fec_contribution_count"))
    attributed_fec_count = _int_value(fec_inventory.get("member_attributed_fec_contribution_count"))
    members_with_fec = _int_value(fec_inventory.get("members_with_fec_candidate_id_count"))
    statement_signal_count = _int_value(fec_inventory.get("public_statement_signal_count"))
    members_with_statement_signal = _int_value(
        fec_inventory.get("members_with_public_statement_signal_count")
    )
    statement_ontology_edge_count = _public_statement_ontology_edge_count(ontology_edge_rows)
    statement_prediction_member_overlap_count = _public_statement_prediction_member_overlap_count(
        ontology_edge_rows,
        [*training_feature_rows, *evaluation_feature_rows],
    )
    (
        jurisdiction_ids,
        implemented_jurisdiction_ids,
        portable_jurisdiction_ids,
        legislative_body_ids,
        legislative_session_ids,
    ) = _legislative_context_counts(
        training_feature_rows,
        evaluation_feature_rows,
        training_label_rows,
        evaluation_label_rows,
        bill_rows,
        ontology_edge_rows,
    )
    source_family_ids = _source_family_ids(
        training_feature_rows,
        evaluation_feature_rows,
        training_label_rows,
        evaluation_label_rows,
        bill_rows,
        ontology_edge_rows,
    )
    if fec_count and attributed_fec_count == 0:
        warnings.append("fec_contributions_not_attributed_to_members")
    if fec_count and members_with_fec == 0:
        warnings.append("missing_member_fec_candidate_ids")
    if statement_signal_count == 0:
        warnings.append("missing_public_statement_signals")
    if statement_ontology_edge_count and statement_prediction_member_overlap_count == 0:
        warnings.append("public_statement_signals_missing_prediction_member_overlap")
    portable_rows_missing_session_count = _portable_rows_missing_session_id_count(
        training_feature_rows,
        evaluation_feature_rows,
        training_label_rows,
        evaluation_label_rows,
        bill_rows,
        ontology_edge_rows,
    )
    portable_rows_missing_body_count = _portable_rows_missing_body_id_count(
        training_feature_rows,
        evaluation_feature_rows,
        training_label_rows,
        evaluation_label_rows,
        bill_rows,
        ontology_edge_rows,
    )
    portable_rows_missing_jurisdiction_count = _portable_rows_missing_jurisdiction_id_count(
        training_feature_rows,
        evaluation_feature_rows,
        training_label_rows,
        evaluation_label_rows,
        bill_rows,
        ontology_edge_rows,
    )
    if portable_rows_missing_jurisdiction_count:
        blocking.append("portable_source_rows_missing_jurisdiction_ids")
    if portable_rows_missing_body_count:
        blocking.append("portable_jurisdiction_rows_missing_body_ids")
    if portable_rows_missing_session_count:
        blocking.append("portable_jurisdiction_rows_missing_session_ids")

    return PredictionInputInventoryPayload(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        training_feature_member_count=len(training_feature_rows),
        evaluation_feature_member_count=len(evaluation_feature_rows),
        training_feature_vote_history_member_count=training_feature_vote_history_count,
        evaluation_feature_vote_history_member_count=evaluation_feature_vote_history_count,
        training_feature_vote_history_source_member_count=(
            training_feature_vote_history_source_count
        ),
        evaluation_feature_vote_history_source_member_count=(
            evaluation_feature_vote_history_source_count
        ),
        training_feature_vote_history_source_coverage_rate=_rate(
            training_feature_vote_history_source_count,
            training_feature_vote_history_count,
        ),
        evaluation_feature_vote_history_source_coverage_rate=_rate(
            evaluation_feature_vote_history_source_count,
            evaluation_feature_vote_history_count,
        ),
        training_label_count=len(training_label_rows),
        evaluation_label_count=len(evaluation_label_rows),
        training_labels_missing_feature_member_count=training_missing_feature_member_count,
        evaluation_labels_missing_feature_member_count=evaluation_missing_feature_member_count,
        training_label_source_url_count=training_source_count,
        evaluation_label_source_url_count=evaluation_source_count,
        training_label_source_url_coverage_rate=_rate(
            training_source_count, len(training_label_rows)
        ),
        evaluation_label_source_url_coverage_rate=_rate(
            evaluation_source_count,
            len(evaluation_label_rows),
        ),
        official_training_label_source_url_count=official_training_source_count,
        official_evaluation_label_source_url_count=official_evaluation_source_count,
        training_label_official_source_url_coverage_rate=_rate(
            official_training_source_count,
            len(training_label_rows),
        ),
        evaluation_label_official_source_url_coverage_rate=_rate(
            official_evaluation_source_count,
            len(evaluation_label_rows),
        ),
        bill_count=len(bill_rows),
        bill_source_url_count=bill_source_count,
        bill_source_url_coverage_rate=_rate(bill_source_count, len(bill_rows)),
        official_bill_source_url_count=official_bill_source_count,
        bill_official_source_url_coverage_rate=_rate(
            official_bill_source_count,
            len(bill_rows),
        ),
        bill_sponsor_count=bill_sponsor_count,
        bill_available_sponsor_count=bill_available_sponsor_count,
        bill_sponsor_availability_rate=_rate(
            bill_available_sponsor_count,
            bill_sponsor_count,
        ),
        bill_primary_sponsor_introduced_date_fallback_count=(
            bill_primary_sponsor_introduced_date_fallback_count
        ),
        ontology_edge_count=len(ontology_edge_rows),
        sourced_ontology_edge_count=sourced_ontology_edges,
        ontology_source_anchor_coverage_rate=_rate(
            sourced_ontology_edges,
            len(ontology_edge_rows),
        ),
        official_sourced_ontology_edge_count=official_sourced_ontology_edges,
        ontology_official_source_anchor_coverage_rate=_rate(
            official_sourced_ontology_edges,
            len(ontology_edge_rows),
        ),
        fec_contribution_count=fec_count,
        member_attributed_fec_contribution_count=attributed_fec_count,
        fec_member_attribution_rate=_rate(attributed_fec_count, fec_count),
        members_with_fec_candidate_id_count=members_with_fec,
        public_statement_signal_count=statement_signal_count,
        members_with_public_statement_signal_count=members_with_statement_signal,
        public_statement_ontology_edge_count=statement_ontology_edge_count,
        public_statement_prediction_member_overlap_count=(
            statement_prediction_member_overlap_count
        ),
        jurisdiction_count=len(jurisdiction_ids),
        implemented_jurisdiction_count=len(implemented_jurisdiction_ids),
        portable_jurisdiction_count=len(portable_jurisdiction_ids),
        portable_rows_missing_jurisdiction_id_count=portable_rows_missing_jurisdiction_count,
        portable_rows_missing_body_id_count=portable_rows_missing_body_count,
        portable_rows_missing_session_id_count=portable_rows_missing_session_count,
        legislative_body_count=len(legislative_body_ids),
        legislative_session_count=len(legislative_session_ids),
        source_family_count=len(source_family_ids),
        jurisdiction_ids=jurisdiction_ids,
        implemented_jurisdiction_ids=implemented_jurisdiction_ids,
        portable_jurisdiction_ids=portable_jurisdiction_ids,
        legislative_body_ids=legislative_body_ids,
        legislative_session_ids=legislative_session_ids,
        source_family_ids=source_family_ids,
        ok=not blocking,
        blocking_reasons=blocking,
        warning_reasons=warnings,
    )


def _public_statement_ontology_edge_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _is_public_statement_edge(row))


def _public_statement_prediction_member_overlap_count(
    ontology_edge_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> int:
    prediction_members = {
        str(row.get("bioguide_id")).strip()
        for row in feature_rows
        if str(row.get("bioguide_id") or "").strip()
    }
    statement_members = {
        str(row.get("subject_node_id")).strip()
        for row in ontology_edge_rows
        if _is_public_statement_edge(row)
        and str(row.get("subject_node_type") or "").strip() == "member"
        and str(row.get("subject_node_id") or "").strip()
    }
    return len(prediction_members & statement_members)


def _is_public_statement_edge(row: dict[str, Any]) -> bool:
    edge_type = row.get("edge_type")
    return edge_type in {
        "member_sector_statement_alignment",
        "member_sector_public_statement_alignment",
    }


def _source_url_count(rows: list[dict[str, Any]], *, key: str = "source_url") -> int:
    return sum(1 for row in rows if _has_source_url(_raw_or_canonical_vote_source_url(row, key)))


def _feature_vote_history_member_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _int_value(row.get("vote_count")) > 0)


def _feature_vote_history_source_member_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if _int_value(row.get("vote_count")) > 0 and _has_source_url(_feature_vote_source_url(row))
    )


def _source_family_ids(
    training_feature_rows: list[dict[str, Any]],
    evaluation_feature_rows: list[dict[str, Any]],
    training_label_rows: list[dict[str, Any]],
    evaluation_label_rows: list[dict[str, Any]],
    bill_rows: list[dict[str, Any]],
    ontology_edge_rows: list[dict[str, Any]],
) -> list[str]:
    families: set[str] = set()
    for row in [*training_feature_rows, *evaluation_feature_rows]:
        if _has_source_url(_feature_vote_source_url(row)):
            families.add(_source_family_id(_vote_source_type(row), row=row))
    for row in [*training_label_rows, *evaluation_label_rows]:
        if _has_source_url(_vote_source_url(row)):
            families.add(_source_family_id(_vote_source_type(row), row=row))
    for row in bill_rows:
        if _has_source_url(_bill_source_url(row)):
            families.add(_bill_source_type(row))
    for row in ontology_edge_rows:
        for anchor in _source_anchors(row):
            source_type = anchor.get("source_type")
            if isinstance(source_type, str) and source_type.strip():
                families.add(_source_family_id(source_type, row=row))
    return sorted(families)


def _feature_vote_source_url(row: dict[str, Any]) -> str | None:
    source_type = _vote_source_type(row)
    raw_url = _optional_string(row.get("latest_vote_source_url"))
    if raw_url and is_official_source_url(source_type, raw_url):
        return raw_url
    if source_type == "vote_event":
        return _feature_congress_vote_source_url(row)
    return None


def _feature_congress_vote_source_url(row: dict[str, Any]) -> str | None:
    event_key = _optional_string(row.get("latest_vote_event_key"))
    if event_key:
        url = _congress_vote_source_url_from_event_key(
            event_key,
            latest_vote_date=_date_from_value(row.get("latest_vote_date")),
        )
        if url is not None:
            return url
    return _congress_vote_source_url(row)


def _congress_vote_source_url_from_event_key(
    event_key: str,
    *,
    latest_vote_date: date | None,
) -> str | None:
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
    if normalized_chamber == "house" and latest_vote_date is not None:
        return f"https://clerk.house.gov/Votes/{latest_vote_date.year}{roll_call_number:03d}"
    return None


def _source_family_id(source_type: str, *, row: dict[str, Any] | None = None) -> str:
    source_type = source_type.strip()
    if source_type in {"vote_event", "congress_vote", "legislative_vote"}:
        return (
            "congress_vote"
            if _row_jurisdiction_id(row) in (None, "", "us_congress")
            else "legislative_vote"
        )
    return source_type


def _source_anchors(row: dict[str, Any]) -> list[dict[str, Any]]:
    anchors = row.get("source_anchors")
    if not isinstance(anchors, list):
        return []
    return [anchor for anchor in anchors if isinstance(anchor, dict)]


def _label_members_missing_from_features(
    label_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> int:
    label_member_ids = _member_context_ids(label_rows)
    if not label_member_ids:
        return 0
    return len(label_member_ids - _member_context_ids(feature_rows))


def _member_context_ids(rows: list[dict[str, Any]]) -> set[str]:
    context_ids: set[str] = set()
    for row in rows:
        if not isinstance(row.get("bioguide_id"), str):
            continue
        bioguide_id = row["bioguide_id"].strip()
        if not bioguide_id:
            continue
        jurisdiction_id = _legislative_context_id(row.get("jurisdiction_id"), "us_congress")
        body_id = _legislative_context_id(
            row.get("legislative_body_id"),
            _default_legislative_body_id(row, jurisdiction_id),
        )
        context_ids.add(f"{jurisdiction_id}:{body_id}:{bioguide_id}")
    return context_ids


def _bill_source_url_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _has_source_url(_raw_or_canonical_bill_source_url(row)))


def _official_label_source_url_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1 for row in rows if is_official_source_url(_vote_source_type(row), _vote_source_url(row))
    )


def _official_bill_source_url_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if is_official_source_url(
            _bill_source_type(row),
            _bill_source_url(row),
        )
    )


def _bill_sponsor_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _optional_string(row.get("sponsor_bioguide_id")) is not None)


def _bill_available_sponsor_count(
    rows: list[dict[str, Any]],
    *,
    feature_cutoff: date,
) -> int:
    return sum(1 for row in rows if _sponsor_available_at_or_before(row, feature_cutoff))


def _bill_primary_sponsor_introduced_date_fallback_count(
    rows: list[dict[str, Any]],
    *,
    feature_cutoff: date,
) -> int:
    return sum(
        1
        for row in rows
        if _uses_primary_sponsor_introduced_date_fallback(row)
        and _sponsor_available_at_or_before(row, feature_cutoff)
    )


def _sponsor_available_at_or_before(row: dict[str, Any], feature_cutoff: date) -> bool:
    if _optional_string(row.get("sponsor_bioguide_id")) is None:
        return False
    sponsor_date = _sponsor_available_date(row)
    return sponsor_date is not None and sponsor_date <= feature_cutoff


def _sponsor_available_date(row: dict[str, Any]) -> date | None:
    sponsor_date = _date_from_value(row.get("sponsor_date"))
    if sponsor_date is not None:
        return sponsor_date
    if _uses_primary_sponsor_introduced_date_fallback(row):
        return _date_from_value(row.get("introduced_date"))
    return None


def _uses_primary_sponsor_introduced_date_fallback(row: dict[str, Any]) -> bool:
    if _date_from_value(row.get("sponsor_date")) is not None:
        return False
    sponsor_role = _optional_string(row.get("sponsor_role"))
    return row.get("is_primary") is True or sponsor_role == "primary"


def _bill_source_url(row: dict[str, Any]) -> str | None:
    source_type = _bill_source_type(row)
    raw_url = _optional_string(row.get("bill_source_url"))
    if raw_url and is_official_source_url(source_type, raw_url):
        return raw_url
    if source_type == "congress_bill":
        return _congress_bill_url(row)
    return None


def _raw_or_canonical_bill_source_url(row: dict[str, Any]) -> str | None:
    raw_url = _optional_string(row.get("bill_source_url"))
    if raw_url:
        return raw_url
    if _bill_source_type(row) == "congress_bill":
        return _congress_bill_url(row)
    return None


def _congress_bill_url(row: dict[str, Any]) -> str | None:
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


def _plain_positive_int(value: object) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _plain_positive_int_string(value: object) -> int | None:
    if not isinstance(value, str) or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _bill_source_type(row: dict[str, Any]) -> str:
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


def _vote_source_type(row: dict[str, Any]) -> str:
    value = row.get("source_type")
    if isinstance(value, str) and value.strip():
        source_type = value.strip()
        if source_type in {"vote_event", "congress_vote", "legislative_vote"}:
            return (
                "vote_event"
                if _row_jurisdiction_id(row) in (None, "", "us_congress")
                else "legislative_vote"
            )
        return source_type
    return (
        "vote_event" if row.get("jurisdiction_id") in (None, "us_congress") else "legislative_vote"
    )


def _row_jurisdiction_id(row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    value = row.get("jurisdiction_id")
    if isinstance(value, str):
        return value.strip()
    return value


def _vote_source_url(row: dict[str, Any]) -> str | None:
    source_type = _vote_source_type(row)
    raw_url = _optional_string(row.get("source_url"))
    if raw_url and is_official_source_url(source_type, raw_url):
        return raw_url
    if source_type == "vote_event":
        return _congress_vote_source_url(row)
    return None


def _raw_or_canonical_vote_source_url(row: dict[str, Any], key: str) -> str | None:
    raw_url = _optional_string(row.get(key))
    if raw_url:
        return raw_url
    if key == "source_url" and _vote_source_type(row) == "vote_event":
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


def _legislative_context_counts(
    *row_groups: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    jurisdictions: set[str] = set()
    legislative_bodies: set[str] = set()
    legislative_sessions: set[str] = set()
    for row in _legislative_context_rows(*row_groups):
        jurisdiction_id = _legislative_context_id(row.get("jurisdiction_id"), "us_congress")
        body_fallback = _default_legislative_body_id(row, jurisdiction_id)
        legislative_body_id = _legislative_context_id(
            row.get("legislative_body_id"),
            body_fallback,
        )
        session_fallback = _default_legislative_session_id(row, jurisdiction_id)
        legislative_session_id = _legislative_context_id(
            row.get("legislative_session_id"),
            session_fallback,
        )
        jurisdictions.add(jurisdiction_id)
        has_body_id = jurisdiction_id == "us_congress" or _has_explicit_legislative_body_id(row)
        if has_body_id:
            legislative_bodies.add(f"{jurisdiction_id}:{legislative_body_id}")
        if jurisdiction_id == "us_congress" or (
            has_body_id and _has_explicit_legislative_session_id(row)
        ):
            legislative_sessions.add(
                f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id}"
            )
    implemented_jurisdictions = {
        jurisdiction for jurisdiction in jurisdictions if jurisdiction == "us_congress"
    }
    portable_jurisdictions = jurisdictions - implemented_jurisdictions
    return (
        sorted(jurisdictions),
        sorted(implemented_jurisdictions),
        sorted(portable_jurisdictions),
        sorted(legislative_bodies),
        sorted(legislative_sessions),
    )


def _legislative_context_id(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _portable_rows_missing_session_id_count(*row_groups: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in _legislative_context_rows(*row_groups)
        if _legislative_context_id(row.get("jurisdiction_id"), "us_congress") != "us_congress"
        and not _has_explicit_legislative_session_id(row)
    )


def _portable_rows_missing_body_id_count(*row_groups: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in _legislative_context_rows(*row_groups)
        if _legislative_context_id(row.get("jurisdiction_id"), "us_congress") != "us_congress"
        and not _has_explicit_legislative_body_id(row)
    )


def _portable_rows_missing_jurisdiction_id_count(*row_groups: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in _legislative_source_context_rows(*row_groups)
        if _declares_portable_source(row) and not _has_explicit_jurisdiction_id(row)
    )


def _legislative_context_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for rows in row_groups:
        for row in rows:
            anchors = _source_anchors(row)
            if not anchors or _has_legislative_context(row):
                contexts.append(row)
            contexts.extend(
                anchor for anchor in _source_anchors(row) if _has_legislative_context(anchor)
            )
    return contexts


def _legislative_source_context_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for rows in row_groups:
        for row in rows:
            if _declares_row_portable_source(row):
                contexts.append(row)
            contexts.extend(_source_anchors(row))
    return contexts


def _declares_portable_source(row: dict[str, Any]) -> bool:
    if _declares_row_portable_source(row):
        return True
    return any(_declares_row_portable_source(anchor) for anchor in _source_anchors(row))


def _declares_row_portable_source(row: dict[str, Any]) -> bool:
    for key in ("source_type", "bill_source_type"):
        value = row.get(key)
        if isinstance(value, str) and value.strip() in {"legislative_vote", "legislative_bill"}:
            return True
    return False


def _has_legislative_context(row: dict[str, Any]) -> bool:
    return (
        _has_explicit_jurisdiction_id(row)
        or _has_explicit_legislative_body_id(row)
        or _has_explicit_legislative_session_id(row)
        or _declares_row_legislative_source(row)
    )


def _declares_row_legislative_source(row: dict[str, Any]) -> bool:
    for key in ("source_type", "bill_source_type"):
        value = row.get(key)
        if isinstance(value, str) and value.strip() in {
            "vote_event",
            "congress_vote",
            "legislative_vote",
            "congress_bill",
            "legislative_bill",
        }:
            return True
    return False


def _has_explicit_jurisdiction_id(row: dict[str, Any]) -> bool:
    value = row.get("jurisdiction_id")
    return isinstance(value, str) and bool(value.strip())


def _has_explicit_legislative_body_id(row: dict[str, Any]) -> bool:
    value = row.get("legislative_body_id")
    return isinstance(value, str) and bool(value.strip())


def _has_explicit_legislative_session_id(row: dict[str, Any]) -> bool:
    value = row.get("legislative_session_id")
    return isinstance(value, str) and bool(value.strip())


def _default_legislative_body_id(row: dict[str, Any], jurisdiction_id: str) -> str:
    if jurisdiction_id != "us_congress":
        return jurisdiction_id
    chamber = row.get("chamber")
    if isinstance(chamber, str) and chamber.strip():
        return f"us_congress_{chamber.strip()}"
    return "us_congress"


def _default_legislative_session_id(row: dict[str, Any], jurisdiction_id: str) -> str:
    if jurisdiction_id != "us_congress":
        return jurisdiction_id
    congress = row.get("congress")
    session_number = row.get("session_number")
    if congress is not None and session_number is not None:
        return f"congress_{congress}_session_{session_number}"
    if congress is not None:
        introduced_date = _date_from_value(row.get("introduced_date"))
        if introduced_date is not None:
            session_from_introduction = 2 if introduced_date.year % 2 == 0 else 1
            return f"congress_{congress}_session_{session_from_introduction}"
        return f"congress_{congress}"
    return "us_congress"


def _date_from_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _official_source_anchor_edge_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if isinstance(row.get("source_anchors"), list) and _has_official_row_source_anchor(row)
    )


def _has_official_row_source_anchor(row: dict[str, Any]) -> bool:
    return any(
        _row_source_anchor_has_official_url(anchor, row=row) for anchor in _source_anchors(row)
    )


def _row_source_anchor_has_official_url(anchor: dict[str, Any], *, row: dict[str, Any]) -> bool:
    source_type = anchor_source_type(anchor)
    if source_type not in SOURCE_TYPES_REQUIRING_URL:
        return False
    normalized_source_type = _source_family_id(source_type, row=row)
    return is_official_source_url(normalized_source_type, anchor_url(anchor))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_source_url(value: Any) -> bool:
    return _optional_string(value) is not None


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if type(value) is int:
        return value
    if value is None:
        return 0
    return int(value)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _ensure_unique_nonblank(values: list[str], *, field_name: str) -> None:
    if any(not value.strip() or value != value.strip() for value in values):
        raise ValueError(f"{field_name} must be unique and nonblank")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique and nonblank")


def _ensure_sorted_unique_nonblank(values: list[str], *, field_name: str) -> None:
    _ensure_unique_nonblank(values, field_name=field_name)
    if values != sorted(values):
        raise ValueError(f"{field_name} must be sorted")
