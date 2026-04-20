from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import (
    get_member_change_summary,
    get_member_history,
    get_movement_feed,
    get_movement_window,
    get_snapshot_index,
)
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.contracts import MemberHistoryPayload
from src.export.filesystem import write_planned_files
from src.export.manifest import SnapshotManifest
from src.export.writer import PlannedFile, snapshot_index_path
from src.export.writer import manifest_path
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import (
    PublishedSnapshotBuilder,
    make_member_history,
    make_snapshot,
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
