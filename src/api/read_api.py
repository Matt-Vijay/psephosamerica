"""Pure read-API response layer for the Psephos America launch endpoints.

  /api/v1/homepage
  /api/v1/zip/:zip
  /api/v1/member/:slug
  /api/v1/evidence/:id
  /api/v1/meta/last-updated

All functions are side-effect-free.  No web framework, no I/O.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.export.contracts import (
    EvidenceCardPayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryCoverageIndexPayload,
    MemberHistoryCoveragePayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    MemberTimelineDimensionPayload,
    MemberTimelineEventPayload,
    MemberTimelineIndexPayload,
    MemberTimelinePagePayload,
    MemberTimelineYearPayload,
    MemberTrendSummaryPayload,
    ZipFeedPayload,
)
from src.homepage.contracts import (
    HomepageFeedPayload,
    MovementWindowPayload,
    SnapshotComparePayload,
)
from src.identity.current_member_lookup import CurrentMemberLookupPayload
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
from src.runtime.history_backfill_types import HistoryBackfillReportPayload

from .contracts import (
    ApiEnvelope,
    BatchMeta,
    CurrentMemberLookupResponse,
    EvidenceResponse,
    HistoryBackfillBootstrapPayload,
    HistoryBackfillBootstrapResponse,
    HistoryBootstrapPayload,
    HistoryBootstrapResponse,
    HistoryEventPagePayload,
    HistoryEventPageResponse,
    HistoryEventResponse,
    HistoryPresetRangePayload,
    HistoryPresetRangeResponse,
    HomepageBootstrapPayload,
    HomepageBootstrapResponse,
    HomepageResponse,
    LastUpdatedPayload,
    LastUpdatedResponse,
    MemberChangeSummaryResponse,
    MemberComparePayload,
    MemberCompareResponse,
    MemberHistoryChartResponse,
    MemberHistoryCoverageIndexResponse,
    MemberHistoryCoverageResponse,
    MemberHistoryPagePayload,
    MemberHistoryPageResponse,
    MemberHistoryResponse,
    MemberPagePayload,
    MemberPageResponse,
    MemberResponse,
    MemberTimelineDimensionResponse,
    MemberTimelineIndexResponse,
    MemberTimelinePageResponse,
    MemberTimelineYearResponse,
    MemberTrendSummaryResponse,
    MemberWindowComparePayload,
    MemberWindowCompareResponse,
    MovementFeedPayload,
    MovementFeedResponse,
    MovementWindowResponse,
    NotFoundBody,
    OntologyGraphResponse,
    OntologyIndexResponse,
    OntologyMemberFeaturesResponse,
    OntologyMemberGraphResponse,
    PredictionBootstrapResponse,
    PredictionCommitteeContextResponse,
    PredictionCommitteeReadinessResponse,
    PredictionMemberContextResponse,
    PredictionMemberReadinessResponse,
    PredictionReadinessIndexResponse,
    PredictionReadinessResponse,
    PredictionSectorContextResponse,
    PredictionSectorReadinessResponse,
    PredictionSourceContextResponse,
    PredictionSourceIndexResponse,
    PredictionTopologyResponse,
    SearchSessionPayload,
    SearchSessionResponse,
    SnapshotCompareResponse,
    SnapshotIndexPayload,
    SnapshotIndexResponse,
    SnapshotSummaryPayload,
    SnapshotSummaryResponse,
    ZipEntryPayload,
    ZipEntryResponse,
    ZipResponse,
)

_CACHE_MAX_AGE = 3600  # 1 hour — matches weekly recompute cadence


# ── Batch metadata ──────────────────────────────────────────────────


def make_batch_meta(
    snapshot_date: date,
    published_at: datetime | None = None,  # defaults to now (UTC)
) -> BatchMeta:
    return BatchMeta(
        snapshot_date=snapshot_date,
        published_at=published_at if published_at is not None else datetime.now(UTC),
    )


# ── Payload wrappers ────────────────────────────────────────────────


def wrap_zip(payload: ZipFeedPayload, meta: BatchMeta) -> ZipResponse:
    return ApiEnvelope[ZipFeedPayload](meta=meta, data=payload)


def wrap_member(payload: MemberProfilePayload, meta: BatchMeta) -> MemberResponse:
    return ApiEnvelope[MemberProfilePayload](meta=meta, data=payload)


def wrap_member_history(
    payload: MemberHistoryPayload,
    meta: BatchMeta,
) -> MemberHistoryResponse:
    return ApiEnvelope[MemberHistoryPayload](meta=meta, data=payload)


def wrap_member_timeline_dimension(
    payload: MemberTimelineDimensionPayload,
    meta: BatchMeta,
) -> MemberTimelineDimensionResponse:
    return ApiEnvelope[MemberTimelineDimensionPayload](meta=meta, data=payload)


def wrap_member_timeline_index(
    payload: MemberTimelineIndexPayload,
    meta: BatchMeta,
) -> MemberTimelineIndexResponse:
    return ApiEnvelope[MemberTimelineIndexPayload](meta=meta, data=payload)


def wrap_member_timeline_page(
    payload: MemberTimelinePagePayload,
    meta: BatchMeta,
) -> MemberTimelinePageResponse:
    return ApiEnvelope[MemberTimelinePagePayload](meta=meta, data=payload)


def wrap_member_timeline_year(
    payload: MemberTimelineYearPayload,
    meta: BatchMeta,
) -> MemberTimelineYearResponse:
    return ApiEnvelope[MemberTimelineYearPayload](meta=meta, data=payload)


def wrap_history_event(
    payload: MemberTimelineEventPayload,
    meta: BatchMeta,
) -> HistoryEventResponse:
    return ApiEnvelope[MemberTimelineEventPayload](meta=meta, data=payload)


def wrap_history_event_page(
    payload: HistoryEventPagePayload,
    meta: BatchMeta,
) -> HistoryEventPageResponse:
    return ApiEnvelope[HistoryEventPagePayload](meta=meta, data=payload)


def wrap_member_history_chart(
    payload: MemberHistoryChartPayload,
    meta: BatchMeta,
) -> MemberHistoryChartResponse:
    return ApiEnvelope[MemberHistoryChartPayload](meta=meta, data=payload)


def wrap_member_history_page(
    payload: MemberHistoryPagePayload,
    meta: BatchMeta,
) -> MemberHistoryPageResponse:
    return ApiEnvelope[MemberHistoryPagePayload](meta=meta, data=payload)


def wrap_member_window_compare(
    payload: MemberWindowComparePayload,
    meta: BatchMeta,
) -> MemberWindowCompareResponse:
    return ApiEnvelope[MemberWindowComparePayload](meta=meta, data=payload)


def wrap_member_change_summary(
    payload: MemberChangeSummaryPayload,
    meta: BatchMeta,
) -> MemberChangeSummaryResponse:
    return ApiEnvelope[MemberChangeSummaryPayload](meta=meta, data=payload)


def wrap_member_history_coverage(
    payload: MemberHistoryCoveragePayload,
    meta: BatchMeta,
) -> MemberHistoryCoverageResponse:
    return ApiEnvelope[MemberHistoryCoveragePayload](meta=meta, data=payload)


def wrap_member_history_coverage_index(
    payload: MemberHistoryCoverageIndexPayload,
    meta: BatchMeta,
) -> MemberHistoryCoverageIndexResponse:
    return ApiEnvelope[MemberHistoryCoverageIndexPayload](meta=meta, data=payload)


def wrap_member_trend_summary(
    payload: MemberTrendSummaryPayload,
    meta: BatchMeta,
) -> MemberTrendSummaryResponse:
    return ApiEnvelope[MemberTrendSummaryPayload](meta=meta, data=payload)


def wrap_member_page(payload: MemberPagePayload, meta: BatchMeta) -> MemberPageResponse:
    return ApiEnvelope[MemberPagePayload](meta=meta, data=payload)


def wrap_member_compare(
    payload: MemberComparePayload,
    meta: BatchMeta,
) -> MemberCompareResponse:
    return ApiEnvelope[MemberComparePayload](meta=meta, data=payload)


def wrap_zip_entry(payload: ZipEntryPayload, meta: BatchMeta) -> ZipEntryResponse:
    return ApiEnvelope[ZipEntryPayload](meta=meta, data=payload)


def wrap_evidence(payload: EvidenceCardPayload, meta: BatchMeta) -> EvidenceResponse:
    return ApiEnvelope[EvidenceCardPayload](meta=meta, data=payload)


def wrap_ontology_graph(
    payload: OntologyGraphPayload,
    meta: BatchMeta,
) -> OntologyGraphResponse:
    return ApiEnvelope[OntologyGraphPayload](meta=meta, data=payload)


def wrap_ontology_index(
    payload: OntologyIndexPayload,
    meta: BatchMeta,
) -> OntologyIndexResponse:
    return ApiEnvelope[OntologyIndexPayload](meta=meta, data=payload)


def wrap_ontology_member_features(
    payload: OntologyMemberFeaturesPayload,
    meta: BatchMeta,
) -> OntologyMemberFeaturesResponse:
    return ApiEnvelope[OntologyMemberFeaturesPayload](meta=meta, data=payload)


def wrap_ontology_member_graph(
    payload: OntologyMemberGraphPayload,
    meta: BatchMeta,
) -> OntologyMemberGraphResponse:
    return ApiEnvelope[OntologyMemberGraphPayload](meta=meta, data=payload)


def wrap_prediction_readiness(
    payload: PredictionReadinessPayload,
    meta: BatchMeta,
) -> PredictionReadinessResponse:
    return ApiEnvelope[PredictionReadinessPayload](meta=meta, data=payload)


def wrap_prediction_bootstrap(
    payload: PredictionBootstrapPayload,
    meta: BatchMeta,
) -> PredictionBootstrapResponse:
    return ApiEnvelope[PredictionBootstrapPayload](meta=meta, data=payload)


def wrap_prediction_topology(
    payload: PredictionTopologyPayload,
    meta: BatchMeta,
) -> PredictionTopologyResponse:
    return ApiEnvelope[PredictionTopologyPayload](meta=meta, data=payload)


def wrap_prediction_readiness_index(
    payload: PredictionReadinessIndexPayload,
    meta: BatchMeta,
) -> PredictionReadinessIndexResponse:
    return ApiEnvelope[PredictionReadinessIndexPayload](meta=meta, data=payload)


def wrap_prediction_source_index(
    payload: PredictionSourceIndexPayload,
    meta: BatchMeta,
) -> PredictionSourceIndexResponse:
    return ApiEnvelope[PredictionSourceIndexPayload](meta=meta, data=payload)


def wrap_prediction_source_context(
    payload: PredictionSourceContextPayload,
    meta: BatchMeta,
) -> PredictionSourceContextResponse:
    return ApiEnvelope[PredictionSourceContextPayload](meta=meta, data=payload)


def wrap_prediction_committee_readiness(
    payload: PredictionCommitteeReadinessPayload,
    meta: BatchMeta,
) -> PredictionCommitteeReadinessResponse:
    return ApiEnvelope[PredictionCommitteeReadinessPayload](meta=meta, data=payload)


def wrap_prediction_committee_context(
    payload: PredictionCommitteeContextPayload,
    meta: BatchMeta,
) -> PredictionCommitteeContextResponse:
    return ApiEnvelope[PredictionCommitteeContextPayload](meta=meta, data=payload)


def wrap_prediction_sector_readiness(
    payload: PredictionSectorReadinessPayload,
    meta: BatchMeta,
) -> PredictionSectorReadinessResponse:
    return ApiEnvelope[PredictionSectorReadinessPayload](meta=meta, data=payload)


def wrap_prediction_sector_context(
    payload: PredictionSectorContextPayload,
    meta: BatchMeta,
) -> PredictionSectorContextResponse:
    return ApiEnvelope[PredictionSectorContextPayload](meta=meta, data=payload)


def wrap_prediction_member_readiness(
    payload: PredictionMemberReadinessPayload,
    meta: BatchMeta,
) -> PredictionMemberReadinessResponse:
    return ApiEnvelope[PredictionMemberReadinessPayload](meta=meta, data=payload)


def wrap_prediction_member_context(
    payload: PredictionMemberContextPayload,
    meta: BatchMeta,
) -> PredictionMemberContextResponse:
    return ApiEnvelope[PredictionMemberContextPayload](meta=meta, data=payload)


def wrap_homepage(payload: HomepageFeedPayload, meta: BatchMeta) -> HomepageResponse:
    return ApiEnvelope[HomepageFeedPayload](meta=meta, data=payload)


def wrap_homepage_bootstrap(
    payload: HomepageBootstrapPayload,
    meta: BatchMeta,
) -> HomepageBootstrapResponse:
    return ApiEnvelope[HomepageBootstrapPayload](meta=meta, data=payload)


def wrap_history_bootstrap(
    payload: HistoryBootstrapPayload,
    meta: BatchMeta,
) -> HistoryBootstrapResponse:
    return ApiEnvelope[HistoryBootstrapPayload](meta=meta, data=payload)


def wrap_history_backfill_bootstrap(
    payload: HistoryBackfillBootstrapPayload,
    meta: BatchMeta,
) -> HistoryBackfillBootstrapResponse:
    return ApiEnvelope[HistoryBackfillBootstrapPayload](meta=meta, data=payload)


def wrap_history_preset_range(
    payload: HistoryPresetRangePayload,
    meta: BatchMeta,
) -> HistoryPresetRangeResponse:
    return ApiEnvelope[HistoryPresetRangePayload](meta=meta, data=payload)


def wrap_history_backfill_report(
    payload: HistoryBackfillReportPayload,
    meta: BatchMeta,
) -> ApiEnvelope[HistoryBackfillReportPayload]:
    return ApiEnvelope[HistoryBackfillReportPayload](meta=meta, data=payload)


def wrap_snapshot_compare(
    payload: SnapshotComparePayload,
    meta: BatchMeta,
) -> SnapshotCompareResponse:
    return ApiEnvelope[SnapshotComparePayload](meta=meta, data=payload)


def wrap_current_member_lookup(
    payload: CurrentMemberLookupPayload,
    meta: BatchMeta,
) -> CurrentMemberLookupResponse:
    return ApiEnvelope[CurrentMemberLookupPayload](meta=meta, data=payload)


def wrap_movement_feed(
    payload: MovementFeedPayload,
    meta: BatchMeta,
) -> MovementFeedResponse:
    return ApiEnvelope[MovementFeedPayload](meta=meta, data=payload)


def wrap_movement_window(
    payload: MovementWindowPayload,
    meta: BatchMeta,
) -> MovementWindowResponse:
    return ApiEnvelope[MovementWindowPayload](meta=meta, data=payload)


def wrap_snapshot_summary(
    payload: SnapshotSummaryPayload,
    meta: BatchMeta,
) -> SnapshotSummaryResponse:
    return ApiEnvelope[SnapshotSummaryPayload](meta=meta, data=payload)


def wrap_snapshot_index(
    payload: SnapshotIndexPayload,
    meta: BatchMeta,
) -> SnapshotIndexResponse:
    return ApiEnvelope[SnapshotIndexPayload](meta=meta, data=payload)


def wrap_search_session(
    payload: SearchSessionPayload,
    meta: BatchMeta,
) -> SearchSessionResponse:
    return ApiEnvelope[SearchSessionPayload](meta=meta, data=payload)


def wrap_last_updated(snapshot_date: date, published_at: datetime) -> LastUpdatedResponse:
    meta = make_batch_meta(snapshot_date, published_at)
    return ApiEnvelope[LastUpdatedPayload](
        meta=meta,
        data=LastUpdatedPayload(snapshot_date=snapshot_date, published_at=published_at),
    )


# ── Headers ─────────────────────────────────────────────────────────


def make_headers(
    snapshot_date: date,
    etag: str | None = None,
) -> dict[str, str]:
    # Always returns a fresh copy. Unquoted strong ETags are wrapped per RFC 7232.
    # Weak validators (W/"...") are passed through unchanged.
    headers: dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": f"public, max-age={_CACHE_MAX_AGE}",
        "X-Snapshot-Date": snapshot_date.isoformat(),
    }
    if etag is not None:
        headers["ETag"] = etag if etag.startswith(('"', "W/")) else f'"{etag}"'
    return headers


# ── Not-found ───────────────────────────────────────────────────────


def not_found(resource_type: str, identifier: str) -> NotFoundBody:
    return NotFoundBody(
        resource_type=resource_type,
        identifier=identifier,
        detail=f"{resource_type} '{identifier}' not found",
    )
