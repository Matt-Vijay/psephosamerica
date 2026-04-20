from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Shared primitives ──────────────────────────────────────────────


class ConfidenceLabel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceAnchor(BaseModel):
    """Pointer to the official record that backs a fact."""

    source_type: str = Field(description="e.g. 'financial_disclosure', 'fec_contribution'")
    source_id: str = Field(description="Primary key or artifact ID in the canonical store")
    url: str | None = Field(default=None, description="Public URL to the source, if available")
    label: str = Field(description="Human-readable label for display")


class EvidenceSection(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    NORMATIVE_JUDGMENT = "normative_judgment"


class EvidenceBlock(BaseModel):
    section: EvidenceSection
    text: str


# ── Evidence Card ──────────────────────────────────────────────────


class EvidenceCardPayload(BaseModel):
    """Public JSON contract for ``/evidence/:id``."""

    evidence_card_id: str
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
    confidence: ConfidenceLabel
    snapshot_date: date
    created_at: datetime


# ── Member Profile ─────────────────────────────────────────────────


class ScoreSummary(BaseModel):
    dimension: str
    current_score: float
    rule_fire_count: int


class RecentRuleFire(BaseModel):
    rule_id: str
    evidence_card_id: str
    short_explanation: str
    score_delta: float
    snapshot_date: date


class CommitteeMembership(BaseModel):
    committee_name: str
    role: str | None = None


class HistoricalCommitteeMembership(BaseModel):
    committee_name: str
    role: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool
    chamber: Literal["house", "senate"]
    committee_type: str


class MemberHistorySnapshot(BaseModel):
    snapshot_date: date
    score_total: float
    score_total_delta: float | None = None
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    published_at: datetime | None = None


class MemberHistoryEvent(BaseModel):
    rule_id: str
    dimension: str
    severity: str
    evidence_card_id: str | None = None
    short_explanation: str
    score_delta: float
    snapshot_date: date | None = None
    fired_at: datetime | None = None


class MemberHistoryPayload(BaseModel):
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


class DimensionChangeSummary(BaseModel):
    dimension: str
    current_score: float
    previous_score: float | None = None
    score_delta: float | None = None
    abs_delta: float
    event_count: int = Field(default=0, ge=0)


class MemberChangeSummaryPayload(BaseModel):
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


class MemberTrendWindowPayload(BaseModel):
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


class MemberTrendSummaryPayload(BaseModel):
    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    latest_snapshot_date: date
    windows: list[MemberTrendWindowPayload] = Field(default_factory=list)


class MemberHistoryChartPoint(BaseModel):
    snapshot_id: str
    snapshot_date: date
    score_total: float
    score_total_delta: float | None = None
    event_count: int = Field(default=0, ge=0)


class MemberHistoryComparePreset(BaseModel):
    preset_key: Literal["latest", "4w", "12w", "cycle"]
    label: str
    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    has_full_window: bool
    score_total_delta: float
    top_evidence_card_ids: list[str] = Field(default_factory=list)


class SnapshotComparePresetPayload(BaseModel):
    preset_key: Literal["latest", "4w", "12w", "cycle"]
    label: str
    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    has_full_window: bool


class SnapshotComparePresetSetPayload(BaseModel):
    default_preset_key: Literal["latest", "4w", "12w", "cycle"]
    presets: list[SnapshotComparePresetPayload] = Field(default_factory=list)


class MemberHistoryChartPayload(BaseModel):
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


class MemberProfilePayload(BaseModel):
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


class ZipMemberSummary(BaseModel):
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


class ZipFeedPayload(BaseModel):
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
