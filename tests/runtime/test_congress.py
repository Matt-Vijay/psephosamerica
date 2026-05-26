"""Tests for src/runtime/congress.py.

No live DB.  All DB and pipeline boundaries are mocked.
Covers: happy path, pipeline failure path, result shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.pipeline.congress_load_run import CongressIngestInputs
from src.runtime.congress import (
    CongressLoadResult,
    _SOURCE_BASE_URL,
    _SOURCE_KIND,
    _SOURCE_NAME,
    _SOURCE_SLUG,
    run_congress_load_runtime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_inputs() -> CongressIngestInputs:
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


def _fake_summary(inserted: int = 5) -> LoadSummary:
    return build_load_summary(
        [TableWriteResult(table="member", inserted=inserted)],
        warn_error=WarnErrorSummary(),
        run_id=99,
    )


_FAKE_DATA_SOURCE = {"id": 7, "slug": _SOURCE_SLUG, "name": _SOURCE_NAME}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRunCongressLoadRuntimeHappyPath:
    def test_returns_typed_result(self):
        conn = MagicMock()
        summary = _fake_summary(inserted=10)

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch(
                "src.runtime.congress.start_ingestion_run",
                return_value=42,
            ),
            patch(
                "src.runtime.congress.run_congress_load",
                return_value=summary,
            ),
            patch("src.runtime.congress.finish_ingestion_run"),
            patch("src.runtime.congress.fail_ingestion_run") as mock_fail,
        ):
            result = run_congress_load_runtime(conn, _empty_inputs())

        assert isinstance(result, CongressLoadResult)
        assert result.data_source == _FAKE_DATA_SOURCE
        assert result.run_id == 42
        assert result.load_summary is summary

        mock_fail.assert_not_called()

    def test_ensure_data_source_called_with_constants(self):
        conn = MagicMock()

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ) as mock_ensure,
            patch("src.runtime.congress.start_ingestion_run", return_value=1),
            patch("src.runtime.congress.run_congress_load", return_value=_fake_summary()),
            patch("src.runtime.congress.finish_ingestion_run"),
            patch("src.runtime.congress.fail_ingestion_run"),
        ):
            run_congress_load_runtime(conn, _empty_inputs())

        mock_ensure.assert_called_once_with(
            conn,
            slug=_SOURCE_SLUG,
            name=_SOURCE_NAME,
            source_kind=_SOURCE_KIND,
            base_url=_SOURCE_BASE_URL,
        )

    def test_start_run_uses_data_source_id(self):
        conn = MagicMock()

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value={"id": 99, "slug": _SOURCE_SLUG},
            ),
            patch(
                "src.runtime.congress.start_ingestion_run",
                return_value=1,
            ) as mock_start,
            patch("src.runtime.congress.run_congress_load", return_value=_fake_summary()),
            patch("src.runtime.congress.finish_ingestion_run"),
            patch("src.runtime.congress.fail_ingestion_run"),
        ):
            run_congress_load_runtime(conn, _empty_inputs())

        mock_start.assert_called_once_with(conn, data_source_id=99, run_type="ingest")

    def test_finish_run_receives_total_inserted(self):
        conn = MagicMock()
        summary = _fake_summary(inserted=17)

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=55),
            patch("src.runtime.congress.run_congress_load", return_value=summary),
            patch("src.runtime.congress.finish_ingestion_run") as mock_finish,
            patch("src.runtime.congress.fail_ingestion_run"),
        ):
            run_congress_load_runtime(conn, _empty_inputs())

        mock_finish.assert_called_once_with(conn, 55, record_count=17)

    def test_run_congress_load_receives_run_id(self):
        conn = MagicMock()

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=77),
            patch(
                "src.runtime.congress.run_congress_load",
                return_value=_fake_summary(),
            ) as mock_load,
            patch("src.runtime.congress.finish_ingestion_run"),
            patch("src.runtime.congress.fail_ingestion_run"),
        ):
            inputs = _empty_inputs()
            run_congress_load_runtime(conn, inputs)

        mock_load.assert_called_once_with(inputs, conn, run_id=77, commit=False)


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


class TestRunCongressLoadRuntimeFailurePath:
    def test_pipeline_error_marks_run_failed_and_reraises(self):
        conn = MagicMock()
        boom = RuntimeError("pipeline exploded")

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=33),
            patch("src.runtime.congress.run_congress_load", side_effect=boom),
            patch("src.runtime.congress.finish_ingestion_run") as mock_finish,
            patch("src.runtime.congress.fail_ingestion_run") as mock_fail,
        ):
            with pytest.raises(RuntimeError, match="pipeline exploded"):
                run_congress_load_runtime(conn, _empty_inputs())

        mock_fail.assert_called_once_with(conn, 33, "pipeline exploded")
        mock_finish.assert_not_called()

    def test_pipeline_error_rolls_back_uncommitted_load_before_marking_failed(self):
        conn = MagicMock()
        order: list[str] = []
        conn.rollback.side_effect = lambda: order.append("rollback")

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=33),
            patch("src.runtime.congress.run_congress_load", side_effect=RuntimeError("boom")),
            patch("src.runtime.congress.finish_ingestion_run"),
            patch(
                "src.runtime.congress.fail_ingestion_run",
                side_effect=lambda *a, **k: order.append("fail"),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                run_congress_load_runtime(conn, _empty_inputs())

        assert order == ["rollback", "fail"]

    def test_finish_error_rolls_back_uncommitted_load(self):
        conn = MagicMock()

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=33),
            patch("src.runtime.congress.run_congress_load", return_value=_fake_summary()),
            patch(
                "src.runtime.congress.finish_ingestion_run",
                side_effect=RuntimeError("finish failed"),
            ),
            patch("src.runtime.congress.fail_ingestion_run"),
        ):
            with pytest.raises(RuntimeError, match="finish failed"):
                run_congress_load_runtime(conn, _empty_inputs())

        conn.rollback.assert_called_once()

    def test_fail_run_receives_error_string(self):
        conn = MagicMock()

        class CustomError(Exception):
            pass

        with (
            patch(
                "src.runtime.congress.ensure_data_source",
                return_value=_FAKE_DATA_SOURCE,
            ),
            patch("src.runtime.congress.start_ingestion_run", return_value=1),
            patch(
                "src.runtime.congress.run_congress_load",
                side_effect=CustomError("bad data"),
            ),
            patch("src.runtime.congress.fail_ingestion_run") as mock_fail,
            patch("src.runtime.congress.finish_ingestion_run"),
        ):
            with pytest.raises(CustomError):
                run_congress_load_runtime(conn, _empty_inputs())

        _conn, _run_id, error_msg = mock_fail.call_args.args
        assert "bad data" in error_msg
