from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from src.export.contracts import (
    EvidenceCardPayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    MemberTrendSummaryPayload,
    SnapshotComparePresetPayload,
    ZipFeedPayload,
)
from src.homepage.contracts import HomepageFeedPayload
from src.homepage.contracts import (
    MemberMovementSummary,
    MovementWindowPayload,
    RecentEventSummary,
    SnapshotComparePayload,
)
from src.identity.current_member_lookup import CurrentMemberLookupEntry, CurrentMemberLookupPayload

T = TypeVar("T", bound=BaseModel)


class BatchMeta(BaseModel):
    """Batch provenance attached to every successful API response."""

    snapshot_date: date = Field(description="Date of the weekly recompute snapshot")
    published_at: datetime = Field(description="When this snapshot was published")
    schema_version: str = Field(default="v1", description="API schema version")


class ApiEnvelope(BaseModel, Generic[T]):
    """Generic success envelope that pairs any payload with batch metadata."""

    ok: Literal[True] = True
    meta: BatchMeta
    data: T


class NotFoundBody(BaseModel):
    """Consistent not-found response shape for all four endpoints."""

    ok: Literal[False] = False
    error: Literal["not_found"] = "not_found"
    resource_type: str = Field(description="e.g. 'zip', 'member', 'evidence'")
    identifier: str = Field(description="The value that was looked up")
    detail: str


class LastUpdatedPayload(BaseModel):
    """Payload for ``/api/v1/meta/last-updated``."""

    snapshot_date: date
    published_at: datetime


class MovementFeedPayload(BaseModel):
    """Dedicated movement/change feed payload for product surfaces."""

    snapshot_date: date
    top_changes: list[MemberMovementSummary]
    recent_events: list[RecentEventSummary]
    recent_evidence_card_ids: list[str] = Field(default_factory=list)


class ArtifactCounts(BaseModel):
    """Count of emitted published artifacts by product-facing family."""

    members: int = Field(ge=0)
    evidence: int = Field(ge=0)
    zip_feeds: int = Field(ge=0)
    homepage_feeds: int = Field(ge=0)
    current_member_lookups: int = Field(ge=0)


class SnapshotSummaryPayload(BaseModel):
    """Compact summary of the latest published snapshot."""

    snapshot_id: str
    snapshot_date: date
    published_at: datetime
    root_sha256: str
    total_files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    artifact_counts: ArtifactCounts


class SnapshotIndexEntry(BaseModel):
    """Historical manifest metadata for one published snapshot."""

    snapshot_id: str
    snapshot_date: date
    published_at: datetime
    root_sha256: str
    total_files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class SnapshotIndexPayload(BaseModel):
    """Index of all published snapshots for time-aware product surfaces."""

    latest_snapshot_id: str
    snapshots: list[SnapshotIndexEntry] = Field(default_factory=list)


class MemberPagePayload(BaseModel):
    """Single-fetch member page payload for the product read path."""

    profile: MemberProfilePayload
    top_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)
    recent_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)


class MemberCompareScoreRow(BaseModel):
    """One normalized score row for side-by-side member comparison."""

    dimension: str
    left_current_score: float | None = None
    right_current_score: float | None = None
    left_rule_fire_count: int = Field(default=0, ge=0)
    right_rule_fire_count: int = Field(default=0, ge=0)
    score_gap: float | None = None


class MemberComparePayload(BaseModel):
    """Single-fetch member compare payload built from published member pages."""

    left: MemberPagePayload
    right: MemberPagePayload
    score_comparisons: list[MemberCompareScoreRow] = Field(default_factory=list)
    shared_committees: list[str] = Field(default_factory=list)
    left_only_committees: list[str] = Field(default_factory=list)
    right_only_committees: list[str] = Field(default_factory=list)
    same_chamber: bool
    same_state: bool


class MemberHistoryPagePayload(BaseModel):
    """Single-fetch member history page payload."""

    member_page: MemberPagePayload
    history: MemberHistoryPayload
    recent_change: MemberChangeSummaryPayload
    chart: MemberHistoryChartPayload
    default_window_compare: MemberWindowComparePayload
    trend_summary: MemberTrendSummaryPayload
    history_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)
    snapshot_index: SnapshotIndexPayload


class MemberWindowComparePayload(BaseModel):
    """Single-fetch member-specific compare payload for one snapshot window."""

    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    summary: MemberChangeSummaryPayload
    evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)


class ZipEntryPayload(BaseModel):
    """Single-fetch homepage ZIP-entry payload."""

    zip_feed: ZipFeedPayload
    member_lookup_entries: list[CurrentMemberLookupEntry] = Field(default_factory=list)
    snapshot: SnapshotSummaryPayload


class HomepageBootstrapPayload(BaseModel):
    """Single-fetch homepage first-paint payload."""

    snapshot: SnapshotSummaryPayload
    movement: MovementFeedPayload
    featured_lookup_entries: list[CurrentMemberLookupEntry] = Field(default_factory=list)


class HistoryBootstrapPayload(BaseModel):
    """Single-fetch historical landing payload."""

    snapshot_index: SnapshotIndexPayload
    movement_window: MovementWindowPayload
    default_compare_preset_key: Literal["latest", "4w", "12w", "cycle"]
    compare_presets: list[SnapshotComparePresetPayload] = Field(default_factory=list)
    featured_member_changes: list[MemberChangeSummaryPayload] = Field(default_factory=list)


class HistoryPresetRangePayload(BaseModel):
    """Single-fetch historical preset-range payload."""

    preset: SnapshotComparePresetPayload
    movement_window: MovementWindowPayload
    snapshot_compare: SnapshotComparePayload


class SearchSessionResult(BaseModel):
    """One ranked search hit plus an optional preview payload."""

    lookup_entry: CurrentMemberLookupEntry
    member_page_preview: MemberPagePayload | None = None


class SearchSessionPayload(BaseModel):
    """Single-fetch search response for the homepage/member lookup flow."""

    query: str
    total_matches: int = Field(ge=0)
    results: list[SearchSessionResult] = Field(default_factory=list)
    snapshot: SnapshotSummaryPayload


# Typed response aliases for the launch endpoints.
ZipResponse = ApiEnvelope[ZipFeedPayload]
MemberResponse = ApiEnvelope[MemberProfilePayload]
EvidenceResponse = ApiEnvelope[EvidenceCardPayload]
HomepageResponse = ApiEnvelope[HomepageFeedPayload]
CurrentMemberLookupResponse = ApiEnvelope[CurrentMemberLookupPayload]
LastUpdatedResponse = ApiEnvelope[LastUpdatedPayload]
MovementFeedResponse = ApiEnvelope[MovementFeedPayload]
SnapshotSummaryResponse = ApiEnvelope[SnapshotSummaryPayload]
MemberPageResponse = ApiEnvelope[MemberPagePayload]
MemberCompareResponse = ApiEnvelope[MemberComparePayload]
MemberHistoryPageResponse = ApiEnvelope[MemberHistoryPagePayload]
MemberHistoryChartResponse = ApiEnvelope[MemberHistoryChartPayload]
MemberWindowCompareResponse = ApiEnvelope[MemberWindowComparePayload]
ZipEntryResponse = ApiEnvelope[ZipEntryPayload]
HomepageBootstrapResponse = ApiEnvelope[HomepageBootstrapPayload]
HistoryBootstrapResponse = ApiEnvelope[HistoryBootstrapPayload]
HistoryPresetRangeResponse = ApiEnvelope[HistoryPresetRangePayload]
SearchSessionResponse = ApiEnvelope[SearchSessionPayload]
SnapshotCompareResponse = ApiEnvelope[SnapshotComparePayload]
MemberHistoryResponse = ApiEnvelope[MemberHistoryPayload]
SnapshotIndexResponse = ApiEnvelope[SnapshotIndexPayload]
MemberChangeSummaryResponse = ApiEnvelope[MemberChangeSummaryPayload]
MemberTrendSummaryResponse = ApiEnvelope[MemberTrendSummaryPayload]
MovementWindowResponse = ApiEnvelope[MovementWindowPayload]
