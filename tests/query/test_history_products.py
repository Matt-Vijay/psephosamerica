"""Tests for src/query/history_products.py.

Pure unit tests only: no filesystem, database, or network calls.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from src.export.contracts import (
    HistoryCoverageDimensionPayload,
    MemberChangeSummaryPayload,
    MemberHistoryCoverageIndexPayload,
    MemberHistoryCoverageIndexEntryPayload,
    MemberHistoryCoveragePayload,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberTimelineIndexPayload,
    MemberTimelinePagePayload,
    MemberTimelineDimensionPayload,
    MemberTimelineYearPayload,
)
from src.feed.changes import FeedEventKind, make_feed_event_id
from src.homepage.contracts import MovementWindowPayload
from src.query.history_products import (
    build_history_coverage,
    build_movement_window,
    build_latest_movement_window,
    build_member_change_summary,
    build_member_history_coverage,
    build_member_history_coverage_index,
    build_member_history_chart,
    build_member_timeline_index,
    build_member_timeline_dimension,
    build_member_timeline_page,
    build_member_timeline_year,
    build_snapshot_compare_presets,
    build_member_trend_summary,
    build_member_window_change_summary,
    build_snapshot_compare_payload,
)


def _snapshot(
    snapshot_date: dt.date,
    *,
    score_total: float,
    score_total_delta: float | None,
    dimension_scores: dict[str, float],
) -> MemberHistorySnapshot:
    return MemberHistorySnapshot(
        snapshot_date=snapshot_date,
        score_total=score_total,
        score_total_delta=score_total_delta,
        dimension_scores=dimension_scores,
        published_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
    )


def _event(
    rule_id: str,
    *,
    dimension: str,
    evidence_card_id: str | None,
    score_delta: float,
    snapshot_date: dt.date,
    fired_at: dt.datetime | None,
    short_explanation: str | None = None,
) -> MemberHistoryEvent:
    return MemberHistoryEvent(
        rule_id=rule_id,
        dimension=dimension,
        severity="high",
        evidence_card_id=evidence_card_id,
        short_explanation=short_explanation or f"{rule_id} explanation",
        score_delta=score_delta,
        snapshot_date=snapshot_date,
        fired_at=fired_at,
    )


def _history(
    *,
    bioguide_id: str = "A000001",
    name: str = "Alice Smith",
    slug: str = "alice-smith",
    chamber: Literal["house", "senate"] = "house",
    state: str = "CA",
    party: str = "Democrat",
    snapshots: list[MemberHistorySnapshot],
    events: list[MemberHistoryEvent],
) -> MemberHistoryPayload:
    return MemberHistoryPayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        district="12" if chamber == "house" else None,
        chamber=chamber,
        party=party,
        snapshots=snapshots,
        events=events,
        committee_history=[],
    )


def test_build_member_change_summary_uses_latest_snapshot_recent_events_and_dimension_deltas() -> (
    None
):
    previous_snapshot_date = dt.date(2026, 1, 1)
    latest_snapshot_date = dt.date(2026, 1, 8)
    history = _history(
        snapshots=[
            _snapshot(
                latest_snapshot_date,
                score_total=65.0,
                score_total_delta=15.0,
                dimension_scores={
                    "conflict_of_interest_risk": 40.0,
                    "ethics_enforcement_risk": 5.0,
                    "transparency_risk": 20.0,
                },
            ),
            _snapshot(
                previous_snapshot_date,
                score_total=50.0,
                score_total_delta=None,
                dimension_scores={
                    "conflict_of_interest_risk": 30.0,
                    "ethics_enforcement_risk": 20.0,
                },
            ),
        ],
        events=[
            _event(
                "previous-window",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-old",
                score_delta=5.0,
                snapshot_date=previous_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest-transparency",
                dimension="transparency_risk",
                evidence_card_id="ec-100",
                score_delta=20.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest-conflict",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-200",
                score_delta=10.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 7, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest-ethics",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-300",
                score_delta=-15.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 6, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_change_summary(history, dimension_limit=3, event_limit=2)

    assert isinstance(result, MemberChangeSummaryPayload)
    assert result.latest_snapshot_date == latest_snapshot_date
    assert result.previous_snapshot_date == previous_snapshot_date
    assert result.latest_score_total == 65.0
    assert result.previous_score_total == 50.0
    assert result.score_total_delta == 15.0
    assert [event.evidence_card_id for event in result.recent_events] == [
        "ec-100",
        "ec-200",
    ]
    assert result.top_evidence_card_ids == ["ec-100", "ec-200"]
    assert [change.dimension for change in result.top_dimension_changes] == [
        "transparency_risk",
        "ethics_enforcement_risk",
        "conflict_of_interest_risk",
    ]
    assert [change.score_delta for change in result.top_dimension_changes] == [
        20.0,
        -15.0,
        10.0,
    ]
    assert [change.event_count for change in result.top_dimension_changes] == [
        1,
        0,
        1,
    ]


def test_build_member_change_summary_without_previous_snapshot_has_no_dimension_changes() -> None:
    snapshot_date = dt.date(2026, 2, 1)
    history = _history(
        snapshots=[
            _snapshot(
                snapshot_date,
                score_total=12.0,
                score_total_delta=4.0,
                dimension_scores={"conflict_of_interest_risk": 12.0},
            )
        ],
        events=[
            _event(
                "latest-only",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-only",
                score_delta=4.0,
                snapshot_date=snapshot_date,
                fired_at=dt.datetime(2026, 2, 1, 9, 0, tzinfo=dt.UTC),
            )
        ],
    )

    result = build_member_change_summary(history)

    assert result.previous_snapshot_date is None
    assert result.previous_score_total is None
    assert result.top_dimension_changes == []
    assert result.top_evidence_card_ids == ["ec-only"]


def test_build_member_timeline_page_chunks_events_with_stable_ids() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2025, 12, 1),
                score_total=30.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 30.0},
            ),
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=45.0,
                score_total_delta=15.0,
                dimension_scores={"conflict_of_interest_risk": 45.0},
            ),
        ],
        events=[
            _event(
                "latest-a",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-latest-a",
                score_delta=8.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest-b",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-latest-b",
                score_delta=7.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "older",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-older",
                score_delta=5.0,
                snapshot_date=dt.date(2025, 12, 20),
                fired_at=dt.datetime(2025, 12, 20, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    first = build_member_timeline_page(history, page=1, page_size=2)
    second = build_member_timeline_page(history, page=2, page_size=2)
    repeated = build_member_timeline_page(history, page=1, page_size=2)

    assert isinstance(first, MemberTimelinePagePayload)
    assert first.page == 1
    assert first.total_pages == 2
    assert first.total_events == 3
    assert first.next_page == 2
    assert first.previous_page is None
    assert [event.evidence_card_id for event in first.events] == [
        "ec-latest-a",
        "ec-latest-b",
    ]
    assert second.page == 2
    assert second.previous_page == 1
    assert second.next_page is None
    assert [event.evidence_card_id for event in second.events] == ["ec-older"]
    assert all(event.event_id.startswith("he-") for event in first.events)
    assert [event.event_id for event in first.events] == [
        event.event_id for event in repeated.events
    ]


def test_build_member_timeline_page_rejects_page_beyond_total_pages() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=45.0,
                score_total_delta=15.0,
                dimension_scores={"conflict_of_interest_risk": 45.0},
            )
        ],
        events=[
            _event(
                "only-event",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-only",
                score_delta=8.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.UTC),
            )
        ],
    )

    try:
        build_member_timeline_page(history, page=2, page_size=25)
    except ValueError as exc:
        assert "total pages" in str(exc)
    else:
        raise AssertionError("expected page beyond total_pages to be rejected")


def test_build_member_timeline_index_tracks_latest_event_and_years() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2025, 12, 1),
                score_total=30.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 30.0},
            ),
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=45.0,
                score_total_delta=15.0,
                dimension_scores={"conflict_of_interest_risk": 45.0},
            ),
        ],
        events=[
            _event(
                "latest-a",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-latest-a",
                score_delta=8.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.UTC),
            ),
            _event(
                "older",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-older",
                score_delta=5.0,
                snapshot_date=dt.date(2025, 12, 20),
                fired_at=dt.datetime(2025, 12, 20, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "oldest",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-oldest",
                score_delta=3.0,
                snapshot_date=dt.date(2024, 2, 5),
                fired_at=dt.datetime(2024, 2, 5, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    index = build_member_timeline_index(history, page_size=1)
    first_page = build_member_timeline_page(history, page=1, page_size=1)

    assert isinstance(index, MemberTimelineIndexPayload)
    assert index.slug == "alice-smith"
    assert index.latest_snapshot_date == dt.date(2026, 1, 15)
    assert index.total_events == 3
    assert index.page_size == 1
    assert index.total_pages == 3
    assert index.latest_event_id == first_page.events[0].event_id
    assert index.latest_event_date == dt.date(2026, 1, 15)
    assert index.earliest_event_date == dt.date(2024, 2, 5)
    assert index.available_years == [2026, 2025, 2024]
    assert [
        (bucket.year, bucket.event_count, bucket.start_page, bucket.end_page)
        for bucket in index.year_buckets
    ] == [
        (2026, 1, 1, 1),
        (2025, 1, 2, 2),
        (2024, 1, 3, 3),
    ]


def test_build_member_history_coverage_summarizes_years_dimensions_and_windows() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2026, 4, 15),
                score_total=50.0,
                score_total_delta=15.0,
                dimension_scores={
                    "conflict_of_interest_risk": 35.0,
                    "transparency_risk": 15.0,
                },
            ),
            _snapshot(
                dt.date(2026, 3, 18),
                score_total=35.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 35.0},
            ),
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=15.0,
                score_total_delta=5.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
            _snapshot(
                dt.date(2026, 1, 1),
                score_total=10.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 10.0},
            ),
        ],
        events=[
            _event(
                "latest-conflict",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-latest",
                score_delta=15.0,
                snapshot_date=dt.date(2026, 4, 15),
                fired_at=dt.datetime(2026, 4, 15, 12, 0, tzinfo=dt.UTC),
            ),
            _event(
                "mid-transparency",
                dimension="transparency_risk",
                evidence_card_id="ec-mid",
                score_delta=10.0,
                snapshot_date=dt.date(2026, 3, 18),
                fired_at=dt.datetime(2026, 3, 18, 12, 0, tzinfo=dt.UTC),
            ),
            _event(
                "old-conflict",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-old",
                score_delta=5.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_history_coverage(history)

    assert isinstance(result, MemberHistoryCoveragePayload)
    assert result.slug == "alice-smith"
    assert result.snapshot_count == 4
    assert result.total_events == 3
    assert result.earliest_snapshot_date == dt.date(2026, 1, 1)
    assert result.latest_snapshot_date == dt.date(2026, 4, 15)
    assert result.available_years == [2026]
    assert [(year.year, year.event_count) for year in result.years] == [(2026, 3)]
    assert [(dimension.dimension, dimension.event_count) for dimension in result.dimensions] == [
        ("conflict_of_interest_risk", 2),
        ("transparency_risk", 1),
    ]
    assert [window.window_key for window in result.windows] == ["4w", "12w", "cycle"]
    assert result.windows[0].has_full_window is True
    assert result.windows[0].start_snapshot_date == dt.date(2026, 3, 18)


def test_build_history_coverage_summarizes_dimensions_across_members() -> None:
    histories = [
        _history(
            bioguide_id="A000001",
            slug="alice-smith",
            snapshots=[
                _snapshot(
                    dt.date(2026, 1, 1),
                    score_total=10.0,
                    score_total_delta=None,
                    dimension_scores={"conflict_of_interest_risk": 10.0},
                )
            ],
            events=[
                _event(
                    "conflict-a",
                    dimension="conflict_of_interest_risk",
                    evidence_card_id="ec-a",
                    score_delta=5.0,
                    snapshot_date=dt.date(2026, 1, 1),
                    fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
                ),
                _event(
                    "transparency-a",
                    dimension="transparency_risk",
                    evidence_card_id="ec-b",
                    score_delta=3.0,
                    snapshot_date=dt.date(2026, 1, 1),
                    fired_at=dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC),
                ),
            ],
        ),
        _history(
            bioguide_id="B000002",
            name="Bob Jones",
            slug="bob-jones",
            snapshots=[
                _snapshot(
                    dt.date(2026, 1, 8),
                    score_total=20.0,
                    score_total_delta=10.0,
                    dimension_scores={"conflict_of_interest_risk": 20.0},
                )
            ],
            events=[
                _event(
                    "conflict-b",
                    dimension="conflict_of_interest_risk",
                    evidence_card_id="ec-c",
                    score_delta=10.0,
                    snapshot_date=dt.date(2026, 1, 8),
                    fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
                ),
            ],
        ),
    ]

    result = build_history_coverage(
        [("2026-01-01", dt.date(2026, 1, 1)), ("2026-01-08", dt.date(2026, 1, 8))],
        histories,
    )

    assert result.snapshot_count == 2
    assert result.total_events == 3
    assert result.dimensions == [
        HistoryCoverageDimensionPayload(
            dimension="conflict_of_interest_risk",
            member_history_count=2,
            total_events=2,
            available_years=[2026],
        ),
        HistoryCoverageDimensionPayload(
            dimension="transparency_risk",
            member_history_count=1,
            total_events=1,
            available_years=[2026],
        ),
    ]


def test_build_member_timeline_year_uses_bucket_start_page() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=42.0,
                score_total_delta=4.0,
                dimension_scores={"conflict_of_interest_risk": 42.0},
            )
        ],
        events=[
            _event(
                "r1",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-1",
                score_delta=2.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "r2",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-2",
                score_delta=2.0,
                snapshot_date=dt.date(2025, 12, 15),
                fired_at=dt.datetime(2025, 12, 15, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "r3",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-3",
                score_delta=2.0,
                snapshot_date=dt.date(2025, 11, 10),
                fired_at=dt.datetime(2025, 11, 10, 9, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_timeline_year(history, year=2025, page_size=1)

    assert isinstance(result, MemberTimelineYearPayload)
    assert result.year == 2025
    assert result.year_bucket.year == 2025
    assert result.year_bucket.start_page == 2
    assert result.timeline_page.page == 2
    assert result.timeline_page.events[0].evidence_card_id == "ec-2"


def test_build_member_timeline_dimension_filters_events_and_paginates() -> None:
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=42.0,
                score_total_delta=4.0,
                dimension_scores={"conflict_of_interest_risk": 42.0},
            )
        ],
        events=[
            _event(
                "r1",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-1",
                score_delta=2.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "r2",
                dimension="transparency_risk",
                evidence_card_id="ec-2",
                score_delta=2.0,
                snapshot_date=dt.date(2026, 1, 8),
                fired_at=dt.datetime(2026, 1, 8, 9, 0, tzinfo=dt.UTC),
            ),
            _event(
                "r3",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-3",
                score_delta=2.0,
                snapshot_date=dt.date(2025, 12, 15),
                fired_at=dt.datetime(2025, 12, 15, 9, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_timeline_dimension(
        history,
        dimension="conflict_of_interest_risk",
        page_size=1,
    )

    assert isinstance(result, MemberTimelineDimensionPayload)
    assert result.dimension == "conflict_of_interest_risk"
    assert result.timeline_index.total_events == 2
    assert result.timeline_index.total_pages == 2
    assert [bucket.year for bucket in result.timeline_index.year_buckets] == [2026, 2025]
    assert result.timeline_page.page == 1
    assert [event.evidence_card_id for event in result.timeline_page.events] == ["ec-1"]


def test_build_member_history_coverage_index_summarizes_members_with_full_window_flags() -> None:
    coverages = [
        MemberHistoryCoveragePayload(
            bioguide_id="A000001",
            name="Alice Smith",
            slug="alice-smith",
            state="CA",
            district="12",
            chamber="house",
            party="Democrat",
            earliest_snapshot_date=dt.date(2026, 1, 1),
            latest_snapshot_date=dt.date(2026, 4, 15),
            latest_event_date=dt.date(2026, 4, 15),
            earliest_event_date=dt.date(2026, 1, 1),
            snapshot_count=5,
            total_events=4,
            available_years=[2026],
            years=[{"year": 2026, "event_count": 4}],
            dimensions=[{"dimension": "conflict_of_interest_risk", "event_count": 3}],
            windows=[
                {
                    "window_key": "4w",
                    "requested_days": 28,
                    "has_full_window": True,
                    "start_snapshot_date": dt.date(2026, 3, 18),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
                {
                    "window_key": "12w",
                    "requested_days": 84,
                    "has_full_window": True,
                    "start_snapshot_date": dt.date(2026, 1, 15),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
                {
                    "window_key": "cycle",
                    "requested_days": None,
                    "has_full_window": True,
                    "start_snapshot_date": dt.date(2026, 1, 1),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
            ],
        ),
        MemberHistoryCoveragePayload(
            bioguide_id="B000002",
            name="Bob Jones",
            slug="bob-jones",
            state="NY",
            district=None,
            chamber="senate",
            party="Independent",
            earliest_snapshot_date=dt.date(2026, 3, 18),
            latest_snapshot_date=dt.date(2026, 4, 15),
            latest_event_date=dt.date(2026, 4, 15),
            earliest_event_date=dt.date(2026, 3, 18),
            snapshot_count=2,
            total_events=1,
            available_years=[2026],
            years=[{"year": 2026, "event_count": 1}],
            dimensions=[{"dimension": "ethics_enforcement_risk", "event_count": 1}],
            windows=[
                {
                    "window_key": "4w",
                    "requested_days": 28,
                    "has_full_window": True,
                    "start_snapshot_date": dt.date(2026, 3, 18),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
                {
                    "window_key": "12w",
                    "requested_days": 84,
                    "has_full_window": False,
                    "start_snapshot_date": dt.date(2026, 3, 18),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
                {
                    "window_key": "cycle",
                    "requested_days": None,
                    "has_full_window": True,
                    "start_snapshot_date": dt.date(2026, 3, 18),
                    "end_snapshot_date": dt.date(2026, 4, 15),
                },
            ],
        ),
    ]

    result = build_member_history_coverage_index(coverages)

    assert isinstance(result, MemberHistoryCoverageIndexPayload)
    assert result.total_members == 2
    assert [entry.slug for entry in result.members] == ["alice-smith", "bob-jones"]
    assert isinstance(result.members[0], MemberHistoryCoverageIndexEntryPayload)
    assert result.members[0].has_full_12w is True
    assert result.members[1].has_full_12w is False


def test_build_latest_movement_window_uses_latest_events_only_and_surfaces_window_metadata() -> (
    None
):
    previous_snapshot_date = dt.date(2026, 1, 1)
    latest_snapshot_date = dt.date(2026, 1, 8)
    alice = _history(
        snapshots=[
            _snapshot(
                previous_snapshot_date,
                score_total=5.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 5.0},
            ),
            _snapshot(
                latest_snapshot_date,
                score_total=12.0,
                score_total_delta=7.0,
                dimension_scores={"conflict_of_interest_risk": 12.0},
            ),
        ],
        events=[
            _event(
                "old-alice",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-old-alice",
                score_delta=3.0,
                snapshot_date=previous_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest-alice",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-alice",
                score_delta=7.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 7, 12, 0, tzinfo=dt.UTC),
            ),
        ],
    )
    bob = _history(
        bioguide_id="B000002",
        name="Bob Jones",
        slug="bob-jones",
        chamber="senate",
        state="NY",
        party="Independent",
        snapshots=[
            _snapshot(
                latest_snapshot_date,
                score_total=18.0,
                score_total_delta=-12.0,
                dimension_scores={"ethics_enforcement_risk": 18.0},
            )
        ],
        events=[
            _event(
                "latest-bob",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-bob",
                score_delta=-12.0,
                snapshot_date=latest_snapshot_date,
                fired_at=None,
            )
        ],
    )

    result = build_latest_movement_window(
        [alice, bob],
        latest_snapshot_id="2026-01-08",
        latest_snapshot_date=latest_snapshot_date,
        previous_snapshot_id="2026-01-01",
        previous_snapshot_date=previous_snapshot_date,
        top_n=5,
        recent_n=5,
    )

    assert isinstance(result, MovementWindowPayload)
    assert result.latest_snapshot_id == "2026-01-08"
    assert result.latest_snapshot_date == latest_snapshot_date
    assert result.previous_snapshot_id == "2026-01-01"
    assert result.previous_snapshot_date == previous_snapshot_date
    assert [change.bioguide_id for change in result.top_changes] == [
        "B000002",
        "A000001",
    ]
    assert [change.top_evidence_card_ids for change in result.top_changes] == [
        ["ec-bob"],
        ["ec-alice"],
    ]
    assert [event.evidence_card_id for event in result.recent_events] == [
        "ec-bob",
        "ec-alice",
    ]
    assert result.recent_evidence_card_ids == ["ec-bob", "ec-alice"]


def test_build_movement_window_uses_requested_snapshot_window() -> None:
    start_snapshot_date = dt.date(2026, 1, 1)
    end_snapshot_date = dt.date(2026, 1, 15)
    history = _history(
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=10.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 10.0},
            ),
            _snapshot(
                dt.date(2026, 1, 8),
                score_total=15.0,
                score_total_delta=5.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=18.0,
                score_total_delta=3.0,
                dimension_scores={"conflict_of_interest_risk": 18.0},
            ),
        ],
        events=[
            _event(
                "old",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-old",
                score_delta=2.0,
                snapshot_date=start_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "mid",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-mid",
                score_delta=5.0,
                snapshot_date=dt.date(2026, 1, 8),
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-new",
                score_delta=3.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_movement_window(
        [history],
        window_key="4w",
        latest_snapshot_id="2026-01-15",
        latest_snapshot_date=end_snapshot_date,
        previous_snapshot_id="2026-01-01",
        previous_snapshot_date=start_snapshot_date,
        has_full_window=False,
        top_n=5,
        recent_n=5,
    )

    assert isinstance(result, MovementWindowPayload)
    assert result.window_key == "4w"
    assert result.has_full_window is False
    assert result.latest_snapshot_id == "2026-01-15"
    assert result.previous_snapshot_id == "2026-01-01"
    assert [event.evidence_card_id for event in result.recent_events] == ["ec-new", "ec-mid"]
    assert result.recent_evidence_card_ids == ["ec-new", "ec-mid"]


def test_build_movement_window_filters_requested_dimension() -> None:
    latest_snapshot_date = dt.date(2026, 1, 15)
    previous_snapshot_date = dt.date(2026, 1, 1)
    history = _history(
        snapshots=[
            _snapshot(
                previous_snapshot_date,
                score_total=10.0,
                score_total_delta=None,
                dimension_scores={
                    "conflict_of_interest_risk": 10.0,
                    "transparency_risk": 0.0,
                },
            ),
            _snapshot(
                latest_snapshot_date,
                score_total=18.0,
                score_total_delta=8.0,
                dimension_scores={
                    "conflict_of_interest_risk": 15.0,
                    "transparency_risk": 3.0,
                },
            ),
        ],
        events=[
            _event(
                "conflict",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-conflict",
                score_delta=5.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "transparency",
                dimension="transparency_risk",
                evidence_card_id="ec-transparency",
                score_delta=3.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_movement_window(
        [history],
        window_key="latest",
        latest_snapshot_id="2026-01-15",
        latest_snapshot_date=latest_snapshot_date,
        previous_snapshot_id="2026-01-01",
        previous_snapshot_date=previous_snapshot_date,
        dimension="transparency_risk",
        top_n=5,
        recent_n=5,
    )

    assert isinstance(result, MovementWindowPayload)
    assert result.dimension == "transparency_risk"
    assert [change.dimension for change in result.top_changes] == ["transparency_risk"]
    assert [event.dimension for event in result.recent_events] == ["transparency_risk"]
    assert result.recent_evidence_card_ids == ["ec-transparency"]


def test_build_member_window_change_summary_compares_requested_window() -> None:
    start_snapshot_date = dt.date(2026, 1, 1)
    end_snapshot_date = dt.date(2026, 1, 15)
    history = _history(
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=10.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 10.0},
            ),
            _snapshot(
                dt.date(2026, 1, 8),
                score_total=15.0,
                score_total_delta=5.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=18.0,
                score_total_delta=3.0,
                dimension_scores={
                    "conflict_of_interest_risk": 16.0,
                    "transparency_risk": 2.0,
                },
            ),
        ],
        events=[
            _event(
                "old",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-old",
                score_delta=2.0,
                snapshot_date=start_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "mid",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-mid",
                score_delta=5.0,
                snapshot_date=dt.date(2026, 1, 8),
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "latest",
                dimension="transparency_risk",
                evidence_card_id="ec-new",
                score_delta=2.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_window_change_summary(
        history,
        start_snapshot_date=start_snapshot_date,
        end_snapshot_date=end_snapshot_date,
    )

    assert result is not None
    assert result.previous_snapshot_date == start_snapshot_date
    assert result.latest_snapshot_date == end_snapshot_date
    assert result.score_total_delta == 8.0
    assert [event.evidence_card_id for event in result.recent_events] == ["ec-mid", "ec-new"]
    assert [change.dimension for change in result.top_dimension_changes] == [
        "conflict_of_interest_risk",
        "transparency_risk",
    ]


def test_build_snapshot_compare_payload_aggregates_window_events_and_featured_members() -> None:
    start_snapshot_date = dt.date(2026, 1, 1)
    end_snapshot_date = dt.date(2026, 1, 8)
    alice = _history(
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=5.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 5.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=12.0,
                score_total_delta=7.0,
                dimension_scores={"conflict_of_interest_risk": 12.0},
            ),
        ],
        events=[
            _event(
                "alice-window",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-alice",
                score_delta=7.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            )
        ],
    )
    bob = _history(
        bioguide_id="B000002",
        name="Bob Jones",
        slug="bob-jones",
        chamber="senate",
        state="NY",
        party="Republican",
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=4.0,
                score_total_delta=None,
                dimension_scores={"ethics_enforcement_risk": 4.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=14.0,
                score_total_delta=10.0,
                dimension_scores={"ethics_enforcement_risk": 14.0},
            ),
        ],
        events=[
            _event(
                "bob-window",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-bob",
                score_delta=10.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 9, 0, tzinfo=dt.UTC),
            )
        ],
    )

    result = build_snapshot_compare_payload(
        [alice, bob],
        start_snapshot_id="2026-01-01",
        start_snapshot_date=start_snapshot_date,
        end_snapshot_id="2026-01-08",
        end_snapshot_date=end_snapshot_date,
        top_n=2,
        recent_n=2,
        featured_n=2,
    )

    assert result.start_snapshot_id == "2026-01-01"
    assert result.end_snapshot_id == "2026-01-08"
    assert [change.slug for change in result.top_changes] == ["bob-jones", "alice-smith"]
    assert [event.evidence_card_id for event in result.recent_events] == ["ec-bob", "ec-alice"]
    assert [summary.slug for summary in result.featured_member_changes] == [
        "bob-jones",
        "alice-smith",
    ]
    assert result.recent_events[0].feed_event_id == make_feed_event_id(
        FeedEventKind.RULE_FIRE,
        "B000002",
        "ethics_enforcement_risk",
        end_snapshot_date,
        "ec-bob",
    )


def test_build_snapshot_compare_payload_excludes_start_snapshot_events_and_applies_limits() -> None:
    start_snapshot_date = dt.date(2026, 1, 1)
    end_snapshot_date = dt.date(2026, 1, 8)
    history = _history(
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=5.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 5.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=15.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
        ],
        events=[
            _event(
                "start-window",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-start",
                score_delta=3.0,
                snapshot_date=start_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "end-window",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-end",
                score_delta=10.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_snapshot_compare_payload(
        [history],
        start_snapshot_id="2026-01-01",
        start_snapshot_date=start_snapshot_date,
        end_snapshot_id="2026-01-08",
        end_snapshot_date=end_snapshot_date,
        top_n=1,
        recent_n=1,
        featured_n=1,
    )

    assert [change.slug for change in result.top_changes] == ["alice-smith"]
    assert [event.evidence_card_id for event in result.recent_events] == ["ec-end"]
    assert result.recent_evidence_card_ids == ["ec-end"]
    assert [summary.slug for summary in result.featured_member_changes] == ["alice-smith"]


def test_build_snapshot_compare_payload_accumulates_historical_window_events() -> None:
    start_snapshot_date = dt.date(2026, 1, 1)
    middle_snapshot_date = dt.date(2026, 1, 8)
    end_snapshot_date = dt.date(2026, 1, 15)
    alice = _history(
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=40.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 40.0},
            ),
            _snapshot(
                middle_snapshot_date,
                score_total=47.0,
                score_total_delta=7.0,
                dimension_scores={"conflict_of_interest_risk": 47.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=55.0,
                score_total_delta=8.0,
                dimension_scores={"conflict_of_interest_risk": 55.0},
            ),
        ],
        events=[
            _event(
                "alice-start",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-alice-start",
                score_delta=5.0,
                snapshot_date=start_snapshot_date,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "alice-middle",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-alice-middle",
                score_delta=7.0,
                snapshot_date=middle_snapshot_date,
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "alice-end",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-alice-end",
                score_delta=8.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )
    bob = _history(
        bioguide_id="B000002",
        name="Bob Jones",
        slug="bob-jones",
        chamber="senate",
        state="NY",
        party="Independent",
        snapshots=[
            _snapshot(
                start_snapshot_date,
                score_total=30.0,
                score_total_delta=None,
                dimension_scores={"ethics_enforcement_risk": 30.0},
            ),
            _snapshot(
                middle_snapshot_date,
                score_total=26.0,
                score_total_delta=-4.0,
                dimension_scores={"ethics_enforcement_risk": 26.0},
            ),
            _snapshot(
                end_snapshot_date,
                score_total=18.0,
                score_total_delta=-8.0,
                dimension_scores={"ethics_enforcement_risk": 18.0},
            ),
        ],
        events=[
            _event(
                "bob-start",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-bob-start",
                score_delta=4.0,
                snapshot_date=start_snapshot_date,
                fired_at=dt.datetime(2026, 1, 2, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "bob-middle",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-bob-middle",
                score_delta=-4.0,
                snapshot_date=middle_snapshot_date,
                fired_at=dt.datetime(2026, 1, 7, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "bob-end",
                dimension="ethics_enforcement_risk",
                evidence_card_id="ec-bob-end",
                score_delta=-8.0,
                snapshot_date=end_snapshot_date,
                fired_at=dt.datetime(2026, 1, 14, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_snapshot_compare_payload(
        [alice, bob],
        start_snapshot_id="2026-01-01",
        start_snapshot_date=start_snapshot_date,
        end_snapshot_id="2026-01-15",
        end_snapshot_date=end_snapshot_date,
        top_n=5,
        recent_n=5,
        featured_n=5,
    )

    assert result.start_snapshot_id == "2026-01-01"
    assert result.end_snapshot_id == "2026-01-15"
    assert [change.slug for change in result.top_changes] == ["alice-smith", "bob-jones"]
    assert [change.score_delta for change in result.top_changes] == [15.0, -12.0]
    assert [change.top_evidence_card_ids for change in result.top_changes] == [
        ["ec-alice-end", "ec-alice-middle"],
        ["ec-bob-end", "ec-bob-middle"],
    ]
    assert [event.evidence_card_id for event in result.recent_events] == [
        "ec-alice-end",
        "ec-bob-end",
        "ec-alice-middle",
        "ec-bob-middle",
    ]
    assert result.recent_evidence_card_ids == [
        "ec-alice-end",
        "ec-bob-end",
        "ec-alice-middle",
        "ec-bob-middle",
    ]
    assert [summary.slug for summary in result.featured_member_changes] == [
        "alice-smith",
        "bob-jones",
    ]
    assert [summary.score_total_delta for summary in result.featured_member_changes] == [
        15.0,
        -12.0,
    ]
    assert [summary.top_evidence_card_ids for summary in result.featured_member_changes] == [
        ["ec-alice-middle", "ec-alice-end"],
        ["ec-bob-middle", "ec-bob-end"],
    ]


def test_build_member_trend_summary_returns_4w_12w_and_cycle_windows() -> None:
    latest_snapshot_date = dt.date(2026, 4, 15)
    history = _history(
        snapshots=[
            _snapshot(
                dt.date(2026, 1, 1),
                score_total=10.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 10.0},
            ),
            _snapshot(
                dt.date(2026, 1, 15),
                score_total=15.0,
                score_total_delta=5.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
            _snapshot(
                dt.date(2026, 2, 19),
                score_total=25.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 25.0},
            ),
            _snapshot(
                dt.date(2026, 3, 18),
                score_total=35.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 35.0},
            ),
            _snapshot(
                latest_snapshot_date,
                score_total=50.0,
                score_total_delta=15.0,
                dimension_scores={"conflict_of_interest_risk": 50.0},
            ),
        ],
        events=[
            _event(
                "apr15",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-apr15",
                score_delta=15.0,
                snapshot_date=latest_snapshot_date,
                fired_at=dt.datetime(2026, 4, 15, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "mar18",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-mar18",
                score_delta=10.0,
                snapshot_date=dt.date(2026, 3, 18),
                fired_at=dt.datetime(2026, 3, 18, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "feb19",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-feb19",
                score_delta=10.0,
                snapshot_date=dt.date(2026, 2, 19),
                fired_at=dt.datetime(2026, 2, 19, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "jan15",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-jan15",
                score_delta=5.0,
                snapshot_date=dt.date(2026, 1, 15),
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "jan01",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-jan01",
                score_delta=10.0,
                snapshot_date=dt.date(2026, 1, 1),
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_trend_summary(history)
    windows = {window.window_key: window for window in result.windows}

    assert result.slug == "alice-smith"
    assert result.latest_snapshot_date == latest_snapshot_date
    assert [window.window_key for window in result.windows] == ["4w", "12w", "cycle"]

    assert windows["4w"].label == "Last 4 weeks"
    assert windows["4w"].requested_days == 28
    assert windows["4w"].has_full_window is True
    assert windows["4w"].start_snapshot_date == dt.date(2026, 3, 18)
    assert windows["4w"].end_snapshot_date == latest_snapshot_date
    assert windows["4w"].current_score_total == 50.0
    assert windows["4w"].previous_score_total == 35.0
    assert windows["4w"].score_total_delta == 15.0
    assert windows["4w"].recent_event_count == 1
    assert windows["4w"].top_evidence_card_ids == ["ec-apr15"]
    assert [change.score_delta for change in windows["4w"].top_dimension_changes] == [15.0]

    assert windows["12w"].label == "Last 12 weeks"
    assert windows["12w"].requested_days == 84
    assert windows["12w"].has_full_window is True
    assert windows["12w"].start_snapshot_date == dt.date(2026, 1, 15)
    assert windows["12w"].previous_score_total == 15.0
    assert windows["12w"].score_total_delta == 35.0
    assert windows["12w"].recent_event_count == 3
    assert windows["12w"].top_evidence_card_ids == [
        "ec-apr15",
        "ec-mar18",
        "ec-feb19",
    ]

    assert windows["cycle"].label == "Cycle to date"
    assert windows["cycle"].requested_days is None
    assert windows["cycle"].has_full_window is True
    assert windows["cycle"].start_snapshot_date == dt.date(2026, 1, 1)
    assert windows["cycle"].previous_score_total == 10.0
    assert windows["cycle"].score_total_delta == 40.0
    assert windows["cycle"].recent_event_count == 4
    assert windows["cycle"].top_evidence_card_ids == [
        "ec-apr15",
        "ec-mar18",
        "ec-feb19",
        "ec-jan15",
    ]


def test_build_member_history_chart_returns_points_and_compare_presets() -> None:
    snapshot_dates = [
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 15),
        dt.date(2026, 2, 19),
        dt.date(2026, 3, 18),
        dt.date(2026, 4, 15),
    ]
    history = _history(
        snapshots=[
            _snapshot(
                snapshot_dates[0],
                score_total=10.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 10.0},
            ),
            _snapshot(
                snapshot_dates[1],
                score_total=15.0,
                score_total_delta=5.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            ),
            _snapshot(
                snapshot_dates[2],
                score_total=25.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 25.0},
            ),
            _snapshot(
                snapshot_dates[3],
                score_total=35.0,
                score_total_delta=10.0,
                dimension_scores={"conflict_of_interest_risk": 35.0},
            ),
            _snapshot(
                snapshot_dates[4],
                score_total=50.0,
                score_total_delta=15.0,
                dimension_scores={"conflict_of_interest_risk": 50.0},
            ),
        ],
        events=[
            _event(
                "apr15",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-apr15",
                score_delta=15.0,
                snapshot_date=snapshot_dates[4],
                fired_at=dt.datetime(2026, 4, 15, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "mar18",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-mar18",
                score_delta=10.0,
                snapshot_date=snapshot_dates[3],
                fired_at=dt.datetime(2026, 3, 18, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "feb19",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-feb19",
                score_delta=10.0,
                snapshot_date=snapshot_dates[2],
                fired_at=dt.datetime(2026, 2, 19, 8, 0, tzinfo=dt.UTC),
            ),
            _event(
                "jan15",
                dimension="conflict_of_interest_risk",
                evidence_card_id="ec-jan15",
                score_delta=5.0,
                snapshot_date=snapshot_dates[1],
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )

    result = build_member_history_chart(
        history,
        snapshot_ids_by_date={
            snapshot_date: snapshot_date.isoformat() for snapshot_date in snapshot_dates
        },
    )

    assert result.slug == "alice-smith"
    assert result.latest_snapshot_id == "2026-04-15"
    assert result.default_preset_key == "4w"
    assert [point.snapshot_id for point in result.points] == [
        "2026-01-01",
        "2026-01-15",
        "2026-02-19",
        "2026-03-18",
        "2026-04-15",
    ]
    assert [point.event_count for point in result.points] == [0, 1, 1, 1, 1]
    assert [preset.preset_key for preset in result.compare_presets] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    presets = {preset.preset_key: preset for preset in result.compare_presets}
    assert presets["latest"].start_snapshot_id == "2026-03-18"
    assert presets["latest"].end_snapshot_id == "2026-04-15"
    assert presets["latest"].score_total_delta == 15.0
    assert presets["4w"].start_snapshot_id == "2026-03-18"
    assert presets["4w"].has_full_window is True
    assert presets["12w"].start_snapshot_id == "2026-01-15"
    assert presets["12w"].score_total_delta == 35.0
    assert presets["cycle"].start_snapshot_id == "2026-01-01"
    assert presets["cycle"].top_evidence_card_ids == [
        "ec-apr15",
        "ec-mar18",
        "ec-feb19",
        "ec-jan15",
    ]


def test_build_snapshot_compare_presets_returns_default_and_resolved_ranges() -> None:
    result = build_snapshot_compare_presets(
        [
            ("2026-01-01", dt.date(2026, 1, 1)),
            ("2026-01-15", dt.date(2026, 1, 15)),
            ("2026-02-19", dt.date(2026, 2, 19)),
            ("2026-03-18", dt.date(2026, 3, 18)),
            ("2026-04-15", dt.date(2026, 4, 15)),
        ]
    )

    assert result.default_preset_key == "4w"
    assert [preset.preset_key for preset in result.presets] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    presets = {preset.preset_key: preset for preset in result.presets}
    assert presets["latest"].start_snapshot_id == "2026-03-18"
    assert presets["latest"].end_snapshot_id == "2026-04-15"
    assert presets["4w"].has_full_window is True
    assert presets["4w"].start_snapshot_id == "2026-03-18"
    assert presets["12w"].start_snapshot_id == "2026-01-15"
    assert presets["cycle"].start_snapshot_id == "2026-01-01"
