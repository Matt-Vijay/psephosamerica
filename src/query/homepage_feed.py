"""Homepage payload assembly from published_rows.fetch_homepage_feed_rows rows.

Pure helpers — no SQL, no I/O, no network calls.

Turns the flat evidence-card + member rows returned by
``fetch_homepage_feed_rows`` into ``FeedEvent`` objects and a
``HomepageFeedPayload``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.feed.changes import FeedEvent, FeedEventKind, FeedIdBuilder, make_feed_event_id
from src.homepage.builders import build_homepage_feed
from src.homepage.contracts import HomepageFeedPayload


class _RankedDate(dt.date):
    """Date-shaped value with an overridden rank for same-day recency sorts."""

    _rank: float

    def __new__(
        cls,
        year: int,
        month: int,
        day: int,
        *,
        rank: float | None = None,
    ) -> _RankedDate:
        obj = super().__new__(cls, year, month, day)
        obj._rank = float(rank if rank is not None else dt.date.toordinal(obj))
        return obj

    @classmethod
    def from_datetime(cls, value: dt.datetime) -> _RankedDate:
        rank = (
            value.toordinal() * 86400
            + value.hour * 3600
            + value.minute * 60
            + value.second
            + (value.microsecond / 1_000_000)
        )
        return cls(value.year, value.month, value.day, rank=rank)


def _to_occurred_at(value: dt.date | dt.datetime | str) -> dt.date:
    if isinstance(value, dt.datetime):
        return _RankedDate.from_datetime(value)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            return dt.date.fromisoformat(value[:10])
        return _RankedDate.from_datetime(parsed)
    return value


def rows_to_feed_events(
    rows: list[dict[str, Any]],
    *,
    snapshot_date: dt.date,
    id_builder: FeedIdBuilder | None = None,
) -> list[FeedEvent]:
    """Convert ``fetch_homepage_feed_rows`` rows to ``FeedEvent`` objects.

    ``snapshot_date`` is set on every event.  ``occurred_at`` is taken from
    ``rendered_at`` when present, otherwise falls back to ``snapshot_date``.

    Uses the real ``member_bioguide_id`` carried on homepage rows.
    """
    _build_id = id_builder if id_builder is not None else make_feed_event_id
    events: list[FeedEvent] = []

    for row in rows:
        slug = row["member_slug"]
        bioguide_id = row["member_bioguide_id"]
        dimension = row["dimension"]
        score_delta = float(row["score_delta"])

        occurred_at: dt.date = snapshot_date
        if row.get("rendered_at") is not None:
            occurred_at = _to_occurred_at(row["rendered_at"])

        events.append(
            FeedEvent(
                feed_event_id=_build_id(
                    FeedEventKind.EVIDENCE_CARD,
                    bioguide_id,
                    dimension,
                    snapshot_date,
                    row["public_id"],
                ),
                kind=FeedEventKind.EVIDENCE_CARD,
                member_bioguide_id=bioguide_id,
                member_name=row.get("member_full_name", ""),
                member_slug=slug,
                dimension=dimension,
                score_delta=score_delta,
                abs_delta=abs(score_delta),
                short_explanation=row.get("short_explanation", ""),
                evidence_card_id=row["public_id"],
                snapshot_date=snapshot_date,
                occurred_at=occurred_at,
            )
        )

    return events


def member_meta_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract a member_meta dict keyed by ``member_bioguide_id``.

    Only the first row per member contributes.
    """
    meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        bioguide_id = row["member_bioguide_id"]
        if bioguide_id not in meta:
            meta[bioguide_id] = {
                "chamber": row.get("chamber", ""),
                "party": row.get("party", ""),
                "state": row.get("state", ""),
            }
    return meta


def assemble_homepage_payload(
    rows: list[dict[str, Any]],
    *,
    snapshot_date: dt.date,
    top_n: int = 10,
    recent_n: int = 20,
    since: dt.date | None = None,
    dimension: str | None = None,
    id_builder: FeedIdBuilder | None = None,
) -> HomepageFeedPayload:
    """Build a ``HomepageFeedPayload`` from raw homepage-feed rows.

    Top-level entry point: fetch rows with
    ``published_rows.fetch_homepage_feed_rows``, then pass them here.
    """
    events = rows_to_feed_events(rows, snapshot_date=snapshot_date, id_builder=id_builder)
    meta = member_meta_from_rows(rows)
    return build_homepage_feed(
        events,
        meta,
        snapshot_date=snapshot_date,
        top_n=top_n,
        recent_n=recent_n,
        since=since,
        dimension=dimension,
    )
