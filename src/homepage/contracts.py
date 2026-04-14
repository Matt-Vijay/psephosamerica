"""Typed contracts for the v1 homepage/feed payload.

These models are isolated from src/export/contracts.py and serve only the
homepage surface.  The payload is centered on movement (what changed and
when), not on rankings or leaderboard position.

All models are read-only JSON shapes — generated after each recompute, not
sources of truth.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class MemberMovementSummary(BaseModel):
    """Per-member aggregate of score movement within the snapshot window.

    Captures what changed for a member across one or more rule fires or
    evidence cards.  Ordered by magnitude, not by absolute score.
    """

    bioguide_id: str
    name: str
    slug: str
    chamber: str = Field(description="'house' or 'senate'")
    party: str
    state: str
    dimension: str = Field(description="Score dimension, e.g. 'conflict_of_interest_risk'")
    score_delta: float = Field(
        description="Net signed score change across all events in the window."
    )
    abs_delta: float = Field(
        description="Absolute magnitude; used for ordering — always >= 0."
    )
    event_count: int = Field(
        description="Number of feed events contributing to this summary.", ge=1
    )
    top_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Up to 3 evidence card IDs ranked by abs_delta, for lightweight linkage.",
    )


class RecentEventSummary(BaseModel):
    """Lightweight representation of one feed event for the recent-events list.

    Contains only what is needed for homepage display — no block text, no
    full source anchors.  Readers follow evidence_card_id to the full card.
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
    """Top-level payload for the homepage/feed surface (v1).

    Centered on movement: what changed, for whom, and when.  Not a
    leaderboard — no all-time rankings or aggregate guilt scores.

    Fields:
        snapshot_date: The recompute snapshot date this payload was built from.
        top_changes: Members with the largest absolute score movement in the
            snapshot window, ordered by abs_delta descending then bioguide_id
            ascending for determinism.
        recent_events: Most recent individual feed events, ordered by
            occurred_at descending then feed_event_id ascending.
        recent_evidence_card_ids: Flat, deduplicated list of evidence card IDs
            drawn from recent_events, in order of first appearance.  Provides
            a lightweight linkage layer for callers that only need card IDs.
    """

    snapshot_date: date
    top_changes: list[MemberMovementSummary]
    recent_events: list[RecentEventSummary]
    recent_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered, deduplicated evidence card IDs from recent_events. "
            "Preserves occurrence order."
        ),
    )
