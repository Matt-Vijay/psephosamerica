from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from src.export.contracts import (
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)

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


# Typed response aliases for the four launch endpoints.
ZipResponse = ApiEnvelope[ZipFeedPayload]
MemberResponse = ApiEnvelope[MemberProfilePayload]
EvidenceResponse = ApiEnvelope[EvidenceCardPayload]
LastUpdatedResponse = ApiEnvelope[LastUpdatedPayload]
