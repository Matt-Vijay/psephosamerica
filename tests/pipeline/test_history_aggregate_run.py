from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.api.read_service import get_zip_entry
from src.export.filesystem import write_planned_files
from src.export.local_store import (
    list_snapshot_ids,
    load_evidence_card,
    load_member_change_summary,
    load_member_history,
    load_member_history_page,
    load_member_page,
    load_member_preset_compare,
    load_member_profile,
    load_movement_window,
    load_snapshot_preset_compare,
    load_snapshot_index,
    load_zip_entry,
)
from src.pipeline.history_aggregate_run import (
    build_aggregated_member_histories,
    build_snapshot_index,
    merge_member_history_payloads,
    write_history_aggregate,
)
from src.export.writer import (
    PlannedFile,
    history_preset_range_path,
    member_change_summary_path,
    member_history_page_path,
    member_page_payload_path,
    member_preset_compare_path,
    movement_window_path,
    serialize_payload,
    snapshot_preset_compare_path,
    zip_entry_path,
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


def test_write_history_aggregate_writes_latest_current_state_and_merged_history(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[make_member_profile(name="Early Nancy", snapshot_date=dt.date(2026, 1, 6))],
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0, name="Early Nancy")],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_profiles=[make_member_profile(name="Latest Nancy", snapshot_date=dt.date(2026, 1, 13))],
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
    assert [summary.slug for summary in cycle_compare.featured_member_changes] == [
        "nancy-pelosi"
    ]


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
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0, evidence_card_id="ec-old")],
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
        member_histories=[_history(dt.date(2026, 1, 13), score_total=55.0, evidence_card_id="ec-latest")],
    )

    write_history_aggregate([first_root, second_root], target_root)

    assert (target_root / member_history_page_path("nancy-pelosi")).is_file()
    page = load_member_history_page(target_root, "nancy-pelosi")
    assert page.member_page.profile.slug == "nancy-pelosi"
    assert page.recent_change.slug == "nancy-pelosi"
    assert page.default_window_compare.summary.score_total_delta == 15.0


def test_write_history_aggregate_copies_latest_member_page_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    target_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[make_member_profile(name="Early Nancy", snapshot_date=dt.date(2026, 1, 6))],
        member_histories=[_history(dt.date(2026, 1, 6), score_total=40.0, name="Early Nancy")],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_profiles=[make_member_profile(name="Latest Nancy", snapshot_date=dt.date(2026, 1, 13))],
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
