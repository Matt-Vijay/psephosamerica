"""Homepage/feed payload builders.

Pure functions.  Inputs: list[FeedEvent] + member_meta dict (bioguide_id →
{chamber, party, state}).  Output: HomepageFeedPayload.

All sorting is deterministic: primary key is a data field (abs_delta or
occurred_at, descending); secondary key is a stable string (bioguide_id or
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
from src.identity.current_member_lookup import CurrentMemberLookupEntry

_DEFAULT_TOP_N = 10
_DEFAULT_RECENT_N = 20
_MAX_CARD_IDS_PER_MEMBER = 3


# Internal helpers


def _coerce_date(value: dt.date | str) -> dt.date:
    if isinstance(value, str):
        return dt.date.fromisoformat(value)
    return value


def _occurred_at_rank(value: dt.date) -> float:
    rank = getattr(value, "_rank", None)
    if isinstance(rank, (int, float)) and not isinstance(rank, bool):
        return float(rank)
    return float(value.toordinal())


def _member_chamber(meta: dict[str, Any]) -> str:
    return str(meta.get("chamber", ""))


def _member_party(meta: dict[str, Any]) -> str:
    return str(meta.get("party", ""))


def _member_state(meta: dict[str, Any]) -> str:
    return str(meta.get("state", ""))


# Top-changes builder


def build_top_changes(
    events: list[FeedEvent],
    member_meta: dict[str, dict[str, Any]],
    *,
    n: int = _DEFAULT_TOP_N,
    dimension: str | None = None,
) -> list[MemberMovementSummary]:
    """Aggregate events by (member, dimension) and return the top-N movers.

    score_delta is summed per (bioguide_id, dimension) pair.  Up to
    _MAX_CARD_IDS_PER_MEMBER card IDs are kept, ranked by abs_delta within
    the group.  Sort: abs_delta desc, bioguide_id asc.
    """
    if n <= 0:
        return []

    pool = events if dimension is None else [e for e in events if e.dimension == dimension]

    groups: dict[tuple[str, str], list[FeedEvent]] = {}
    for ev in pool:
        groups.setdefault((ev.member_bioguide_id, ev.dimension), []).append(ev)

    summaries: list[MemberMovementSummary] = []
    for (bioguide_id, dim), group_events in groups.items():
        meta = member_meta.get(bioguide_id, {})
        net_delta = sum(e.score_delta for e in group_events)

        card_events = sorted(
            [e for e in group_events if e.evidence_card_id],
            key=lambda e: (-e.abs_delta, e.feed_event_id),
        )
        top_card_ids: list[str] = []
        seen_card_ids: set[str] = set()
        for event in card_events:
            card_id = event.evidence_card_id
            if card_id is None or card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            top_card_ids.append(card_id)
            if len(top_card_ids) >= _MAX_CARD_IDS_PER_MEMBER:
                break

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
                abs_delta=abs(net_delta),
                event_count=len(group_events),
                top_evidence_card_ids=top_card_ids,
            )
        )

    summaries.sort(key=lambda s: (-s.abs_delta, s.bioguide_id))
    return summaries[:n]


# Recent-events builder


def build_recent_events(
    events: list[FeedEvent],
    *,
    since: dt.date | None = None,
    n: int = _DEFAULT_RECENT_N,
) -> list[RecentEventSummary]:
    """Return the N most recent events.  Sort: occurred_at desc, feed_event_id asc."""
    if n <= 0:
        return []

    pool = events if since is None else [e for e in events if e.occurred_at >= since]
    pool = sorted(pool, key=lambda e: (-_occurred_at_rank(e.occurred_at), e.feed_event_id))

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


# Card-ID linkage helper


def extract_recent_evidence_card_ids(recent_events: list[RecentEventSummary]) -> list[str]:
    """Ordered, deduplicated card IDs from recent_events (first-occurrence order)."""
    seen: set[str] = set()
    result: list[str] = []
    for ev in recent_events:
        if ev.evidence_card_id and ev.evidence_card_id not in seen:
            seen.add(ev.evidence_card_id)
            result.append(ev.evidence_card_id)
    return result


def build_featured_lookup_entries(
    top_changes: list[MemberMovementSummary],
    recent_events: list[RecentEventSummary],
    lookup_entries: list[CurrentMemberLookupEntry],
) -> list[CurrentMemberLookupEntry]:
    """Select ordered, deduped lookup entries visible in the homepage bootstrap."""

    lookup_by_bioguide_id = {entry.bioguide_id: entry for entry in lookup_entries}
    result: list[CurrentMemberLookupEntry] = []
    seen_ids: set[str] = set()

    for change in top_changes:
        if change.bioguide_id in seen_ids:
            continue
        entry = lookup_by_bioguide_id.get(change.bioguide_id)
        if entry is None:
            continue
        seen_ids.add(change.bioguide_id)
        result.append(entry)

    for event in recent_events:
        if event.member_bioguide_id in seen_ids:
            continue
        entry = lookup_by_bioguide_id.get(event.member_bioguide_id)
        if entry is None:
            continue
        seen_ids.add(event.member_bioguide_id)
        result.append(entry)

    return result


# Top-level payload builder


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
    """Build HomepageFeedPayload from a list of FeedEvents.

    ``since`` filters recent_events only; top_changes aggregates the full pool.
    ``dimension`` restricts both surfaces.
    """
    top_changes = build_top_changes(events, member_meta, n=top_n, dimension=dimension)

    recent_events = build_recent_events(events, since=since, n=recent_n)
    if dimension is not None:
        recent_events = [e for e in recent_events if e.dimension == dimension][:recent_n]

    return HomepageFeedPayload(
        snapshot_date=snapshot_date,
        top_changes=top_changes,
        recent_events=recent_events,
        recent_evidence_card_ids=extract_recent_evidence_card_ids(recent_events),
    )
