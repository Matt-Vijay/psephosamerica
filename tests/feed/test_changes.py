"""Tests for src/feed/changes.py — pure, no I/O, no network calls."""

from __future__ import annotations

import datetime as dt

from src.feed.changes import (
    FeedEvent,
    FeedEventKind,
    events_from_evidence_cards,
    events_from_rule_fires,
    events_from_score_deltas,
    make_feed_event_id,
    select_recent,
    select_top_changes,
    sort_by_magnitude,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SNAP = dt.date(2025, 3, 1)

MEMBERS = {
    "A000001": {"full_name": "Alice Smith", "slug": "alice-smith", "bioguide_id": "A000001"},
    "B000002": {"full_name": "Bob Jones", "slug": "bob-jones", "bioguide_id": "B000002"},
    "C000003": {"full_name": "Carol Lee", "slug": "carol-lee", "bioguide_id": "C000003"},
}


def _rf(
    fire_id: str,
    bioguide_id: str,
    *,
    severity: str = "medium",
    dimension: str = "conflict_of_interest_risk",
    explanation: str = "Test rule fire",
    snapshot_date: dt.date = SNAP,
    score_delta: float | None = None,
) -> dict:
    d = {
        "fire_id": fire_id,
        "rule_id": f"coi.{fire_id}.v1",
        "member_bioguide_id": bioguide_id,
        "dimension": dimension,
        "severity": severity,
        "explanation": explanation,
        "snapshot_date": snapshot_date,
    }
    if score_delta is not None:
        d["score_delta"] = score_delta
    return d


def _card(
    public_id: str,
    bioguide_id: str,
    *,
    score_delta: float = -20.0,
    dimension: str = "conflict_of_interest_risk",
    explanation: str = "Test card",
    snapshot_date: dt.date = SNAP,
    rendered_at: dt.date | None = None,
) -> dict:
    d = {
        "public_id": public_id,
        "member_bioguide_id": bioguide_id,
        "dimension": dimension,
        "score_delta": score_delta,
        "short_explanation": explanation,
        "snapshot_date": snapshot_date,
    }
    if rendered_at is not None:
        d["rendered_at"] = rendered_at
    return d


def _sd(
    bioguide_id: str,
    *,
    delta: float,
    dimension: str = "conflict_of_interest_risk",
    snapshot_date: dt.date = SNAP,
    explanation: str = "",
) -> dict:
    return {
        "member_bioguide_id": bioguide_id,
        "dimension": dimension,
        "delta": delta,
        "snapshot_date": snapshot_date,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# make_feed_event_id
# ---------------------------------------------------------------------------


class TestMakeFeedEventId:
    def test_returns_fe_prefix(self):
        fid = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP)
        assert fid.startswith("fe_")

    def test_deterministic(self):
        a = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP, "fire1")
        b = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP, "fire1")
        assert a == b

    def test_different_discriminators_differ(self):
        a = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP, "fire1")
        b = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP, "fire2")
        assert a != b

    def test_different_members_differ(self):
        a = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP)
        b = make_feed_event_id(FeedEventKind.RULE_FIRE, "B000002", "coi", SNAP)
        assert a != b

    def test_different_kinds_differ(self):
        a = make_feed_event_id(FeedEventKind.RULE_FIRE, "A000001", "coi", SNAP)
        b = make_feed_event_id(FeedEventKind.EVIDENCE_CARD, "A000001", "coi", SNAP)
        assert a != b

    def test_length(self):
        fid = make_feed_event_id(FeedEventKind.SCORE_DELTA, "A000001", "coi", SNAP)
        # "fe_" + 16 hex chars
        assert len(fid) == 19


# ---------------------------------------------------------------------------
# events_from_rule_fires
# ---------------------------------------------------------------------------


class TestEventsFromRuleFires:
    def test_basic(self):
        fires = [_rf("f1", "A000001")]
        events = events_from_rule_fires(fires, MEMBERS)
        assert len(events) == 1
        e = events[0]
        assert e.kind == FeedEventKind.RULE_FIRE
        assert e.member_bioguide_id == "A000001"
        assert e.member_name == "Alice Smith"
        assert e.member_slug == "alice-smith"

    def test_severity_defaults_applied(self):
        low = _rf("f_low", "A000001", severity="low")
        med = _rf("f_med", "A000001", severity="medium")
        high = _rf("f_high", "A000001", severity="high")
        critical = _rf("f_critical", "A000001", severity="critical")
        events = events_from_rule_fires([low, med, high, critical], MEMBERS)
        all_deltas = [e.score_delta for e in events]
        assert -1.0 in all_deltas
        assert -2.0 in all_deltas
        assert -4.0 in all_deltas
        assert -8.0 in all_deltas

    def test_explicit_score_delta_overrides_severity(self):
        fires = [_rf("f1", "A000001", severity="low", score_delta=-99.0)]
        events = events_from_rule_fires(fires, MEMBERS)
        assert events[0].score_delta == -99.0

    def test_explicit_zero_score_delta_is_preserved(self):
        fires = [_rf("f1", "A000001", severity="critical", score_delta=0.0)]
        events = events_from_rule_fires(fires, MEMBERS)
        assert events[0].score_delta == 0.0
        assert events[0].abs_delta == 0.0

    def test_abs_delta_always_nonnegative(self):
        fires = [_rf("f1", "A000001", score_delta=-25.0)]
        events = events_from_rule_fires(fires, MEMBERS)
        assert events[0].abs_delta == 25.0

    def test_unknown_member_skipped(self):
        fires = [_rf("f1", "Z999999")]
        events = events_from_rule_fires(fires, MEMBERS)
        assert events == []

    def test_iso_string_snapshot_date_parsed(self):
        fires = [_rf("f1", "A000001", snapshot_date="2025-03-01")]
        events = events_from_rule_fires(fires, MEMBERS)
        assert events[0].snapshot_date == dt.date(2025, 3, 1)

    def test_custom_severity_map(self):
        fires = [_rf("f1", "A000001", severity="high")]
        events = events_from_rule_fires(fires, MEMBERS, score_delta_by_severity={"high": -99.0})
        assert events[0].score_delta == -99.0

    def test_missing_severity_raises_when_score_delta_missing(self):
        fires = [_rf("f1", "A000001")]
        del fires[0]["severity"]
        try:
            events_from_rule_fires(fires, MEMBERS)
            raise AssertionError("expected ValueError for missing severity")
        except ValueError as exc:
            assert "severity is required" in str(exc)

    def test_feed_event_id_stable(self):
        fires = [_rf("f1", "A000001")]
        e1 = events_from_rule_fires(fires, MEMBERS)[0]
        e2 = events_from_rule_fires(fires, MEMBERS)[0]
        assert e1.feed_event_id == e2.feed_event_id

    def test_multiple_fires_unique_ids(self):
        fires = [_rf("f1", "A000001"), _rf("f2", "A000001")]
        events = events_from_rule_fires(fires, MEMBERS)
        ids = [e.feed_event_id for e in events]
        assert len(set(ids)) == 2


# ---------------------------------------------------------------------------
# events_from_evidence_cards
# ---------------------------------------------------------------------------


class TestEventsFromEvidenceCards:
    def test_basic(self):
        cards = [_card("card-1", "B000002", score_delta=-18.0)]
        events = events_from_evidence_cards(cards, MEMBERS)
        assert len(events) == 1
        e = events[0]
        assert e.kind == FeedEventKind.EVIDENCE_CARD
        assert e.evidence_card_id == "card-1"
        assert e.score_delta == -18.0
        assert e.abs_delta == 18.0

    def test_positive_delta_allowed_for_recovery_cards(self):
        cards = [_card("card-1", "B000002", score_delta=18.0)]
        events = events_from_evidence_cards(cards, MEMBERS)
        assert len(events) == 1
        assert events[0].score_delta == 18.0
        assert events[0].abs_delta == 18.0

    def test_occurred_at_falls_back_to_snapshot(self):
        cards = [_card("card-1", "A000001")]
        events = events_from_evidence_cards(cards, MEMBERS)
        assert events[0].occurred_at == SNAP

    def test_rendered_at_used_when_present(self):
        rendered = dt.date(2025, 3, 10)
        cards = [_card("card-1", "A000001", rendered_at=rendered)]
        events = events_from_evidence_cards(cards, MEMBERS)
        assert events[0].occurred_at == rendered

    def test_rendered_at_as_string(self):
        cards = [_card("card-1", "A000001", rendered_at="2025-03-10")]
        events = events_from_evidence_cards(cards, MEMBERS)
        assert events[0].occurred_at == dt.date(2025, 3, 10)

    def test_unknown_member_skipped(self):
        cards = [_card("card-x", "Z999999")]
        assert events_from_evidence_cards(cards, MEMBERS) == []

    def test_feed_event_id_stable(self):
        cards = [_card("card-1", "A000001")]
        e1 = events_from_evidence_cards(cards, MEMBERS)[0]
        e2 = events_from_evidence_cards(cards, MEMBERS)[0]
        assert e1.feed_event_id == e2.feed_event_id


# ---------------------------------------------------------------------------
# events_from_score_deltas
# ---------------------------------------------------------------------------


class TestEventsFromScoreDeltas:
    def test_basic(self):
        deltas = [_sd("C000003", delta=-12.5)]
        events = events_from_score_deltas(deltas, MEMBERS)
        assert len(events) == 1
        e = events[0]
        assert e.kind == FeedEventKind.SCORE_DELTA
        assert e.score_delta == -12.5
        assert e.abs_delta == 12.5

    def test_positive_delta_allowed(self):
        deltas = [_sd("A000001", delta=5.0)]
        events = events_from_score_deltas(deltas, MEMBERS)
        assert events[0].score_delta == 5.0
        assert events[0].abs_delta == 5.0

    def test_unknown_member_skipped(self):
        deltas = [_sd("Z999999", delta=-1.0)]
        assert events_from_score_deltas(deltas, MEMBERS) == []

    def test_iso_string_snapshot(self):
        deltas = [_sd("A000001", delta=-5.0, snapshot_date="2025-01-15")]
        events = events_from_score_deltas(deltas, MEMBERS)
        assert events[0].snapshot_date == dt.date(2025, 1, 15)

    def test_deterministic_id_no_discriminators(self):
        deltas = [_sd("A000001", delta=-5.0)]
        e1 = events_from_score_deltas(deltas, MEMBERS)[0]
        e2 = events_from_score_deltas(deltas, MEMBERS)[0]
        assert e1.feed_event_id == e2.feed_event_id


# ---------------------------------------------------------------------------
# sort_by_magnitude
# ---------------------------------------------------------------------------


class TestSortByMagnitude:
    def _event(self, feed_event_id: str, abs_delta: float) -> FeedEvent:
        return FeedEvent(
            feed_event_id=feed_event_id,
            kind=FeedEventKind.SCORE_DELTA,
            member_bioguide_id="A000001",
            member_name="Alice",
            member_slug="alice",
            dimension="coi",
            score_delta=-abs_delta,
            abs_delta=abs_delta,
            short_explanation="",
            snapshot_date=SNAP,
            occurred_at=SNAP,
        )

    def test_descending_magnitude(self):
        events = [self._event("e1", 5.0), self._event("e2", 30.0), self._event("e3", 15.0)]
        sorted_events = sort_by_magnitude(events)
        assert [e.abs_delta for e in sorted_events] == [30.0, 15.0, 5.0]

    def test_stable_tie_break_by_id(self):
        events = [self._event("e_z", 10.0), self._event("e_a", 10.0)]
        sorted_events = sort_by_magnitude(events)
        assert sorted_events[0].feed_event_id == "e_a"
        assert sorted_events[1].feed_event_id == "e_z"

    def test_empty(self):
        assert sort_by_magnitude([]) == []

    def test_single(self):
        events = [self._event("e1", 7.0)]
        assert sort_by_magnitude(events) == events


# ---------------------------------------------------------------------------
# select_recent
# ---------------------------------------------------------------------------


class TestSelectRecent:
    def _event(self, fid: str, occurred_at: dt.date, abs_delta: float = 10.0) -> FeedEvent:
        return FeedEvent(
            feed_event_id=fid,
            kind=FeedEventKind.SCORE_DELTA,
            member_bioguide_id="A000001",
            member_name="Alice",
            member_slug="alice",
            dimension="coi",
            score_delta=-abs_delta,
            abs_delta=abs_delta,
            short_explanation="",
            snapshot_date=occurred_at,
            occurred_at=occurred_at,
        )

    def test_filters_older_events(self):
        old = self._event("old", dt.date(2025, 1, 1))
        new = self._event("new", dt.date(2025, 3, 1))
        result = select_recent([old, new], since=dt.date(2025, 2, 1))
        assert len(result) == 1
        assert result[0].feed_event_id == "new"

    def test_inclusive_boundary(self):
        boundary = self._event("boundary", dt.date(2025, 2, 1))
        result = select_recent([boundary], since=dt.date(2025, 2, 1))
        assert len(result) == 1

    def test_most_recent_first(self):
        e1 = self._event("e1", dt.date(2025, 1, 5))
        e2 = self._event("e2", dt.date(2025, 1, 10))
        e3 = self._event("e3", dt.date(2025, 1, 8))
        result = select_recent([e1, e2, e3], since=dt.date(2024, 1, 1))
        assert result[0].occurred_at == dt.date(2025, 1, 10)
        assert result[1].occurred_at == dt.date(2025, 1, 8)

    def test_limit_applied(self):
        events = [self._event(f"e{i}", dt.date(2025, 1, i + 1)) for i in range(5)]
        result = select_recent(events, since=dt.date(2024, 1, 1), limit=3)
        assert len(result) == 3

    def test_empty_input(self):
        assert select_recent([], since=SNAP) == []

    def test_all_filtered_returns_empty(self):
        old = self._event("old", dt.date(2024, 6, 1))
        result = select_recent([old], since=dt.date(2025, 1, 1))
        assert result == []


# ---------------------------------------------------------------------------
# select_top_changes
# ---------------------------------------------------------------------------


class TestSelectTopChanges:
    def _event(
        self, fid: str, abs_delta: float, dimension: str = "conflict_of_interest_risk"
    ) -> FeedEvent:
        return FeedEvent(
            feed_event_id=fid,
            kind=FeedEventKind.SCORE_DELTA,
            member_bioguide_id="A000001",
            member_name="Alice",
            member_slug="alice",
            dimension=dimension,
            score_delta=-abs_delta,
            abs_delta=abs_delta,
            short_explanation="",
            snapshot_date=SNAP,
            occurred_at=SNAP,
        )

    def test_returns_n_largest(self):
        events = [self._event(f"e{i}", float(i)) for i in range(10)]
        result = select_top_changes(events, n=3)
        assert len(result) == 3
        assert result[0].abs_delta == 9.0

    def test_dimension_filter(self):
        coi = self._event("coi", 100.0, "conflict_of_interest_risk")
        other = self._event("other", 50.0, "other_dimension")
        result = select_top_changes([coi, other], n=5, dimension="conflict_of_interest_risk")
        assert len(result) == 1
        assert result[0].feed_event_id == "coi"

    def test_n_zero_returns_empty(self):
        events = [self._event("e1", 10.0)]
        assert select_top_changes(events, n=0) == []

    def test_n_larger_than_pool(self):
        events = [self._event("e1", 5.0), self._event("e2", 10.0)]
        result = select_top_changes(events, n=100)
        assert len(result) == 2

    def test_empty_input(self):
        assert select_top_changes([], n=5) == []

    def test_ordered_by_magnitude_desc(self):
        events = [self._event("e1", 3.0), self._event("e2", 1.0), self._event("e3", 2.0)]
        result = select_top_changes(events, n=3)
        assert [e.abs_delta for e in result] == [3.0, 2.0, 1.0]


# ---------------------------------------------------------------------------
# Injectable id_builder
# ---------------------------------------------------------------------------


def _constant_id_builder(prefix: str):
    """Return a FeedIdBuilder that always emits ``<prefix>-<kind>-<bioguide>``."""

    def _builder(
        kind: FeedEventKind | str,
        member_bioguide_id: str,
        dimension: str,
        snapshot_date: dt.date,
        *discriminators: str,
    ) -> str:
        return f"{prefix}-{kind}-{member_bioguide_id}"

    return _builder


def _recording_id_builder():
    """Return a (builder, calls_list) pair.  Appends each call's args to the list."""
    calls: list[tuple] = []

    def _builder(
        kind: FeedEventKind | str,
        member_bioguide_id: str,
        dimension: str,
        snapshot_date: dt.date,
        *discriminators: str,
    ) -> str:
        calls.append((kind, member_bioguide_id, dimension, snapshot_date) + discriminators)
        return f"recorded-{member_bioguide_id}-{len(calls)}"

    return _builder, calls


class TestInjectableIdBuilder:
    """Verify that id_builder is called and its return value is used."""

    def test_rule_fires_uses_custom_builder(self):
        builder = _constant_id_builder("custom")
        fires = [_rf("f1", "A000001")]
        events = events_from_rule_fires(fires, MEMBERS, id_builder=builder)
        assert events[0].feed_event_id == f"custom-{FeedEventKind.RULE_FIRE}-A000001"

    def test_rule_fires_builder_receives_correct_args(self):
        builder, calls = _recording_id_builder()
        fires = [_rf("f99", "B000002", dimension="coi", snapshot_date=SNAP)]
        events_from_rule_fires(fires, MEMBERS, id_builder=builder)
        assert len(calls) == 1
        kind, bioguide, dimension, snap, *discriminators = calls[0]
        assert kind == FeedEventKind.RULE_FIRE
        assert bioguide == "B000002"
        assert dimension == "coi"
        assert snap == SNAP
        assert "f99" in discriminators

    def test_evidence_cards_uses_custom_builder(self):
        builder = _constant_id_builder("pub")
        cards = [_card("card-42", "A000001")]
        events = events_from_evidence_cards(cards, MEMBERS, id_builder=builder)
        assert events[0].feed_event_id == f"pub-{FeedEventKind.EVIDENCE_CARD}-A000001"

    def test_evidence_cards_builder_receives_correct_args(self):
        builder, calls = _recording_id_builder()
        cards = [_card("card-99", "C000003", snapshot_date=SNAP)]
        events_from_evidence_cards(cards, MEMBERS, id_builder=builder)
        assert len(calls) == 1
        kind, bioguide, dimension, snap, *discriminators = calls[0]
        assert kind == FeedEventKind.EVIDENCE_CARD
        assert bioguide == "C000003"
        assert snap == SNAP
        assert "card-99" in discriminators

    def test_score_deltas_uses_custom_builder(self):
        builder = _constant_id_builder("sd")
        deltas = [_sd("A000001", delta=-5.0)]
        events = events_from_score_deltas(deltas, MEMBERS, id_builder=builder)
        assert events[0].feed_event_id == f"sd-{FeedEventKind.SCORE_DELTA}-A000001"

    def test_score_deltas_builder_receives_correct_args(self):
        builder, calls = _recording_id_builder()
        deltas = [_sd("B000002", delta=-7.0, dimension="coi", snapshot_date=SNAP)]
        events_from_score_deltas(deltas, MEMBERS, id_builder=builder)
        assert len(calls) == 1
        kind, bioguide, dimension, snap = calls[0]
        assert kind == FeedEventKind.SCORE_DELTA
        assert bioguide == "B000002"
        assert snap == SNAP

    def test_default_builder_unchanged_without_injection(self):
        """Omitting id_builder must give the same IDs as the explicit default."""
        fires = [_rf("f1", "A000001")]
        default_events = events_from_rule_fires(fires, MEMBERS)
        explicit_events = events_from_rule_fires(fires, MEMBERS, id_builder=make_feed_event_id)
        assert default_events[0].feed_event_id == explicit_events[0].feed_event_id

    def test_multiple_fires_all_use_custom_builder(self):
        builder, calls = _recording_id_builder()
        fires = [_rf("f1", "A000001"), _rf("f2", "B000002")]
        events_from_rule_fires(fires, MEMBERS, id_builder=builder)
        assert len(calls) == 2

    def test_injected_id_differs_from_default(self):
        """Custom builder output must differ from the SHA-256 default."""
        builder = _constant_id_builder("custom")
        fires = [_rf("f1", "A000001")]
        custom_events = events_from_rule_fires(fires, MEMBERS, id_builder=builder)
        default_events = events_from_rule_fires(fires, MEMBERS)
        assert custom_events[0].feed_event_id != default_events[0].feed_event_id
