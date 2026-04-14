"""Pure read-API response layer for the four Open Pact launch endpoints.

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
    MemberProfilePayload,
    ZipFeedPayload,
)

from .contracts import (
    ApiEnvelope,
    BatchMeta,
    EvidenceResponse,
    LastUpdatedPayload,
    LastUpdatedResponse,
    MemberResponse,
    NotFoundBody,
    ZipResponse,
)

_CACHE_MAX_AGE = 3600  # 1 hour — matches weekly recompute cadence


# ── Batch metadata ──────────────────────────────────────────────────


def make_batch_meta(
    snapshot_date: date,
    published_at: datetime | None = None,
) -> BatchMeta:
    """Return a :class:`BatchMeta`, defaulting *published_at* to now (UTC)."""
    return BatchMeta(
        snapshot_date=snapshot_date,
        published_at=published_at if published_at is not None else datetime.now(UTC),
    )


# ── Payload wrappers ────────────────────────────────────────────────


def wrap_zip(payload: ZipFeedPayload, meta: BatchMeta) -> ZipResponse:
    """Wrap a ZIP feed payload in the standard API envelope."""
    return ApiEnvelope[ZipFeedPayload](meta=meta, data=payload)


def wrap_member(payload: MemberProfilePayload, meta: BatchMeta) -> MemberResponse:
    """Wrap a member profile payload in the standard API envelope."""
    return ApiEnvelope[MemberProfilePayload](meta=meta, data=payload)


def wrap_evidence(payload: EvidenceCardPayload, meta: BatchMeta) -> EvidenceResponse:
    """Wrap an evidence card payload in the standard API envelope."""
    return ApiEnvelope[EvidenceCardPayload](meta=meta, data=payload)


def wrap_last_updated(snapshot_date: date, published_at: datetime) -> LastUpdatedResponse:
    """Build the ``/api/v1/meta/last-updated`` response."""
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
    """Return a stable headers dict for a read-API response.

    Always returns a fresh copy; callers may mutate it freely.
    ETags that are not already quoted are wrapped in double-quotes per RFC 7232.
    """
    headers: dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": f"public, max-age={_CACHE_MAX_AGE}",
        "X-Snapshot-Date": snapshot_date.isoformat(),
    }
    if etag is not None:
        headers["ETag"] = etag if etag.startswith('"') else f'"{etag}"'
    return headers


# ── Not-found ───────────────────────────────────────────────────────


def not_found(resource_type: str, identifier: str) -> NotFoundBody:
    """Return a consistent not-found response body for any endpoint."""
    return NotFoundBody(
        resource_type=resource_type,
        identifier=identifier,
        detail=f"{resource_type} '{identifier}' not found",
    )
