from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.api.read_service import get_zip_entry
from src.export.filesystem import write_planned_files
from src.export.local_store import (
    list_snapshot_ids,
    load_evidence_card,
    load_history_event,
    load_history_event_page,
    load_history_coverage,
    load_member_change_summary,
    load_member_history,
    load_member_history_coverage,
    load_member_history_coverage_index,
    load_member_history_page,
    load_member_timeline_index,
    load_member_timeline_dimension,
    load_member_timeline_page,
    load_member_page,
    load_member_preset_compare,
    load_member_profile,
    load_movement_window,
    load_snapshot_preset_compare,
    load_snapshot_index,
    load_zip_entry,
)
from src.pipeline.history_aggregate_run import (
    _EvidenceCardResolver,
    build_aggregated_member_histories,
    build_snapshot_index,
    merge_member_history_payloads,
    write_history_aggregate,
)
from src.export.writer import (
    PlannedFile,
    history_preset_range_path,
    history_event_path,
    history_event_page_path,
    history_coverage_path,
    member_change_summary_path,
    member_history_path,
    member_history_coverage_path,
    member_history_coverage_index_path,
    member_history_page_path,
    member_timeline_index_path,
    member_timeline_page_path,
    member_timeline_dimension_path,
    member_timeline_year_path,
    member_page_payload_path,
    member_preset_compare_path,
    movement_window_path,
    serialize_payload,
    snapshot_preset_compare_path,
    zip_entry_path,
    evidence_path,
)
from src.export.contracts import MemberHistoryPayload
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_history,
    make_member_profile,
    make_snapshot,
    make_zip_feed,
)


def _history(
    snapshot_date: dt.date,
    *,
    score_total: float,
    dimension_scores: dict[str, float] | None = None,
    evidence_card_id: str = "ec-0001",
    party: str = "Democrat",
    name: str = "Nancy Pelosi",
) -> MemberHistoryPayload:
    payload = make_member_history(
        name=name,
        snapshot_date=snapshot_date,
        published_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
        fired_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
    )
    snapshot = payload.snapshots[0].model_copy(
        update={
            "score_total": score_total,
            "score_total_delta": score_total - 5.0,
            "dimension_scores": dimension_scores or payload.snapshots[0].dimension_scores,
        }
    )
    return payload.model_copy(
        update={
            "party": party,
            "snapshots": [snapshot],
            "events": [
                event.model_copy(update={"evidence_card_id": evidence_card_id})
                for event in payload.events
            ],
        }
    )


def test_build_snapshot_index_orders_roots_and_tracks_latest(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    make_snapshot(first_root, snapshot_id="2026-01-06")
    make_snapshot(second_root, snapshot_id="2026-01-13")

    result = build_snapshot_index([second_root, first_root])

    assert [entry.snapshot_id for entry in result.snapshots] == [
        "2026-01-06",
        "2026-01-13",
    ]
    assert result.latest_snapshot_id == "2026-01-13"


def test_merge_member_history_payloads_uses_latest_identity_and_combines_snapshots() -> None:
    early = _history(dt.date(2026, 1, 6), score_total=40.0, party="Democrat", name="Nancy Pelosi")
    late = _history(dt.date(2026, 1, 13), score_total=55.0, party="Independent", name="N. Pelosi")

    result = merge_member_history_payloads([early, late])

    assert result.party == "Independent"
    assert result.name == "N. Pelosi"
    assert [snapshot.snapshot_date for snapshot in result.snapshots] == [
        dt.date(2026, 1, 6),
        dt.date(2026, 1, 13),
    ]
    assert [snapshot.score_total for snapshot in result.snapshots] == [40.0, 55.0]
    assert len(result.events) == 2


def test_build_aggregated_member_histories_merges_same_slug_across_roots(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0)],
    )

    histories = build_aggregated_member_histories([first_root, second_root])

    assert len(histories) == 1
    assert [snapshot.snapshot_date for snapshot in histories[0].snapshots] == [
        dt.date(2026, 1, 6),
        dt.date(2026, 1, 13),
    ]


def test_write_history_aggregate_writes_latest_current_state_and_merged_history(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[
            make_member_profile(name="Early Nancy", snapshot_date=dt.date(2026, 1, 6))
        ],
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0, name="Early Nancy")],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_profiles=[
            make_member_profile(name="Latest Nancy", snapshot_date=dt.date(2026, 1, 13))
        ],
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0, name="Latest Nancy")],
    )

    result = write_history_aggregate([first_root, second_root], target_root)

    assert result.latest_snapshot_id == "2026-01-13"
    assert result.member_history_count == 1
    assert list_snapshot_ids(target_root) == ["2026-01-06", "2026-01-13"]
    snapshot_index = load_snapshot_index(target_root)
    assert snapshot_index.latest_snapshot_id == "2026-01-13"
    assert load_member_profile(target_root, "nancy-pelosi").name == "Latest Nancy"
    merged_history = load_member_history(target_root, "nancy-pelosi")
    assert merged_history.name == "Latest Nancy"
    assert [snapshot.snapshot_date for snapshot in merged_history.snapshots] == [
        dt.date(2026, 1, 6),
        dt.date(2026, 1, 13),
    ]


def test_write_history_aggregate_writes_member_change_summaries_and_latest_movement_window(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[
            _history(
                dt.date(2026, 1, 6),
                score_total=40.0,
                dimension_scores={"conflict_of_interest_risk": 5.0},
            )
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[
            _history(
                dt.date(2026, 1, 13),
                score_total=55.0,
                dimension_scores={"conflict_of_interest_risk": 15.0},
            )
        ],
    )

    result = write_history_aggregate([first_root, second_root], target_root)

    assert result.member_change_summary_count == 1
    assert (target_root / member_change_summary_path("nancy-pelosi")).is_file()
    assert (target_root / movement_window_path()).is_file()
    assert (target_root / movement_window_path("4w")).is_file()
    assert (target_root / movement_window_path("12w")).is_file()
    assert (target_root / movement_window_path("cycle")).is_file()
    assert (target_root / history_preset_range_path("latest")).is_file()
    assert (target_root / history_preset_range_path("4w")).is_file()
    assert (target_root / history_preset_range_path("12w")).is_file()
    assert (target_root / history_preset_range_path("cycle")).is_file()

    summary = load_member_change_summary(target_root, "nancy-pelosi")
    assert summary.latest_snapshot_date == dt.date(2026, 1, 13)
    assert summary.previous_snapshot_date == dt.date(2026, 1, 6)
    assert summary.latest_score_total == 55.0
    assert summary.previous_score_total == 40.0
    assert [change.dimension for change in summary.top_dimension_changes] == [
        "conflict_of_interest_risk"
    ]
    assert [change.score_delta for change in summary.top_dimension_changes] == [10.0]

    movement = load_movement_window(target_root)
    assert movement.latest_snapshot_id == "2026-01-13"
    assert movement.previous_snapshot_id == "2026-01-06"
    assert movement.window_key == "latest"
    assert [change.slug for change in movement.top_changes] == ["nancy-pelosi"]
    assert movement.recent_evidence_card_ids == ["ec-0001"]

    cycle_movement = load_movement_window(target_root, "cycle")
    assert cycle_movement.window_key == "cycle"
    assert cycle_movement.previous_snapshot_id == "2026-01-06"
    assert cycle_movement.has_full_window is True


def test_write_history_aggregate_copies_historical_evidence_cards_from_older_roots(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    old_snapshot_date = dt.date(2026, 1, 6)
    latest_snapshot_date = dt.date(2026, 1, 13)
    old_timestamp = dt.datetime.combine(old_snapshot_date, dt.time.min, tzinfo=dt.UTC)
    latest_timestamp = dt.datetime.combine(latest_snapshot_date, dt.time.min, tzinfo=dt.UTC)

    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=old_snapshot_date,
                created_at=old_timestamp,
            )
        ],
        member_histories=[
            _history(
                old_snapshot_date,
                score_total=40.0,
                evidence_card_id="ec-old",
            )
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=latest_snapshot_date,
                created_at=latest_timestamp,
            )
        ],
        member_histories=[
            _history(
                latest_snapshot_date,
                score_total=55.0,
                evidence_card_id="ec-latest",
            )
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    merged_history = load_member_history(target_root, "nancy-pelosi")
    assert [event.evidence_card_id for event in merged_history.events] == [
        "ec-latest",
        "ec-old",
    ]

    copied_latest = load_evidence_card(target_root, "ec-latest")
    copied_old = load_evidence_card(target_root, "ec-old")
    assert copied_latest.snapshot_date == latest_snapshot_date
    assert copied_old.snapshot_date == old_snapshot_date


def test_write_history_aggregate_emits_dimension_movement_windows(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    first_date = dt.date(2026, 1, 6)
    second_date = dt.date(2026, 1, 13)

    first_history = _history(
        first_date,
        score_total=40.0,
        dimension_scores={
            "conflict_of_interest_risk": 30.0,
            "transparency_risk": 10.0,
        },
        evidence_card_id="ec-0001",
    ).model_copy(
        update={
            "events": [
                event.model_copy(update={"dimension": "transparency_risk"})
                for event in _history(
                    first_date,
                    score_total=40.0,
                    dimension_scores={
                        "conflict_of_interest_risk": 30.0,
                        "transparency_risk": 10.0,
                    },
                    evidence_card_id="ec-0001",
                ).events
            ]
        }
    )
    second_history = _history(
        second_date,
        score_total=55.0,
        dimension_scores={
            "conflict_of_interest_risk": 35.0,
            "transparency_risk": 20.0,
        },
        evidence_card_id="ec-0002",
    ).model_copy(
        update={
            "events": [
                event.model_copy(update={"dimension": "transparency_risk"})
                for event in _history(
                    second_date,
                    score_total=55.0,
                    dimension_scores={
                        "conflict_of_interest_risk": 35.0,
                        "transparency_risk": 20.0,
                    },
                    evidence_card_id="ec-0002",
                ).events
            ]
        }
    )

    make_snapshot(first_root, snapshot_id="2026-01-06", member_histories=[first_history])
    make_snapshot(second_root, snapshot_id="2026-01-13", member_histories=[second_history])

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / movement_window_path(dimension="transparency_risk")).is_file()
    assert (target_root / movement_window_path("4w", dimension="transparency_risk")).is_file()
    assert not (target_root / movement_window_path(dimension="ethics_enforcement_risk")).exists()

    movement = load_movement_window(target_root, dimension="transparency_risk")
    assert movement.dimension == "transparency_risk"
    assert [change.dimension for change in movement.top_changes] == ["transparency_risk"]


def test_write_history_aggregate_writes_member_preset_compare_artifacts(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(
                dt.date(2026, 1, 6),
                score_total=40.0,
                evidence_card_id="ec-old",
            )
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(
                dt.date(2026, 1, 13),
                score_total=55.0,
                evidence_card_id="ec-latest",
            )
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_preset_compare_path("nancy-pelosi", "latest")).is_file()
    assert (target_root / member_preset_compare_path("nancy-pelosi", "cycle")).is_file()

    latest_compare = load_member_preset_compare(target_root, "nancy-pelosi", "latest")
    assert latest_compare.start_snapshot_id == "2026-01-06"
    assert latest_compare.end_snapshot_id == "2026-01-13"
    assert latest_compare.summary.score_total_delta == 15.0
    assert [card.evidence_card_id for card in latest_compare.evidence_cards] == [
        "ec-latest",
    ]

    cycle_compare = load_member_preset_compare(target_root, "nancy-pelosi", "cycle")
    assert cycle_compare.summary.score_total_delta == 15.0
    assert [card.evidence_card_id for card in cycle_compare.evidence_cards] == ["ec-latest"]


def test_write_history_aggregate_writes_snapshot_preset_compare_artifacts(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    third_root = tmp_path / "2026-01-20"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0)],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0)],
    )
    make_snapshot(
        third_root,
        snapshot_id="2026-01-20",
        member_histories=[_history(dt.date(2026, 1, 20), score_total=60.0)],
    )

    write_history_aggregate([first_root, second_root, third_root], target_root)

    assert (target_root / snapshot_preset_compare_path("latest")).is_file()
    assert (target_root / snapshot_preset_compare_path("cycle")).is_file()

    latest_compare = load_snapshot_preset_compare(target_root, "latest")
    assert latest_compare.start_snapshot_id == "2026-01-13"
    assert latest_compare.end_snapshot_id == "2026-01-20"
    assert [change.slug for change in latest_compare.top_changes] == ["nancy-pelosi"]

    cycle_compare = load_snapshot_preset_compare(target_root, "cycle")
    assert cycle_compare.start_snapshot_id == "2026-01-06"
    assert cycle_compare.end_snapshot_id == "2026-01-20"
    assert [summary.slug for summary in cycle_compare.featured_member_changes] == ["nancy-pelosi"]


def test_write_history_aggregate_writes_member_history_page_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_history_page_path("nancy-pelosi")).is_file()
    page = load_member_history_page(target_root, "nancy-pelosi")
    assert page.member_page.profile.slug == "nancy-pelosi"
    assert page.recent_change.slug == "nancy-pelosi"
    assert page.default_window_compare.summary.score_total_delta == 15.0
    assert page.coverage is not None
    assert page.coverage.snapshot_count == 2
    assert page.timeline_index is not None
    assert page.timeline_page is not None
    assert page.timeline_index.total_events == 2
    assert [event.evidence_card_id for event in page.timeline_page.events] == [
        "ec-latest",
        "ec-old",
    ]


def test_write_history_aggregate_prunes_stale_generated_files_on_rerun(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    stale_history = _history(
        dt.date(2026, 1, 6),
        score_total=12.0,
        name="Retired Member",
    ).model_copy(
        update={
            "bioguide_id": "R000001",
            "slug": "retired-member",
            "name": "Retired Member",
        }
    )
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[
            make_member_profile(snapshot_date=dt.date(2026, 1, 6)),
            make_member_profile(
                bioguide_id="R000001",
                slug="retired-member",
                name="Retired Member",
                snapshot_date=dt.date(2026, 1, 6),
            ),
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0),
            stale_history,
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0)],
    )

    write_history_aggregate([first_root], target_root)
    assert (target_root / member_history_path("retired-member")).is_file()

    write_history_aggregate([second_root], target_root)

    assert not (target_root / member_history_path("retired-member")).exists()
    assert not (target_root / member_history_page_path("retired-member")).exists()
    assert not (target_root / member_page_payload_path("retired-member")).exists()


def test_write_history_aggregate_rejects_missing_referenced_evidence(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "2026-01-06"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        source_root,
        snapshot_id="2026-01-06",
        evidence_cards=[],
        member_histories=[
            _history(
                dt.date(2026, 1, 6),
                score_total=40.0,
                evidence_card_id="missing-evidence-card",
            )
        ],
    )

    with pytest.raises(ValueError, match="missing evidence card"):
        write_history_aggregate([source_root], target_root)


def test_evidence_resolver_copy_planned_rejects_invalid_evidence_sidecar(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "2026-01-06"
    make_snapshot(source_root, snapshot_id="2026-01-06")
    evidence_file = source_root / evidence_path("ec-0001")
    payload = json.loads(evidence_file.read_text(encoding="utf-8"))
    payload["source_anchors"][0]["url"] = None
    evidence_file.write_text(json.dumps(payload), encoding="utf-8")

    resolver = _EvidenceCardResolver([source_root])

    with pytest.raises(ValueError, match="financial_disclosure.*fd-001"):
        resolver.copy_planned(["ec-0001"], existing_paths=set())


def test_write_history_aggregate_writes_member_history_coverage_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_history_coverage_path("nancy-pelosi")).is_file()
    coverage = load_member_history_coverage(target_root, "nancy-pelosi")
    assert coverage.slug == "nancy-pelosi"
    assert coverage.snapshot_count == 2
    assert coverage.total_events == 2


def test_write_history_aggregate_writes_member_history_coverage_index_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_history_coverage_index_path()).is_file()
    coverage_index = load_member_history_coverage_index(target_root)
    assert coverage_index.total_members == 1
    assert coverage_index.members[0].slug == "nancy-pelosi"


def test_write_history_aggregate_writes_member_timeline_artifacts(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_timeline_index_path("nancy-pelosi")).is_file()
    assert (target_root / member_timeline_page_path("nancy-pelosi", 1)).is_file()
    assert (
        target_root / member_timeline_dimension_path("nancy-pelosi", "conflict_of_interest_risk")
    ).is_file()
    assert (target_root / member_timeline_year_path("nancy-pelosi", 2026)).is_file()

    timeline_index = load_member_timeline_index(target_root, "nancy-pelosi")
    assert timeline_index.total_events == 2
    assert timeline_index.total_pages == 1
    assert timeline_index.latest_event_id is not None

    timeline_page = load_member_timeline_page(target_root, "nancy-pelosi", 1)
    assert [event.evidence_card_id for event in timeline_page.events] == [
        "ec-latest",
        "ec-old",
    ]
    assert (target_root / history_event_path(timeline_page.events[0].event_id)).is_file()

    event_payload = load_history_event(target_root, timeline_page.events[0].event_id)
    assert event_payload.event_id == timeline_page.events[0].event_id
    assert event_payload.slug == "nancy-pelosi"


def test_write_history_aggregate_writes_member_timeline_dimension_artifacts_per_member(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"

    def _grassley_history(
        snapshot_date: dt.date,
        *,
        score_total: float,
        evidence_card_id: str,
    ) -> MemberHistoryPayload:
        payload = make_member_history(
            bioguide_id="G000386",
            slug="chuck-grassley",
            name="Chuck Grassley",
            state="IA",
            snapshot_date=snapshot_date,
            published_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
            fired_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
        )
        snapshot = payload.snapshots[0].model_copy(
            update={
                "score_total": score_total,
                "score_total_delta": score_total - 5.0,
                "dimension_scores": {"transparency_and_disclosure_risk": score_total},
            }
        )
        return payload.model_copy(
            update={
                "party": "Republican",
                "snapshots": [snapshot],
                "events": [
                    payload.events[0].model_copy(
                        update={
                            "dimension": "transparency_and_disclosure_risk",
                            "evidence_card_id": evidence_card_id,
                        }
                    )
                ],
            }
        )

    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-nancy-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            ),
            make_evidence_card(
                evidence_card_id="ec-chuck-old",
                member_slug="chuck-grassley",
                member_bioguide_id="G000386",
                member_name="Chuck Grassley",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            ),
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-nancy-old"),
            _grassley_history(
                dt.date(2026, 1, 6),
                score_total=18.0,
                evidence_card_id="ec-chuck-old",
            ),
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-nancy-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            ),
            make_evidence_card(
                evidence_card_id="ec-chuck-latest",
                member_slug="chuck-grassley",
                member_bioguide_id="G000386",
                member_name="Chuck Grassley",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            ),
        ],
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-nancy-latest"),
            _grassley_history(
                dt.date(2026, 1, 13),
                score_total=24.0,
                evidence_card_id="ec-chuck-latest",
            ),
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    nancy_dimension = load_member_timeline_dimension(
        target_root,
        "nancy-pelosi",
        "conflict_of_interest_risk",
    )
    assert nancy_dimension.dimension == "conflict_of_interest_risk"
    assert {event.dimension for event in nancy_dimension.timeline_page.events} == {
        "conflict_of_interest_risk"
    }

    chuck_dimension = load_member_timeline_dimension(
        target_root,
        "chuck-grassley",
        "transparency_and_disclosure_risk",
    )
    assert chuck_dimension.dimension == "transparency_and_disclosure_risk"
    assert {event.dimension for event in chuck_dimension.timeline_page.events} == {
        "transparency_and_disclosure_risk"
    }
    assert not (
        target_root / member_timeline_dimension_path("chuck-grassley", "conflict_of_interest_risk")
    ).exists()


def test_write_history_aggregate_writes_history_event_page_artifacts(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    timeline_page = load_member_timeline_page(target_root, "nancy-pelosi", 1)
    event_id = timeline_page.events[0].event_id
    assert (target_root / history_event_page_path(event_id)).is_file()

    event_page = load_history_event_page(target_root, event_id)
    assert event_page.event.event_id == event_id
    assert event_page.member_page is not None
    assert event_page.member_page.profile.slug == "nancy-pelosi"
    assert event_page.evidence_card is not None
    assert event_page.evidence_card.evidence_card_id == "ec-latest"


def test_write_history_aggregate_writes_history_coverage_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-old",
                snapshot_date=dt.date(2026, 1, 6),
                created_at=dt.datetime(2026, 1, 6, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-latest",
                snapshot_date=dt.date(2026, 1, 13),
                created_at=dt.datetime(2026, 1, 13, tzinfo=dt.UTC),
            )
        ],
        member_histories=[
            _history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")
        ],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / history_coverage_path()).is_file()
    coverage = load_history_coverage(target_root)
    assert coverage.earliest_snapshot_id == "2026-01-06"
    assert coverage.latest_snapshot_id == "2026-01-13"
    assert coverage.snapshot_count == 2
    assert coverage.member_history_count == 1
    assert coverage.total_events == 2
    assert coverage.available_years == [2026]


def test_write_history_aggregate_copies_latest_member_page_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[
            make_member_profile(name="Early Nancy", snapshot_date=dt.date(2026, 1, 6))
        ],
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0, name="Early Nancy")],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_profiles=[
            make_member_profile(name="Latest Nancy", snapshot_date=dt.date(2026, 1, 13))
        ],
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0, name="Latest Nancy")],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_page_payload_path("nancy-pelosi")).is_file()
    payload = load_member_page(target_root, "nancy-pelosi")
    assert payload.profile.name == "Latest Nancy"


def test_write_history_aggregate_copies_latest_zip_entry_artifact_when_present(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0)],
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=dt.date(2026, 1, 6))],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0)],
        zip_feeds=[make_zip_feed(zip_code="94102", snapshot_date=dt.date(2026, 1, 13))],
    )
    zip_entry = get_zip_entry("94102", snapshot_root=second_root)
    assert not isinstance(zip_entry, dict)
    assert getattr(zip_entry, "ok", False) is True
    write_planned_files(
        [
            PlannedFile.from_bytes(
                zip_entry_path("94102"),
                serialize_payload(zip_entry.data),
            )
        ],
        second_root,
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / zip_entry_path("94102")).is_file()
    payload = load_zip_entry(target_root, "94102")
    assert payload.zip_feed.snapshot_date == dt.date(2026, 1, 13)
