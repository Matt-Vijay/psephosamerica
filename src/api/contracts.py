from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

from src.export.contracts import (
    EvidenceCardPayload,
    ExportContractModel,
    HistoryCoverageDimensionPayload,
    HistoryCoveragePayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryCoveragePayload,
    MemberHistoryCoverageIndexPayload,
    MemberHistoryPayload,
    MemberTimelineDimensionPayload,
    MemberTimelineEventPayload,
    MemberTimelineIndexPayload,
    MemberTimelinePagePayload,
    MemberTimelineYearPayload,
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
from src.ontology.contracts import (
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
)
from src.prediction.contracts import (
    PredictionBootstrapPayload,
    PredictionCommitteeContextPayload,
    PredictionCommitteeReadinessPayload,
    PredictionMemberContextPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessIndexPayload,
    PredictionReadinessPayload,
    PredictionSectorContextPayload,
    PredictionSectorReadinessPayload,
    PredictionSourceContextPayload,
    PredictionSourceIndexPayload,
    PredictionTopologyPayload,
)

T = TypeVar("T", bound=BaseModel)


class ApiContractModel(ExportContractModel):
    pass


class BatchMeta(ApiContractModel):
    """Batch provenance attached to every successful API response."""

    snapshot_date: date = Field(description="Date of the weekly recompute snapshot")
    published_at: datetime = Field(description="When this snapshot was published")
    schema_version: str = Field(default="v1", description="API schema version")


class ApiEnvelope(ApiContractModel, Generic[T]):
    """Generic success envelope that pairs any payload with batch metadata."""

    ok: Literal[True] = True
    meta: BatchMeta
    data: T


class NotFoundBody(ApiContractModel):
    """Consistent not-found response shape for all four endpoints."""

    ok: Literal[False] = False
    error: Literal["not_found"] = "not_found"
    resource_type: str = Field(description="e.g. 'zip', 'member', 'evidence'")
    identifier: str = Field(description="The value that was looked up")
    detail: str


class LastUpdatedPayload(ApiContractModel):
    """Payload for ``/api/v1/meta/last-updated``."""

    snapshot_date: date
    published_at: datetime


class MovementFeedPayload(ApiContractModel):
    """Dedicated movement/change feed payload for product surfaces."""

    snapshot_date: date
    top_changes: list[MemberMovementSummary]
    recent_events: list[RecentEventSummary]
    recent_evidence_card_ids: list[str] = Field(default_factory=list)
    evidence_cards: list[EvidenceCardPayload] = Field(
        default_factory=list,
        description="Resolved source-backed cards for displayed top changes and recent events.",
    )
    missing_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Displayed evidence IDs that could not be resolved to sidecar payloads.",
    )


class ArtifactCounts(ApiContractModel):
    """Count of emitted published artifacts by product-facing family."""

    members: int = Field(ge=0)
    evidence: int = Field(ge=0)
    ontology_edges: int = Field(default=0, ge=0)
    ontology_member_graphs: int = Field(default=0, ge=0)
    zip_feeds: int = Field(ge=0)
    homepage_feeds: int = Field(ge=0)
    current_member_lookups: int = Field(ge=0)


class SnapshotSummaryPayload(ApiContractModel):
    """Compact summary of the latest published snapshot."""

    snapshot_id: str
    snapshot_date: date
    published_at: datetime
    root_sha256: str
    total_files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    artifact_counts: ArtifactCounts

    @field_validator("root_sha256")
    @classmethod
    def root_sha256_is_hex(cls, value: str) -> str:
        return _validate_sha256_hex(value, field_name="root_sha256")


class SnapshotIndexEntry(ApiContractModel):
    """Historical manifest metadata for one published snapshot."""

    snapshot_id: str
    snapshot_date: date
    published_at: datetime
    root_sha256: str
    total_files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)

    @field_validator("root_sha256")
    @classmethod
    def root_sha256_is_hex(cls, value: str) -> str:
        return _validate_sha256_hex(value, field_name="root_sha256")


class SnapshotIndexPayload(ApiContractModel):
    """Index of all published snapshots for time-aware product surfaces."""

    latest_snapshot_id: str
    snapshots: list[SnapshotIndexEntry] = Field(default_factory=list)


def _validate_sha256_hex(value: str, *, field_name: str) -> str:
    if len(value) != 64 or not all(char in "0123456789abcdefABCDEF" for char in value):
        raise ValueError(f"{field_name} must be a 64-character hex string")
    return value


class MemberPagePayload(ApiContractModel):
    """Single-fetch member page payload for the product read path."""

    profile: MemberProfilePayload
    top_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)
    recent_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)
    ontology_graph: OntologyMemberGraphPayload | None = None
    ontology_features: OntologyMemberFeaturesPayload | None = None
    prediction_readiness: PredictionMemberReadinessPayload | None = None


class MemberCompareScoreRow(ApiContractModel):
    """One normalized score row for side-by-side member comparison."""

    dimension: str
    left_current_score: float | None = None
    right_current_score: float | None = None
    left_rule_fire_count: int = Field(default=0, ge=0)
    right_rule_fire_count: int = Field(default=0, ge=0)
    score_gap: float | None = None


class MemberComparePayload(ApiContractModel):
    """Single-fetch member compare payload built from published member pages."""

    left: MemberPagePayload
    right: MemberPagePayload
    score_comparisons: list[MemberCompareScoreRow] = Field(default_factory=list)
    shared_committees: list[str] = Field(default_factory=list)
    left_only_committees: list[str] = Field(default_factory=list)
    right_only_committees: list[str] = Field(default_factory=list)
    same_chamber: bool
    same_state: bool


class MemberHistoryPagePayload(ApiContractModel):
    """Single-fetch member history page payload."""

    member_page: MemberPagePayload
    history: MemberHistoryPayload
    recent_change: MemberChangeSummaryPayload
    chart: MemberHistoryChartPayload
    default_window_compare: MemberWindowComparePayload
    trend_summary: MemberTrendSummaryPayload
    coverage: MemberHistoryCoveragePayload | None = None
    history_evidence_cards: list[EvidenceCardPayload] = Field(default_factory=list)
    snapshot_index: SnapshotIndexPayload
    timeline_index: MemberTimelineIndexPayload | None = None
    timeline_page: MemberTimelinePagePayload | None = None


class HistoryEventPagePayload(ApiContractModel):
    """Single-fetch event permalink payload."""

    event: MemberTimelineEventPayload
    member_page: MemberPagePayload | None = None
    evidence_card: EvidenceCardPayload | None = None
    previous_event_id: str | None = None
    next_event_id: str | None = None


class MemberWindowComparePayload(ApiContractModel):
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


class ZipEntryPayload(ApiContractModel):
    """Single-fetch homepage ZIP-entry payload."""

    zip_feed: ZipFeedPayload
    member_lookup_entries: list[CurrentMemberLookupEntry] = Field(default_factory=list)
    snapshot: SnapshotSummaryPayload


class HomepageBootstrapPayload(ApiContractModel):
    """Single-fetch homepage first-paint payload."""

    snapshot: SnapshotSummaryPayload
    movement: MovementFeedPayload
    featured_lookup_entries: list[CurrentMemberLookupEntry] = Field(default_factory=list)


class HistoryBootstrapPayload(ApiContractModel):
    """Single-fetch historical landing payload."""

    dimension: str | None = None
    snapshot_index: SnapshotIndexPayload
    movement_window: MovementWindowPayload
    coverage: HistoryCoveragePayload | None = None
    dimension_coverage: HistoryCoverageDimensionPayload | None = None
    default_compare_preset_key: Literal["latest", "4w", "12w", "cycle"]
    compare_presets: list[SnapshotComparePresetPayload] = Field(default_factory=list)
    featured_member_changes: list[MemberChangeSummaryPayload] = Field(default_factory=list)


class HistoryBackfillFailurePayload(ApiContractModel):
    snapshot_id: str
    snapshot_date: date
    reason: str | None = None
    error: str | None = None


class HistoryBackfillBootstrapPayload(ApiContractModel):
    dimension: str | None = None
    congress: int
    cadence: str
    date_window_start: date
    date_window_end: date
    bounded_by_today: bool
    planned_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    aggregate_source_count: int = Field(ge=0)
    latest_snapshot_id: str | None = None
    aggregate_target_root: str | None = None
    aggregate_error: str | None = None
    aggregate_verify_ok: bool | None = None
    aggregate_verify_errors: int | None = None
    coverage: HistoryCoveragePayload | None = None
    dimension_coverage: HistoryCoverageDimensionPayload | None = None
    member_coverage_index: MemberHistoryCoverageIndexPayload | None = None
    readiness_status: Literal["ready", "partial", "blocked"]
    readiness_score: int = Field(ge=0, le=100)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failed_attempts: list[HistoryBackfillFailurePayload] = Field(default_factory=list)


class HistoryPresetRangePayload(ApiContractModel):
    """Single-fetch historical preset-range payload."""

    preset: SnapshotComparePresetPayload
    movement_window: MovementWindowPayload
    snapshot_compare: SnapshotComparePayload


class SearchSessionResult(ApiContractModel):
    """One ranked search hit plus an optional preview payload."""

    lookup_entry: CurrentMemberLookupEntry
    member_page_preview: MemberPagePayload | None = None


class SearchSessionPayload(ApiContractModel):
    """Single-fetch search response for the homepage/member lookup flow."""

    query: str
    total_matches: int = Field(ge=0)
    results: list[SearchSessionResult] = Field(default_factory=list)
    snapshot: SnapshotSummaryPayload


# Typed response aliases for the launch endpoints.
ZipResponse = ApiEnvelope[ZipFeedPayload]
MemberResponse = ApiEnvelope[MemberProfilePayload]
EvidenceResponse = ApiEnvelope[EvidenceCardPayload]
OntologyGraphResponse = ApiEnvelope[OntologyGraphPayload]
OntologyIndexResponse = ApiEnvelope[OntologyIndexPayload]
OntologyMemberFeaturesResponse = ApiEnvelope[OntologyMemberFeaturesPayload]
OntologyMemberGraphResponse = ApiEnvelope[OntologyMemberGraphPayload]
HomepageResponse = ApiEnvelope[HomepageFeedPayload]
CurrentMemberLookupResponse = ApiEnvelope[CurrentMemberLookupPayload]
LastUpdatedResponse = ApiEnvelope[LastUpdatedPayload]
MovementFeedResponse = ApiEnvelope[MovementFeedPayload]
SnapshotSummaryResponse = ApiEnvelope[SnapshotSummaryPayload]
PredictionReadinessResponse = ApiEnvelope[PredictionReadinessPayload]
PredictionBootstrapResponse = ApiEnvelope[PredictionBootstrapPayload]
PredictionTopologyResponse = ApiEnvelope[PredictionTopologyPayload]
PredictionReadinessIndexResponse = ApiEnvelope[PredictionReadinessIndexPayload]
PredictionSourceIndexResponse = ApiEnvelope[PredictionSourceIndexPayload]
PredictionSourceContextResponse = ApiEnvelope[PredictionSourceContextPayload]
PredictionCommitteeReadinessResponse = ApiEnvelope[PredictionCommitteeReadinessPayload]
PredictionCommitteeContextResponse = ApiEnvelope[PredictionCommitteeContextPayload]
PredictionSectorReadinessResponse = ApiEnvelope[PredictionSectorReadinessPayload]
PredictionSectorContextResponse = ApiEnvelope[PredictionSectorContextPayload]
PredictionMemberReadinessResponse = ApiEnvelope[PredictionMemberReadinessPayload]
PredictionMemberContextResponse = ApiEnvelope[PredictionMemberContextPayload]
MemberPageResponse = ApiEnvelope[MemberPagePayload]
MemberCompareResponse = ApiEnvelope[MemberComparePayload]
MemberHistoryPageResponse = ApiEnvelope[MemberHistoryPagePayload]
MemberHistoryCoverageResponse = ApiEnvelope[MemberHistoryCoveragePayload]
MemberHistoryCoverageIndexResponse = ApiEnvelope[MemberHistoryCoverageIndexPayload]
MemberHistoryChartResponse = ApiEnvelope[MemberHistoryChartPayload]
MemberWindowCompareResponse = ApiEnvelope[MemberWindowComparePayload]
ZipEntryResponse = ApiEnvelope[ZipEntryPayload]
HomepageBootstrapResponse = ApiEnvelope[HomepageBootstrapPayload]
HistoryBootstrapResponse = ApiEnvelope[HistoryBootstrapPayload]
HistoryBackfillBootstrapResponse = ApiEnvelope[HistoryBackfillBootstrapPayload]
HistoryPresetRangeResponse = ApiEnvelope[HistoryPresetRangePayload]
SearchSessionResponse = ApiEnvelope[SearchSessionPayload]
SnapshotCompareResponse = ApiEnvelope[SnapshotComparePayload]
MemberHistoryResponse = ApiEnvelope[MemberHistoryPayload]
MemberTimelineDimensionResponse = ApiEnvelope[MemberTimelineDimensionPayload]
MemberTimelineIndexResponse = ApiEnvelope[MemberTimelineIndexPayload]
MemberTimelinePageResponse = ApiEnvelope[MemberTimelinePagePayload]
MemberTimelineYearResponse = ApiEnvelope[MemberTimelineYearPayload]
HistoryEventResponse = ApiEnvelope[MemberTimelineEventPayload]
HistoryEventPageResponse = ApiEnvelope[HistoryEventPagePayload]
SnapshotIndexResponse = ApiEnvelope[SnapshotIndexPayload]
MemberChangeSummaryResponse = ApiEnvelope[MemberChangeSummaryPayload]
MemberTrendSummaryResponse = ApiEnvelope[MemberTrendSummaryPayload]
MovementWindowResponse = ApiEnvelope[MovementWindowPayload]
