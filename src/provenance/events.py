"""Builders for append-only StageEvents in the provenance chain.

All functions are pure: they accept facts and return StageEvent instances
with UTC timestamps set at call time.  No I/O.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .models import RunStatus, StageEvent


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _payload_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON serialisation of *payload*."""
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


# Stage event factories


def fetch_event(
    artifact_sha256: str,
    source_url: str | None = None,
    notes: str | None = None,
) -> StageEvent:
    """Record a successful artifact fetch."""
    return StageEvent(
        stage="fetch",
        status="succeeded",
        artifact_sha256=artifact_sha256,
        occurred_at=_utcnow(),
        notes=notes,
        metadata={"source_url": source_url} if source_url else {},
    )


def fetch_failed_event(
    error_message: str,
    source_url: str | None = None,
) -> StageEvent:
    """Record a failed artifact fetch."""
    return StageEvent(
        stage="fetch",
        status="failed",
        artifact_sha256=None,
        occurred_at=_utcnow(),
        error_message=error_message,
        metadata={"source_url": source_url} if source_url else {},
    )


def parse_event(
    artifact_sha256: str,
    parser_name: str,
    parser_version: str,
    parsed_payload: Any,
    notes: str | None = None,
) -> StageEvent:
    """Record a successful parse of one artifact."""
    phash = _payload_hash(parsed_payload)
    return StageEvent(
        stage="parse",
        status="succeeded",
        artifact_sha256=artifact_sha256,
        occurred_at=_utcnow(),
        payload_hash=phash,
        notes=notes,
        metadata={
            "parser_name": parser_name,
            "parser_version": parser_version,
        },
    )


def parse_failed_event(
    artifact_sha256: str,
    parser_name: str,
    parser_version: str,
    error_message: str,
) -> StageEvent:
    """Record a parse failure for one artifact."""
    return StageEvent(
        stage="parse",
        status="failed",
        artifact_sha256=artifact_sha256,
        occurred_at=_utcnow(),
        error_message=error_message,
        metadata={
            "parser_name": parser_name,
            "parser_version": parser_version,
        },
    )


def normalize_event(
    artifact_sha256: str,
    normalized_payload: Any,
    notes: str | None = None,
) -> StageEvent:
    """Record a successful normalisation step."""
    phash = _payload_hash(normalized_payload)
    return StageEvent(
        stage="normalize",
        status="succeeded",
        artifact_sha256=artifact_sha256,
        occurred_at=_utcnow(),
        payload_hash=phash,
        notes=notes,
    )


def export_event(
    snapshot_sha256: str,
    export_key: str,
    notes: str | None = None,
) -> StageEvent:
    """Record a successful export of a snapshot artifact."""
    return StageEvent(
        stage="export",
        status="succeeded",
        artifact_sha256=snapshot_sha256,
        occurred_at=_utcnow(),
        notes=notes,
        metadata={"export_key": export_key},
    )


def generic_event(
    stage: str,
    status: RunStatus,
    artifact_sha256: str | None = None,
    payload: Any | None = None,
    error_message: str | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StageEvent:
    """Low-level builder for any stage/status combination."""
    phash = _payload_hash(payload) if payload is not None else None
    return StageEvent(
        stage=stage,
        status=status,
        artifact_sha256=artifact_sha256,
        occurred_at=_utcnow(),
        payload_hash=phash,
        error_message=error_message,
        notes=notes,
        metadata=metadata or {},
    )
