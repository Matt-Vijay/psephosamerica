"""Feed-event selection and ranking primitives.

Pure helpers — no I/O, no database access, no network calls.

ID semantics
------------
Two distinct ID systems coexist:

* **Internal feed-ranking IDs** — ``make_feed_event_id``, SHA-256, ``fe_`` prefix
  (underscore).  Used only inside ``src.feed`` for ordering and deduplication.
  Never exposed in public URLs.

* **Public ZIP-feed IDs** — ``src.identity.public_ids.build_feed_event_id``,
  BLAKE2b + base32, ``fe-`` prefix (hyphen).  Incorporates the ZIP code so the
  same evidence card in two different ZIP feeds gets distinct stable identifiers.
  Only these appear in exported JSON and public URLs.

Constructors accept an optional ``id_builder`` so callers can inject public IDs
when building ZIP-feed payloads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field
from src.rules.models import Severity
from src.scoring.semantics import severity_score_delta


# ---------------------------------------------------------------------------
# ID builder protocol
# ---------------------------------------------------------------------------


class FeedIdBuilder(Protocol):
    """Injectable ID builder for feed events.

    Default: ``make_feed_event_id`` (internal SHA-256, ``fe_`` prefix).
    For public ZIP-feed IDs inject ``src.identity.public_ids.build_feed_event_id``
    (BLAKE2b + base32, ``fe-`` prefix).
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
    """A single ranked item for homepage/feed display."""

    feed_event_id: str
    kind: FeedEventKind
    member_bioguide_id: str
    member_name: str
    member_slug: str
    dimension: str
    score_delta: float = Field(
        description="Signed score movement; negative = penalty, positive = recovery."
    )
    abs_delta: float = Field(description="Always >= 0; used for ranking.")
    short_explanation: str
    evidence_card_id: str | None = None
    snapshot_date: dt.date
    occurred_at: dt.date = Field(
        description="Best available date for recency sorting; falls back to snapshot_date."
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
    """Internal feed-ranking ID (``fe_`` prefix, SHA-256, first 16 hex chars).

    Not for public URLs — see ``src.identity.public_ids.build_feed_event_id``.
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
    """Build FeedEvents from rule-fire dicts.

    Each dict needs: ``fire_id``, ``rule_id``, ``member_bioguide_id``,
    ``dimension``, ``severity`` (low|medium|high|critical), ``explanation``,
    ``snapshot_date``.  Optional: ``score_delta`` (overrides severity default).

    Severity defaults use the canonical signed score semantics.
    """
    _build_id = id_builder if id_builder is not None else make_feed_event_id
    events: list[FeedEvent] = []
    for rf in rule_fires:
        bioguide_id = rf["member_bioguide_id"]
        member = members.get(bioguide_id)
        if member is None:
            continue

        snap = rf["snapshot_date"]
        if isinstance(snap, str):
            snap = dt.date.fromisoformat(snap)

        if "score_delta" in rf and rf["score_delta"] is not None:
            delta = float(rf["score_delta"])
        else:
            severity = rf.get("severity")
            if not isinstance(severity, (Severity, str)):
                raise ValueError("severity is required when score_delta is not provided")
            delta = severity_score_delta(
                severity,
                overrides=score_delta_by_severity,
            )

        events.append(
            FeedEvent(
                feed_event_id=_build_id(
                    FeedEventKind.RULE_FIRE,
                    bioguide_id,
                    rf["dimension"],
                    snap,
                    rf["fire_id"],
                ),
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

    Each dict needs: ``public_id``, ``member_bioguide_id``, ``dimension``,
    ``score_delta`` (signed score movement), ``short_explanation``,
    ``snapshot_date``.
    Optional: ``rendered_at`` (used as ``occurred_at`` when present).
    """
    _build_id = id_builder if id_builder is not None else make_feed_event_id
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
        events.append(
            FeedEvent(
                feed_event_id=_build_id(
                    FeedEventKind.EVIDENCE_CARD,
                    bioguide_id,
                    card["dimension"],
                    snap,
                    card["public_id"],
                ),
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

    Each dict needs: ``member_bioguide_id``, ``dimension``, ``delta``
    (signed score movement), ``snapshot_date``.  Optional: ``explanation``.
    """
    _build_id = id_builder if id_builder is not None else make_feed_event_id
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
        events.append(
            FeedEvent(
                feed_event_id=_build_id(
                    FeedEventKind.SCORE_DELTA,
                    bioguide_id,
                    sd["dimension"],
                    snap,
                ),
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
    """Sort by abs_delta descending; break ties by feed_event_id ascending."""
    return sorted(events, key=lambda e: (-e.abs_delta, e.feed_event_id))


def select_recent(
    events: list[FeedEvent],
    *,
    since: dt.date,
    limit: int | None = None,
) -> list[FeedEvent]:
    """Return events with occurred_at >= since, most recent first."""
    filtered = [e for e in events if e.occurred_at >= since]
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
    """Return the top-N events by magnitude, optionally filtered by dimension."""
    if n <= 0:
        return []
    pool = events if dimension is None else [e for e in events if e.dimension == dimension]
    return sort_by_magnitude(pool)[:n]
