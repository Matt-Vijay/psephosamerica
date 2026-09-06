from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from src.api.read_service import get_homepage_bootstrap, get_zip_entry
from src.export.filesystem import write_planned_files
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.writer import (
    PlannedFile,
    history_preset_range_path,
    homepage_bootstrap_path,
    member_change_summary_path,
    movement_window_path,
    serialize_payload,
    snapshot_index_path,
    snapshot_preset_compare_path,
    zip_entry_path,
)
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_history,
    make_snapshot,
    make_zip_feed,
)


def _history(
    snapshot_date: dt.date,
    *,
    score_total: float,
    score_total_delta: float | None,
    evidence_card_id: str,
    score_delta: float,
):
    published_at = dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC)
    payload = make_member_history(
        snapshot_date=snapshot_date,
        published_at=published_at,
        fired_at=published_at,
    )
    snapshot = payload.snapshots[0].model_copy(
        update={
            "score_total": score_total,
            "score_total_delta": score_total_delta,
            "dimension_scores": {"conflict_of_interest_risk": score_total},
        }
    )
    event = payload.events[0].model_copy(
        update={
            "evidence_card_id": evidence_card_id,
            "score_delta": score_delta,
        }
    )
    return payload.model_copy(update={"snapshots": [snapshot], "events": [event]})


def _history_root(tmp_path: Path) -> Path:
    snapshot_dates = [
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 15),
        dt.date(2026, 2, 19),
        dt.date(2026, 3, 18),
        dt.date(2026, 4, 15),
    ]
    scores = [10.0, 15.0, 25.0, 35.0, 50.0]
    deltas = [10.0, 5.0, 10.0, 10.0, 15.0]
    root_paths = [tmp_path / snapshot_date.isoformat() for snapshot_date in snapshot_dates]
    aggregate_root = tmp_path / "aggregate"

    for root, snapshot_date, score_total, score_delta in zip(
        root_paths,
        snapshot_dates,
        scores,
        deltas,
        strict=True,
    ):
        evidence_card_id = f"ec-{snapshot_date.isoformat()}"
        make_snapshot(
            root,
            snapshot_id=snapshot_date.isoformat(),
            evidence_cards=[
                make_evidence_card(
                    evidence_card_id=evidence_card_id,
                    snapshot_date=snapshot_date,
                    created_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
                )
            ],
            member_histories=[
                _history(
                    snapshot_date,
                    score_total=score_total,
                    score_total_delta=None if snapshot_date == snapshot_dates[0] else score_delta,
                    evidence_card_id=evidence_card_id,
                    score_delta=score_delta,
                )
            ],
        )

    write_history_aggregate(root_paths, aggregate_root)
    return aggregate_root


def _write_homepage_feed(root: Path, snapshot_date: dt.date) -> None:
    payload = HomepageFeedPayload(
        snapshot_date=snapshot_date,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=15.0,
                abs_delta=15.0,
                event_count=1,
                top_evidence_card_ids=["ec-2026-01-13"],
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-2026-01-13",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=15.0,
                short_explanation="Test event.",
                evidence_card_id="ec-2026-01-13",
                occurred_at=snapshot_date,
            )
        ],
        recent_evidence_card_ids=["ec-2026-01-13"],
    )
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload.model_dump(mode="json")), encoding="utf-8")


def _history_root_with_current_aggregates(tmp_path: Path) -> Path:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    aggregate_root = tmp_path / "aggregate"
    first_date = dt.date(2026, 1, 6)
    second_date = dt.date(2026, 1, 13)

    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[
            _history(
                first_date,
                score_total=40.0,
                score_total_delta=None,
                evidence_card_id="ec-2026-01-06",
                score_delta=5.0,
            )
        ],
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=first_date)],
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-2026-01-06",
                snapshot_date=first_date,
                created_at=dt.datetime.combine(first_date, dt.time.min, tzinfo=dt.UTC),
            )
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[
            _history(
                second_date,
                score_total=55.0,
                score_total_delta=15.0,
                evidence_card_id="ec-2026-01-13",
                score_delta=15.0,
            )
        ],
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=second_date)],
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-2026-01-13",
                snapshot_date=second_date,
                created_at=dt.datetime.combine(second_date, dt.time.min, tzinfo=dt.UTC),
            )
        ],
    )
    _write_homepage_feed(second_root, second_date)

    homepage_bootstrap = get_homepage_bootstrap(snapshot_root=second_root)
    assert getattr(homepage_bootstrap, "ok", False) is True
    zip_entry = get_zip_entry("94102", snapshot_root=second_root)
    assert getattr(zip_entry, "ok", False) is True
    write_planned_files(
        [
            PlannedFile.from_bytes(
                homepage_bootstrap_path(),
                serialize_payload(homepage_bootstrap.data),
            ),
            PlannedFile.from_bytes(
                zip_entry_path("94102"),
                serialize_payload(zip_entry.data),
            ),
        ],
        second_root,
    )

    write_history_aggregate([first_root, second_root], aggregate_root)
    return aggregate_root


def test_verify_history_aggregate_reports_all_stages_ok_for_valid_root(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)

    result = verify_history_aggregate_local(aggregate_root)

    assert result.ok is True
    assert [stage.stage for stage in result.stages] == [
        "snapshot_index",
        "bootstrap",
        "current_aggregates",
        "snapshot_presets",
        "members",
        "member_timelines",
        "member_pages",
    ]
    assert result.total_errors == 0


def test_verify_history_aggregate_flags_missing_snapshot_preset_compare(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / snapshot_preset_compare_path("latest")
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("snapshot_presets")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == snapshot_preset_compare_path("latest") for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_preset_movement_window(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / movement_window_path("4w")
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("snapshot_presets")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == movement_window_path("4w") for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_dimension_movement_window(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / movement_window_path(
        "4w", dimension="conflict_of_interest_risk"
    )
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("snapshot_presets")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path == movement_window_path("4w", dimension="conflict_of_interest_risk")
        for issue in stage.issues
    )


def test_verify_history_aggregate_flags_missing_history_preset_range(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / history_preset_range_path("4w")
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("snapshot_presets")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == history_preset_range_path("4w") for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_history_coverage_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / "history" / "coverage.json"
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("bootstrap")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "history/coverage.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_member_history_coverage_index_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / "history" / "member-coverage" / "index.json"
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("members")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "history/member-coverage/index.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_mismatched_member_change_summary(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    summary_path = aggregate_root / member_change_summary_path("nancy-pelosi")
    payload = json.loads(summary_path.read_text())
    payload["score_total_delta"] = -999.0
    summary_path.write_text(json.dumps(payload, sort_keys=True))

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("members")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == member_change_summary_path("nancy-pelosi") for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_zip_entry_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    (aggregate_root / zip_entry_path("94102")).unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == zip_entry_path("94102") for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_current_member_lookup_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    (aggregate_root / "identity" / "current-member-lookup.json").unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "identity/current-member-lookup.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_stale_homepage_bootstrap_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    artifact_path = aggregate_root / homepage_bootstrap_path()
    payload = json.loads(artifact_path.read_text())
    payload["snapshot"]["snapshot_id"] = "stale-snapshot"
    payload["snapshot"]["root_sha256"] = "f" * 64
    artifact_path.write_text(json.dumps(payload, sort_keys=True))

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == homepage_bootstrap_path() for issue in stage.issues)


def test_verify_history_aggregate_rejects_invalid_snapshot_index_root_hash(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    artifact_path = aggregate_root / snapshot_index_path()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["snapshots"][0]["root_sha256"] = "z" * 64
    artifact_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("snapshot_index")
    assert stage is not None
    assert stage.ok is False
    assert any("root_sha256" in issue.message and "hex" in issue.message for issue in stage.issues)
    assert not any("root_sha256 mismatch" in issue.message for issue in stage.issues)


def test_verify_history_aggregate_flags_stale_current_member_lookup_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    artifact_path = aggregate_root / "identity" / "current-member-lookup.json"
    payload = json.loads(artifact_path.read_text())
    payload["m"][0]["q"] = "stale lookup name"
    artifact_path.write_text(json.dumps(payload, sort_keys=True))

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "identity/current-member-lookup.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_stale_current_member_page_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    artifact_path = aggregate_root / "member-pages" / "nancy-pelosi.json"
    payload = json.loads(artifact_path.read_text())
    payload["profile"]["name"] = "Stale Member Name"
    artifact_path.write_text(json.dumps(payload, sort_keys=True))

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "member-pages/nancy-pelosi.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_current_member_page_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root_with_current_aggregates(tmp_path)
    (aggregate_root / "member-pages" / "nancy-pelosi.json").unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("current_aggregates")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "member-pages/nancy-pelosi.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_member_timeline_index_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / "history" / "member-timelines" / "nancy-pelosi" / "index.json"
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path == "history/member-timelines/nancy-pelosi/index.json" for issue in stage.issues
    )


def test_verify_history_aggregate_flags_missing_member_history_coverage_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = aggregate_root / "history" / "member-coverage" / "nancy-pelosi.json"
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_pages")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == "history/member-coverage/nancy-pelosi.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_member_timeline_year_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = (
        aggregate_root / "history" / "member-timelines" / "nancy-pelosi" / "years" / "2026.json"
    )
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path == "history/member-timelines/nancy-pelosi/years/2026.json"
        for issue in stage.issues
    )


def test_verify_history_aggregate_flags_missing_member_timeline_dimension_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    missing_path = (
        aggregate_root
        / "history"
        / "member-timelines"
        / "nancy-pelosi"
        / "dimensions"
        / "conflict_of_interest_risk.json"
    )
    missing_path.unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path
        == "history/member-timelines/nancy-pelosi/dimensions/conflict_of_interest_risk.json"
        for issue in stage.issues
    )


def test_verify_history_aggregate_flags_unexpected_member_timeline_dimension_artifact(
    tmp_path: Path,
) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    extra_path = (
        aggregate_root
        / "history"
        / "member-timelines"
        / "nancy-pelosi"
        / "dimensions"
        / "stale_dimension.json"
    )
    extra_path.write_text("{}")

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path == "history/member-timelines/nancy-pelosi/dimensions/stale_dimension.json"
        for issue in stage.issues
    )


def test_verify_history_aggregate_flags_stale_member_timeline_page_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    artifact_path = (
        aggregate_root / "history" / "member-timelines" / "nancy-pelosi" / "pages" / "1.json"
    )
    payload = json.loads(artifact_path.read_text())
    payload["events"][0]["short_explanation"] = "stale timeline event"
    artifact_path.write_text(json.dumps(payload, sort_keys=True))

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(
        issue.path == "history/member-timelines/nancy-pelosi/pages/1.json" for issue in stage.issues
    )


def test_verify_history_aggregate_flags_missing_history_event_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    timeline_page_path = (
        aggregate_root / "history" / "member-timelines" / "nancy-pelosi" / "pages" / "1.json"
    )
    timeline_page = json.loads(timeline_page_path.read_text())
    event_id = timeline_page["events"][0]["event_id"]
    (aggregate_root / "history" / "events" / f"{event_id}.json").unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == f"history/events/{event_id}.json" for issue in stage.issues)


def test_verify_history_aggregate_flags_missing_history_event_page_artifact(tmp_path: Path) -> None:
    from src.runtime.history_verify import verify_history_aggregate_local

    aggregate_root = _history_root(tmp_path)
    timeline_page_path = (
        aggregate_root / "history" / "member-timelines" / "nancy-pelosi" / "pages" / "1.json"
    )
    timeline_page = json.loads(timeline_page_path.read_text())
    event_id = timeline_page["events"][0]["event_id"]
    (aggregate_root / "history" / "event-pages" / f"{event_id}.json").unlink()

    result = verify_history_aggregate_local(aggregate_root)

    stage = result.stage_result("member_timelines")
    assert stage is not None
    assert stage.ok is False
    assert any(issue.path == f"history/event-pages/{event_id}.json" for issue in stage.issues)
