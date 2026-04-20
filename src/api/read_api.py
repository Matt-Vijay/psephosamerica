"""Pure read-API response layer for the Open Pact launch endpoints.

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
    MemberHistoryPayload,
    MemberProfilePayload,
    MemberTrendSummaryPayload,
    ZipFeedPayload,
)
from src.homepage.contracts import (
    HomepageFeedPayload,
    MovementWindowPayload,
    SnapshotComparePayload,
)
from src.identity.current_member_lookup import CurrentMemberLookupPayload

from .contracts import (
    ApiEnvelope,
    BatchMeta,
    CurrentMemberLookupResponse,
    EvidenceResponse,
    HistoryBootstrapPayload,
    HistoryPresetRangePayload,
    HistoryBootstrapResponse,
    HistoryPresetRangeResponse,
    HomepageBootstrapPayload,
    HomepageBootstrapResponse,
    HomepageResponse,
    LastUpdatedPayload,
    LastUpdatedResponse,
    MemberChangeSummaryResponse,
    MemberHistoryResponse,
    MemberHistoryChartResponse,
    MemberComparePayload,
    MemberCompareResponse,
    MemberHistoryPagePayload,
    MemberHistoryPageResponse,
    MemberWindowComparePayload,
    MemberWindowCompareResponse,
    MemberPagePayload,
    MemberPageResponse,
    MovementFeedPayload,
    MovementFeedResponse,
    MovementWindowResponse,
    MemberTrendSummaryResponse,
    MemberResponse,
    NotFoundBody,
    SnapshotSummaryPayload,
    SnapshotSummaryResponse,
    SearchSessionPayload,
    SearchSessionResponse,
    SnapshotCompareResponse,
    SnapshotIndexPayload,
    SnapshotIndexResponse,
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


def wrap_history_preset_range(
    payload: HistoryPresetRangePayload,
    meta: BatchMeta,
) -> HistoryPresetRangeResponse:
    return ApiEnvelope[HistoryPresetRangePayload](meta=meta, data=payload)


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
