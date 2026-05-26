"""Typed models for the provenance chain.

Mirrors the provenance/review tables in db/schema.sql:
  ingestion_run, source_artifact, parse_run, and stage events.
No I/O; pure data containers only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional


RunType = Literal["ingest", "recompute", "export"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
ArtifactKind = Literal["pdf", "xml", "csv", "json", "html", "txt", "other"]
ConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class IngestionRunContext:
    """Snapshot of metadata for a single ingestion_run row.

    Mirrors ingestion_run columns used in the provenance chain.
    db id is optional because a run may not yet be persisted.
    """

    data_source_slug: str
    run_type: RunType
    status: RunStatus
    parameters: dict[str, Any] = field(default_factory=dict)
    db_id: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    record_count: int = 0
    error_message: Optional[str] = None


@dataclass(frozen=True)
class SourceArtifactMeta:
    """Metadata for a source_artifact row.

    storage_uri and sha256 are the canonical immutable identifiers.
    """

    data_source_slug: str
    artifact_kind: ArtifactKind
    storage_uri: str
    sha256: str  # 64-char hex
    source_url: Optional[str] = None
    mime_type: Optional[str] = None
    fetched_at: Optional[datetime] = None
    source_record_id: Optional[str] = None
    ingestion_run_id: Optional[int] = None
    db_id: Optional[int] = None

    def __post_init__(self) -> None:
        _require_sha256_hex(self.sha256, field_name="sha256")


@dataclass(frozen=True)
class ParseRunContext:
    """Snapshot of metadata for a parse_run row.

    Captures parser identity and result summary without any I/O.
    """

    source_artifact_id: int
    parser_name: str
    parser_version: str
    status: RunStatus
    page_count: Optional[int] = None
    ocr_page_count: Optional[int] = None
    confidence_summary: dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    ingestion_run_id: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    db_id: Optional[int] = None


@dataclass(frozen=True)
class StageEvent:
    """Single append-only stage event in a provenance chain.

    Records what happened at a pipeline stage, when, against which
    artifact, and with what content hash.  Built by events.py helpers.
    """

    stage: str  # e.g. "fetch", "parse", "normalize"
    status: RunStatus
    artifact_sha256: Optional[str]  # sha256 of the artifact touched
    occurred_at: datetime
    payload_hash: Optional[str] = None  # sha256 of any derived payload
    notes: Optional[str] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.artifact_sha256 is not None:
            _require_sha256_hex(self.artifact_sha256, field_name="artifact_sha256")
        if self.payload_hash is not None:
            _require_sha256_hex(self.payload_hash, field_name="payload_hash")


@dataclass
class StageLog:
    """Ordered sequence of StageEvents for one pipeline run."""

    run_id: Optional[int]
    events: list[StageEvent] = field(default_factory=list)

    def append(self, event: StageEvent) -> None:
        self.events.append(event)

    @property
    def final_status(self) -> Optional[RunStatus]:
        if not self.events:
            return None
        return self.events[-1].status


def _require_sha256_hex(value: str, *, field_name: str) -> None:
    if len(value) != 64 or not all(char in "0123456789abcdefABCDEF" for char in value):
        raise ValueError(f"{field_name} must be a 64-character hex string")
