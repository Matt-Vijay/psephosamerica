from __future__ import annotations

import datetime as dt
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_history_bootstrap
from src.export.contracts import MemberHistoryPayload
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import make_member_history, make_snapshot


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


def _history_root(tmp_path: Path) -> Path:
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
    return aggregate_root


def _multi_member_history_root(tmp_path: Path) -> Path:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        first_root,
        snapshot_id="2026-01-01",
        member_histories=[
            make_member_history(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                state="CA",
                chamber="house",
                snapshot_date=dt.date(2026, 1, 1),
                published_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                fired_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            ),
            make_member_history(
                bioguide_id="S000033",
                slug="chuck-schumer",
                name="Chuck Schumer",
                state="NY",
                chamber="senate",
                snapshot_date=dt.date(2026, 1, 1),
                published_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                fired_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            ),
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[
            make_member_history(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                state="CA",
                chamber="house",
                snapshot_date=dt.date(2026, 1, 8),
                published_at=dt.datetime(2026, 1, 8, tzinfo=dt.UTC),
                fired_at=dt.datetime(2026, 1, 8, tzinfo=dt.UTC),
            ),
            make_member_history(
                bioguide_id="S000033",
                slug="chuck-schumer",
                name="Chuck Schumer",
                state="NY",
                chamber="senate",
                snapshot_date=dt.date(2026, 1, 8),
                published_at=dt.datetime(2026, 1, 8, tzinfo=dt.UTC),
                fired_at=dt.datetime(2026, 1, 8, tzinfo=dt.UTC),
            ),
        ],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)
    return aggregate_root


def test_get_history_bootstrap_returns_snapshot_index_movement_and_member_changes(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_history_bootstrap(snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.snapshot_index.latest_snapshot_id == "2026-01-08"
    assert result.data.movement_window.latest_snapshot_id == "2026-01-08"
    assert result.data.coverage.latest_snapshot_id == "2026-01-08"
    assert result.data.coverage.snapshot_count == 2
    assert result.data.coverage.member_history_count == 1
    assert [dimension.dimension for dimension in result.data.coverage.dimensions] == [
        "conflict_of_interest_risk"
    ]
    assert result.data.default_compare_preset_key == "latest"
    assert [preset.preset_key for preset in result.data.compare_presets] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    assert result.data.featured_member_changes[0].slug == "nancy-pelosi"


def test_get_history_bootstrap_supports_dimension_scope(tmp_path: Path) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_history_bootstrap(
        snapshot_root=aggregate_root,
        dimension="conflict_of_interest_risk",
    )

    assert result.ok is True
    assert result.data.dimension == "conflict_of_interest_risk"
    assert result.data.movement_window.dimension == "conflict_of_interest_risk"
    assert result.data.dimension_coverage is not None
    assert result.data.dimension_coverage.dimension == "conflict_of_interest_risk"


def test_get_history_bootstrap_returns_not_found_for_unknown_dimension(tmp_path: Path) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_history_bootstrap(
        snapshot_root=aggregate_root,
        dimension="transparency_risk",
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "history_dimension"
    assert result.identifier == "transparency_risk"


def test_get_history_bootstrap_returns_not_found_without_snapshots(tmp_path: Path) -> None:
    result = get_history_bootstrap(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot"
    assert result.identifier == "latest"


def test_get_history_bootstrap_keeps_featured_changes_aligned_with_visible_top_changes(
    tmp_path: Path,
) -> None:
    aggregate_root = _multi_member_history_root(tmp_path)

    precomputed = get_history_bootstrap(
        snapshot_root=aggregate_root,
        top_changes_limit=1,
        featured_limit=5,
    )
    (aggregate_root / "history" / "bootstrap.json").unlink()
    fallback = get_history_bootstrap(
        snapshot_root=aggregate_root,
        top_changes_limit=1,
        featured_limit=5,
    )

    assert precomputed.ok is True
    assert fallback.ok is True
    assert [change.slug for change in precomputed.data.movement_window.top_changes] == [
        "nancy-pelosi"
    ]
    assert [summary.slug for summary in precomputed.data.featured_member_changes] == [
        "nancy-pelosi"
    ]
    assert [summary.slug for summary in fallback.data.featured_member_changes] == ["nancy-pelosi"]


def test_get_history_bootstrap_keeps_compare_presets_aligned_with_fallback(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    precomputed = get_history_bootstrap(snapshot_root=aggregate_root)
    (aggregate_root / "history" / "bootstrap.json").unlink()
    fallback = get_history_bootstrap(snapshot_root=aggregate_root)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.default_compare_preset_key == fallback.data.default_compare_preset_key
    assert precomputed.data.coverage.model_dump(mode="json") == fallback.data.coverage.model_dump(
        mode="json"
    )
    assert [preset.model_dump(mode="json") for preset in precomputed.data.compare_presets] == [
        preset.model_dump(mode="json") for preset in fallback.data.compare_presets
    ]


def test_src_api_exports_history_bootstrap_helper() -> None:
    assert api.get_history_bootstrap is get_history_bootstrap
