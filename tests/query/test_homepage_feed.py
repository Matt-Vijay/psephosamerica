"""Tests for src/query/homepage_feed.py.

Pure unit tests — no I/O, no database, no network calls.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.feed.changes import FeedEventKind, make_feed_event_id
from src.homepage.contracts import HomepageFeedPayload
from src.query.homepage_feed import (
    assemble_homepage_payload,
    member_meta_from_rows,
    rows_to_feed_events,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SNAP = dt.date(2025, 3, 1)
_RENDERED = dt.date(2025, 2, 20)
_EARLIER = dt.date(2025, 1, 15)


def _row(
    public_id: str,
    slug: str,
    *,
    score_delta: float = -20.0,
    dimension: str = "conflict_of_interest_risk",
    short_explanation: str = "Test",
    rendered_at: dt.date | dt.datetime | str | None = _RENDERED,
    member_full_name: str = "Alice Smith",
    chamber: str = "house",
    party: str = "D",
    state: str = "CA",
) -> dict[str, Any]:
    return {
        "public_id": public_id,
        "member_slug": slug,
        "dimension": dimension,
        "score_delta": score_delta,
        "short_explanation": short_explanation,
        "rendered_at": rendered_at,
        "member_full_name": member_full_name,
        "confidence_label": "HIGH",
        "chamber": chamber,
        "party": party,
        "state": state,
    }


# ---------------------------------------------------------------------------
# rows_to_feed_events
# ---------------------------------------------------------------------------


class TestRowsToFeedEvents:
    def test_empty_rows_returns_empty(self):
        assert rows_to_feed_events([], snapshot_date=_SNAP) == []

    def test_single_row_produces_one_event(self):
        rows = [_row("card-1", "alice-smith")]
        events = rows_to_feed_events(rows, snapshot_date=_SNAP)
        assert len(events) == 1

    def test_event_kind_is_evidence_card(self):
        rows = [_row("card-1", "alice-smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.kind == FeedEventKind.EVIDENCE_CARD

    def test_member_slug_used_as_bioguide_id(self):
        rows = [_row("card-1", "bob-jones")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.member_bioguide_id == "bob-jones"
        assert ev.member_slug == "bob-jones"

    def test_member_name_populated(self):
        rows = [_row("card-1", "alice-smith", member_full_name="Alice Smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.member_name == "Alice Smith"

    def test_score_delta_and_abs_delta(self):
        rows = [_row("card-1", "alice-smith", score_delta=-35.0)]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.score_delta == pytest.approx(-35.0)
        assert ev.abs_delta == pytest.approx(35.0)

    def test_abs_delta_positive_for_positive_delta(self):
        rows = [_row("card-1", "alice-smith", score_delta=10.0)]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.abs_delta == pytest.approx(10.0)

    def test_evidence_card_id_set_from_public_id(self):
        rows = [_row("card-42", "alice-smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.evidence_card_id == "card-42"

    def test_snapshot_date_set_on_event(self):
        rows = [_row("card-1", "alice-smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.snapshot_date == _SNAP

    def test_occurred_at_uses_rendered_at_date(self):
        rows = [_row("card-1", "alice-smith", rendered_at=_RENDERED)]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.occurred_at == _RENDERED

    def test_occurred_at_falls_back_to_snapshot_when_rendered_at_none(self):
        rows = [_row("card-1", "alice-smith", rendered_at=None)]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.occurred_at == _SNAP

    def test_rendered_at_as_string(self):
        rows = [_row("card-1", "alice-smith", rendered_at="2025-02-20")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.occurred_at == dt.date(2025, 2, 20)

    def test_rendered_at_as_datetime(self):
        rendered_dt = dt.datetime(2025, 2, 20, 12, 0, 0)
        rows = [_row("card-1", "alice-smith", rendered_at=rendered_dt)]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.occurred_at == dt.date(2025, 2, 20)

    def test_rendered_at_as_date_object(self):
        rows = [_row("card-1", "alice-smith", rendered_at=dt.date(2025, 2, 20))]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.occurred_at == dt.date(2025, 2, 20)

    def test_feed_event_id_is_deterministic(self):
        rows = [_row("card-1", "alice-smith")]
        e1 = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        e2 = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert e1.feed_event_id == e2.feed_event_id

    def test_feed_event_id_has_fe_prefix_by_default(self):
        rows = [_row("card-1", "alice-smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        assert ev.feed_event_id.startswith("fe_")

    def test_different_public_ids_produce_different_event_ids(self):
        r1 = _row("card-1", "alice-smith")
        r2 = _row("card-2", "alice-smith")
        e1 = rows_to_feed_events([r1], snapshot_date=_SNAP)[0]
        e2 = rows_to_feed_events([r2], snapshot_date=_SNAP)[0]
        assert e1.feed_event_id != e2.feed_event_id

    def test_multiple_rows_produce_multiple_events(self):
        rows = [
            _row("card-1", "alice-smith"),
            _row("card-2", "bob-jones", member_full_name="Bob Jones"),
        ]
        events = rows_to_feed_events(rows, snapshot_date=_SNAP)
        assert len(events) == 2

    def test_injectable_id_builder_used(self):
        def fixed_builder(kind, slug, dimension, snap, *discriminators):
            return f"custom-{slug}"

        rows = [_row("card-1", "alice-smith")]
        ev = rows_to_feed_events(rows, snapshot_date=_SNAP, id_builder=fixed_builder)[0]
        assert ev.feed_event_id == "custom-alice-smith"

    def test_injectable_id_builder_receives_public_id_as_discriminator(self):
        seen_discriminators: list[tuple] = []

        def recording_builder(kind, slug, dimension, snap, *discriminators):
            seen_discriminators.append(discriminators)
            return f"rec-{len(seen_discriminators)}"

        rows = [_row("card-xyz", "alice-smith")]
        rows_to_feed_events(rows, snapshot_date=_SNAP, id_builder=recording_builder)
        assert "card-xyz" in seen_discriminators[0]

    def test_default_builder_matches_explicit_make_feed_event_id(self):
        rows = [_row("card-1", "alice-smith")]
        default_ev = rows_to_feed_events(rows, snapshot_date=_SNAP)[0]
        explicit_ev = rows_to_feed_events(
            rows, snapshot_date=_SNAP, id_builder=make_feed_event_id
        )[0]
        assert default_ev.feed_event_id == explicit_ev.feed_event_id


# ---------------------------------------------------------------------------
# member_meta_from_rows
# ---------------------------------------------------------------------------


class TestMemberMetaFromRows:
    def test_empty_rows_returns_empty_dict(self):
        assert member_meta_from_rows([]) == {}

    def test_single_row_produces_entry(self):
        rows = [_row("card-1", "alice-smith", chamber="house", party="D", state="CA")]
        meta = member_meta_from_rows(rows)
        assert "alice-smith" in meta
        assert meta["alice-smith"]["chamber"] == "house"
        assert meta["alice-smith"]["party"] == "D"
        assert meta["alice-smith"]["state"] == "CA"

    def test_multiple_slugs_produce_separate_entries(self):
        rows = [
            _row("card-1", "alice-smith"),
            _row("card-2", "bob-jones", member_full_name="Bob Jones", chamber="senate"),
        ]
        meta = member_meta_from_rows(rows)
        assert "alice-smith" in meta
        assert "bob-jones" in meta

    def test_duplicate_slug_uses_first_occurrence(self):
        # Two rows for the same member; only the first should set chamber
        rows = [
            _row("card-1", "alice-smith", chamber="house"),
            _row("card-2", "alice-smith", chamber="senate"),
        ]
        meta = member_meta_from_rows(rows)
        assert meta["alice-smith"]["chamber"] == "house"

    def test_missing_fields_default_to_empty_string(self):
        row: dict[str, Any] = {"member_slug": "bare-slug", "public_id": "x",
                                "dimension": "coi", "score_delta": -5.0}
        meta = member_meta_from_rows([row])
        assert meta["bare-slug"]["chamber"] == ""
        assert meta["bare-slug"]["party"] == ""
        assert meta["bare-slug"]["state"] == ""


# ---------------------------------------------------------------------------
# assemble_homepage_payload
# ---------------------------------------------------------------------------


class TestAssembleHomepagePayload:
    def _rows(self) -> list[dict[str, Any]]:
        return [
            _row("card-a", "alice-smith", score_delta=-30.0,
                 rendered_at=dt.date(2025, 2, 25), chamber="house", party="D", state="CA"),
            _row("card-b", "bob-jones", score_delta=-15.0,
                 rendered_at=dt.date(2025, 2, 20),
                 member_full_name="Bob Jones", chamber="senate", party="R", state="TX"),
            _row("card-c", "carol-lee", score_delta=-5.0,
                 rendered_at=None,
                 member_full_name="Carol Lee", chamber="house", party="R", state="FL"),
        ]

    def test_returns_homepage_feed_payload(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        assert isinstance(result, HomepageFeedPayload)

    def test_snapshot_date_set(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        assert result.snapshot_date == _SNAP

    def test_top_changes_ordered_by_magnitude(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        deltas = [abs(s.score_delta) for s in result.top_changes]
        assert deltas == sorted(deltas, reverse=True)

    def test_recent_events_sorted_by_occurred_at_desc(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        dates = [r.occurred_at for r in result.recent_events]
        assert dates == sorted(dates, reverse=True)

    def test_recent_evidence_card_ids_populated(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        # card-c has rendered_at=None so occurred_at=snapshot; others have dates
        assert "card-a" in result.recent_evidence_card_ids
        assert "card-b" in result.recent_evidence_card_ids

    def test_empty_rows_returns_empty_payload(self):
        result = assemble_homepage_payload([], snapshot_date=_SNAP)
        assert result.top_changes == []
        assert result.recent_events == []
        assert result.recent_evidence_card_ids == []

    def test_top_n_respected(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP, top_n=2)
        assert len(result.top_changes) <= 2

    def test_recent_n_respected(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP, recent_n=2)
        assert len(result.recent_events) <= 2

    def test_since_filters_recent_events(self):
        cutoff = dt.date(2025, 2, 21)
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP, since=cutoff)
        for ev in result.recent_events:
            assert ev.occurred_at >= cutoff

    def test_dimension_filter_restricts_both_surfaces(self):
        rows = self._rows() + [
            _row("card-other", "alice-smith", dimension="other_dim", score_delta=-99.0,
                 rendered_at=dt.date(2025, 2, 28)),
        ]
        result = assemble_homepage_payload(
            rows, snapshot_date=_SNAP, dimension="conflict_of_interest_risk"
        )
        for s in result.top_changes:
            assert s.dimension == "conflict_of_interest_risk"
        for ev in result.recent_events:
            assert ev.dimension == "conflict_of_interest_risk"

    def test_injectable_id_builder_propagated(self):
        calls: list = []

        def recording_builder(kind, slug, dimension, snap, *discriminators):
            calls.append(slug)
            return f"pub-{slug}-{len(calls)}"

        assemble_homepage_payload(self._rows(), snapshot_date=_SNAP, id_builder=recording_builder)
        assert len(calls) == len(self._rows())

    def test_member_meta_chamber_party_state_in_top_changes(self):
        result = assemble_homepage_payload(self._rows(), snapshot_date=_SNAP)
        alice = next(s for s in result.top_changes if s.slug == "alice-smith")
        assert alice.chamber == "house"
        assert alice.party == "D"
        assert alice.state == "CA"

    def test_deterministic_across_input_orderings(self):
        import random

        rows = self._rows()
        results: list[HomepageFeedPayload] = []
        for _ in range(5):
            shuffled = random.sample(rows, len(rows))
            results.append(assemble_homepage_payload(shuffled, snapshot_date=_SNAP))

        first = results[0]
        for other in results[1:]:
            assert [s.slug for s in other.top_changes] == [s.slug for s in first.top_changes]
            assert [e.feed_event_id for e in other.recent_events] == [
                e.feed_event_id for e in first.recent_events
            ]
