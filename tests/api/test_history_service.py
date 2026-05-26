from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import (
    get_history_bootstrap,
    get_history_backfill_bootstrap,
    get_history_backfill_report,
    get_history_event,
    get_history_event_page,
    get_member_change_summary,
    get_member_history_coverage,
    get_member_history_coverage_index,
    get_member_history,
    get_member_timeline_dimension,
    get_member_timeline_index,
    get_member_timeline_page,
    get_member_timeline_year,
    get_movement_feed,
    get_movement_window,
    get_snapshot_index,
)
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.contracts import MemberHistoryPayload
from src.export.filesystem import write_planned_files
from src.export.manifest import SnapshotManifest
from src.export.writer import PlannedFile, history_bootstrap_path, snapshot_index_path
from src.export.writer import manifest_path
from src.pipeline.history_aggregate_run import write_history_aggregate
from src.runtime.history_backfill import history_backfill_report_path
from src.runtime.history_backfill_types import (
    HistoryBackfillAggregatePayload,
    HistoryBackfillAttemptPayload,
    HistoryBackfillDateWindowPayload,
    HistoryBackfillReportPayload,
)
from tests.support.published_snapshot_fixtures import (
    PublishedSnapshotBuilder,
    make_member_history,
    make_snapshot,
)


def _write_history_backfill_report(root: Path) -> None:
    report = HistoryBackfillReportPayload(
        congress=119,
        cadence="weekly:monday",
        target_root=str(root),
        report_path=str(history_backfill_report_path(root)),
        overwrite=False,
        continue_on_error=True,
        date_window=HistoryBackfillDateWindowPayload(
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 13),
            bounded_by_today=True,
        ),
        planned_count=2,
        attempted_count=2,
        completed_count=1,
        skipped_count=1,
        failed_count=0,
        remaining_count=0,
        aggregate_source_count=2,
        attempts=[
            HistoryBackfillAttemptPayload(
                snapshot_id="2025-01-06",
                snapshot_date=dt.date(2025, 1, 6),
                publish_root=str(root / "2025-01-06"),
                status="completed",
                ok=True,
            ),
            HistoryBackfillAttemptPayload(
                snapshot_id="2025-01-13",
                snapshot_date=dt.date(2025, 1, 13),
                publish_root=str(root / "2025-01-13"),
                status="skipped_existing",
                reason="existing_manifest",
                ok=None,
            ),
        ],
        aggregate=HistoryBackfillAggregatePayload(
            latest_snapshot_id="2025-01-13",
            snapshot_count=2,
            member_history_count=4,
            target_root=str(root / "aggregate"),
        ),
    )
    report_path = history_backfill_report_path(root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )


def test_get_member_history_returns_published_history(tmp_path: Path) -> None:
    PublishedSnapshotBuilder(tmp_path).with_member_histories([make_member_history()]).build()

    result = get_member_history("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.snapshots[0].snapshot_date.isoformat() == "2026-01-01"


def test_get_member_history_returns_not_found_for_missing_member(tmp_path: Path) -> None:
    PublishedSnapshotBuilder(tmp_path).build()

    result = get_member_history("ghost-member", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member_history"
    assert result.identifier == "ghost-member"


def _history(snapshot_date: dt.date, *, score_total: float) -> MemberHistoryPayload:
    published_at = dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC)
    payload = make_member_history(
        snapshot_date=snapshot_date,
        published_at=published_at,
        fired_at=published_at,
    )
    snapshot = payload.snapshots[0].model_copy(
        update={
            "score_total": score_total,
            "score_total_delta": score_total - 5.0,
        }
    )
    return payload.model_copy(update={"snapshots": [snapshot]})


def _write_conflicting_homepage_feed(root: Path) -> None:
    homepage_file = root / HOMEPAGE_FEED_PATH
    homepage_file.parent.mkdir(parents=True, exist_ok=True)
    homepage_file.write_text(
        json.dumps(
            {
                "snapshot_date": "1999-01-01",
                "top_changes": [],
                "recent_events": [],
                "recent_evidence_card_ids": ["stale-card"],
            }
        ),
        encoding="utf-8",
    )


def test_get_member_change_summary_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_change_summary("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.latest_snapshot_date.isoformat() == "2026-01-01"
    assert result.data.top_evidence_card_ids == ["ec-0001"]


def test_get_member_timeline_index_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_index("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.total_events == 1
    assert result.data.total_pages == 1
    assert result.data.latest_event_id is not None
    assert result.data.earliest_event_date.isoformat() == "2026-01-01"
    assert [
        (bucket.year, bucket.event_count, bucket.start_page, bucket.end_page)
        for bucket in result.data.year_buckets
    ] == [(2026, 1, 1, 1)]


def test_get_member_history_coverage_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_history_coverage("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.snapshot_count == 1
    assert result.data.total_events == 1
    assert result.data.available_years == [2026]
    assert result.data.dimensions[0].dimension == "conflict_of_interest_risk"


def test_get_member_history_coverage_index_builds_from_member_histories_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_history_coverage_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.total_members == 1
    assert result.data.members[0].slug == "nancy-pelosi"
    assert result.data.members[0].has_full_cycle is True


def test_get_member_timeline_page_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_page("nancy-pelosi", page=1, snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.page == 1
    assert result.data.total_events == 1
    assert result.data.events[0].event_id.startswith("he-")
    assert result.data.events[0].evidence_card_id == "ec-0001"


def test_get_member_timeline_year_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_year("nancy-pelosi", 2026, snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.year == 2026
    assert result.data.year_bucket.year == 2026
    assert result.data.timeline_page.page == 1


def test_get_member_timeline_dimension_builds_from_member_history_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_dimension(
        "nancy-pelosi",
        "conflict_of_interest_risk",
        snapshot_root=tmp_path,
    )

    assert result.ok is True
    assert result.data.dimension == "conflict_of_interest_risk"
    assert result.data.timeline_index.total_events == 1
    assert result.data.timeline_page.page == 1


def test_get_history_event_finds_event_from_member_histories(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])
    timeline = get_member_timeline_page("nancy-pelosi", page=1, snapshot_root=tmp_path)
    assert timeline.ok is True
    event_id = timeline.data.events[0].event_id

    result = get_history_event(event_id, snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.event_id == event_id
    assert result.data.slug == "nancy-pelosi"


def test_get_history_event_page_builds_from_member_histories_when_artifact_missing(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])
    timeline = get_member_timeline_page("nancy-pelosi", page=1, snapshot_root=tmp_path)
    assert timeline.ok is True
    event_id = timeline.data.events[0].event_id

    result = get_history_event_page(event_id, snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.event.event_id == event_id
    assert result.data.member_page is not None
    assert result.data.member_page.profile.slug == "nancy-pelosi"


def test_get_history_backfill_report_returns_typed_report_payload(tmp_path: Path) -> None:
    _write_history_backfill_report(tmp_path)

    result = get_history_backfill_report(target_root=tmp_path)

    assert result.ok is True
    assert result.data.congress == 119
    assert result.data.completed_count == 1
    assert result.data.aggregate is not None
    assert result.data.aggregate.member_history_count == 4
    assert result.meta.snapshot_date.isoformat() == "2025-01-13"


def test_get_history_backfill_bootstrap_combines_report_with_aggregate_coverage(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2025-01-06"
    second_root = tmp_path / "2025-01-13"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2025-01-06",
        member_histories=[_history(dt.date(2025, 1, 6), score_total=10.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2025-01-13",
        member_histories=[_history(dt.date(2025, 1, 13), score_total=15.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    _write_history_backfill_report(tmp_path)
    report = json.loads(history_backfill_report_path(tmp_path).read_text(encoding="utf-8"))
    report["aggregate"]["target_root"] = str(aggregate_root)
    report["aggregate"]["verify"] = {
        "ok": True,
        "total_checked": 10,
        "total_errors": 0,
        "total_warnings": 0,
        "stages": [],
    }
    history_backfill_report_path(tmp_path).write_text(
        json.dumps(report, sort_keys=True),
        encoding="utf-8",
    )

    result = get_history_backfill_bootstrap(target_root=tmp_path)

    assert result.ok is True
    assert result.data.congress == 119
    assert result.data.latest_snapshot_id == "2025-01-13"
    assert result.data.coverage is not None
    assert result.data.coverage.snapshot_count == 2
    assert result.data.member_coverage_index is not None
    assert result.data.member_coverage_index.total_members == 1
    assert result.data.member_coverage_index.members[0].slug == "nancy-pelosi"
    assert result.data.readiness_status == "ready"
    assert result.data.readiness_score == 100
    assert result.data.blockers == []
    assert result.data.warnings == []


def test_get_history_backfill_bootstrap_marks_blocked_when_failures_or_verify_errors_exist(
    tmp_path: Path,
) -> None:
    _write_history_backfill_report(tmp_path)
    report = json.loads(history_backfill_report_path(tmp_path).read_text(encoding="utf-8"))
    report["failed_count"] = 1
    report["attempts"].append(
        {
            "snapshot_id": "2025-01-20",
            "snapshot_date": "2025-01-20",
            "publish_root": str(tmp_path / "2025-01-20"),
            "status": "failed",
            "reason": "oracle_stage_failure",
            "error": "verify failed",
            "ok": False,
        }
    )
    report["aggregate_error"] = "aggregate root missing"
    history_backfill_report_path(tmp_path).write_text(
        json.dumps(report, sort_keys=True),
        encoding="utf-8",
    )

    result = get_history_backfill_bootstrap(target_root=tmp_path)

    assert result.ok is True
    assert result.data.readiness_status == "blocked"
    assert result.data.readiness_score < 100
    assert any("failed snapshots" in blocker for blocker in result.data.blockers)
    assert any("aggregate error" in blocker for blocker in result.data.blockers)


def test_get_history_backfill_bootstrap_supports_dimension_scope(tmp_path: Path) -> None:
    first_root = tmp_path / "2025-01-06"
    second_root = tmp_path / "2025-01-13"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2025-01-06",
        member_histories=[_history(dt.date(2025, 1, 6), score_total=10.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2025-01-13",
        member_histories=[_history(dt.date(2025, 1, 13), score_total=15.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    _write_history_backfill_report(tmp_path)
    report = json.loads(history_backfill_report_path(tmp_path).read_text(encoding="utf-8"))
    report["aggregate"]["target_root"] = str(aggregate_root)
    history_backfill_report_path(tmp_path).write_text(
        json.dumps(report, sort_keys=True),
        encoding="utf-8",
    )

    result = get_history_backfill_bootstrap(
        target_root=tmp_path,
        dimension="conflict_of_interest_risk",
    )

    assert result.ok is True
    assert result.data.dimension == "conflict_of_interest_risk"
    assert result.data.dimension_coverage is not None
    assert result.data.dimension_coverage.dimension == "conflict_of_interest_risk"


def test_get_history_backfill_bootstrap_returns_not_found_for_unknown_dimension(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2025-01-06"
    second_root = tmp_path / "2025-01-13"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2025-01-06",
        member_histories=[_history(dt.date(2025, 1, 6), score_total=10.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2025-01-13",
        member_histories=[_history(dt.date(2025, 1, 13), score_total=15.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    _write_history_backfill_report(tmp_path)
    report = json.loads(history_backfill_report_path(tmp_path).read_text(encoding="utf-8"))
    report["aggregate"]["target_root"] = str(aggregate_root)
    history_backfill_report_path(tmp_path).write_text(
        json.dumps(report, sort_keys=True),
        encoding="utf-8",
    )

    result = get_history_backfill_bootstrap(
        target_root=tmp_path,
        dimension="transparency_risk",
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "history_dimension"
    assert result.identifier == "transparency_risk"


def test_get_movement_window_builds_from_aggregate_histories_when_artifact_missing(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    (aggregate_root / "history" / "movement" / "latest.json").unlink()

    result = get_movement_window(snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.latest_snapshot_id == "2026-01-08"
    assert result.data.previous_snapshot_id == "2026-01-01"
    assert result.data.top_changes[0].slug == "nancy-pelosi"


def test_get_movement_window_builds_requested_preset_from_aggregate_histories_when_artifact_missing(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    (aggregate_root / "history" / "movement" / "4w.json").unlink()

    result = get_movement_window(name="4w", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.window_key == "4w"
    assert result.data.has_full_window is False
    assert result.data.latest_snapshot_id == "2026-01-08"
    assert result.data.previous_snapshot_id == "2026-01-01"
    assert result.data.top_changes[0].slug == "nancy-pelosi"


def test_get_movement_window_supports_dimension_filter(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    first_history = _history(dt.date(2026, 1, 1), score_total=40.0)
    base_history = _history(dt.date(2026, 1, 8), score_total=55.0)
    second_history = base_history.model_copy(
        update={
            "snapshots": [
                base_history.snapshots[0].model_copy(
                    update={
                        "dimension_scores": {
                            "conflict_of_interest_risk": 45.0,
                            "transparency_risk": 10.0,
                        }
                    }
                )
            ],
            "events": [
                base_history.events[0].model_copy(update={"dimension": "transparency_risk"})
            ],
        }
    )
    make_snapshot(first_root, snapshot_id="2026-01-01", member_histories=[first_history])
    make_snapshot(second_root, snapshot_id="2026-01-08", member_histories=[second_history])
    write_history_aggregate([first_root, second_root], aggregate_root)

    result = get_movement_window(
        snapshot_root=aggregate_root,
        dimension="transparency_risk",
    )

    assert result.ok is True
    assert result.data.dimension == "transparency_risk"
    assert [change.dimension for change in result.data.top_changes] == ["transparency_risk"]


def test_get_movement_window_returns_not_found_for_empty_dimension_window(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)

    result = get_movement_window(
        snapshot_root=aggregate_root,
        dimension="transparency_risk",
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "movement_window"


def test_get_movement_feed_prefers_precomputed_movement_window_over_homepage_feed(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    _write_conflicting_homepage_feed(aggregate_root)

    result = get_movement_feed(snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.snapshot_date == dt.date(2026, 1, 8)
    assert result.data.top_changes[0].slug == "nancy-pelosi"
    assert result.data.recent_evidence_card_ids == ["ec-0001"]


def test_get_movement_feed_rejects_stale_homepage_feed_fallback(
    tmp_path: Path,
) -> None:
    make_snapshot(
        tmp_path,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    _write_conflicting_homepage_feed(tmp_path)

    result = get_movement_feed(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "movement_feed"
    assert result.identifier == "current"


def test_get_history_bootstrap_ignores_stale_precomputed_bootstrap(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    artifact_path = aggregate_root / history_bootstrap_path()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["snapshot_index"]["latest_snapshot_id"] = "stale-snapshot"
    artifact_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = get_history_bootstrap(snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.snapshot_index.latest_snapshot_id == "2026-01-08"


def test_get_history_bootstrap_dimension_featured_members_come_from_visible_changes(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    artifact_path = aggregate_root / history_bootstrap_path()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["featured_member_changes"] = []
    artifact_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = get_history_bootstrap(
        snapshot_root=aggregate_root,
        dimension="conflict_of_interest_risk",
    )

    assert result.ok is True
    assert [summary.slug for summary in result.data.featured_member_changes] == ["nancy-pelosi"]


def test_get_movement_window_prefers_precomputed_movement_window_over_homepage_feed(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[_history(dt.date(2026, 1, 1), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[_history(dt.date(2026, 1, 8), score_total=55.0)],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    _write_conflicting_homepage_feed(aggregate_root)

    result = get_movement_window(snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.latest_snapshot_id == "2026-01-08"
    assert result.data.latest_snapshot_date == dt.date(2026, 1, 8)
    assert result.data.top_changes[0].slug == "nancy-pelosi"
    assert result.data.recent_evidence_card_ids == ["ec-0001"]


def test_get_snapshot_index_returns_all_snapshot_manifests(tmp_path: Path) -> None:
    PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
    PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-02-01").build()

    result = get_snapshot_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert [entry.snapshot_id for entry in result.data.snapshots] == [
        "2026-01-01",
        "2026-02-01",
    ]
    assert result.data.latest_snapshot_id == "2026-02-01"


def test_get_snapshot_index_prefers_precomputed_artifact_when_present(tmp_path: Path) -> None:
    PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
    PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-02-01").build()
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

    result = get_snapshot_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshots[0].root_sha256 == "a" * 64
    assert result.data.snapshots[1].root_sha256 == "b" * 64


def test_get_snapshot_index_fallback_orders_by_snapshot_date_not_snapshot_id(
    tmp_path: Path,
) -> None:
    write_planned_files(
        [
            PlannedFile.from_bytes(
                manifest_path("z-early"),
                json.dumps(
                    SnapshotManifest(
                        snapshot_id="z-early",
                        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                        root_sha256="a" * 64,
                        entries=[],
                        total_files=0,
                        total_bytes=0,
                    ).model_dump(mode="json"),
                    sort_keys=True,
                ).encode("utf-8"),
            ),
            PlannedFile.from_bytes(
                manifest_path("a-late"),
                json.dumps(
                    SnapshotManifest(
                        snapshot_id="a-late",
                        created_at=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
                        root_sha256="b" * 64,
                        entries=[],
                        total_files=0,
                        total_bytes=0,
                    ).model_dump(mode="json"),
                    sort_keys=True,
                ).encode("utf-8"),
            ),
        ],
        tmp_path,
    )

    result = get_snapshot_index(snapshot_root=tmp_path)

    assert result.ok is True
    assert [entry.snapshot_id for entry in result.data.snapshots] == [
        "z-early",
        "a-late",
    ]
    assert result.data.latest_snapshot_id == "a-late"


def test_src_api_exports_history_helpers() -> None:
    assert api.get_member_change_summary is get_member_change_summary
    assert api.get_member_history is get_member_history
    assert api.get_movement_feed is get_movement_feed
    assert api.get_movement_window is get_movement_window
    assert api.get_snapshot_index is get_snapshot_index
