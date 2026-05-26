from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from types import UnionType
from typing import Literal, Self, Union, get_args, get_origin

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from src.export.evidence_policy import (
    official_source_url_count,
    primary_source_anchor,
    validate_evidence_card_policy,
)


# ── Shared primitives ──────────────────────────────────────────────


def _annotation_accepts_int(annotation: object) -> bool:
    if annotation is int:
        return True
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(_annotation_accepts_int(argument) for argument in get_args(annotation))
    return False


def _annotation_accepts_int_map_values(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(
            _annotation_accepts_int_map_values(argument) for argument in get_args(annotation)
        )
    if origin is dict:
        args = get_args(annotation)
        return len(args) == 2 and _annotation_accepts_int(args[1])
    return False


class ExportContractModel(BaseModel):
    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def reject_boolean_int_fields(cls, value: object, info: ValidationInfo) -> object:
        if info.field_name is None:
            return value
        field = cls.model_fields.get(info.field_name)
        if (
            isinstance(value, bool)
            and field is not None
            and _annotation_accepts_int(field.annotation)
        ):
            raise ValueError(f"{info.field_name} must be an integer")
        if (
            isinstance(value, dict)
            and field is not None
            and _annotation_accepts_int_map_values(field.annotation)
            and any(isinstance(item, bool) for item in value.values())
        ):
            raise ValueError(f"{info.field_name} values must be integers")
        return value


class ConfidenceLabel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceAnchor(ExportContractModel):
    """Pointer to the official record that backs a fact."""

    source_type: str = Field(
        min_length=1,
        description="e.g. 'financial_disclosure', 'fec_contribution'",
    )
    source_id: str = Field(
        min_length=1,
        description="Primary key or artifact ID in the canonical store",
    )
    jurisdiction_id: str | None = Field(
        default=None,
        description="Jurisdiction that owns the source record, when the source is legislative.",
    )
    legislative_body_id: str | None = Field(
        default=None,
        description="Legislative body that owns the source record, when applicable.",
    )
    legislative_session_id: str | None = Field(
        default=None,
        description="Legislative session that owns the source record, when applicable.",
    )
    url: str | None = Field(default=None, description="Public URL to the source, if available")
    label: str = Field(min_length=1, description="Human-readable label for display")

    @field_validator("source_type", "source_id", "label", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "jurisdiction_id",
        "legislative_body_id",
        "legislative_session_id",
        mode="before",
    )
    @classmethod
    def strip_optional_context_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("url", mode="before")
    @classmethod
    def strip_optional_url(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def identity_fields_are_not_blank(self) -> Self:
        if not self.source_type.strip():
            raise ValueError("source_type must not be blank")
        if not self.source_id.strip():
            raise ValueError("source_id must not be blank")
        if not self.label.strip():
            raise ValueError("label must not be blank")
        return self


class EvidenceSection(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    NORMATIVE_JUDGMENT = "normative_judgment"


class EvidenceBlock(ExportContractModel):
    section: EvidenceSection
    text: str


# ── Evidence Card ──────────────────────────────────────────────────


class EvidenceCardPayload(ExportContractModel):
    """Public JSON contract for ``/evidence/:id``."""

    evidence_card_id: str
    rule_fire_source_record_id: str | None = Field(
        default=None,
        exclude=True,
        description="Internal load hint for resolving evidence_card.rule_fire_id.",
    )
    member_bioguide_id: str
    member_name: str
    member_slug: str
    dimension: str = Field(description="Score dimension, e.g. 'conflict_of_interest_risk'")
    rule_id: str
    rule_version: int
    score_delta: float
    short_explanation: str
    blocks: list[EvidenceBlock]
    source_anchors: list[SourceAnchor]
    source_count: int = Field(default=0, ge=0)
    official_source_count: int = Field(default=0, ge=0)
    primary_source_url: str | None = None
    primary_source_label: str | None = None
    confidence: ConfidenceLabel
    snapshot_date: date
    created_at: datetime

    @model_validator(mode="after")
    def validate_public_evidence_contract(self) -> Self:
        validate_evidence_card_policy(self)
        primary_anchor = primary_source_anchor(self.source_anchors)
        self.source_count = len(self.source_anchors)
        self.official_source_count = official_source_url_count(self.source_anchors)
        self.primary_source_url = primary_anchor.url if primary_anchor is not None else None
        self.primary_source_label = primary_anchor.label if primary_anchor is not None else None
        return self


# ── Member Profile ─────────────────────────────────────────────────


class ScoreSummary(ExportContractModel):
    dimension: str
    current_score: float
    rule_fire_count: int


class RecentRuleFire(ExportContractModel):
    rule_id: str
    evidence_card_id: str
    short_explanation: str
    score_delta: float
    snapshot_date: date


class CommitteeMembership(ExportContractModel):
    committee_name: str
    role: str | None = None


class HistoricalCommitteeMembership(ExportContractModel):
    committee_name: str
    role: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool
    chamber: Literal["house", "senate"]
    committee_type: str


class MemberHistorySnapshot(ExportContractModel):
    snapshot_date: date
    score_total: float
    score_total_delta: float | None = None
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    published_at: datetime | None = None


class MemberHistoryEvent(ExportContractModel):
    rule_id: str
    dimension: str
    severity: str
    evidence_card_id: str | None = None
    short_explanation: str
    score_delta: float
    snapshot_date: date | None = None
    fired_at: datetime | None = None


class MemberHistoryPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    snapshots: list[MemberHistorySnapshot] = Field(default_factory=list)
    events: list[MemberHistoryEvent] = Field(default_factory=list)
    committee_history: list[HistoricalCommitteeMembership] = Field(default_factory=list)


class MemberTimelineEventPayload(ExportContractModel):
    event_id: str
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    event_date: date
    snapshot_date: date | None = None
    fired_at: datetime | None = None
    rule_id: str
    dimension: str
    severity: str
    evidence_card_id: str | None = None
    short_explanation: str
    score_delta: float


class MemberTimelinePagePayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
    total_events: int = Field(ge=0)
    next_page: int | None = None
    previous_page: int | None = None
    events: list[MemberTimelineEventPayload] = Field(default_factory=list)
    evidence_cards: list[EvidenceCardPayload] = Field(
        default_factory=list,
        description="Resolved source-backed cards for events displayed on this page.",
    )
    missing_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Displayed event evidence IDs that could not be resolved to sidecar payloads.",
    )


class MemberTimelineYearBucketPayload(ExportContractModel):
    year: int
    event_count: int = Field(ge=0)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    latest_event_date: date
    earliest_event_date: date


class MemberTimelineIndexPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    latest_snapshot_date: date
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
    total_events: int = Field(ge=0)
    latest_event_id: str | None = None
    latest_event_date: date | None = None
    earliest_event_date: date | None = None
    available_years: list[int] = Field(default_factory=list)
    year_buckets: list[MemberTimelineYearBucketPayload] = Field(default_factory=list)


class MemberTimelineYearPayload(ExportContractModel):
    year: int
    timeline_index: MemberTimelineIndexPayload
    year_bucket: MemberTimelineYearBucketPayload
    timeline_page: MemberTimelinePagePayload


class MemberTimelineDimensionPayload(ExportContractModel):
    dimension: str
    timeline_index: MemberTimelineIndexPayload
    timeline_page: MemberTimelinePagePayload


class HistoryCoverageDimensionPayload(ExportContractModel):
    dimension: str
    member_history_count: int = Field(ge=0)
    total_events: int = Field(ge=0)
    available_years: list[int] = Field(default_factory=list)


class HistoryCoveragePayload(ExportContractModel):
    earliest_snapshot_id: str
    earliest_snapshot_date: date
    latest_snapshot_id: str
    latest_snapshot_date: date
    snapshot_count: int = Field(ge=0)
    member_history_count: int = Field(ge=0)
    total_events: int = Field(ge=0)
    available_years: list[int] = Field(default_factory=list)
    dimensions: list[HistoryCoverageDimensionPayload] = Field(default_factory=list)


class DimensionChangeSummary(ExportContractModel):
    dimension: str
    current_score: float
    previous_score: float | None = None
    score_delta: float | None = None
    abs_delta: float
    event_count: int = Field(default=0, ge=0)


class MemberChangeSummaryPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    latest_snapshot_date: date
    previous_snapshot_date: date | None = None
    latest_score_total: float
    previous_score_total: float | None = None
    score_total_delta: float | None = None
    top_dimension_changes: list[DimensionChangeSummary] = Field(default_factory=list)
    recent_events: list[MemberHistoryEvent] = Field(default_factory=list)
    top_evidence_card_ids: list[str] = Field(default_factory=list)


class MemberTrendWindowPayload(ExportContractModel):
    window_key: Literal["4w", "12w", "cycle"]
    label: str
    requested_days: int | None = None
    has_full_window: bool
    start_snapshot_date: date | None = None
    end_snapshot_date: date
    current_score_total: float
    previous_score_total: float | None = None
    score_total_delta: float
    top_dimension_changes: list[DimensionChangeSummary] = Field(default_factory=list)
    recent_event_count: int = Field(default=0, ge=0)
    top_evidence_card_ids: list[str] = Field(default_factory=list)


class MemberTrendSummaryPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    latest_snapshot_date: date
    windows: list[MemberTrendWindowPayload] = Field(default_factory=list)


class MemberHistoryCoverageWindowPayload(ExportContractModel):
    window_key: Literal["4w", "12w", "cycle"]
    requested_days: int | None = None
    has_full_window: bool
    start_snapshot_date: date | None = None
    end_snapshot_date: date


class MemberHistoryCoverageYearPayload(ExportContractModel):
    year: int
    event_count: int = Field(ge=0)


class MemberHistoryCoverageDimensionPayload(ExportContractModel):
    dimension: str
    event_count: int = Field(ge=0)


class MemberHistoryCoveragePayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    earliest_snapshot_date: date
    latest_snapshot_date: date
    latest_event_date: date | None = None
    earliest_event_date: date | None = None
    snapshot_count: int = Field(ge=0)
    total_events: int = Field(ge=0)
    available_years: list[int] = Field(default_factory=list)
    years: list[MemberHistoryCoverageYearPayload] = Field(default_factory=list)
    dimensions: list[MemberHistoryCoverageDimensionPayload] = Field(default_factory=list)
    windows: list[MemberHistoryCoverageWindowPayload] = Field(default_factory=list)


class MemberHistoryCoverageIndexEntryPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    earliest_snapshot_date: date
    latest_snapshot_date: date
    latest_event_date: date | None = None
    snapshot_count: int = Field(ge=0)
    total_events: int = Field(ge=0)
    available_years: list[int] = Field(default_factory=list)
    has_full_4w: bool
    has_full_12w: bool
    has_full_cycle: bool


class MemberHistoryCoverageIndexPayload(ExportContractModel):
    total_members: int = Field(ge=0)
    members: list[MemberHistoryCoverageIndexEntryPayload] = Field(default_factory=list)


class MemberHistoryChartPoint(ExportContractModel):
    snapshot_id: str
    snapshot_date: date
    score_total: float
    score_total_delta: float | None = None
    event_count: int = Field(default=0, ge=0)


class MemberHistoryComparePreset(ExportContractModel):
    preset_key: Literal["latest", "4w", "12w", "cycle"]
    label: str
    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    has_full_window: bool
    score_total_delta: float
    top_evidence_card_ids: list[str] = Field(default_factory=list)


class SnapshotComparePresetPayload(ExportContractModel):
    preset_key: Literal["latest", "4w", "12w", "cycle"]
    label: str
    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    has_full_window: bool


class SnapshotComparePresetSetPayload(ExportContractModel):
    default_preset_key: Literal["latest", "4w", "12w", "cycle"]
    presets: list[SnapshotComparePresetPayload] = Field(default_factory=list)


class MemberHistoryChartPayload(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    latest_snapshot_id: str
    latest_snapshot_date: date
    default_preset_key: Literal["latest", "4w", "12w", "cycle"]
    points: list[MemberHistoryChartPoint] = Field(default_factory=list)
    compare_presets: list[MemberHistoryComparePreset] = Field(default_factory=list)


class MemberProfilePayload(ExportContractModel):
    """Public JSON contract for ``/member/:slug``."""

    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    scores: list[ScoreSummary]
    recent_rule_fires: list[RecentRuleFire]
    top_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Up to 3 evidence card IDs for quick drill-in from the member page",
    )
    committees: list[CommitteeMembership]
    total_evidence_cards: int
    snapshot_date: date


# ── ZIP Feed ───────────────────────────────────────────────────────


class ZipMemberSummary(ExportContractModel):
    bioguide_id: str
    name: str
    slug: str
    chamber: Literal["house", "senate"]
    party: str
    scores: list[ScoreSummary]
    top_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Up to 3 evidence card IDs for quick drill-in from the ZIP feed",
    )


class ZipFeedPayload(ExportContractModel):
    """Public JSON contract for ``/zip/:zip``."""

    zip_code: str = Field(min_length=5, max_length=5)
    congressional_district: str | None = Field(
        default=None, description="Plurality district when ZIP maps to multiple"
    )
    ambiguity_note: str | None = Field(
        default=None,
        description="Shown when a ZIP spans multiple districts",
    )
    members: list[ZipMemberSummary]
    snapshot_date: date
