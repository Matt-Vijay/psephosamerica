"""Pure builders for precomputed historical product read models."""

from __future__ import annotations

import datetime as dt

from src.export.contracts import (
    DimensionChangeSummary,
    HistoryCoverageDimensionPayload,
    HistoryCoveragePayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryChartPoint,
    MemberHistoryComparePreset,
    MemberHistoryCoverageDimensionPayload,
    MemberHistoryCoverageIndexEntryPayload,
    MemberHistoryCoverageIndexPayload,
    MemberHistoryCoveragePayload,
    MemberHistoryCoverageWindowPayload,
    MemberHistoryCoverageYearPayload,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberTimelineDimensionPayload,
    MemberTimelineEventPayload,
    MemberTimelineIndexPayload,
    MemberTimelinePagePayload,
    MemberTimelineYearBucketPayload,
    MemberTimelineYearPayload,
    MemberTrendSummaryPayload,
    MemberTrendWindowPayload,
    SnapshotComparePresetPayload,
    SnapshotComparePresetSetPayload,
)
from src.feed.changes import FeedEvent, FeedEventKind, make_feed_event_id
from src.homepage.builders import build_homepage_feed
from src.homepage.contracts import MovementWindowPayload, SnapshotComparePayload
from src.identity.public_ids import build_history_event_id


def _ordered_snapshots(history: MemberHistoryPayload) -> list[MemberHistorySnapshot]:
    return sorted(history.snapshots, key=lambda snapshot: snapshot.snapshot_date)


def _snapshot_pair(
    history: MemberHistoryPayload,
) -> tuple[MemberHistorySnapshot, MemberHistorySnapshot | None]:
    ordered = _ordered_snapshots(history)
    if not ordered:
        raise ValueError("history must contain at least one snapshot")
    latest = ordered[-1]
    previous = ordered[-2] if len(ordered) > 1 else None
    return latest, previous


def _recent_events(
    history: MemberHistoryPayload,
    latest_snapshot_date: dt.date,
    *,
    limit: int,
) -> list[MemberHistoryEvent]:
    events = [event for event in history.events if event.snapshot_date == latest_snapshot_date]
    return events[:limit]


def _window_events(
    history: MemberHistoryPayload,
    *,
    start_snapshot_date: dt.date,
    end_snapshot_date: dt.date,
) -> list[MemberHistoryEvent]:
    return [
        event
        for event in history.events
        if event.snapshot_date is not None
        and start_snapshot_date < event.snapshot_date <= end_snapshot_date
    ]


def _top_evidence_card_ids(events: list[MemberHistoryEvent]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for event in events:
        card_id = event.evidence_card_id
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        result.append(card_id)
    return result


def _timeline_event_date(
    event: MemberHistoryEvent,
    *,
    fallback_date: dt.date,
) -> dt.date:
    if event.fired_at is not None:
        return event.fired_at.date()
    if event.snapshot_date is not None:
        return event.snapshot_date
    return fallback_date


def _ordered_timeline_events(
    history: MemberHistoryPayload,
) -> list[MemberHistoryEvent]:
    minimum_dt = dt.datetime.min.replace(tzinfo=dt.UTC)
    return sorted(
        history.events,
        key=lambda event: (
            event.fired_at or minimum_dt,
            event.snapshot_date or dt.date.min,
            event.evidence_card_id or "",
            event.rule_id,
            event.short_explanation,
            event.score_delta,
        ),
        reverse=True,
    )


def build_member_timeline_events(
    history: MemberHistoryPayload,
) -> list[MemberTimelineEventPayload]:
    latest_snapshot, _previous = _snapshot_pair(history)
    events = _ordered_timeline_events(history)
    duplicate_counts: dict[
        tuple[dt.date, dt.date | None, dt.datetime | None, str, str, str | None, str, float],
        int,
    ] = {}
    payloads: list[MemberTimelineEventPayload] = []
    for event in events:
        event_date = _timeline_event_date(event, fallback_date=latest_snapshot.snapshot_date)
        duplicate_key = (
            event_date,
            event.snapshot_date,
            event.fired_at,
            event.rule_id,
            event.dimension,
            event.evidence_card_id,
            event.short_explanation,
            event.score_delta,
        )
        ordinal = duplicate_counts.get(duplicate_key, 0) + 1
        duplicate_counts[duplicate_key] = ordinal
        payloads.append(
            MemberTimelineEventPayload(
                event_id=build_history_event_id(
                    history.bioguide_id,
                    event.snapshot_date or event_date,
                    event.rule_id,
                    event.dimension,
                    evidence_card_id=event.evidence_card_id,
                    fired_at=event.fired_at,
                    ordinal=ordinal,
                ),
                bioguide_id=history.bioguide_id,
                name=history.name,
                slug=history.slug,
                state=history.state,
                district=history.district,
                chamber=history.chamber,
                party=history.party,
                event_date=event_date,
                snapshot_date=event.snapshot_date,
                fired_at=event.fired_at,
                rule_id=event.rule_id,
                dimension=event.dimension,
                severity=event.severity,
                evidence_card_id=event.evidence_card_id,
                short_explanation=event.short_explanation,
                score_delta=event.score_delta,
            )
        )
    return payloads


def _timeline_page_from_events(
    history: MemberHistoryPayload,
    events: list[MemberTimelineEventPayload],
    *,
    page: int,
    page_size: int = 25,
) -> MemberTimelinePagePayload:
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")

    total_events = len(events)
    total_pages = max(1, (total_events + page_size - 1) // page_size)
    if page > total_pages:
        raise ValueError(f"page must be <= total pages ({total_pages})")
    start = (page - 1) * page_size
    end = start + page_size
    page_events = events[start:end]
    return MemberTimelinePagePayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        total_events=total_events,
        next_page=(page + 1) if end < total_events else None,
        previous_page=(page - 1) if page > 1 else None,
        events=page_events,
    )


def _timeline_index_from_events(
    history: MemberHistoryPayload,
    events: list[MemberTimelineEventPayload],
    *,
    page_size: int = 25,
) -> MemberTimelineIndexPayload:
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    latest_snapshot, _previous = _snapshot_pair(history)
    total_events = len(events)
    total_pages = max(1, (total_events + page_size - 1) // page_size)
    years = sorted({event.event_date.year for event in events}, reverse=True)
    latest_event = events[0] if events else None
    earliest_event = events[-1] if events else None
    year_buckets: list[MemberTimelineYearBucketPayload] = []
    for year in years:
        year_positions = [
            position for position, event in enumerate(events) if event.event_date.year == year
        ]
        if not year_positions:
            continue
        first_position = year_positions[0]
        last_position = year_positions[-1]
        year_events = events[first_position : last_position + 1]
        year_buckets.append(
            MemberTimelineYearBucketPayload(
                year=year,
                event_count=len(year_events),
                start_page=(first_position // page_size) + 1,
                end_page=(last_position // page_size) + 1,
                latest_event_date=year_events[0].event_date,
                earliest_event_date=year_events[-1].event_date,
            )
        )
    return MemberTimelineIndexPayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        latest_snapshot_date=latest_snapshot.snapshot_date,
        page_size=page_size,
        total_pages=total_pages,
        total_events=total_events,
        latest_event_id=latest_event.event_id if latest_event is not None else None,
        latest_event_date=latest_event.event_date if latest_event is not None else None,
        earliest_event_date=earliest_event.event_date if earliest_event is not None else None,
        available_years=years,
        year_buckets=year_buckets,
    )


def build_member_timeline_page(
    history: MemberHistoryPayload,
    *,
    page: int,
    page_size: int = 25,
) -> MemberTimelinePagePayload:
    events = build_member_timeline_events(history)
    return _timeline_page_from_events(history, events, page=page, page_size=page_size)


def build_member_timeline_index(
    history: MemberHistoryPayload,
    *,
    page_size: int = 25,
) -> MemberTimelineIndexPayload:
    events = build_member_timeline_events(history)
    return _timeline_index_from_events(history, events, page_size=page_size)


def build_member_timeline_year(
    history: MemberHistoryPayload,
    *,
    year: int,
    page_size: int = 25,
) -> MemberTimelineYearPayload:
    timeline_index = build_member_timeline_index(history, page_size=page_size)
    year_bucket = next(
        (bucket for bucket in timeline_index.year_buckets if bucket.year == year),
        None,
    )
    if year_bucket is None:
        raise ValueError(f"year {year} is not available for member timeline")
    timeline_page = build_member_timeline_page(
        history,
        page=year_bucket.start_page,
        page_size=page_size,
    )
    return MemberTimelineYearPayload(
        year=year,
        timeline_index=timeline_index,
        year_bucket=year_bucket,
        timeline_page=timeline_page,
    )


def build_member_timeline_dimension(
    history: MemberHistoryPayload,
    *,
    dimension: str,
    page_size: int = 25,
) -> MemberTimelineDimensionPayload:
    events = [
        event for event in build_member_timeline_events(history) if event.dimension == dimension
    ]
    if not events:
        raise ValueError(f"dimension {dimension} is not available for member timeline")
    return MemberTimelineDimensionPayload(
        dimension=dimension,
        timeline_index=_timeline_index_from_events(history, events, page_size=page_size),
        timeline_page=_timeline_page_from_events(history, events, page=1, page_size=page_size),
    )


def build_history_coverage(
    snapshot_compare_entries: list[tuple[str, dt.date]],
    histories: list[MemberHistoryPayload],
) -> HistoryCoveragePayload:
    if not snapshot_compare_entries:
        raise ValueError("snapshot_compare_entries must not be empty")
    ordered_entries = sorted(snapshot_compare_entries, key=lambda entry: (entry[1], entry[0]))
    earliest_snapshot_id, earliest_snapshot_date = ordered_entries[0]
    latest_snapshot_id, latest_snapshot_date = ordered_entries[-1]
    available_years = sorted({entry[1].year for entry in ordered_entries})
    total_events = sum(len(history.events) for history in histories)
    dimension_member_counts: dict[str, set[str]] = {}
    dimension_event_counts: dict[str, int] = {}
    dimension_years: dict[str, set[int]] = {}
    for history in histories:
        member_dimensions: set[str] = set()
        for event in build_member_timeline_events(history):
            dimension_event_counts[event.dimension] = (
                dimension_event_counts.get(event.dimension, 0) + 1
            )
            dimension_years.setdefault(event.dimension, set()).add(event.event_date.year)
            if event.dimension not in member_dimensions:
                dimension_member_counts.setdefault(event.dimension, set()).add(history.bioguide_id)
                member_dimensions.add(event.dimension)
    return HistoryCoveragePayload(
        earliest_snapshot_id=earliest_snapshot_id,
        earliest_snapshot_date=earliest_snapshot_date,
        latest_snapshot_id=latest_snapshot_id,
        latest_snapshot_date=latest_snapshot_date,
        snapshot_count=len(ordered_entries),
        member_history_count=len(histories),
        total_events=total_events,
        available_years=available_years,
        dimensions=[
            HistoryCoverageDimensionPayload(
                dimension=dimension,
                member_history_count=len(dimension_member_counts[dimension]),
                total_events=dimension_event_counts[dimension],
                available_years=sorted(dimension_years.get(dimension, set())),
            )
            for dimension in sorted(
                dimension_event_counts,
                key=lambda dimension: (-dimension_event_counts[dimension], dimension),
            )
        ],
    )


def build_member_change_summary(
    history: MemberHistoryPayload,
    *,
    dimension_limit: int = 5,
    event_limit: int = 10,
) -> MemberChangeSummaryPayload:
    latest, previous = _snapshot_pair(history)
    latest_events = _recent_events(history, latest.snapshot_date, limit=event_limit)
    event_counts: dict[str, int] = {}
    for event in latest_events:
        event_counts[event.dimension] = event_counts.get(event.dimension, 0) + 1

    dimension_changes: list[DimensionChangeSummary] = []
    if previous is not None:
        all_dimensions = sorted(set(latest.dimension_scores) | set(previous.dimension_scores))
        for dimension in all_dimensions:
            current_score = float(latest.dimension_scores.get(dimension, 0.0))
            previous_score = float(previous.dimension_scores.get(dimension, 0.0))
            score_delta = current_score - previous_score
            if score_delta == 0:
                continue
            dimension_changes.append(
                DimensionChangeSummary(
                    dimension=dimension,
                    current_score=current_score,
                    previous_score=previous_score,
                    score_delta=score_delta,
                    abs_delta=abs(score_delta),
                    event_count=event_counts.get(dimension, 0),
                )
            )
        dimension_changes.sort(key=lambda summary: (-summary.abs_delta, summary.dimension))

    return MemberChangeSummaryPayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        latest_snapshot_date=latest.snapshot_date,
        previous_snapshot_date=previous.snapshot_date if previous is not None else None,
        latest_score_total=latest.score_total,
        previous_score_total=previous.score_total if previous is not None else None,
        score_total_delta=latest.score_total_delta,
        top_dimension_changes=dimension_changes[:dimension_limit],
        recent_events=latest_events,
        top_evidence_card_ids=_top_evidence_card_ids(latest_events),
    )


def build_member_history_coverage(
    history: MemberHistoryPayload,
) -> MemberHistoryCoveragePayload:
    ordered_snapshots = _ordered_snapshots(history)
    if not ordered_snapshots:
        raise ValueError("history must contain at least one snapshot")
    events = build_member_timeline_events(history)
    years = sorted({snapshot.snapshot_date.year for snapshot in ordered_snapshots}, reverse=True)
    year_counts = {
        year: sum(1 for event in events if event.event_date.year == year) for year in years
    }
    dimension_counts: dict[str, int] = {}
    for event in events:
        dimension_counts[event.dimension] = dimension_counts.get(event.dimension, 0) + 1
    trend_summary = build_member_trend_summary(history)
    latest_event = events[0] if events else None
    earliest_event = events[-1] if events else None
    return MemberHistoryCoveragePayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        earliest_snapshot_date=ordered_snapshots[0].snapshot_date,
        latest_snapshot_date=ordered_snapshots[-1].snapshot_date,
        latest_event_date=latest_event.event_date if latest_event is not None else None,
        earliest_event_date=earliest_event.event_date if earliest_event is not None else None,
        snapshot_count=len(ordered_snapshots),
        total_events=len(events),
        available_years=years,
        years=[
            MemberHistoryCoverageYearPayload(year=year, event_count=year_counts[year])
            for year in years
        ],
        dimensions=[
            MemberHistoryCoverageDimensionPayload(
                dimension=dimension,
                event_count=dimension_counts[dimension],
            )
            for dimension in sorted(
                dimension_counts,
                key=lambda dimension: (-dimension_counts[dimension], dimension),
            )
        ],
        windows=[
            MemberHistoryCoverageWindowPayload(
                window_key=window.window_key,
                requested_days=window.requested_days,
                has_full_window=window.has_full_window,
                start_snapshot_date=window.start_snapshot_date,
                end_snapshot_date=window.end_snapshot_date,
            )
            for window in trend_summary.windows
        ],
    )


def build_member_history_coverage_index(
    coverages: list[MemberHistoryCoveragePayload],
) -> MemberHistoryCoverageIndexPayload:
    members: list[MemberHistoryCoverageIndexEntryPayload] = []
    for coverage in sorted(coverages, key=lambda payload: payload.slug):
        windows = {window.window_key: window for window in coverage.windows}
        full_4w = windows["4w"].has_full_window if "4w" in windows else False
        full_12w = windows["12w"].has_full_window if "12w" in windows else False
        full_cycle = windows["cycle"].has_full_window if "cycle" in windows else False
        members.append(
            MemberHistoryCoverageIndexEntryPayload(
                bioguide_id=coverage.bioguide_id,
                name=coverage.name,
                slug=coverage.slug,
                state=coverage.state,
                district=coverage.district,
                chamber=coverage.chamber,
                party=coverage.party,
                earliest_snapshot_date=coverage.earliest_snapshot_date,
                latest_snapshot_date=coverage.latest_snapshot_date,
                latest_event_date=coverage.latest_event_date,
                snapshot_count=coverage.snapshot_count,
                total_events=coverage.total_events,
                available_years=coverage.available_years,
                has_full_4w=full_4w,
                has_full_12w=full_12w,
                has_full_cycle=full_cycle,
            )
        )
    return MemberHistoryCoverageIndexPayload(
        total_members=len(members),
        members=members,
    )


def _member_meta(
    histories: list[MemberHistoryPayload],
) -> dict[str, dict[str, str]]:
    return {
        history.bioguide_id: {
            "chamber": history.chamber,
            "party": history.party,
            "state": history.state,
        }
        for history in histories
    }


def _snapshot_on_or_before(
    history: MemberHistoryPayload,
    snapshot_date: dt.date,
) -> MemberHistorySnapshot | None:
    candidates = [
        snapshot
        for snapshot in _ordered_snapshots(history)
        if snapshot.snapshot_date <= snapshot_date
    ]
    return candidates[-1] if candidates else None


def _window_baseline_snapshot(
    history: MemberHistoryPayload,
    *,
    end_snapshot_date: dt.date,
    requested_days: int | None,
) -> tuple[MemberHistorySnapshot | None, bool]:
    if requested_days is None:
        ordered = _ordered_snapshots(history)
        if not ordered:
            return None, False
        return ordered[0], True

    target_date = end_snapshot_date - dt.timedelta(days=requested_days)
    baseline = _snapshot_on_or_before(history, target_date)
    if baseline is not None:
        return baseline, True

    ordered = _ordered_snapshots(history)
    if not ordered:
        return None, False
    return ordered[0], False


def _movement_events(
    histories: list[MemberHistoryPayload],
    *,
    latest_snapshot_date: dt.date,
) -> list[FeedEvent]:
    events: list[FeedEvent] = []
    for history in histories:
        for event in _recent_events(history, latest_snapshot_date, limit=len(history.events)):
            discriminator = event.evidence_card_id or event.rule_id
            occurred_at = (
                event.fired_at.date() if event.fired_at is not None else latest_snapshot_date
            )
            events.append(
                FeedEvent(
                    feed_event_id=make_feed_event_id(
                        FeedEventKind.RULE_FIRE,
                        history.bioguide_id,
                        event.dimension,
                        latest_snapshot_date,
                        discriminator,
                    ),
                    kind=FeedEventKind.RULE_FIRE,
                    member_bioguide_id=history.bioguide_id,
                    member_name=history.name,
                    member_slug=history.slug,
                    dimension=event.dimension,
                    score_delta=event.score_delta,
                    abs_delta=abs(event.score_delta),
                    short_explanation=event.short_explanation,
                    evidence_card_id=event.evidence_card_id,
                    snapshot_date=latest_snapshot_date,
                    occurred_at=occurred_at,
                )
            )
    return events


def _window_movement_events(
    histories: list[MemberHistoryPayload],
    *,
    start_snapshot_date: dt.date,
    end_snapshot_date: dt.date,
) -> list[FeedEvent]:
    events: list[FeedEvent] = []
    for history in histories:
        for event in _window_events(
            history,
            start_snapshot_date=start_snapshot_date,
            end_snapshot_date=end_snapshot_date,
        ):
            discriminator = event.evidence_card_id or event.rule_id
            occurred_at = (
                event.fired_at.date()
                if event.fired_at is not None
                else event.snapshot_date or end_snapshot_date
            )
            events.append(
                FeedEvent(
                    feed_event_id=make_feed_event_id(
                        FeedEventKind.RULE_FIRE,
                        history.bioguide_id,
                        event.dimension,
                        event.snapshot_date or end_snapshot_date,
                        discriminator,
                    ),
                    kind=FeedEventKind.RULE_FIRE,
                    member_bioguide_id=history.bioguide_id,
                    member_name=history.name,
                    member_slug=history.slug,
                    dimension=event.dimension,
                    score_delta=event.score_delta,
                    abs_delta=abs(event.score_delta),
                    short_explanation=event.short_explanation,
                    evidence_card_id=event.evidence_card_id,
                    snapshot_date=event.snapshot_date or end_snapshot_date,
                    occurred_at=occurred_at,
                )
            )
    return events


def build_member_window_change_summary(
    history: MemberHistoryPayload,
    *,
    start_snapshot_date: dt.date,
    end_snapshot_date: dt.date,
    dimension_limit: int = 5,
    event_limit: int = 10,
) -> MemberChangeSummaryPayload | None:
    end_snapshot = _snapshot_on_or_before(history, end_snapshot_date)
    if end_snapshot is None:
        return None
    start_snapshot = _snapshot_on_or_before(history, start_snapshot_date)
    window_events = _window_events(
        history,
        start_snapshot_date=start_snapshot_date,
        end_snapshot_date=end_snapshot_date,
    )[:event_limit]

    event_counts: dict[str, int] = {}
    for event in window_events:
        event_counts[event.dimension] = event_counts.get(event.dimension, 0) + 1

    baseline_scores = start_snapshot.dimension_scores if start_snapshot is not None else {}
    all_dimensions = sorted(set(end_snapshot.dimension_scores) | set(baseline_scores))
    dimension_changes: list[DimensionChangeSummary] = []
    for dimension in all_dimensions:
        current_score = float(end_snapshot.dimension_scores.get(dimension, 0.0))
        previous_score = (
            float(baseline_scores.get(dimension, 0.0)) if start_snapshot is not None else None
        )
        score_delta = (
            current_score - previous_score if previous_score is not None else current_score
        )
        if score_delta == 0 and event_counts.get(dimension, 0) == 0:
            continue
        dimension_changes.append(
            DimensionChangeSummary(
                dimension=dimension,
                current_score=current_score,
                previous_score=previous_score,
                score_delta=score_delta,
                abs_delta=abs(score_delta),
                event_count=event_counts.get(dimension, 0),
            )
        )
    dimension_changes.sort(key=lambda summary: (-summary.abs_delta, summary.dimension))

    previous_total = start_snapshot.score_total if start_snapshot is not None else None
    score_total_delta = (
        end_snapshot.score_total - previous_total
        if previous_total is not None
        else end_snapshot.score_total
    )

    return MemberChangeSummaryPayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        latest_snapshot_date=end_snapshot.snapshot_date,
        previous_snapshot_date=start_snapshot.snapshot_date if start_snapshot is not None else None,
        latest_score_total=end_snapshot.score_total,
        previous_score_total=previous_total,
        score_total_delta=score_total_delta,
        top_dimension_changes=dimension_changes[:dimension_limit],
        recent_events=window_events,
        top_evidence_card_ids=_top_evidence_card_ids(window_events),
    )


def build_member_trend_summary(
    history: MemberHistoryPayload,
    *,
    end_snapshot_date: dt.date | None = None,
) -> MemberTrendSummaryPayload:
    latest_snapshot = (
        _snapshot_on_or_before(history, end_snapshot_date)
        if end_snapshot_date is not None
        else _ordered_snapshots(history)[-1]
    )
    if latest_snapshot is None:
        raise ValueError("history must contain at least one snapshot")

    window_specs: list[tuple[str, str, int | None]] = [
        ("4w", "Last 4 weeks", 28),
        ("12w", "Last 12 weeks", 84),
        ("cycle", "Cycle to date", None),
    ]
    windows: list[MemberTrendWindowPayload] = []
    for window_key, label, requested_days in window_specs:
        baseline, has_full_window = _window_baseline_snapshot(
            history,
            end_snapshot_date=latest_snapshot.snapshot_date,
            requested_days=requested_days,
        )
        start_snapshot_date = baseline.snapshot_date if baseline is not None else None
        summary = build_member_window_change_summary(
            history,
            start_snapshot_date=start_snapshot_date or latest_snapshot.snapshot_date,
            end_snapshot_date=latest_snapshot.snapshot_date,
        )
        if summary is None:
            continue
        windows.append(
            MemberTrendWindowPayload(
                window_key=window_key,  # type: ignore[arg-type]
                label=label,
                requested_days=requested_days,
                has_full_window=has_full_window,
                start_snapshot_date=start_snapshot_date,
                end_snapshot_date=latest_snapshot.snapshot_date,
                current_score_total=summary.latest_score_total,
                previous_score_total=summary.previous_score_total,
                score_total_delta=summary.score_total_delta or 0.0,
                top_dimension_changes=summary.top_dimension_changes,
                recent_event_count=len(summary.recent_events),
                top_evidence_card_ids=summary.top_evidence_card_ids,
            )
        )

    return MemberTrendSummaryPayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        latest_snapshot_date=latest_snapshot.snapshot_date,
        windows=windows,
    )


def build_member_history_chart(
    history: MemberHistoryPayload,
    *,
    snapshot_ids_by_date: dict[dt.date, str],
) -> MemberHistoryChartPayload:
    ordered = _ordered_snapshots(history)
    if not ordered:
        raise ValueError("history must contain at least one snapshot")

    missing_dates = [
        snapshot.snapshot_date
        for snapshot in ordered
        if snapshot.snapshot_date not in snapshot_ids_by_date
    ]
    if missing_dates:
        raise ValueError(
            f"missing snapshot ids for history dates: {', '.join(sorted(date.isoformat() for date in missing_dates))}"
        )

    event_counts: dict[dt.date, int] = {}
    for event in history.events:
        if event.snapshot_date is None:
            continue
        event_counts[event.snapshot_date] = event_counts.get(event.snapshot_date, 0) + 1

    latest_snapshot = ordered[-1]
    latest_snapshot_id = snapshot_ids_by_date[latest_snapshot.snapshot_date]
    points = [
        MemberHistoryChartPoint(
            snapshot_id=snapshot_ids_by_date[snapshot.snapshot_date],
            snapshot_date=snapshot.snapshot_date,
            score_total=snapshot.score_total,
            score_total_delta=snapshot.score_total_delta,
            event_count=event_counts.get(snapshot.snapshot_date, 0),
        )
        for snapshot in ordered
    ]

    compare_presets: list[MemberHistoryComparePreset] = []
    if len(ordered) > 1:
        previous_snapshot = ordered[-2]
        latest_change = build_member_window_change_summary(
            history,
            start_snapshot_date=previous_snapshot.snapshot_date,
            end_snapshot_date=latest_snapshot.snapshot_date,
        )
        if latest_change is not None:
            compare_presets.append(
                MemberHistoryComparePreset(
                    preset_key="latest",
                    label="Latest change",
                    start_snapshot_id=snapshot_ids_by_date[previous_snapshot.snapshot_date],
                    start_snapshot_date=previous_snapshot.snapshot_date,
                    end_snapshot_id=latest_snapshot_id,
                    end_snapshot_date=latest_snapshot.snapshot_date,
                    has_full_window=True,
                    score_total_delta=latest_change.score_total_delta or 0.0,
                    top_evidence_card_ids=latest_change.top_evidence_card_ids,
                )
            )

    trend_summary = build_member_trend_summary(history)
    for window in trend_summary.windows:
        if window.start_snapshot_date is None:
            continue
        start_snapshot_id = snapshot_ids_by_date.get(window.start_snapshot_date)
        if start_snapshot_id is None:
            continue
        compare_presets.append(
            MemberHistoryComparePreset(
                preset_key=window.window_key,
                label=window.label,
                start_snapshot_id=start_snapshot_id,
                start_snapshot_date=window.start_snapshot_date,
                end_snapshot_id=latest_snapshot_id,
                end_snapshot_date=window.end_snapshot_date,
                has_full_window=window.has_full_window,
                score_total_delta=window.score_total_delta,
                top_evidence_card_ids=window.top_evidence_card_ids,
            )
        )

    default_preset_key = "cycle"
    for candidate in ("4w", "12w", "latest", "cycle"):
        if any(preset.preset_key == candidate for preset in compare_presets):
            default_preset_key = candidate
            break

    return MemberHistoryChartPayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        latest_snapshot_id=latest_snapshot_id,
        latest_snapshot_date=latest_snapshot.snapshot_date,
        default_preset_key=default_preset_key,  # type: ignore[arg-type]
        points=points,
        compare_presets=compare_presets,
    )


def _snapshot_entry_on_or_before(
    snapshot_entries: list[tuple[str, dt.date]],
    target_date: dt.date,
) -> tuple[str, dt.date] | None:
    for snapshot_id, snapshot_date in reversed(snapshot_entries):
        if snapshot_date <= target_date:
            return snapshot_id, snapshot_date
    return None


def build_snapshot_compare_presets(
    snapshot_entries: list[tuple[str, dt.date]],
) -> SnapshotComparePresetSetPayload:
    if not snapshot_entries:
        raise ValueError("snapshot_entries must not be empty")

    ordered = sorted(snapshot_entries, key=lambda entry: (entry[1], entry[0]))
    latest_snapshot_id, latest_snapshot_date = ordered[-1]
    presets: list[SnapshotComparePresetPayload] = []

    if len(ordered) > 1:
        previous_snapshot_id, previous_snapshot_date = ordered[-2]
        presets.append(
            SnapshotComparePresetPayload(
                preset_key="latest",
                label="Latest change",
                start_snapshot_id=previous_snapshot_id,
                start_snapshot_date=previous_snapshot_date,
                end_snapshot_id=latest_snapshot_id,
                end_snapshot_date=latest_snapshot_date,
                has_full_window=True,
            )
        )

    for preset_key, label, requested_days in (
        ("4w", "Last 4 weeks", 28),
        ("12w", "Last 12 weeks", 84),
        ("cycle", "Cycle to date", None),
    ):
        if requested_days is None:
            start_snapshot_id, start_snapshot_date = ordered[0]
            has_full_window = True
        else:
            target_date = latest_snapshot_date - dt.timedelta(days=requested_days)
            baseline = _snapshot_entry_on_or_before(ordered, target_date)
            if baseline is None:
                start_snapshot_id, start_snapshot_date = ordered[0]
                has_full_window = False
            else:
                start_snapshot_id, start_snapshot_date = baseline
                has_full_window = True

        presets.append(
            SnapshotComparePresetPayload(
                preset_key=preset_key,  # type: ignore[arg-type]
                label=label,
                start_snapshot_id=start_snapshot_id,
                start_snapshot_date=start_snapshot_date,
                end_snapshot_id=latest_snapshot_id,
                end_snapshot_date=latest_snapshot_date,
                has_full_window=has_full_window,
            )
        )

    four_week_preset = next(
        (preset for preset in presets if preset.preset_key == "4w"),
        None,
    )
    latest_preset = next(
        (preset for preset in presets if preset.preset_key == "latest"),
        None,
    )
    if four_week_preset is not None and four_week_preset.has_full_window:
        default_preset_key = "4w"
    elif latest_preset is not None:
        default_preset_key = "latest"
    elif any(preset.preset_key == "12w" for preset in presets):
        default_preset_key = "12w"
    else:
        default_preset_key = "cycle"

    return SnapshotComparePresetSetPayload(
        default_preset_key=default_preset_key,  # type: ignore[arg-type]
        presets=presets,
    )


def build_latest_movement_window(
    histories: list[MemberHistoryPayload],
    *,
    latest_snapshot_id: str,
    latest_snapshot_date: dt.date,
    previous_snapshot_id: str | None = None,
    previous_snapshot_date: dt.date | None = None,
    dimension: str | None = None,
    top_n: int = 10,
    recent_n: int = 20,
) -> MovementWindowPayload:
    return build_movement_window(
        histories,
        window_key="latest",
        latest_snapshot_id=latest_snapshot_id,
        latest_snapshot_date=latest_snapshot_date,
        previous_snapshot_id=previous_snapshot_id,
        previous_snapshot_date=previous_snapshot_date,
        has_full_window=True,
        dimension=dimension,
        top_n=top_n,
        recent_n=recent_n,
    )


def build_movement_window(
    histories: list[MemberHistoryPayload],
    *,
    window_key: str,
    latest_snapshot_id: str,
    latest_snapshot_date: dt.date,
    previous_snapshot_id: str | None = None,
    previous_snapshot_date: dt.date | None = None,
    has_full_window: bool = True,
    dimension: str | None = None,
    top_n: int = 10,
    recent_n: int = 20,
) -> MovementWindowPayload:
    if window_key == "latest":
        events = _movement_events(histories, latest_snapshot_date=latest_snapshot_date)
    else:
        if previous_snapshot_date is None:
            raise ValueError("previous_snapshot_date is required for non-latest movement windows")
        events = _window_movement_events(
            histories,
            start_snapshot_date=previous_snapshot_date,
            end_snapshot_date=latest_snapshot_date,
        )

    feed = build_homepage_feed(
        events,
        _member_meta(histories),
        snapshot_date=latest_snapshot_date,
        top_n=top_n,
        recent_n=recent_n,
        dimension=dimension,
    )
    return MovementWindowPayload(
        window_key=window_key,
        dimension=dimension,
        has_full_window=has_full_window,
        latest_snapshot_id=latest_snapshot_id,
        latest_snapshot_date=latest_snapshot_date,
        previous_snapshot_id=previous_snapshot_id,
        previous_snapshot_date=previous_snapshot_date,
        top_changes=feed.top_changes,
        recent_events=feed.recent_events,
        recent_evidence_card_ids=feed.recent_evidence_card_ids,
    )


def build_snapshot_compare_payload(
    histories: list[MemberHistoryPayload],
    *,
    start_snapshot_id: str,
    start_snapshot_date: dt.date,
    end_snapshot_id: str,
    end_snapshot_date: dt.date,
    top_n: int = 10,
    recent_n: int = 20,
    featured_n: int = 5,
) -> SnapshotComparePayload:
    events = _window_movement_events(
        histories,
        start_snapshot_date=start_snapshot_date,
        end_snapshot_date=end_snapshot_date,
    )
    feed = build_homepage_feed(
        events,
        _member_meta(histories),
        snapshot_date=end_snapshot_date,
        top_n=top_n,
        recent_n=recent_n,
    )
    summaries_by_slug: dict[str, MemberChangeSummaryPayload] = {}
    for history in histories:
        summary = build_member_window_change_summary(
            history,
            start_snapshot_date=start_snapshot_date,
            end_snapshot_date=end_snapshot_date,
        )
        if summary is None:
            continue
        summaries_by_slug[summary.slug] = summary
    featured_member_changes = [
        summaries_by_slug[change.slug]
        for change in feed.top_changes
        if change.slug in summaries_by_slug
    ][:featured_n]
    return SnapshotComparePayload(
        start_snapshot_id=start_snapshot_id,
        start_snapshot_date=start_snapshot_date,
        end_snapshot_id=end_snapshot_id,
        end_snapshot_date=end_snapshot_date,
        top_changes=feed.top_changes,
        recent_events=feed.recent_events,
        recent_evidence_card_ids=feed.recent_evidence_card_ids,
        featured_member_changes=featured_member_changes,
    )
