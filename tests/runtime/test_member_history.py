from __future__ import annotations

import json
from pathlib import Path

import src.runtime as runtime
from src.api.contracts import (
    HistoryBootstrapPayload,
    HistoryEventPagePayload,
    HistoryPresetRangePayload,
    MemberHistoryPagePayload,
    MemberPagePayload,
)
from src.export.contracts import (
    DimensionChangeSummary,
    HistoryCoveragePayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryChartPoint,
    MemberHistoryComparePreset,
    MemberHistoryCoverageIndexPayload,
    MemberHistoryCoveragePayload,
    MemberTimelineDimensionPayload,
    MemberTimelineEventPayload,
    MemberTimelineIndexPayload,
    MemberTimelinePagePayload,
    MemberTimelineYearPayload,
    MemberTrendSummaryPayload,
    MemberTrendWindowPayload,
    SnapshotComparePresetPayload,
)
from src.export.filesystem import write_planned_files
from src.export.writer import (
    PlannedFile,
    history_bootstrap_path,
    history_coverage_path,
    history_event_page_path,
    history_event_path,
    history_preset_range_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_coverage_index_path,
    member_history_coverage_path,
    member_history_page_path,
    member_preset_compare_path,
    member_timeline_dimension_path,
    member_timeline_index_path,
    member_timeline_page_path,
    member_timeline_year_path,
    member_trend_summary_path,
    movement_window_path,
    snapshot_index_path,
    snapshot_preset_compare_path,
)
from src.homepage.contracts import (
    MemberMovementSummary,
    MovementWindowPayload,
    RecentEventSummary,
    SnapshotComparePayload,
)
from src.runtime.inspect import (
    load_local_history_bootstrap,
    load_local_history_coverage,
    load_local_history_event,
    load_local_history_event_page,
    load_local_history_preset_range,
    load_local_member_change_summary,
    load_local_member_history,
    load_local_member_history_chart,
    load_local_member_history_coverage,
    load_local_member_history_coverage_index,
    load_local_member_history_page,
    load_local_member_page,
    load_local_member_preset_compare,
    load_local_member_timeline_dimension,
    load_local_member_timeline_index,
    load_local_member_timeline_page,
    load_local_member_timeline_year,
    load_local_member_trend_summary,
    load_local_movement_window,
    load_local_snapshot_index,
    load_local_snapshot_preset_compare,
)
from tests.support.published_snapshot_fixtures import make_member_history, make_snapshot


def test_load_local_member_history_roundtrip(tmp_path: Path) -> None:
    history = make_member_history()
    make_snapshot(tmp_path, member_histories=[history])

    result = load_local_member_history("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.snapshots[0].snapshot_date.isoformat() == "2026-01-01"


def test_runtime_exports_member_history_loader() -> None:
    assert runtime.load_local_member_history is load_local_member_history


def test_load_local_member_change_summary_roundtrip(tmp_path: Path) -> None:
    payload = MemberChangeSummaryPayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        latest_snapshot_date="2026-02-01",
        previous_snapshot_date="2026-01-01",
        latest_score_total=15.0,
        previous_score_total=5.0,
        score_total_delta=10.0,
        top_dimension_changes=[
            DimensionChangeSummary(
                dimension="conflict_of_interest_risk",
                current_score=15.0,
                previous_score=5.0,
                score_delta=10.0,
                abs_delta=10.0,
                event_count=1,
            )
        ],
        recent_events=[],
        top_evidence_card_ids=["ec-0001"],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_change_summary_path(payload.slug),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_change_summary("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.latest_score_total == 15.0
    assert result.top_dimension_changes[0].score_delta == 10.0


def test_runtime_exports_member_change_summary_loader() -> None:
    assert runtime.load_local_member_change_summary is load_local_member_change_summary


def test_load_local_member_history_coverage_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryCoveragePayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        earliest_snapshot_date="2026-01-01",
        latest_snapshot_date="2026-02-01",
        latest_event_date="2026-02-01",
        earliest_event_date="2026-01-01",
        snapshot_count=2,
        total_events=3,
        available_years=[2026],
        years=[{"year": 2026, "event_count": 3}],
        dimensions=[{"dimension": "conflict_of_interest_risk", "event_count": 3}],
        windows=[
            {
                "window_key": "4w",
                "requested_days": 28,
                "has_full_window": True,
                "start_snapshot_date": "2026-01-01",
                "end_snapshot_date": "2026-02-01",
            }
        ],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_history_coverage_path(payload.slug),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_history_coverage("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.snapshot_count == 2
    assert result.dimensions[0].dimension == "conflict_of_interest_risk"


def test_runtime_exports_member_history_coverage_loader() -> None:
    assert runtime.load_local_member_history_coverage is load_local_member_history_coverage


def test_load_local_member_history_coverage_index_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryCoverageIndexPayload(
        total_members=1,
        members=[
            {
                "bioguide_id": "P000197",
                "name": "Nancy Pelosi",
                "slug": "nancy-pelosi",
                "state": "CA",
                "district": "11",
                "chamber": "house",
                "party": "Democrat",
                "earliest_snapshot_date": "2026-01-01",
                "latest_snapshot_date": "2026-02-01",
                "latest_event_date": "2026-02-01",
                "snapshot_count": 2,
                "total_events": 3,
                "available_years": [2026],
                "has_full_4w": True,
                "has_full_12w": False,
                "has_full_cycle": True,
            }
        ],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_history_coverage_index_path(),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_history_coverage_index(snapshot_root=tmp_path)

    assert result.total_members == 1
    assert result.members[0].slug == "nancy-pelosi"


def test_runtime_exports_member_history_coverage_index_loader() -> None:
    assert (
        runtime.load_local_member_history_coverage_index is load_local_member_history_coverage_index
    )


def test_load_local_member_trend_summary_roundtrip(tmp_path: Path) -> None:
    payload = MemberTrendSummaryPayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        latest_snapshot_date="2026-02-01",
        windows=[
            MemberTrendWindowPayload(
                window_key="4w",
                label="Last 4 weeks",
                requested_days=28,
                has_full_window=True,
                start_snapshot_date="2026-01-04",
                end_snapshot_date="2026-02-01",
                current_score_total=15.0,
                previous_score_total=5.0,
                score_total_delta=10.0,
                top_dimension_changes=[],
                recent_event_count=2,
                top_evidence_card_ids=["ec-0001"],
            )
        ],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_trend_summary_path(payload.slug),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_trend_summary("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.windows[0].window_key == "4w"
    assert result.windows[0].score_total_delta == 10.0


def test_runtime_exports_member_trend_summary_loader() -> None:
    assert runtime.load_local_member_trend_summary is load_local_member_trend_summary


def test_load_local_member_history_chart_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryChartPayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        latest_snapshot_id="2026-02-01",
        latest_snapshot_date="2026-02-01",
        default_preset_key="cycle",
        points=[
            MemberHistoryChartPoint(
                snapshot_id="2026-02-01",
                snapshot_date="2026-02-01",
                score_total=15.0,
                score_total_delta=10.0,
                event_count=2,
            )
        ],
        compare_presets=[
            MemberHistoryComparePreset(
                preset_key="cycle",
                label="Cycle to date",
                start_snapshot_id="2026-01-01",
                start_snapshot_date="2026-01-01",
                end_snapshot_id="2026-02-01",
                end_snapshot_date="2026-02-01",
                has_full_window=True,
                score_total_delta=10.0,
                top_evidence_card_ids=["ec-0001"],
            )
        ],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_history_chart_path(payload.slug),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_history_chart("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.default_preset_key == "cycle"
    assert result.points[0].snapshot_id == "2026-02-01"
    assert result.compare_presets[0].preset_key == "cycle"


def test_runtime_exports_member_history_chart_loader() -> None:
    assert runtime.load_local_member_history_chart is load_local_member_history_chart


def test_load_local_member_timeline_index_roundtrip(tmp_path: Path) -> None:
    payload = MemberTimelineIndexPayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        latest_snapshot_date="2026-02-01",
        page_size=25,
        total_pages=1,
        total_events=1,
        latest_event_id="he-test",
        latest_event_date="2026-02-01",
        earliest_event_date="2026-02-01",
        available_years=[2026],
        year_buckets=[
            {
                "year": 2026,
                "event_count": 1,
                "start_page": 1,
                "end_page": 1,
                "latest_event_date": "2026-02-01",
                "earliest_event_date": "2026-02-01",
            }
        ],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_timeline_index_path(payload.slug),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_timeline_index("nancy-pelosi", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.latest_event_id == "he-test"


def test_runtime_exports_member_timeline_index_loader() -> None:
    assert runtime.load_local_member_timeline_index is load_local_member_timeline_index


def test_load_local_member_timeline_year_roundtrip(tmp_path: Path) -> None:
    payload = MemberTimelineYearPayload(
        year=2026,
        timeline_index=MemberTimelineIndexPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_date="2026-02-01",
            page_size=25,
            total_pages=1,
            total_events=1,
            latest_event_id="he-test",
            latest_event_date="2026-02-01",
            earliest_event_date="2026-02-01",
            available_years=[2026],
            year_buckets=[
                {
                    "year": 2026,
                    "event_count": 1,
                    "start_page": 1,
                    "end_page": 1,
                    "latest_event_date": "2026-02-01",
                    "earliest_event_date": "2026-02-01",
                }
            ],
        ),
        year_bucket={
            "year": 2026,
            "event_count": 1,
            "start_page": 1,
            "end_page": 1,
            "latest_event_date": "2026-02-01",
            "earliest_event_date": "2026-02-01",
        },
        timeline_page=MemberTimelinePagePayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            page=1,
            page_size=25,
            total_pages=1,
            total_events=1,
            next_page=None,
            previous_page=None,
            events=[],
        ),
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_timeline_year_path("nancy-pelosi", 2026),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_timeline_year("nancy-pelosi", 2026, snapshot_root=tmp_path)

    assert result.year == 2026
    assert result.timeline_page.page == 1


def test_runtime_exports_member_timeline_year_loader() -> None:
    assert runtime.load_local_member_timeline_year is load_local_member_timeline_year


def test_load_local_member_timeline_dimension_roundtrip(tmp_path: Path) -> None:
    payload = MemberTimelineDimensionPayload(
        dimension="conflict_of_interest_risk",
        timeline_index=MemberTimelineIndexPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_date="2026-02-01",
            page_size=25,
            total_pages=1,
            total_events=1,
            latest_event_id="he-test",
            latest_event_date="2026-02-01",
            earliest_event_date="2026-02-01",
            available_years=[2026],
            year_buckets=[
                {
                    "year": 2026,
                    "event_count": 1,
                    "start_page": 1,
                    "end_page": 1,
                    "latest_event_date": "2026-02-01",
                    "earliest_event_date": "2026-02-01",
                }
            ],
        ),
        timeline_page=MemberTimelinePagePayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            page=1,
            page_size=25,
            total_pages=1,
            total_events=1,
            next_page=None,
            previous_page=None,
            events=[],
        ),
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_timeline_dimension_path("nancy-pelosi", "conflict_of_interest_risk"),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_timeline_dimension(
        "nancy-pelosi",
        "conflict_of_interest_risk",
        snapshot_root=tmp_path,
    )

    assert result.dimension == "conflict_of_interest_risk"
    assert result.timeline_page.page == 1


def test_runtime_exports_member_timeline_dimension_loader() -> None:
    assert runtime.load_local_member_timeline_dimension is load_local_member_timeline_dimension


def test_load_local_member_timeline_page_roundtrip(tmp_path: Path) -> None:
    event = MemberTimelineEventPayload(
        event_id="he-test",
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        event_date="2026-02-01",
        snapshot_date="2026-02-01",
        fired_at="2026-02-01T00:00:00Z",
        rule_id="committee_sector_trade.v1",
        dimension="conflict_of_interest_risk",
        severity="high",
        evidence_card_id="ec-0001",
        short_explanation="Test event.",
        score_delta=10.0,
    )
    payload = MemberTimelinePagePayload(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        page=1,
        page_size=25,
        total_pages=1,
        total_events=1,
        next_page=None,
        previous_page=None,
        events=[event],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_timeline_page_path(payload.slug, payload.page),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            ),
            PlannedFile.from_bytes(
                history_event_path(event.event_id),
                json.dumps(event.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            ),
        ],
        tmp_path,
    )

    page = load_local_member_timeline_page("nancy-pelosi", 1, snapshot_root=tmp_path)
    event_result = load_local_history_event("he-test", snapshot_root=tmp_path)

    assert page.slug == "nancy-pelosi"
    assert page.events[0].event_id == "he-test"
    assert event_result.event_id == "he-test"
    assert event_result.slug == "nancy-pelosi"


def test_runtime_exports_member_timeline_page_loader() -> None:
    assert runtime.load_local_member_timeline_page is load_local_member_timeline_page


def test_runtime_exports_history_event_loader() -> None:
    assert runtime.load_local_history_event is load_local_history_event


def test_load_local_history_event_page_roundtrip(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    event = MemberTimelineEventPayload(
        event_id="he-test",
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        state="CA",
        district="11",
        chamber="house",
        party="Democrat",
        event_date="2026-02-01",
        snapshot_date="2026-02-01",
        fired_at="2026-02-01T00:00:00Z",
        rule_id="committee_sector_trade.v1",
        dimension="conflict_of_interest_risk",
        severity="high",
        evidence_card_id="ec-0001",
        short_explanation="Test event.",
        score_delta=10.0,
    )
    payload = HistoryEventPagePayload(
        event=event,
        member_page=load_local_member_page("nancy-pelosi", snapshot_root=tmp_path),
        evidence_card=None,
        previous_event_id=None,
        next_event_id=None,
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                history_event_page_path(event.event_id),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_history_event_page("he-test", snapshot_root=tmp_path)

    assert result.event.event_id == "he-test"
    assert result.member_page is not None
    assert result.member_page.profile.slug == "nancy-pelosi"


def test_runtime_exports_history_event_page_loader() -> None:
    assert runtime.load_local_history_event_page is load_local_history_event_page


def test_load_local_history_coverage_roundtrip(tmp_path: Path) -> None:
    payload = HistoryCoveragePayload(
        earliest_snapshot_id="2026-01-01",
        earliest_snapshot_date="2026-01-01",
        latest_snapshot_id="2026-02-01",
        latest_snapshot_date="2026-02-01",
        snapshot_count=5,
        member_history_count=2,
        total_events=7,
        available_years=[2026],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                history_coverage_path(),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_history_coverage(snapshot_root=tmp_path)

    assert result.latest_snapshot_id == "2026-02-01"
    assert result.total_events == 7


def test_runtime_exports_history_coverage_loader() -> None:
    assert runtime.load_local_history_coverage is load_local_history_coverage


def test_load_local_member_preset_compare_roundtrip(tmp_path: Path) -> None:
    payload = {
        "bioguide_id": "P000197",
        "name": "Nancy Pelosi",
        "slug": "nancy-pelosi",
        "state": "CA",
        "district": "11",
        "chamber": "house",
        "party": "Democrat",
        "start_snapshot_id": "2026-01-01",
        "start_snapshot_date": "2026-01-01",
        "end_snapshot_id": "2026-02-01",
        "end_snapshot_date": "2026-02-01",
        "summary": MemberChangeSummaryPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_date="2026-02-01",
            previous_snapshot_date="2026-01-01",
            latest_score_total=15.0,
            previous_score_total=5.0,
            score_total_delta=10.0,
            top_dimension_changes=[],
            recent_events=[],
            top_evidence_card_ids=["ec-0001"],
        ).model_dump(mode="json"),
        "evidence_cards": [],
    }
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_preset_compare_path("nancy-pelosi", "cycle"),
                json.dumps(payload, sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_preset_compare("nancy-pelosi", "cycle", snapshot_root=tmp_path)

    assert result.slug == "nancy-pelosi"
    assert result.end_snapshot_id == "2026-02-01"
    assert result.summary.score_total_delta == 10.0


def test_runtime_exports_member_preset_compare_loader() -> None:
    assert runtime.load_local_member_preset_compare is load_local_member_preset_compare


def test_load_local_snapshot_preset_compare_roundtrip(tmp_path: Path) -> None:
    payload = SnapshotComparePayload(
        start_snapshot_id="2026-01-01",
        start_snapshot_date="2026-01-01",
        end_snapshot_id="2026-02-01",
        end_snapshot_date="2026-02-01",
        top_changes=[],
        recent_events=[],
        recent_evidence_card_ids=[],
        featured_member_changes=[],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                snapshot_preset_compare_path("latest"),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_snapshot_preset_compare("latest", snapshot_root=tmp_path)

    assert result.start_snapshot_id == "2026-01-01"
    assert result.end_snapshot_id == "2026-02-01"


def test_runtime_exports_snapshot_preset_compare_loader() -> None:
    assert runtime.load_local_snapshot_preset_compare is load_local_snapshot_preset_compare


def test_load_local_member_history_page_roundtrip(tmp_path: Path) -> None:
    payload = MemberHistoryPagePayload(
        member_page=MemberPagePayload(
            profile=make_member_history().model_dump(mode="json")
            if False
            else {
                "bioguide_id": "P000197",
                "name": "Nancy Pelosi",
                "slug": "nancy-pelosi",
                "state": "CA",
                "district": "11",
                "chamber": "house",
                "party": "Democrat",
                "scores": [],
                "recent_rule_fires": [],
                "top_evidence_card_ids": [],
                "committees": [],
                "total_evidence_cards": 0,
                "snapshot_date": "2026-02-01",
            },
            top_evidence_cards=[],
            recent_evidence_cards=[],
        ),
        history=make_member_history(),
        recent_change=MemberChangeSummaryPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_date="2026-02-01",
            previous_snapshot_date="2026-01-01",
            latest_score_total=15.0,
            previous_score_total=5.0,
            score_total_delta=10.0,
            top_dimension_changes=[],
            recent_events=[],
            top_evidence_card_ids=["ec-0001"],
        ),
        chart=MemberHistoryChartPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_id="2026-02-01",
            latest_snapshot_date="2026-02-01",
            default_preset_key="cycle",
            points=[],
            compare_presets=[],
        ),
        default_window_compare={
            "bioguide_id": "P000197",
            "name": "Nancy Pelosi",
            "slug": "nancy-pelosi",
            "state": "CA",
            "district": "11",
            "chamber": "house",
            "party": "Democrat",
            "start_snapshot_id": "2026-01-01",
            "start_snapshot_date": "2026-01-01",
            "end_snapshot_id": "2026-02-01",
            "end_snapshot_date": "2026-02-01",
            "summary": MemberChangeSummaryPayload(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                state="CA",
                district="11",
                chamber="house",
                party="Democrat",
                latest_snapshot_date="2026-02-01",
                previous_snapshot_date="2026-01-01",
                latest_score_total=15.0,
                previous_score_total=5.0,
                score_total_delta=10.0,
                top_dimension_changes=[],
                recent_events=[],
                top_evidence_card_ids=["ec-0001"],
            ).model_dump(mode="json"),
            "evidence_cards": [],
        },
        trend_summary=MemberTrendSummaryPayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            district="11",
            chamber="house",
            party="Democrat",
            latest_snapshot_date="2026-02-01",
            windows=[],
        ),
        history_evidence_cards=[],
        snapshot_index={
            "latest_snapshot_id": "2026-02-01",
            "snapshots": [
                {
                    "snapshot_id": "2026-02-01",
                    "snapshot_date": "2026-02-01",
                    "published_at": "2026-02-01T00:00:00Z",
                    "root_sha256": "b" * 64,
                    "total_files": 5,
                    "total_bytes": 1400,
                }
            ],
        },
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                member_history_page_path("nancy-pelosi"),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_member_history_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.member_page.profile.slug == "nancy-pelosi"
    assert result.default_window_compare.end_snapshot_id == "2026-02-01"


def test_runtime_exports_member_history_page_loader() -> None:
    assert runtime.load_local_member_history_page is load_local_member_history_page


def test_load_local_history_bootstrap_roundtrip(tmp_path: Path) -> None:
    payload = HistoryBootstrapPayload(
        snapshot_index={
            "latest_snapshot_id": "2026-02-01",
            "snapshots": [
                {
                    "snapshot_id": "2026-02-01",
                    "snapshot_date": "2026-02-01",
                    "published_at": "2026-02-01T00:00:00Z",
                    "root_sha256": "b" * 64,
                    "total_files": 5,
                    "total_bytes": 1400,
                }
            ],
        },
        movement_window=MovementWindowPayload(
            latest_snapshot_id="2026-02-01",
            latest_snapshot_date="2026-02-01",
            previous_snapshot_id="2026-01-01",
            previous_snapshot_date="2026-01-01",
            top_changes=[],
            recent_events=[],
            recent_evidence_card_ids=[],
        ),
        default_compare_preset_key="latest",
        compare_presets=[
            SnapshotComparePresetPayload(
                preset_key="latest",
                label="Latest change",
                start_snapshot_id="2026-01-01",
                start_snapshot_date="2026-01-01",
                end_snapshot_id="2026-02-01",
                end_snapshot_date="2026-02-01",
                has_full_window=True,
            )
        ],
        featured_member_changes=[],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                history_bootstrap_path(),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_history_bootstrap(snapshot_root=tmp_path)

    assert result.snapshot_index.latest_snapshot_id == "2026-02-01"
    assert result.movement_window.latest_snapshot_id == "2026-02-01"
    assert result.default_compare_preset_key == "latest"
    assert result.compare_presets[0].preset_key == "latest"


def test_runtime_exports_history_bootstrap_loader() -> None:
    assert runtime.load_local_history_bootstrap is load_local_history_bootstrap


def test_load_local_history_preset_range_roundtrip(tmp_path: Path) -> None:
    payload = HistoryPresetRangePayload(
        preset=SnapshotComparePresetPayload(
            preset_key="latest",
            label="Latest change",
            start_snapshot_id="2026-01-01",
            start_snapshot_date="2026-01-01",
            end_snapshot_id="2026-02-01",
            end_snapshot_date="2026-02-01",
            has_full_window=True,
        ),
        movement_window=MovementWindowPayload(
            latest_snapshot_id="2026-02-01",
            latest_snapshot_date="2026-02-01",
            previous_snapshot_id="2026-01-01",
            previous_snapshot_date="2026-01-01",
            top_changes=[],
            recent_events=[],
            recent_evidence_card_ids=[],
        ),
        snapshot_compare=SnapshotComparePayload(
            start_snapshot_id="2026-01-01",
            start_snapshot_date="2026-01-01",
            end_snapshot_id="2026-02-01",
            end_snapshot_date="2026-02-01",
            top_changes=[],
            recent_events=[],
            recent_evidence_card_ids=[],
            featured_member_changes=[],
        ),
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                history_preset_range_path("latest"),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_history_preset_range("latest", snapshot_root=tmp_path)

    assert result.preset.preset_key == "latest"
    assert result.movement_window.latest_snapshot_id == "2026-02-01"
    assert result.snapshot_compare.end_snapshot_id == "2026-02-01"


def test_runtime_exports_history_preset_range_loader() -> None:
    assert runtime.load_local_history_preset_range is load_local_history_preset_range


def test_load_local_movement_window_roundtrip(tmp_path: Path) -> None:
    payload = MovementWindowPayload(
        latest_snapshot_id="2026-02-01",
        latest_snapshot_date="2026-02-01",
        previous_snapshot_id="2026-01-01",
        previous_snapshot_date="2026-01-01",
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=-10.0,
                abs_delta=10.0,
                event_count=1,
                top_evidence_card_ids=["ec-0001"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="fr_001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=-10.0,
                short_explanation="Test explanation.",
                evidence_card_id="ec-0001",
                occurred_at="2026-02-01",
            )
        ],
        recent_evidence_card_ids=["ec-0001"],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                movement_window_path(),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_movement_window(snapshot_root=tmp_path)

    assert result.latest_snapshot_id == "2026-02-01"
    assert result.previous_snapshot_id == "2026-01-01"
    assert result.top_changes[0].slug == "nancy-pelosi"


def test_load_local_named_movement_window_roundtrip(tmp_path: Path) -> None:
    payload = MovementWindowPayload(
        window_key="12w",
        has_full_window=False,
        latest_snapshot_id="2026-02-01",
        latest_snapshot_date="2026-02-01",
        previous_snapshot_id="2026-01-01",
        previous_snapshot_date="2026-01-01",
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=-10.0,
                abs_delta=10.0,
                event_count=1,
                top_evidence_card_ids=["ec-0001"],
            )
        ],
        recent_events=[],
        recent_evidence_card_ids=[],
    )
    write_planned_files(
        [
            PlannedFile.from_bytes(
                movement_window_path("12w"),
                json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_movement_window("12w", snapshot_root=tmp_path)

    assert result.window_key == "12w"
    assert result.has_full_window is False
    assert result.latest_snapshot_id == "2026-02-01"


def test_runtime_exports_movement_window_loader() -> None:
    assert runtime.load_local_movement_window is load_local_movement_window


def test_load_local_snapshot_index_roundtrip(tmp_path: Path) -> None:
    write_planned_files(
        [
            PlannedFile.from_bytes(
                snapshot_index_path(),
                json.dumps(
                    {
                        "latest_snapshot_id": "2026-02-01",
                        "snapshots": [
                            {
                                "snapshot_id": "2026-01-01",
                                "snapshot_date": "2026-01-01",
                                "published_at": "2026-01-01T00:00:00Z",
                                "root_sha256": "a" * 64,
                                "total_files": 4,
                                "total_bytes": 1200,
                            },
                            {
                                "snapshot_id": "2026-02-01",
                                "snapshot_date": "2026-02-01",
                                "published_at": "2026-02-01T00:00:00Z",
                                "root_sha256": "b" * 64,
                                "total_files": 5,
                                "total_bytes": 1400,
                            },
                        ],
                    },
                    sort_keys=True,
                ).encode("utf-8"),
            )
        ],
        tmp_path,
    )

    result = load_local_snapshot_index(snapshot_root=tmp_path)

    assert result.latest_snapshot_id == "2026-02-01"
    assert [entry.snapshot_id for entry in result.snapshots] == [
        "2026-01-01",
        "2026-02-01",
    ]


def test_runtime_exports_snapshot_index_loader() -> None:
    assert runtime.load_local_snapshot_index is load_local_snapshot_index
