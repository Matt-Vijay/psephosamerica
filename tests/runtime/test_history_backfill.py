from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.runtime as runtime
import pytest

from src.runtime.history_backfill import (
    CongressDateWindow,
    HistoricalSnapshotTarget,
    congress_term_bounds,
    derive_weekly_snapshot_dates,
    execute_history_backfill,
    plan_congress_history_backfill,
    run_local_history_backfill,
    resolve_congress_date_window,
)
from src.runtime.history_verify_types import (
    HistoryVerifyIssue,
    HistoryVerifyResult,
    HistoryVerifyStageResult,
)
from src.export.writer import manifest_path
from src.runtime.oracle_contracts import CongressOracleOptions, CongressStageSummary, LocalOracleRunResult
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)


def test_congress_term_bounds_resolve_inclusive_window() -> None:
    assert congress_term_bounds(119) == (dt.date(2025, 1, 3), dt.date(2027, 1, 2))


def test_resolve_congress_date_window_caps_current_congress_at_today() -> None:
    result = resolve_congress_date_window(119, today=dt.date(2026, 4, 19))
    assert result.start_date == dt.date(2025, 1, 3)
    assert result.end_date == dt.date(2026, 4, 19)
    assert result.bounded_by_today is True


def test_resolve_congress_date_window_keeps_completed_congress_full_term() -> None:
    result = resolve_congress_date_window(118, today=dt.date(2026, 4, 19))
    assert result.start_date == dt.date(2023, 1, 3)
    assert result.end_date == dt.date(2025, 1, 2)
    assert result.bounded_by_today is False


def test_resolve_congress_date_window_clamps_explicit_bounds() -> None:
    result = resolve_congress_date_window(
        119,
        start_date=dt.date(2024, 12, 1),
        end_date=dt.date(2025, 1, 10),
        today=dt.date(2026, 4, 19),
    )
    assert result.start_date == dt.date(2025, 1, 3)
    assert result.end_date == dt.date(2025, 1, 10)


def test_resolve_congress_date_window_raises_on_empty_range() -> None:
    with pytest.raises(ValueError, match="Resolved history window is empty"):
        resolve_congress_date_window(
            119,
            start_date=dt.date(2027, 1, 3),
            end_date=dt.date(2027, 1, 4),
            today=dt.date(2026, 4, 19),
        )


def test_derive_weekly_snapshot_dates_aligns_to_requested_weekday() -> None:
    dates = derive_weekly_snapshot_dates(
        dt.date(2025, 1, 3),
        dt.date(2025, 1, 20),
        weekday=0,
    )
    assert dates == [
        dt.date(2025, 1, 6),
        dt.date(2025, 1, 13),
        dt.date(2025, 1, 20),
    ]


def test_derive_weekly_snapshot_dates_includes_same_day_anchor() -> None:
    dates = derive_weekly_snapshot_dates(
        dt.date(2025, 1, 6),
        dt.date(2025, 1, 6),
        weekday=0,
    )
    assert dates == [dt.date(2025, 1, 6)]


def test_derive_weekly_snapshot_dates_rejects_invalid_weekday() -> None:
    with pytest.raises(ValueError, match="weekday must be 0-6"):
        derive_weekly_snapshot_dates(dt.date(2025, 1, 1), dt.date(2025, 1, 2), weekday=7)


def test_plan_congress_history_backfill_builds_ordered_targets(tmp_path: Path) -> None:
    plan = plan_congress_history_backfill(
        119,
        target_root=tmp_path,
        start_date=dt.date(2025, 1, 3),
        end_date=dt.date(2025, 1, 20),
        today=dt.date(2026, 4, 19),
    )
    assert plan.cadence == "weekly:monday"
    assert [target.snapshot_id for target in plan.targets] == [
        "2025-01-06",
        "2025-01-13",
        "2025-01-20",
    ]
    assert plan.targets[0].publish_root == tmp_path / "2025-01-06"


def test_runtime_exports_history_backfill_helpers() -> None:
    assert runtime.plan_congress_history_backfill is plan_congress_history_backfill
    assert runtime.resolve_congress_date_window is resolve_congress_date_window
    assert runtime.execute_history_backfill is execute_history_backfill
    assert runtime.run_local_history_backfill is run_local_history_backfill


def test_execute_history_backfill_runs_targets_in_order(tmp_path: Path) -> None:
    plan = plan_congress_history_backfill(
        119,
        target_root=tmp_path,
        start_date=dt.date(2025, 1, 3),
        end_date=dt.date(2025, 1, 13),
        today=dt.date(2026, 4, 19),
    )
    seen: list[str] = []

    def _replay(target: object) -> None:
        from src.runtime.history_backfill import HistoricalSnapshotTarget

        assert isinstance(target, HistoricalSnapshotTarget)
        seen.append(target.snapshot_id)

    result = execute_history_backfill(plan, _replay)

    assert seen == ["2025-01-06", "2025-01-13"]
    assert result.ok is True
    assert result.completed_count == 2


def test_execute_history_backfill_skips_existing_manifest_by_default(tmp_path: Path) -> None:
    plan = plan_congress_history_backfill(
        119,
        target_root=tmp_path,
        start_date=dt.date(2025, 1, 3),
        end_date=dt.date(2025, 1, 6),
        today=dt.date(2026, 4, 19),
    )
    target = plan.targets[0]
    manifest_file = target.publish_root / manifest_path(target.snapshot_id)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text("{}", encoding="utf-8")

    seen: list[str] = []
    result = execute_history_backfill(plan, lambda _target: seen.append("called"))

    assert seen == []
    assert result.attempts[0].status == "skipped_existing"
    assert result.attempts[0].reason == "existing_manifest"
    assert result.skipped_count == 1


def test_execute_history_backfill_fails_on_partial_existing_tree(tmp_path: Path) -> None:
    plan = plan_congress_history_backfill(
        119,
        target_root=tmp_path,
        start_date=dt.date(2025, 1, 3),
        end_date=dt.date(2025, 1, 6),
        today=dt.date(2026, 4, 19),
    )
    target = plan.targets[0]
    target.publish_root.mkdir(parents=True, exist_ok=True)

    result = execute_history_backfill(plan, lambda _target: None)

    assert result.ok is False
    assert result.failed_count == 1
    assert result.attempts[0].reason == "partial_existing_tree"
    assert "missing the expected manifest" in (result.attempts[0].error or "")


def test_execute_history_backfill_can_continue_after_failure(tmp_path: Path) -> None:
    plan = plan_congress_history_backfill(
        119,
        target_root=tmp_path,
        start_date=dt.date(2025, 1, 3),
        end_date=dt.date(2025, 1, 13),
        today=dt.date(2026, 4, 19),
    )
    seen: list[str] = []

    def _replay(target: object) -> None:
        from src.runtime.history_backfill import HistoricalSnapshotTarget

        assert isinstance(target, HistoricalSnapshotTarget)
        seen.append(target.snapshot_id)
        if target.snapshot_id == "2025-01-06":
            raise RuntimeError("boom")

    result = execute_history_backfill(plan, _replay, continue_on_error=True)

    assert seen == ["2025-01-06", "2025-01-13"]
    assert result.completed_count == 1
    assert result.failed_count == 1
    assert result.attempts[0].reason == "replay_exception"


def _verify_result(ok: bool) -> PublishVerifyResult:
    issues = ()
    if not ok:
        issues = (
            PublishVerifyIssue(
                stage="manifest",
                message="manifest failed",
                severity="error",
            ),
        )
    return PublishVerifyResult(
        stages=(PublishVerifyStageResult(stage="manifest", checked=1, issues=issues),)
    )


def _roundtrip_result(ok: bool) -> PublishRoundtripResult:
    issues = ()
    if not ok:
        issues = (
            PublishRoundtripIssue(
                stage="snapshot",
                message="snapshot failed",
                severity="error",
            ),
        )
    return PublishRoundtripResult(
        stages=(PublishRoundtripStageResult(stage="snapshot", checked=1, issues=issues),)
    )


def _oracle_result(
    snapshot_id: str,
    snapshot_date: dt.date,
    *,
    congress_ok: bool = True,
    disclosures_ok: bool = True,
    publish_ok: bool = True,
    verify_ok: bool = True,
    roundtrip_ok: bool = True,
) -> LocalOracleRunResult:
    return LocalOracleRunResult(
        snapshot_id=snapshot_id,
        congress=CongressStageSummary(
            run_id=1,
            source_slug="congress_core",
            total_inserted=10,
            total_written=10,
            load_ok=congress_ok,
            configured_congress=119,
            congress_source="explicit-arg",
            include_votes=False,
        ),
        disclosures={
            "run_id": 2,
            "source_slug": "financial_disclosures",
            "requested_chamber": "both",
            "artifact_limit": None,
            "parse_succeeded": 1,
            "parse_failed": 0,
            "transform_count": 1,
            "total_written": 1,
            "load_ok": disclosures_ok,
        },
        recompute={
            "run_id": 3,
            "source_slug": "conflict_recompute",
            "rule_fires": 2,
            "evidence_cards": 2,
        },
        publish={
            "run_id": 4,
            "snapshot_id": snapshot_id,
            "source_slug": "snapshot_publish",
            "written_count": 12,
            "succeeded": publish_ok,
            "zip_feeds_generated": False,
        },
        verify=_verify_result(verify_ok),
        roundtrip=_roundtrip_result(roundtrip_ok),
    )


def test_run_local_history_backfill_executes_per_snapshot_oracle_runs(tmp_path: Path) -> None:
    ctx = MagicMock()
    bundle = MagicMock()
    conn_a = MagicMock()
    conn_b = MagicMock()
    with (
        patch("src.runtime.history_backfill.open_connection", side_effect=[conn_a, conn_b]) as mock_open,
        patch(
            "src.runtime.history_backfill.run_oracle_local",
            side_effect=[
                _oracle_result("2025-01-06", dt.date(2025, 1, 6)),
                _oracle_result("2025-01-13", dt.date(2025, 1, 13)),
            ],
        ) as mock_run,
    ):
        result = run_local_history_backfill(
            ctx,
            Path("/archive/congress"),
            bundle,
            congress=119,
            target_root=tmp_path,
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 13),
            chamber="senate",
            limit=5,
            artifact_root=Path("/artifacts"),
        )

    assert result.ok is True
    assert result.completed_count == 2
    assert mock_open.call_count == 2
    assert conn_a.close.call_count == 1
    assert conn_b.close.call_count == 1
    first_options = mock_run.call_args_list[0].args[3]
    assert first_options.congress_options == CongressOracleOptions(
        congress=119,
        chamber="senate",
        limit=5,
        congress_source="explicit-arg",
    )
    assert first_options.snapshot_date == dt.date(2025, 1, 6)
    assert first_options.snapshot_id == "2025-01-06"
    assert first_options.target_dir == tmp_path / "2025-01-06"
    assert first_options.artifact_root == Path("/artifacts")


def test_run_local_history_backfill_records_stage_failures(tmp_path: Path) -> None:
    ctx = MagicMock()
    bundle = MagicMock()
    with (
        patch("src.runtime.history_backfill.open_connection", return_value=MagicMock()),
        patch(
            "src.runtime.history_backfill.run_oracle_local",
            return_value=_oracle_result(
                "2025-01-06",
                dt.date(2025, 1, 6),
                verify_ok=False,
            ),
        ),
    ):
        result = run_local_history_backfill(
            ctx,
            Path("/archive/congress"),
            bundle,
            congress=119,
            target_root=tmp_path,
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 6),
            continue_on_error=True,
        )

    assert result.ok is False
    assert result.failed_count == 1
    assert result.attempts[0].reason == "oracle_stage_failure"
    assert "2025-01-06" in result.snapshot_results
    assert "verify.ok=false" in (result.attempts[0].error or "")


def test_run_local_history_backfill_aggregates_completed_and_existing_roots(tmp_path: Path) -> None:
    ctx = MagicMock()
    bundle = MagicMock()
    existing_root = tmp_path / "2025-01-06"
    existing_manifest = existing_root / manifest_path("2025-01-06")
    existing_manifest.parent.mkdir(parents=True, exist_ok=True)
    existing_manifest.write_text("{}", encoding="utf-8")
    aggregate_root = tmp_path / "aggregate"
    aggregate_result = SimpleNamespace(
        latest_snapshot_id="2025-01-13",
        snapshot_index=SimpleNamespace(snapshots=[object(), object()]),
        member_history_count=4,
        target_root=aggregate_root,
    )
    verify_result = HistoryVerifyResult(
        stages=(HistoryVerifyStageResult(stage="snapshot_index", checked=1, issues=()),)
    )
    with (
        patch("src.runtime.history_backfill.open_connection", return_value=MagicMock()),
        patch(
            "src.runtime.history_backfill.run_oracle_local",
            return_value=_oracle_result("2025-01-13", dt.date(2025, 1, 13)),
        ),
        patch(
            "src.pipeline.history_aggregate_run.write_history_aggregate",
            return_value=aggregate_result,
        ) as mock_aggregate,
        patch(
            "src.runtime.history_backfill.verify_history_aggregate_local",
            return_value=verify_result,
        ) as mock_verify,
    ):
        result = run_local_history_backfill(
            ctx,
            Path("/archive/congress"),
            bundle,
            congress=119,
            target_root=tmp_path,
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 13),
            aggregate_root=aggregate_root,
        )

    assert result.completed_count == 1
    assert result.skipped_count == 1
    assert result.overwrite is False
    assert result.continue_on_error is False
    assert [path.name for path in result.aggregate_source_roots] == ["2025-01-06", "2025-01-13"]
    mock_aggregate.assert_called_once_with(
        [tmp_path / "2025-01-06", tmp_path / "2025-01-13"],
        aggregate_root,
    )
    mock_verify.assert_called_once_with(aggregate_root)
    assert result.aggregate is not None
    assert result.aggregate.latest_snapshot_id == "2025-01-13"
    assert result.aggregate.verify is verify_result


def test_run_local_history_backfill_marks_result_not_ok_when_aggregate_verify_fails(tmp_path: Path) -> None:
    ctx = MagicMock()
    bundle = MagicMock()
    aggregate_root = tmp_path / "aggregate"
    aggregate_result = SimpleNamespace(
        latest_snapshot_id="2025-01-13",
        snapshot_index=SimpleNamespace(snapshots=[object(), object()]),
        member_history_count=1,
        target_root=aggregate_root,
    )
    verify_result = HistoryVerifyResult(
        stages=(
            HistoryVerifyStageResult(
                stage="snapshot_index",
                checked=1,
                issues=(HistoryVerifyIssue(stage="snapshot_index", message="bad", severity="error"),),
            ),
        )
    )
    with (
        patch("src.runtime.history_backfill.open_connection", return_value=MagicMock()),
        patch(
            "src.runtime.history_backfill.run_oracle_local",
            return_value=_oracle_result("2025-01-13", dt.date(2025, 1, 13)),
        ),
        patch(
            "src.pipeline.history_aggregate_run.write_history_aggregate",
            return_value=aggregate_result,
        ),
        patch(
            "src.runtime.history_backfill.verify_history_aggregate_local",
            return_value=verify_result,
        ),
    ):
        result = run_local_history_backfill(
            ctx,
            Path("/archive/congress"),
            bundle,
            congress=119,
            target_root=tmp_path,
            start_date=dt.date(2025, 1, 13),
            end_date=dt.date(2025, 1, 13),
            aggregate_root=aggregate_root,
        )

    assert result.aggregate is not None
    assert result.aggregate.verify is verify_result
    assert result.ok is False


def test_run_local_history_backfill_records_aggregate_error_when_no_valid_roots(tmp_path: Path) -> None:
    ctx = MagicMock()
    bundle = MagicMock()
    with (
        patch("src.runtime.history_backfill.open_connection", return_value=MagicMock()),
        patch(
            "src.runtime.history_backfill.run_oracle_local",
            side_effect=RuntimeError("oracle boom"),
        ),
    ):
        result = run_local_history_backfill(
            ctx,
            Path("/archive/congress"),
            bundle,
            congress=119,
            target_root=tmp_path,
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 6),
            aggregate_root=tmp_path / "aggregate",
            continue_on_error=True,
        )

    assert result.ok is False
    assert result.aggregate is None
    assert result.target_root == tmp_path
    assert result.aggregate_error == "no successful or existing snapshot roots available to aggregate"
