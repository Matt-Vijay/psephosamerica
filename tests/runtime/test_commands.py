"""Tests for src/runtime/commands.py.

No live DB.  All connection and runtime boundaries are mocked via patch()
on top-level names imported into commands.py.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, sentinel

import pytest

from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.commands import (
    _oracle_summary_ok,
    dispatch_command,
    load_congress,
    load_congress_local,
    load_disclosures,
    process_disclosures_local,
    publish_snapshot,
    recompute_snapshot,
    run_oracle_local_command,
    verify_history_aggregate_local,
    verify_publish_local,
    verify_publish_roundtrip_local,
)
from src.runtime.history_verify_types import HistoryVerifyResult, HistoryVerifyStageResult
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_bundle_process import DisclosuresBundleProcessResult
from src.runtime.oracle_contracts import CongressOracleOptions, LocalOracleOptions, LocalOracleRunResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import PublishRoundtripResult, PublishRoundtripStageResult
from src.runtime.publish_verify_types import PublishVerifyResult, PublishVerifyStageResult
from src.runtime.recompute import RuntimeRecomputeResult

_MODULE = "src.runtime.commands"

_SNAPSHOT_DATE = dt.date(2024, 6, 1)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.taxonomy = sentinel.taxonomy
    ctx.issuer_sector_resolver = sentinel.resolver
    return ctx


@contextmanager
def _command_env():
    """Patch open_connection and yield ctx + conn for command-level tests."""
    ctx = _ctx()
    conn = MagicMock()
    with patch(f"{_MODULE}.open_connection", return_value=conn) as mock_open:
        yield SimpleNamespace(ctx=ctx, conn=conn, mock_open=mock_open)


class TestOracleSummaryOk:
    def test_returns_false_when_congress_stage_fails(self) -> None:
        summary = {
            "congress": {"load_ok": False},
            "disclosures": {"load_ok": True},
            "publish": {"succeeded": True},
            "verify": {"ok": True},
            "roundtrip": {"ok": True},
        }

        assert _oracle_summary_ok(summary) is False

    def test_returns_false_when_disclosures_stage_fails(self) -> None:
        summary = {
            "congress": {"load_ok": True},
            "disclosures": {"load_ok": False},
            "publish": {"succeeded": True},
            "verify": {"ok": True},
            "roundtrip": {"ok": True},
        }

        assert _oracle_summary_ok(summary) is False


# ---------------------------------------------------------------------------
# load_congress
# ---------------------------------------------------------------------------


class TestLoadCongress:
    def test_opens_connection_and_delegates(self) -> None:
        options = CongressLoadOptions(
            congress=119,
            include_votes=True,
            house_vote_year=2025,
            senate_session=1,
        )
        expected = MagicMock(spec=CongressLoadResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_live_congress_load_full", return_value=expected) as mock_run:
                result = load_congress(env.ctx, options)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(
            env.conn,
            env.ctx.settings,
            congress=119,
            include_votes=True,
            house_vote_year=2025,
            senate_session=1,
        )
        assert result is expected

    def test_option_defaults_are_forwarded(self) -> None:
        options = CongressLoadOptions(congress=118)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_live_congress_load_full", return_value=MagicMock()) as mock_run:
                load_congress(env.ctx, options)

        args, kwargs = mock_run.call_args
        assert args == (env.conn, env.ctx.settings)
        assert kwargs == {
            "congress": 118,
            "include_votes": False,
            "house_vote_year": None,
            "senate_session": None,
        }

    def test_propagates_exception(self) -> None:
        options = CongressLoadOptions(congress=119)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_live_congress_load_full", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError, match="boom"):
                    load_congress(env.ctx, options)


# ---------------------------------------------------------------------------
# load_disclosures
# ---------------------------------------------------------------------------


class TestLoadDisclosures:
    def test_opens_connection_and_delegates(self) -> None:
        results: list = []
        expected = MagicMock(spec=DisclosuresLoadRuntimeResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_load_runtime", return_value=expected) as mock_run:
                result = load_disclosures(env.ctx, results)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(env.conn, results)
        assert result is expected

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_load_runtime", side_effect=ValueError("bad data")):
                with pytest.raises(ValueError, match="bad data"):
                    load_disclosures(env.ctx, [])


# ---------------------------------------------------------------------------
# recompute_snapshot
# ---------------------------------------------------------------------------


class TestRecompute:
    def test_opens_connection_and_delegates(self) -> None:
        expected = MagicMock(spec=RuntimeRecomputeResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_recompute_runtime", return_value=expected) as mock_run:
                result = recompute_snapshot(env.ctx, _SNAPSHOT_DATE)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(
            env.conn,
            _SNAPSHOT_DATE,
            taxonomy=sentinel.taxonomy,
            issuer_sector_resolver=sentinel.resolver,
        )
        assert result is expected

    def test_passes_ctx_taxonomy_and_resolver(self) -> None:
        with _command_env() as env:
            env.ctx.taxonomy = sentinel.my_taxonomy
            env.ctx.issuer_sector_resolver = sentinel.my_resolver
            with patch(f"{_MODULE}.run_recompute_runtime", return_value=MagicMock()) as mock_run:
                recompute_snapshot(env.ctx, _SNAPSHOT_DATE)

        _, kwargs = mock_run.call_args
        assert kwargs["taxonomy"] is sentinel.my_taxonomy
        assert kwargs["issuer_sector_resolver"] is sentinel.my_resolver

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_recompute_runtime", side_effect=RuntimeError("recompute failed")):
                with pytest.raises(RuntimeError, match="recompute failed"):
                    recompute_snapshot(env.ctx, _SNAPSHOT_DATE)


# ---------------------------------------------------------------------------
# publish_snapshot
# ---------------------------------------------------------------------------


class TestPublishSnapshot:
    def test_opens_connection_and_delegates(self) -> None:
        target_dir = Path("/tmp/snapshots")
        zip_inputs = MagicMock(spec=ZipBundleInputs)
        expected = MagicMock(spec=PublishRuntimeResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_publish_runtime", return_value=expected) as mock_run:
                result = publish_snapshot(env.ctx, _SNAPSHOT_DATE, target_dir, zip_inputs)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(env.conn, _SNAPSHOT_DATE, target_dir, zip_inputs)
        assert result is expected

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_publish_runtime", side_effect=RuntimeError("publish failed")):
                with pytest.raises(RuntimeError, match="publish failed"):
                    publish_snapshot(env.ctx, _SNAPSHOT_DATE, Path("/tmp"), MagicMock())


# ---------------------------------------------------------------------------
# load_congress_local
# ---------------------------------------------------------------------------


class TestLoadCongressLocal:
    def test_opens_connection_and_delegates(self) -> None:
        archive = Path("/data/congress/119")
        options = CongressLoadOptions(congress=119)
        expected = MagicMock(spec=CongressLoadResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_congress_archive_load", return_value=expected) as mock_run:
                result = load_congress_local(env.ctx, archive, options)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(env.conn, archive, options)
        assert result is expected

    def test_archive_path_forwarded(self) -> None:
        archive = Path("/data/congress/118")
        options = CongressLoadOptions(congress=118)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_congress_archive_load", return_value=MagicMock()) as mock_run:
                load_congress_local(env.ctx, archive, options)

        args, _ = mock_run.call_args
        assert args[1] == archive

    def test_options_forwarded(self) -> None:
        archive = Path("/data/congress/119")
        options = CongressLoadOptions(
            congress=119,
            include_votes=True,
            house_vote_year=2025,
            senate_session=1,
        )

        with _command_env() as env:
            with patch(f"{_MODULE}.run_congress_archive_load", return_value=MagicMock()) as mock_run:
                load_congress_local(env.ctx, archive, options)

        args, _ = mock_run.call_args
        assert args[2] is options

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_congress_archive_load", side_effect=RuntimeError("archive missing")):
                with pytest.raises(RuntimeError, match="archive missing"):
                    load_congress_local(env.ctx, Path("/data/congress/119"), CongressLoadOptions(congress=119))


# ---------------------------------------------------------------------------
# process_disclosures_local
# ---------------------------------------------------------------------------


class TestProcessDisclosuresLocal:
    def test_opens_connection_and_delegates(self) -> None:
        bundle = MagicMock(name="bundle")
        local_root = Path("/data/disclosures")
        expected = MagicMock(spec=DisclosuresBundleProcessResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=expected) as mock_run:
                result = process_disclosures_local(env.ctx, bundle, local_root=local_root)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(env.conn, bundle, local_root=local_root)
        assert result is expected

    def test_local_root_none_forwarded(self) -> None:
        bundle = MagicMock(name="bundle")

        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=MagicMock()) as mock_run:
                process_disclosures_local(env.ctx, bundle)

        _, kwargs = mock_run.call_args
        assert kwargs["local_root"] is None

    def test_bundle_forwarded_as_second_arg(self) -> None:
        bundle = MagicMock(name="bundle")

        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=MagicMock()) as mock_run:
                process_disclosures_local(env.ctx, bundle)

        args, _ = mock_run.call_args
        assert args[1] is bundle

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_disclosures_bundle_process", side_effect=ValueError("bad bundle")):
                with pytest.raises(ValueError, match="bad bundle"):
                    process_disclosures_local(env.ctx, MagicMock())


# ---------------------------------------------------------------------------
# run_oracle_local_command
# ---------------------------------------------------------------------------


def _oracle_options() -> LocalOracleOptions:
    return LocalOracleOptions(
        congress_options=CongressOracleOptions(congress=119),
        snapshot_date=_SNAPSHOT_DATE,
        target_dir=Path("/tmp/publish"),
    )


class TestRunOracleLocalCommand:
    def test_opens_connection_and_delegates(self) -> None:
        archive = Path("/data/congress/119")
        bundle = MagicMock(name="bundle")
        options = _oracle_options()
        expected = MagicMock(spec=LocalOracleRunResult)

        with _command_env() as env:
            with patch(f"{_MODULE}.run_oracle_local", return_value=expected) as mock_run:
                result = run_oracle_local_command(env.ctx, archive, bundle, options)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_run.assert_called_once_with(env.conn, archive, bundle, options)
        assert result is expected

    def test_congress_archive_forwarded(self) -> None:
        archive = Path("/custom/archive/119")
        bundle = MagicMock(name="bundle")
        options = _oracle_options()

        with _command_env() as env:
            with patch(f"{_MODULE}.run_oracle_local", return_value=MagicMock()) as mock_run:
                run_oracle_local_command(env.ctx, archive, bundle, options)

        args, _ = mock_run.call_args
        assert args[1] == archive

    def test_disclosures_bundle_forwarded(self) -> None:
        archive = Path("/data/congress/119")
        bundle = MagicMock(name="bundle")
        options = _oracle_options()

        with _command_env() as env:
            with patch(f"{_MODULE}.run_oracle_local", return_value=MagicMock()) as mock_run:
                run_oracle_local_command(env.ctx, archive, bundle, options)

        args, _ = mock_run.call_args
        assert args[2] is bundle

    def test_options_forwarded_as_fourth_arg(self) -> None:
        archive = Path("/data/congress/119")
        bundle = MagicMock(name="bundle")
        options = _oracle_options()

        with _command_env() as env:
            with patch(f"{_MODULE}.run_oracle_local", return_value=MagicMock()) as mock_run:
                run_oracle_local_command(env.ctx, archive, bundle, options)

        args, _ = mock_run.call_args
        assert args[3] is options

    def test_propagates_exception(self) -> None:
        with _command_env() as env:
            with patch(f"{_MODULE}.run_oracle_local", side_effect=RuntimeError("oracle failed")):
                with pytest.raises(RuntimeError, match="oracle failed"):
                    run_oracle_local_command(
                        env.ctx,
                        Path("/data/congress/119"),
                        MagicMock(),
                        _oracle_options(),
                    )


# ---------------------------------------------------------------------------
# verify_publish_local
# ---------------------------------------------------------------------------


def _clean_verify_result() -> PublishVerifyResult:
    """Return a minimal ok PublishVerifyResult for use in stubs."""
    stage = PublishVerifyStageResult(stage="manifest", checked=1, issues=())
    return PublishVerifyResult(stages=(stage,))


class TestVerifyPublishLocal:
    def test_delegates_to_verify_local_publish(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        expected = _clean_verify_result()
        with patch(f"{_MODULE}._verify_local_publish", return_value=expected) as mock_verify:
            result = verify_publish_local(publish_root)

        mock_verify.assert_called_once_with(publish_root)
        assert result is expected

    def test_does_not_open_connection(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        with (
            patch(f"{_MODULE}._verify_local_publish", return_value=_clean_verify_result()),
            patch(f"{_MODULE}.open_connection") as mock_open,
        ):
                verify_publish_local(publish_root)

        mock_open.assert_not_called()

    def test_returns_publish_verify_result_instance(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        expected = _clean_verify_result()
        with patch(f"{_MODULE}._verify_local_publish", return_value=expected):
            result = verify_publish_local(publish_root)

        assert isinstance(result, PublishVerifyResult)

    def test_propagates_exception(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        with patch(
            f"{_MODULE}._verify_local_publish",
            side_effect=FileNotFoundError("missing tree"),
        ):
            with pytest.raises(FileNotFoundError, match="missing tree"):
                verify_publish_local(publish_root)


# ---------------------------------------------------------------------------
# verify_publish_roundtrip_local
# ---------------------------------------------------------------------------


def _clean_roundtrip_result() -> PublishRoundtripResult:
    """Return a minimal ok PublishRoundtripResult for use in stubs."""
    stage = PublishRoundtripStageResult(stage="snapshot", checked=1, issues=())
    return PublishRoundtripResult(stages=(stage,))


def _clean_history_verify_result() -> HistoryVerifyResult:
    stage = HistoryVerifyStageResult(stage="snapshot_index", checked=1, issues=())
    return HistoryVerifyResult(stages=(stage,))


class TestVerifyPublishRoundtripLocal:
    def test_opens_connection_and_delegates(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        expected = _clean_roundtrip_result()

        with _command_env() as env:
            with patch(f"{_MODULE}._verify_roundtrip", return_value=expected) as mock_verify:
                result = verify_publish_roundtrip_local(env.ctx, publish_root)

        env.mock_open.assert_called_once_with(env.ctx)
        mock_verify.assert_called_once_with(env.conn, publish_root)
        assert result is expected

    def test_returns_publish_roundtrip_result_instance(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")
        expected = _clean_roundtrip_result()

        with _command_env() as env:
            with patch(f"{_MODULE}._verify_roundtrip", return_value=expected):
                result = verify_publish_roundtrip_local(env.ctx, publish_root)

        assert isinstance(result, PublishRoundtripResult)

    def test_passes_conn_not_ctx_to_verify_roundtrip(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")

        with _command_env() as env:
            with patch(f"{_MODULE}._verify_roundtrip", return_value=_clean_roundtrip_result()) as mock_verify:
                verify_publish_roundtrip_local(env.ctx, publish_root)

        conn_arg, root_arg = mock_verify.call_args[0]
        assert conn_arg is env.conn
        assert root_arg is publish_root

    def test_publish_root_forwarded(self) -> None:
        publish_root = Path("/data/snapshots/2026-04-14")

        with _command_env() as env:
            with patch(f"{_MODULE}._verify_roundtrip", return_value=_clean_roundtrip_result()) as mock_verify:
                verify_publish_roundtrip_local(env.ctx, publish_root)

        _, root_arg = mock_verify.call_args[0]
        assert root_arg == publish_root

    def test_propagates_exception(self) -> None:
        publish_root = Path("/tmp/publish/2024-06-01")

        with _command_env() as env:
            with patch(f"{_MODULE}._verify_roundtrip", side_effect=RuntimeError("db down")):
                with pytest.raises(RuntimeError, match="db down"):
                    verify_publish_roundtrip_local(env.ctx, publish_root)


class TestVerifyHistoryAggregateLocal:
    def test_delegates_to_verify_history_aggregate(self) -> None:
        publish_root = Path("/tmp/history")
        expected = _clean_history_verify_result()

        with patch(f"{_MODULE}._verify_local_history_aggregate", return_value=expected) as mock_verify:
            result = verify_history_aggregate_local(publish_root)

        mock_verify.assert_called_once_with(publish_root)
        assert result is expected

    def test_returns_history_verify_result_instance(self) -> None:
        publish_root = Path("/tmp/history")
        expected = _clean_history_verify_result()

        with patch(f"{_MODULE}._verify_local_history_aggregate", return_value=expected):
            result = verify_history_aggregate_local(publish_root)

        assert isinstance(result, HistoryVerifyResult)

    def test_publish_root_forwarded(self) -> None:
        publish_root = Path("/data/history/aggregate")

        with patch(
            f"{_MODULE}._verify_local_history_aggregate",
            return_value=_clean_history_verify_result(),
        ) as mock_verify:
            verify_history_aggregate_local(publish_root)

        assert mock_verify.call_args.args[0] == publish_root

    def test_propagates_exception(self) -> None:
        publish_root = Path("/tmp/history")

        with patch(
            f"{_MODULE}._verify_local_history_aggregate",
            side_effect=RuntimeError("bad history root"),
        ):
            with pytest.raises(RuntimeError, match="bad history root"):
                verify_history_aggregate_local(publish_root)


class TestHistoryDispatchCommands:
    def test_run_oracle_local_dispatch_respects_explicit_congress_and_artifact_root(self) -> None:
        runtime = SimpleNamespace(context=sentinel.ctx)
        oracle_result = MagicMock(spec=LocalOracleRunResult)
        summary = {
            "snapshot_id": "2025-01-15",
            "congress": {"load_ok": True},
            "disclosures": {"load_ok": True},
            "recompute": {},
            "publish": {"succeeded": True},
            "verify": {"ok": True},
            "roundtrip": {"ok": True},
        }
        with (
            patch(f"{_MODULE}.build_runtime", return_value=runtime),
            patch(f"{_MODULE}.load_disclosures_bundle", return_value=sentinel.bundle),
            patch(f"{_MODULE}.run_oracle_local_command", return_value=oracle_result) as mock_run,
            patch(f"{_MODULE}.summarize_local_oracle_run_result", return_value=summary),
        ):
            result = dispatch_command(
                SimpleNamespace(
                    command="run-oracle-local",
                    congress_archive="/tmp/congress-118",
                    disclosures_bundle="/tmp/disclosures.json",
                    congress=118,
                    snapshot_date=dt.date(2025, 1, 15),
                    target_dir="/tmp/publish",
                    chamber="both",
                    limit=10,
                    snapshot_id=None,
                    artifact_root="/tmp/artifacts",
                )
            )

        options = mock_run.call_args.args[3]
        assert options.congress_options.congress == 118
        assert options.congress_options.congress_source == "explicit-arg"
        assert options.artifact_root == Path("/tmp/artifacts")
        assert result["ok"] is True

    def test_plan_history_backfill_dispatch_returns_targets(self, tmp_path: Path) -> None:
        result = dispatch_command(
            SimpleNamespace(
                command="plan-history-backfill",
                congress=119,
                target_root=str(tmp_path / "roots"),
                start_date=dt.date(2025, 1, 3),
                end_date=dt.date(2025, 1, 20),
            )
        )

        assert result["ok"] is True
        assert result["command"] == "plan-history-backfill"
        assert result["snapshot_count"] == 3
        assert [target["snapshot_id"] for target in result["targets"]] == [
            "2025-01-06",
            "2025-01-13",
            "2025-01-20",
        ]

    def test_aggregate_history_dispatch_delegates_and_summarizes(self, tmp_path: Path) -> None:
        expected = SimpleNamespace(
            latest_snapshot_id="2026-01-13",
            snapshot_index=SimpleNamespace(snapshots=[object(), object()]),
            member_history_count=8,
            target_root=tmp_path / "aggregate",
        )
        verify_result = _clean_history_verify_result()
        verify_summary = {
            "ok": True,
            "total_checked": 11,
            "total_errors": 0,
            "total_warnings": 0,
            "stages": [],
        }
        with patch(
            "src.pipeline.history_aggregate_run.write_history_aggregate",
            return_value=expected,
        ) as mock_write, patch(
            f"{_MODULE}.verify_history_aggregate_local",
            return_value=verify_result,
        ) as mock_verify, patch(
            f"{_MODULE}.summarize_history_verify_result",
            return_value=verify_summary,
        ):
            result = dispatch_command(
                SimpleNamespace(
                    command="aggregate-history",
                    source_root=["/tmp/snap-a", "/tmp/snap-b"],
                    target_root=str(tmp_path / "aggregate"),
                )
            )

        mock_write.assert_called_once_with(
            [Path("/tmp/snap-a"), Path("/tmp/snap-b")],
            tmp_path / "aggregate",
        )
        mock_verify.assert_called_once_with(tmp_path / "aggregate")
        assert result == {
            "ok": True,
            "command": "aggregate-history",
            "latest_snapshot_id": "2026-01-13",
            "snapshot_count": 2,
            "member_history_count": 8,
            "target_root": str(tmp_path / "aggregate"),
            "verify": verify_summary,
        }

    def test_verify_history_aggregate_dispatch_delegates_and_summarizes(self, tmp_path: Path) -> None:
        summary = {
            "ok": False,
            "total_checked": 11,
            "total_errors": 2,
            "total_warnings": 0,
            "stages": [
                {
                    "stage": "snapshot_index",
                    "checked": 5,
                    "ok": False,
                    "errors": 2,
                    "warnings": 0,
                }
            ],
        }
        result_obj = _clean_history_verify_result()
        with (
            patch(
                f"{_MODULE}.verify_history_aggregate_local",
                return_value=result_obj,
            ) as mock_verify,
            patch(
                f"{_MODULE}.summarize_history_verify_result",
                return_value=summary,
            ),
        ):
            result = dispatch_command(
                SimpleNamespace(
                    command="verify-history-aggregate",
                    publish_root=str(tmp_path / "aggregate"),
                )
            )

        mock_verify.assert_called_once_with(tmp_path / "aggregate")
        assert result == {
            "ok": result_obj.ok,
            "command": "verify-history-aggregate",
            **summary,
        }

    def test_run_history_backfill_local_dispatch_delegates_and_summarizes(self, tmp_path: Path) -> None:
        runtime = SimpleNamespace(context=sentinel.ctx)
        result_obj = SimpleNamespace(ok=False)
        summary = {
            "congress": 119,
            "cadence": "weekly:monday",
            "planned_count": 3,
            "attempted_count": 2,
            "completed_count": 1,
            "skipped_count": 1,
            "failed_count": 0,
            "remaining_count": 1,
            "attempts": [],
            "aggregate": None,
        }
        with (
            patch(f"{_MODULE}.build_runtime", return_value=runtime),
            patch(f"{_MODULE}.load_disclosures_bundle", return_value=sentinel.bundle),
            patch(
                f"{_MODULE}.run_history_backfill_local_command",
                return_value=result_obj,
            ) as mock_run,
            patch(
                f"{_MODULE}.summarize_local_history_backfill_result",
                return_value=summary,
            ),
        ):
            result = dispatch_command(
                SimpleNamespace(
                    command="run-history-backfill-local",
                    congress_archive="/tmp/congress-119",
                    disclosures_bundle="/tmp/disclosures.json",
                    congress=119,
                    target_root=str(tmp_path / "history"),
                    aggregate_root=str(tmp_path / "aggregate"),
                    start_date=dt.date(2025, 1, 3),
                    end_date=dt.date(2025, 1, 20),
                    chamber="senate",
                    limit=25,
                    artifact_root="/tmp/artifacts",
                    overwrite=True,
                    continue_on_error=True,
                )
            )

        mock_run.assert_called_once_with(
            sentinel.ctx,
            Path("/tmp/congress-119"),
            sentinel.bundle,
            congress=119,
            target_root=tmp_path / "history",
            aggregate_root=tmp_path / "aggregate",
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 20),
            chamber="senate",
            limit=25,
            artifact_root=Path("/tmp/artifacts"),
            overwrite=True,
            continue_on_error=True,
        )
        assert result == {
            "ok": False,
            "command": "run-history-backfill-local",
            **summary,
        }
