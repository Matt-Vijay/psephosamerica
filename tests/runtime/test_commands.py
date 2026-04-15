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
    load_congress,
    load_congress_local,
    load_disclosures,
    process_disclosures_local,
    publish_snapshot,
    recompute_snapshot,
    run_oracle_local_command,
    verify_publish_local,
    verify_publish_roundtrip_local,
)
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
