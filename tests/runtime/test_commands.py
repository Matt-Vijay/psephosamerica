"""Tests for src/runtime/commands.py.

No live DB.  All connection and runtime boundaries are mocked.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch, sentinel

import pytest

from src.pipeline.congress_load_run import CongressIngestInputs
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.commands import (
    load_congress,
    load_disclosures,
    publish_snapshot,
    recompute_snapshot,
)
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.recompute import RuntimeRecomputeResult

_MODULE = "src.runtime.commands"

_SNAPSHOT_DATE = dt.date(2024, 6, 1)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.taxonomy = sentinel.taxonomy
    ctx.issuer_sector_resolver = sentinel.resolver
    return ctx


def _empty_congress_inputs() -> CongressIngestInputs:
    return CongressIngestInputs(
        members=[],
        member_terms=[],
        committees=[],
        memberships=[],
        bills=[],
        primary_sponsors=[],
        cosponsors=[],
        vote_events=[],
        vote_casts=[],
    )


# ---------------------------------------------------------------------------
# load_congress
# ---------------------------------------------------------------------------


class TestLoadCongress:
    def test_opens_connection_and_delegates(self) -> None:
        ctx = _ctx()
        inputs = _empty_congress_inputs()
        conn = MagicMock()
        expected = MagicMock(spec=CongressLoadResult)

        with (
            patch(f"{_MODULE}.open_connection", return_value=conn) as mock_open,
            patch(f"{_MODULE}.run_congress_load_runtime", return_value=expected) as mock_run,
        ):
            result = load_congress(ctx, inputs)

        mock_open.assert_called_once_with(ctx)
        mock_run.assert_called_once_with(conn, inputs)
        assert result is expected

    def test_propagates_exception(self) -> None:
        ctx = _ctx()
        inputs = _empty_congress_inputs()

        with (
            patch(f"{_MODULE}.open_connection", return_value=MagicMock()),
            patch(f"{_MODULE}.run_congress_load_runtime", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                load_congress(ctx, inputs)


# ---------------------------------------------------------------------------
# load_disclosures
# ---------------------------------------------------------------------------


class TestLoadDisclosures:
    def test_opens_connection_and_delegates(self) -> None:
        ctx = _ctx()
        results: list = []
        conn = MagicMock()
        expected = MagicMock(spec=DisclosuresLoadRuntimeResult)

        with (
            patch(f"{_MODULE}.open_connection", return_value=conn) as mock_open,
            patch(f"{_MODULE}.run_disclosures_load_runtime", return_value=expected) as mock_run,
        ):
            result = load_disclosures(ctx, results)

        mock_open.assert_called_once_with(ctx)
        mock_run.assert_called_once_with(conn, results)
        assert result is expected

    def test_propagates_exception(self) -> None:
        ctx = _ctx()

        with (
            patch(f"{_MODULE}.open_connection", return_value=MagicMock()),
            patch(f"{_MODULE}.run_disclosures_load_runtime", side_effect=ValueError("bad data")),
        ):
            with pytest.raises(ValueError, match="bad data"):
                load_disclosures(ctx, [])


# ---------------------------------------------------------------------------
# recompute_snapshot
# ---------------------------------------------------------------------------


class TestRecompute:
    def test_opens_connection_and_delegates(self) -> None:
        ctx = _ctx()
        conn = MagicMock()
        expected = MagicMock(spec=RuntimeRecomputeResult)

        with (
            patch(f"{_MODULE}.open_connection", return_value=conn) as mock_open,
            patch(f"{_MODULE}.run_recompute_runtime", return_value=expected) as mock_run,
        ):
            result = recompute_snapshot(ctx, _SNAPSHOT_DATE)

        mock_open.assert_called_once_with(ctx)
        mock_run.assert_called_once_with(
            conn,
            _SNAPSHOT_DATE,
            taxonomy=sentinel.taxonomy,
            issuer_sector_resolver=sentinel.resolver,
        )
        assert result is expected

    def test_passes_ctx_taxonomy_and_resolver(self) -> None:
        ctx = _ctx()
        ctx.taxonomy = sentinel.my_taxonomy
        ctx.issuer_sector_resolver = sentinel.my_resolver
        conn = MagicMock()

        with (
            patch(f"{_MODULE}.open_connection", return_value=conn),
            patch(f"{_MODULE}.run_recompute_runtime", return_value=MagicMock()) as mock_run,
        ):
            recompute_snapshot(ctx, _SNAPSHOT_DATE)

        _, kwargs = mock_run.call_args
        assert kwargs["taxonomy"] is sentinel.my_taxonomy
        assert kwargs["issuer_sector_resolver"] is sentinel.my_resolver

    def test_propagates_exception(self) -> None:
        ctx = _ctx()

        with (
            patch(f"{_MODULE}.open_connection", return_value=MagicMock()),
            patch(f"{_MODULE}.run_recompute_runtime", side_effect=RuntimeError("recompute failed")),
        ):
            with pytest.raises(RuntimeError, match="recompute failed"):
                recompute_snapshot(ctx, _SNAPSHOT_DATE)


# ---------------------------------------------------------------------------
# publish_snapshot
# ---------------------------------------------------------------------------


class TestPublishSnapshot:
    def test_opens_connection_and_delegates(self) -> None:
        ctx = _ctx()
        conn = MagicMock()
        target_dir = Path("/tmp/snapshots")
        zip_inputs = MagicMock(spec=ZipBundleInputs)
        expected = MagicMock(spec=PublishRuntimeResult)

        with (
            patch(f"{_MODULE}.open_connection", return_value=conn) as mock_open,
            patch(f"{_MODULE}.run_publish_runtime", return_value=expected) as mock_run,
        ):
            result = publish_snapshot(ctx, _SNAPSHOT_DATE, target_dir, zip_inputs)

        mock_open.assert_called_once_with(ctx)
        mock_run.assert_called_once_with(conn, _SNAPSHOT_DATE, target_dir, zip_inputs)
        assert result is expected

    def test_propagates_exception(self) -> None:
        ctx = _ctx()

        with (
            patch(f"{_MODULE}.open_connection", return_value=MagicMock()),
            patch(f"{_MODULE}.run_publish_runtime", side_effect=RuntimeError("publish failed")),
        ):
            with pytest.raises(RuntimeError, match="publish failed"):
                publish_snapshot(ctx, _SNAPSHOT_DATE, Path("/tmp"), MagicMock())
