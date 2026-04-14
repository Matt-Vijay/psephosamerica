"""Feed-event selection and ranking primitives.

Pure helpers.  No I/O, no database access, no network calls.

Input shapes:
  - rule_fire dicts  (subset of RuleFire fields)
  - evidence_card dicts (subset of EvidenceCardPayload fields)
  - score_delta dicts  (member_bioguide_id, dimension, delta, snapshot_date)
  - member dicts  (bioguide_id, full_name, slug, chamber, party, state)

Output: list[FeedEvent] — deterministic objects suitable for feed ordering.

ID semantics
------------
Two distinct ID systems coexist in Open Pact:

* **Internal feed-ranking IDs** (this module) — built by
  :func:`make_feed_event_id`.  These use SHA-256 with an ``fe_`` prefix and
  are used only inside ``src.feed`` for ordering, deduplication, and
  deterministic tie-breaking within a recompute batch.  They are *not*
  exposed in public URLs.

* **Public ZIP-feed IDs** — built by
  :func:`src.identity.public_ids.build_feed_event_id`.  These use BLAKE2b +
  base32 with an ``fe-`` prefix (hyphen, not underscore) and incorporate the
  ZIP code so the same evidence card shown in two different ZIP feeds gets
  distinct stable identifiers.  Only these IDs appear in exported JSON and
  public URLs.

The constructor helpers (:func:`events_from_rule_fires`,
:func:`events_from_evidence_cards`, :func:`events_from_score_deltas`) accept
an optional ``id_builder`` parameter (a :class:`FeedIdBuilder`) so callers
can substitute an alternative implementation — for example, to wire public
IDs into the ``feed_event_id`` field when building ZIP-feed payloads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ID builder protocol
# ---------------------------------------------------------------------------


class FeedIdBuilder(Protocol):
    """Callable protocol for feed-ranking ID builders.

    The default implementation is :func:`make_feed_event_id`, which produces
    internal SHA-256-based IDs (``fe_`` prefix).  Callers that need public
    ZIP-feed IDs should inject their own builder that delegates to
    :func:`src.identity.public_ids.build_feed_event_id` (``fe-`` prefix,
    BLAKE2b + base32) or any other deterministic scheme.

    All implementations must be pure functions — same inputs always produce
    the same output.
    """

    def __call__(
        self,
        kind: "FeedEventKind | str",
        member_bioguide_id: str,
        dimension: str,
        snapshot_date: dt.date,
        *discriminators: str,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FeedEventKind(str, Enum):
    RULE_FIRE = "rule_fire"
    EVIDENCE_CARD = "evidence_card"
    SCORE_DELTA = "score_delta"


# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------


class FeedEvent(BaseModel):
    """A single ranked item suitable for homepage/feed display.

    All fields are populated from deterministic inputs; no random values.
    """

    feed_event_id: str = Field(
        description="Stable, deterministic identifier for deduplication and ordering."
    )
    kind: FeedEventKind
    member_bioguide_id: str
    member_name: str
    member_slug: str
    dimension: str
    score_delta: float = Field(
        description="Signed score change; negative means a penalty was applied."
    )
    abs_delta: float = Field(
        description="Absolute magnitude used for ranking; always >= 0."
    )
    short_explanation: str
    evidence_card_id: str | None = None
    snapshot_date: dt.date
    occurred_at: dt.date = Field(
        description="Best available date for recency sorting (snapshot_date fallback)."
    )


# ---------------------------------------------------------------------------
# Stable ID builder
# ---------------------------------------------------------------------------


def make_feed_event_id(
    kind: FeedEventKind | str,
    member_bioguide_id: str,
    dimension: str,
    snapshot_date: dt.date,
    *discriminators: str,
) -> str:
    """Return a deterministic **internal** feed-ranking ID (``fe_`` prefix).

    This is the default :class:`FeedIdBuilder` implementation.  IDs are
    built from SHA-256 of the canonical input fields; the first 16 hex
    characters are enough for uniqueness within a single recompute batch.

    These IDs are used exclusively inside ``src.feed`` for ordering and
    deduplication.  They are *not* the same as the public ZIP-feed IDs
    produced by :func:`src.identity.public_ids.build_feed_event_id`, which
    use a different hash (BLAKE2b + base32), include a ZIP code, and use a
    hyphen separator (``fe-``) rather than an underscore.

    Args:
        kind: FeedEventKind or string label.
        member_bioguide_id: Canonical member identifier.
        dimension: Score dimension (e.g. 'conflict_of_interest_risk').
        snapshot_date: Date of the recompute snapshot.
        *discriminators: Any additional strings (rule_id, card id, etc.)
            that make this event unique within the member+dimension+date group.
    """
    parts = [
        str(kind),
        member_bioguide_id,
        dimension,
        snapshot_date.isoformat(),
        *discriminators,
    ]
    raw = "|".join(parts).encode()
    return "fe_" + hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def events_from_rule_fires(
    rule_fires: list[dict[str, Any]],
    members: dict[str, dict[str, Any]],
    *,
    score_delta_by_severity: dict[str, float] | None = None,
    id_builder: FeedIdBuilder | None = None,
) -> list[FeedEvent]:
    """Build FeedEvents from raw rule-fire dicts.

    Args:
        rule_fires: Each dict must contain:
            - ``fire_id`` (str)
            - ``rule_id`` (str)
            - ``member_bioguide_id`` (str)
            - ``dimension`` (str)
            - ``severity`` (str: 'low' | 'medium' | 'high')
            - ``explanation`` (str)
            - ``snapshot_date`` (date or ISO str)
            - optionally ``score_delta`` (float)
        members: Mapping of bioguide_id → member dict with
            ``full_name``, ``slug`` fields.
        score_delta_by_severity: Optional override map from severity label to
            a signed float delta.  Defaults to low=-5, medium=-15, high=-30.
        id_builder: Optional :class:`FeedIdBuilder` to construct the
            ``feed_event_id``.  Defaults to :func:`make_feed_event_id`
            (internal SHA-256 IDs).  Inject a custom builder when public
            ZIP-feed IDs are required.
    """
    default_deltas: dict[str, float] = {
        "low": -5.0,
        "medium": -15.0,
        "high": -30.0,
        "critical": -50.0,
    }
    delta_map = {**default_deltas, **(score_delta_by_severity or {})}

    events: list[FeedEvent] = []
    for rf in rule_fires:
        bioguide_id = rf["member_bioguide_id"]
        member = members.get(bioguide_id)
        if member is None:
            continue

        snap = rf["snapshot_date"]
        if isinstance(snap, str):
            snap = dt.date.fromisoformat(snap)

        delta = float(rf.get("score_delta") or delta_map.get(rf.get("severity", "low"), -5.0))

        _build_id = id_builder if id_builder is not None else make_feed_event_id
        feed_event_id = _build_id(
            FeedEventKind.RULE_FIRE,
            bioguide_id,
            rf["dimension"],
            snap,
            rf["fire_id"],
        )

        events.append(
            FeedEvent(
                feed_event_id=feed_event_id,
                kind=FeedEventKind.RULE_FIRE,
                member_bioguide_id=bioguide_id,
                member_name=member.get("full_name") or member.get("name", ""),
                member_slug=member["slug"],
                dimension=rf["dimension"],
                score_delta=delta,
                abs_delta=abs(delta),
                short_explanation=rf.get("explanation", ""),
                evidence_card_id=rf.get("evidence_card_id"),
                snapshot_date=snap,
                occurred_at=snap,
            )
        )
    return events


def events_from_evidence_cards(
    evidence_cards: list[dict[str, Any]],
    members: dict[str, dict[str, Any]],
    *,
    id_builder: FeedIdBuilder | None = None,
) -> list[FeedEvent]:
    """Build FeedEvents from evidence-card dicts.

    Args:
        evidence_cards: Each dict must contain:
            - ``public_id`` (str)
            - ``member_bioguide_id`` (str)
            - ``dimension`` (str)
            - ``score_delta`` (float)
            - ``short_explanation`` (str)
            - ``snapshot_date`` (date or ISO str)
            - optionally ``rendered_at`` (date or ISO str) for recency
        members: Mapping of bioguide_id → member dict with
            ``full_name``, ``slug`` fields.
        id_builder: Optional :class:`FeedIdBuilder` to construct the
            ``feed_event_id``.  Defaults to :func:`make_feed_event_id`
            (internal SHA-256 IDs).
    """
    events: list[FeedEvent] = []
    for card in evidence_cards:
        bioguide_id = card["member_bioguide_id"]
        member = members.get(bioguide_id)
        if member is None:
            continue

        snap = card["snapshot_date"]
        if isinstance(snap, str):
            snap = dt.date.fromisoformat(snap)

        occurred: dt.date = snap
        if card.get("rendered_at"):
            raw_rendered = card["rendered_at"]
            if isinstance(raw_rendered, str):
                raw_rendered = dt.date.fromisoformat(raw_rendered[:10])
            elif isinstance(raw_rendered, dt.datetime):
                raw_rendered = raw_rendered.date()
            occurred = raw_rendered

        delta = float(card["score_delta"])
        _build_id = id_builder if id_builder is not None else make_feed_event_id
        feed_event_id = _build_id(
            FeedEventKind.EVIDENCE_CARD,
            bioguide_id,
            card["dimension"],
            snap,
            card["public_id"],
        )

        events.append(
            FeedEvent(
                feed_event_id=feed_event_id,
                kind=FeedEventKind.EVIDENCE_CARD,
                member_bioguide_id=bioguide_id,
                member_name=member.get("full_name") or member.get("name", ""),
                member_slug=member["slug"],
                dimension=card["dimension"],
                score_delta=delta,
                abs_delta=abs(delta),
                short_explanation=card.get("short_explanation", ""),
                evidence_card_id=card["public_id"],
                snapshot_date=snap,
                occurred_at=occurred,
            )
        )
    return events


def events_from_score_deltas(
    score_deltas: list[dict[str, Any]],
    members: dict[str, dict[str, Any]],
    *,
    id_builder: FeedIdBuilder | None = None,
) -> list[FeedEvent]:
    """Build FeedEvents from per-member per-dimension score deltas.

    Useful for generating aggregate feed items when individual card/fire
    IDs are not available.

    Args:
        score_deltas: Each dict must contain:
            - ``member_bioguide_id`` (str)
            - ``dimension`` (str)
            - ``delta`` (float)
            - ``snapshot_date`` (date or ISO str)
            - optionally ``explanation`` (str)
        members: Mapping of bioguide_id → member dict.
        id_builder: Optional :class:`FeedIdBuilder` to construct the
            ``feed_event_id``.  Defaults to :func:`make_feed_event_id`
            (internal SHA-256 IDs).
    """
    events: list[FeedEvent] = []
    for sd in score_deltas:
        bioguide_id = sd["member_bioguide_id"]
        member = members.get(bioguide_id)
        if member is None:
            continue

        snap = sd["snapshot_date"]
        if isinstance(snap, str):
            snap = dt.date.fromisoformat(snap)

        delta = float(sd["delta"])
        _build_id = id_builder if id_builder is not None else make_feed_event_id
        feed_event_id = _build_id(
            FeedEventKind.SCORE_DELTA,
            bioguide_id,
            sd["dimension"],
            snap,
        )

        events.append(
            FeedEvent(
                feed_event_id=feed_event_id,
                kind=FeedEventKind.SCORE_DELTA,
                member_bioguide_id=bioguide_id,
                member_name=member.get("full_name") or member.get("name", ""),
                member_slug=member["slug"],
                dimension=sd["dimension"],
                score_delta=delta,
                abs_delta=abs(delta),
                short_explanation=sd.get("explanation", ""),
                evidence_card_id=None,
                snapshot_date=snap,
                occurred_at=snap,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Ranking and selection helpers
# ---------------------------------------------------------------------------


def sort_by_magnitude(events: list[FeedEvent]) -> list[FeedEvent]:
    """Return events sorted by abs_delta descending, then feed_event_id ascending.

    The secondary sort on feed_event_id guarantees a deterministic, stable
    order when multiple events share the same magnitude.
    """
    return sorted(events, key=lambda e: (-e.abs_delta, e.feed_event_id))


def select_recent(
    events: list[FeedEvent],
    *,
    since: dt.date,
    limit: int | None = None,
) -> list[FeedEvent]:
    """Return events whose occurred_at >= *since*, most recent first.

    Args:
        events: Input feed events (any order).
        since: Inclusive lower bound on occurred_at.
        limit: Optional cap on the number of results returned.
    """
    filtered = [e for e in events if e.occurred_at >= since]
    # Sort by occurred_at desc, then by abs_delta desc, then by id for stability.
    sorted_events = sorted(
        filtered,
        key=lambda e: (e.occurred_at, e.abs_delta, e.feed_event_id),
        reverse=True,
    )
    if limit is not None:
        return sorted_events[:limit]
    return sorted_events


def select_top_changes(
    events: list[FeedEvent],
    *,
    n: int,
    dimension: str | None = None,
) -> list[FeedEvent]:
    """Return the top-N events by magnitude, optionally filtered by dimension.

    Args:
        events: Input feed events (any order).
        n: Maximum number of results.
        dimension: If given, restrict to events matching this dimension.
    """
    if n <= 0:
        return []
    pool = events if dimension is None else [e for e in events if e.dimension == dimension]
    return sort_by_magnitude(pool)[:n]
