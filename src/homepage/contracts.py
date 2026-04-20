"""Homepage/feed payload contracts.

Isolated from src/export/contracts.py.  Centered on movement (what changed
and when), not on rankings or leaderboard position.  Read-only JSON shapes
generated after each recompute — not sources of truth.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from src.export.contracts import MemberChangeSummaryPayload


class MemberMovementSummary(BaseModel):
    """Per-member aggregate of score movement within a snapshot window."""

    bioguide_id: str
    name: str
    slug: str
    chamber: str  # "house" or "senate"
    party: str
    state: str
    dimension: str
    score_delta: float  # net signed score movement; negative = penalty, positive = recovery
    abs_delta: float    # always >= 0; used for ordering
    event_count: int = Field(ge=1)
    top_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Up to 3 card IDs ranked by abs_delta.",
    )


class RecentEventSummary(BaseModel):
    """One feed event for the homepage recent-events list.

    Lightweight: no block text, no source anchors.  Follow evidence_card_id
    for the full card.
    """

    feed_event_id: str
    member_bioguide_id: str
    member_name: str
    member_slug: str
    dimension: str
    score_delta: float
    short_explanation: str
    evidence_card_id: str | None = None
    occurred_at: date


class HomepageFeedPayload(BaseModel):
    """Top-level homepage/feed payload.

    Centered on movement — not a leaderboard.

    top_changes: ordered by abs_delta desc, bioguide_id asc.
    recent_events: ordered by occurred_at desc, feed_event_id asc.
    recent_evidence_card_ids: deduplicated card IDs from recent_events,
        preserving first-occurrence order.
    """

    snapshot_date: date
    top_changes: list[MemberMovementSummary]
    recent_events: list[RecentEventSummary]
    recent_evidence_card_ids: list[str] = Field(default_factory=list)


class MovementWindowPayload(BaseModel):
    """Historical movement payload spanning the latest published window."""

    window_key: str = "latest"
    has_full_window: bool = True
    latest_snapshot_id: str
    latest_snapshot_date: date
    previous_snapshot_id: str | None = None
    previous_snapshot_date: date | None = None
    top_changes: list[MemberMovementSummary]
    recent_events: list[RecentEventSummary]
    recent_evidence_card_ids: list[str] = Field(default_factory=list)


class SnapshotComparePayload(BaseModel):
    """Compare two published snapshots across the historical aggregate root."""

    start_snapshot_id: str
    start_snapshot_date: date
    end_snapshot_id: str
    end_snapshot_date: date
    top_changes: list[MemberMovementSummary] = Field(default_factory=list)
    recent_events: list[RecentEventSummary] = Field(default_factory=list)
    recent_evidence_card_ids: list[str] = Field(default_factory=list)
    featured_member_changes: list[MemberChangeSummaryPayload] = Field(default_factory=list)
