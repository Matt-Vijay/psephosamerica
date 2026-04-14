"""Tests for src/homepage/builders.py.

Pure unit tests — no I/O, no database, no network calls.
All FeedEvent objects are constructed directly using the src/feed/changes
helpers so the test inputs are representative of real pipeline outputs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.feed.changes import FeedEvent, FeedEventKind, make_feed_event_id
from src.homepage.builders import (
    build_homepage_feed,
    build_recent_events,
    build_top_changes,
    extract_recent_evidence_card_ids,
)
from src.homepage.contracts import (
    HomepageFeedPayload,
    RecentEventSummary,
)

# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

_SNAP = dt.date(2024, 11, 1)
_EARLIER = dt.date(2024, 10, 15)
_LATER = dt.date(2024, 11, 15)

_MEMBER_META: dict[str, dict] = {
    "A000001": {"chamber": "house", "party": "D", "state": "CA"},
    "B000002": {"chamber": "senate", "party": "R", "state": "TX"},
    "C000003": {"chamber": "house", "party": "R", "state": "FL"},
}


def _make_event(
    bioguide_id: str,
    *,
    delta: float,
    dimension: str = "conflict_of_interest_risk",
    occurred_at: dt.date = _SNAP,
    snapshot_date: dt.date = _SNAP,
    card_id: str | None = None,
    discriminator: str = "",
    name: str | None = None,
    slug: str | None = None,
) -> FeedEvent:
    name = name or f"Member {bioguide_id}"
    slug = slug or bioguide_id.lower()
    feed_event_id = make_feed_event_id(
        FeedEventKind.EVIDENCE_CARD if card_id else FeedEventKind.RULE_FIRE,
        bioguide_id,
        dimension,
        snapshot_date,
        card_id or discriminator or "x",
    )
    return FeedEvent(
        feed_event_id=feed_event_id,
        kind=FeedEventKind.EVIDENCE_CARD if card_id else FeedEventKind.RULE_FIRE,
        member_bioguide_id=bioguide_id,
        member_name=name,
        member_slug=slug,
        dimension=dimension,
        score_delta=delta,
        abs_delta=abs(delta),
        short_explanation=f"Explanation for {bioguide_id}",
        evidence_card_id=card_id,
        snapshot_date=snapshot_date,
        occurred_at=occurred_at,
    )


# ---------------------------------------------------------------------------
# build_top_changes
# ---------------------------------------------------------------------------


class TestBuildTopChanges:
    def test_returns_empty_for_no_events(self):
        result = build_top_changes([], _MEMBER_META, n=10)
        assert result == []

    def test_returns_empty_for_n_zero(self):
        events = [_make_event("A000001", delta=-30.0)]
        result = build_top_changes(events, _MEMBER_META, n=0)
        assert result == []

    def test_single_event_produces_one_summary(self):
        events = [_make_event("A000001", delta=-30.0, card_id="card-1")]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert len(result) == 1
        s = result[0]
        assert s.bioguide_id == "A000001"
        assert s.score_delta == -30.0
        assert s.abs_delta == 30.0
        assert s.event_count == 1
        assert s.top_evidence_card_ids == ["card-1"]

    def test_aggregates_multiple_events_for_same_member_dimension(self):
        events = [
            _make_event("A000001", delta=-15.0, card_id="card-1", discriminator="1"),
            _make_event("A000001", delta=-10.0, card_id="card-2", discriminator="2"),
        ]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert len(result) == 1
        s = result[0]
        assert s.score_delta == pytest.approx(-25.0)
        assert s.abs_delta == pytest.approx(25.0)
        assert s.event_count == 2

    def test_top_card_ids_capped_at_three(self):
        events = [
            _make_event("A000001", delta=-30.0, card_id=f"card-{i}", discriminator=str(i))
            for i in range(5)
        ]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert len(result[0].top_evidence_card_ids) == 3

    def test_top_card_ids_ordered_by_abs_delta(self):
        # Three events with different magnitudes; top-3 should be largest first
        events = [
            _make_event("A000001", delta=-5.0, card_id="small", discriminator="1"),
            _make_event("A000001", delta=-30.0, card_id="large", discriminator="2"),
            _make_event("A000001", delta=-15.0, card_id="medium", discriminator="3"),
            _make_event("A000001", delta=-25.0, card_id="almost", discriminator="4"),
        ]
        result = build_top_changes(events, _MEMBER_META, n=10)
        ids = result[0].top_evidence_card_ids
        assert ids[0] == "large"
        assert ids[1] == "almost"
        assert ids[2] == "medium"

    def test_sorted_by_abs_delta_descending(self):
        events = [
            _make_event("A000001", delta=-5.0),
            _make_event("B000002", delta=-30.0),
            _make_event("C000003", delta=-15.0),
        ]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert [s.bioguide_id for s in result] == ["B000002", "C000003", "A000001"]

    def test_tie_broken_by_bioguide_id_ascending(self):
        # Same abs_delta for A and C
        events = [
            _make_event("C000003", delta=-20.0, discriminator="c"),
            _make_event("A000001", delta=-20.0, discriminator="a"),
        ]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert result[0].bioguide_id == "A000001"
        assert result[1].bioguide_id == "C000003"

    def test_n_caps_results(self):
        events = [
            _make_event("A000001", delta=-30.0, discriminator="a"),
            _make_event("B000002", delta=-20.0, discriminator="b"),
            _make_event("C000003", delta=-10.0, discriminator="c"),
        ]
        result = build_top_changes(events, _MEMBER_META, n=2)
        assert len(result) == 2

    def test_dimension_filter(self):
        events = [
            _make_event("A000001", delta=-30.0, dimension="conflict_of_interest_risk"),
            _make_event("B000002", delta=-20.0, dimension="other_dimension"),
        ]
        result = build_top_changes(
            events, _MEMBER_META, n=10, dimension="conflict_of_interest_risk"
        )
        assert len(result) == 1
        assert result[0].bioguide_id == "A000001"

    def test_member_meta_fields_populated(self):
        events = [_make_event("A000001", delta=-10.0)]
        result = build_top_changes(events, _MEMBER_META, n=10)
        s = result[0]
        assert s.chamber == "house"
        assert s.party == "D"
        assert s.state == "CA"

    def test_missing_member_meta_uses_empty_strings(self):
        events = [_make_event("Z999999", delta=-10.0)]
        result = build_top_changes(events, _MEMBER_META, n=10)
        assert len(result) == 1
        s = result[0]
        assert s.chamber == ""
        assert s.party == ""
        assert s.state == ""

    def test_deterministic_across_calls(self):
        import random

        events = [
            _make_event("A000001", delta=-30.0, discriminator="a"),
            _make_event("B000002", delta=-20.0, discriminator="b"),
            _make_event("C000003", delta=-10.0, discriminator="c"),
        ]
        results = []
        for _ in range(5):
            shuffled = random.sample(events, len(events))
            results.append([s.bioguide_id for s in build_top_changes(shuffled, _MEMBER_META)])
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# build_recent_events
# ---------------------------------------------------------------------------


class TestBuildRecentEvents:
    def test_returns_empty_for_no_events(self):
        assert build_recent_events([], n=10) == []

    def test_returns_empty_for_n_zero(self):
        events = [_make_event("A000001", delta=-10.0)]
        assert build_recent_events(events, n=0) == []

    def test_single_event(self):
        ev = _make_event("A000001", delta=-10.0, card_id="card-1")
        result = build_recent_events([ev], n=10)
        assert len(result) == 1
        r = result[0]
        assert r.feed_event_id == ev.feed_event_id
        assert r.evidence_card_id == "card-1"
        assert r.score_delta == -10.0

    def test_sorted_by_occurred_at_descending(self):
        events = [
            _make_event("A000001", delta=-10.0, occurred_at=_EARLIER, discriminator="a"),
            _make_event("B000002", delta=-20.0, occurred_at=_LATER, discriminator="b"),
            _make_event("C000003", delta=-5.0, occurred_at=_SNAP, discriminator="c"),
        ]
        result = build_recent_events(events, n=10)
        dates = [r.occurred_at for r in result]
        assert dates == [_LATER, _SNAP, _EARLIER]

    def test_tie_on_date_broken_by_feed_event_id_ascending(self):
        # Two events on the same date — order must be stable
        e1 = _make_event("A000001", delta=-10.0, occurred_at=_SNAP, discriminator="zzz")
        e2 = _make_event("B000002", delta=-20.0, occurred_at=_SNAP, discriminator="aaa")
        result = build_recent_events([e1, e2], n=10)
        # Both share occurred_at; secondary sort by feed_event_id asc
        assert result[0].feed_event_id <= result[1].feed_event_id

    def test_since_filter_excludes_older_events(self):
        events = [
            _make_event("A000001", delta=-10.0, occurred_at=_EARLIER, discriminator="a"),
            _make_event("B000002", delta=-20.0, occurred_at=_SNAP, discriminator="b"),
        ]
        result = build_recent_events(events, since=_SNAP, n=10)
        assert len(result) == 1
        assert result[0].member_bioguide_id == "B000002"

    def test_since_filter_inclusive(self):
        events = [_make_event("A000001", delta=-10.0, occurred_at=_SNAP, discriminator="a")]
        result = build_recent_events(events, since=_SNAP, n=10)
        assert len(result) == 1

    def test_n_caps_results(self):
        events = [
            _make_event("A000001", delta=-10.0, occurred_at=_LATER, discriminator="1"),
            _make_event("B000002", delta=-20.0, occurred_at=_SNAP, discriminator="2"),
            _make_event("C000003", delta=-5.0, occurred_at=_EARLIER, discriminator="3"),
        ]
        result = build_recent_events(events, n=2)
        assert len(result) == 2

    def test_none_card_id_preserved(self):
        ev = _make_event("A000001", delta=-10.0, card_id=None, discriminator="no-card")
        result = build_recent_events([ev], n=10)
        assert result[0].evidence_card_id is None

    def test_deterministic_across_calls(self):
        import random

        events = [
            _make_event("A000001", delta=-10.0, occurred_at=_LATER, discriminator="1"),
            _make_event("B000002", delta=-20.0, occurred_at=_SNAP, discriminator="2"),
            _make_event("C000003", delta=-5.0, occurred_at=_EARLIER, discriminator="3"),
        ]
        results = []
        for _ in range(5):
            shuffled = random.sample(events, len(events))
            results.append([r.feed_event_id for r in build_recent_events(shuffled, n=10)])
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# extract_recent_evidence_card_ids
# ---------------------------------------------------------------------------


class TestExtractRecentEvidenceCardIds:
    def _make_summary(self, card_id: str | None) -> RecentEventSummary:
        return RecentEventSummary(
            feed_event_id="fe_test",
            member_bioguide_id="A000001",
            member_name="Test Member",
            member_slug="a000001",
            dimension="conflict_of_interest_risk",
            score_delta=-10.0,
            short_explanation="Test",
            evidence_card_id=card_id,
            occurred_at=_SNAP,
        )

    def test_empty_input(self):
        assert extract_recent_evidence_card_ids([]) == []

    def test_skips_none_card_ids(self):
        summaries = [self._make_summary(None), self._make_summary(None)]
        assert extract_recent_evidence_card_ids(summaries) == []

    def test_deduplicates(self):
        summaries = [
            self._make_summary("card-1"),
            self._make_summary("card-2"),
            self._make_summary("card-1"),
        ]
        result = extract_recent_evidence_card_ids(summaries)
        assert result == ["card-1", "card-2"]

    def test_preserves_first_occurrence_order(self):
        summaries = [
            self._make_summary("card-b"),
            self._make_summary("card-a"),
            self._make_summary("card-c"),
        ]
        result = extract_recent_evidence_card_ids(summaries)
        assert result == ["card-b", "card-a", "card-c"]


# ---------------------------------------------------------------------------
# build_homepage_feed (integration)
# ---------------------------------------------------------------------------


class TestBuildHomepageFeed:
    def _events(self) -> list[FeedEvent]:
        return [
            _make_event(
                "A000001",
                delta=-30.0,
                card_id="card-a1",
                occurred_at=_LATER,
                discriminator="1",
            ),
            _make_event(
                "B000002",
                delta=-15.0,
                card_id="card-b1",
                occurred_at=_SNAP,
                discriminator="2",
            ),
            _make_event(
                "C000003",
                delta=-5.0,
                occurred_at=_EARLIER,
                discriminator="3",
            ),
        ]

    def test_returns_homepage_feed_payload(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP
        )
        assert isinstance(result, HomepageFeedPayload)

    def test_snapshot_date_set(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP
        )
        assert result.snapshot_date == _SNAP

    def test_top_changes_ordered_by_magnitude(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP
        )
        # A: abs 30, B: abs 15, C: abs 5
        assert [s.bioguide_id for s in result.top_changes] == [
            "A000001",
            "B000002",
            "C000003",
        ]

    def test_recent_events_ordered_by_occurred_at(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP
        )
        dates = [r.occurred_at for r in result.recent_events]
        assert dates == sorted(dates, reverse=True)

    def test_recent_evidence_card_ids_deduped_and_ordered(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP
        )
        # card-a1 (LATER), card-b1 (SNAP), C has no card
        assert result.recent_evidence_card_ids == ["card-a1", "card-b1"]

    def test_top_n_respected(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP, top_n=2
        )
        assert len(result.top_changes) == 2

    def test_recent_n_respected(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP, recent_n=2
        )
        assert len(result.recent_events) == 2

    def test_since_filter_applied_to_recent_events(self):
        result = build_homepage_feed(
            self._events(), _MEMBER_META, snapshot_date=_SNAP, since=_SNAP
        )
        assert all(r.occurred_at >= _SNAP for r in result.recent_events)

    def test_dimension_filter_applied(self):
        mixed = self._events() + [
            _make_event(
                "A000001",
                delta=-50.0,
                dimension="other_dimension",
                discriminator="other",
            )
        ]
        result = build_homepage_feed(
            mixed,
            _MEMBER_META,
            snapshot_date=_SNAP,
            dimension="conflict_of_interest_risk",
        )
        for s in result.top_changes:
            assert s.dimension == "conflict_of_interest_risk"
        for r in result.recent_events:
            assert r.dimension == "conflict_of_interest_risk"

    def test_empty_events(self):
        result = build_homepage_feed([], _MEMBER_META, snapshot_date=_SNAP)
        assert result.top_changes == []
        assert result.recent_events == []
        assert result.recent_evidence_card_ids == []

    def test_payload_is_deterministic(self):
        import random

        events = self._events()
        results: list[HomepageFeedPayload] = []
        for _ in range(5):
            shuffled = random.sample(events, len(events))
            results.append(
                build_homepage_feed(shuffled, _MEMBER_META, snapshot_date=_SNAP)
            )

        first = results[0]
        for other in results[1:]:
            assert [s.bioguide_id for s in other.top_changes] == [
                s.bioguide_id for s in first.top_changes
            ]
            assert [r.feed_event_id for r in other.recent_events] == [
                r.feed_event_id for r in first.recent_events
            ]
            assert other.recent_evidence_card_ids == first.recent_evidence_card_ids
