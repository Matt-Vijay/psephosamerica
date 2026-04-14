"""Builders for the v1 homepage/feed payload.

Pure functions only.  No I/O, no database access, no network calls.

Inputs:
  - list[FeedEvent] from src/feed/changes.py
  - member_meta: dict[bioguide_id, dict] with at minimum
      {chamber, party, state} alongside the fields already on FeedEvent

Output:
  - HomepageFeedPayload

Sorting is always deterministic: primary key is a data field (abs_delta or
occurred_at, descending), secondary key is a stable string (bioguide_id or
feed_event_id, ascending).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.feed.changes import FeedEvent
from src.homepage.contracts import (
    HomepageFeedPayload,
    MemberMovementSummary,
    RecentEventSummary,
)

_DEFAULT_TOP_N = 10
_DEFAULT_RECENT_N = 20
_MAX_CARD_IDS_PER_MEMBER = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: dt.date | str) -> dt.date:
    if isinstance(value, str):
        return dt.date.fromisoformat(value)
    return value


def _member_chamber(meta: dict[str, Any]) -> str:
    return str(meta.get("chamber", ""))


def _member_party(meta: dict[str, Any]) -> str:
    return str(meta.get("party", ""))


def _member_state(meta: dict[str, Any]) -> str:
    return str(meta.get("state", ""))


# ---------------------------------------------------------------------------
# Top-changes builder
# ---------------------------------------------------------------------------


def build_top_changes(
    events: list[FeedEvent],
    member_meta: dict[str, dict[str, Any]],
    *,
    n: int = _DEFAULT_TOP_N,
    dimension: str | None = None,
) -> list[MemberMovementSummary]:
    """Aggregate events by (member, dimension) and return the top-N movers.

    Aggregation sums score_delta per (bioguide_id, dimension) pair and
    collects up to _MAX_CARD_IDS_PER_MEMBER evidence card IDs ranked by
    abs_delta within that group.

    Sorting: abs_delta descending, then bioguide_id ascending (deterministic).

    Args:
        events: Feed events to aggregate.
        member_meta: Mapping of bioguide_id → dict with chamber/party/state.
        n: Maximum number of summaries to return.
        dimension: If given, restrict to events for this dimension only.
    """
    if n <= 0:
        return []

    pool = events if dimension is None else [e for e in events if e.dimension == dimension]

    # Accumulate per (bioguide_id, dimension)
    # key → (net_delta, event_count, [(abs_delta, card_id)])
    groups: dict[tuple[str, str], list[FeedEvent]] = {}
    for ev in pool:
        key = (ev.member_bioguide_id, ev.dimension)
        groups.setdefault(key, []).append(ev)

    summaries: list[MemberMovementSummary] = []
    for (bioguide_id, dim), group_events in groups.items():
        meta = member_meta.get(bioguide_id, {})
        net_delta = sum(e.score_delta for e in group_events)
        abs_delta = abs(net_delta)

        # Pick top card IDs by abs_delta within this group, stable by feed_event_id
        card_events = sorted(
            [e for e in group_events if e.evidence_card_id],
            key=lambda e: (-e.abs_delta, e.feed_event_id),
        )
        top_card_ids = [e.evidence_card_id for e in card_events[:_MAX_CARD_IDS_PER_MEMBER]]

        # Use the name/slug from the first event (they're stable per member)
        first = group_events[0]

        summaries.append(
            MemberMovementSummary(
                bioguide_id=bioguide_id,
                name=first.member_name,
                slug=first.member_slug,
                chamber=_member_chamber(meta),
                party=_member_party(meta),
                state=_member_state(meta),
                dimension=dim,
                score_delta=net_delta,
                abs_delta=abs_delta,
                event_count=len(group_events),
                top_evidence_card_ids=top_card_ids,
            )
        )

    # Deterministic sort: abs_delta desc, bioguide_id asc
    summaries.sort(key=lambda s: (-s.abs_delta, s.bioguide_id))
    return summaries[:n]


# ---------------------------------------------------------------------------
# Recent-events builder
# ---------------------------------------------------------------------------


def build_recent_events(
    events: list[FeedEvent],
    *,
    since: dt.date | None = None,
    n: int = _DEFAULT_RECENT_N,
) -> list[RecentEventSummary]:
    """Return the N most recent feed events as RecentEventSummary objects.

    Sorting: occurred_at descending, then feed_event_id ascending (deterministic).

    Args:
        events: Feed events to filter and rank.
        since: If given, exclude events with occurred_at < since.
        n: Maximum results to return.
    """
    if n <= 0:
        return []

    pool = events
    if since is not None:
        pool = [e for e in pool if e.occurred_at >= since]

    # Deterministic sort: occurred_at desc (primary), feed_event_id asc (secondary)
    pool = sorted(pool, key=lambda e: (-e.occurred_at.toordinal(), e.feed_event_id))

    return [
        RecentEventSummary(
            feed_event_id=ev.feed_event_id,
            member_bioguide_id=ev.member_bioguide_id,
            member_name=ev.member_name,
            member_slug=ev.member_slug,
            dimension=ev.dimension,
            score_delta=ev.score_delta,
            short_explanation=ev.short_explanation,
            evidence_card_id=ev.evidence_card_id,
            occurred_at=ev.occurred_at,
        )
        for ev in pool[:n]
    ]


# ---------------------------------------------------------------------------
# Card-ID linkage helper
# ---------------------------------------------------------------------------


def extract_recent_evidence_card_ids(recent_events: list[RecentEventSummary]) -> list[str]:
    """Return ordered, deduplicated evidence card IDs from recent_events.

    Preserves first-occurrence order so the list mirrors the recency ranking.
    """
    seen: set[str] = set()
    result: list[str] = []
    for ev in recent_events:
        if ev.evidence_card_id and ev.evidence_card_id not in seen:
            seen.add(ev.evidence_card_id)
            result.append(ev.evidence_card_id)
    return result


# ---------------------------------------------------------------------------
# Top-level payload builder
# ---------------------------------------------------------------------------


def build_homepage_feed(
    events: list[FeedEvent],
    member_meta: dict[str, dict[str, Any]],
    *,
    snapshot_date: dt.date,
    top_n: int = _DEFAULT_TOP_N,
    recent_n: int = _DEFAULT_RECENT_N,
    since: dt.date | None = None,
    dimension: str | None = None,
) -> HomepageFeedPayload:
    """Build the complete HomepageFeedPayload from a list of FeedEvents.

    Args:
        events: All feed events for this snapshot.  May contain multiple
            kinds (rule_fire, evidence_card, score_delta).
        member_meta: Mapping of bioguide_id → dict with at minimum
            ``chamber``, ``party``, ``state``.
        snapshot_date: The recompute snapshot date for this payload.
        top_n: Maximum members in top_changes.
        recent_n: Maximum events in recent_events.
        since: Lower bound (inclusive) on occurred_at for recent_events.
            Does not affect top_changes aggregation.
        dimension: If given, restrict both top_changes and recent_events to
            this dimension only.
    """
    top_changes = build_top_changes(
        events,
        member_meta,
        n=top_n,
        dimension=dimension,
    )

    recent_events = build_recent_events(
        events,
        since=since,
        n=recent_n,
    )
    if dimension is not None:
        recent_events = [e for e in recent_events if e.dimension == dimension][:recent_n]

    card_ids = extract_recent_evidence_card_ids(recent_events)

    return HomepageFeedPayload(
        snapshot_date=snapshot_date,
        top_changes=top_changes,
        recent_events=recent_events,
        recent_evidence_card_ids=card_ids,
    )
