"""Tests for src/runtime/publish.py.

No live DB. DB and pipeline boundaries are mocked at module-import paths.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.publish import (
    PublishRuntimeResult,
    default_snapshot_id,
    run_publish_runtime,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_ENSURE = "src.runtime.publish.ensure_data_source"
_START = "src.runtime.publish.start_ingestion_run"
_FINISH = "src.runtime.publish.finish_ingestion_run"
_FAIL = "src.runtime.publish.fail_ingestion_run"
_PUBLISH_SNAP = "src.runtime.publish.publish_snapshot_run"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SNAP_DATE = date(2026, 4, 14)
_SNAP_ID = "2026-04-14"
_DS_ROW = {
    "id": 7,
    "slug": "snapshot-publish",
    "name": "Published Score Snapshot",
    "source_kind": "artifact",
}
_RUN_ID = 42


def _succeeded_publish_result():
    result = MagicMock()
    result.succeeded = True
    result.written_count = 12
    result.verification_failures = []
    return result


def _failed_publish_result(failures=("bad hash",)):
    result = MagicMock()
    result.succeeded = False
    result.written_count = 0
    result.verification_failures = list(failures)
    return result


# ---------------------------------------------------------------------------
# default_snapshot_id
# ---------------------------------------------------------------------------


class TestDefaultSnapshotId:
    def test_returns_isoformat(self):
        assert default_snapshot_id(date(2026, 4, 14)) == "2026-04-14"

    def test_single_digit_month_and_day(self):
        assert default_snapshot_id(date(2026, 1, 5)) == "2026-01-05"


# ---------------------------------------------------------------------------
# run_publish_runtime — happy path
# ---------------------------------------------------------------------------


class TestRunPublishRuntimeSuccess:
    def _run(self, snapshot_id=None):
        conn = MagicMock()
        target_dir = Path("/tmp/snap")
        zip_inputs = MagicMock()

        with (
            patch(_ENSURE, return_value=_DS_ROW) as mock_ensure,
            patch(_START, return_value=_RUN_ID) as mock_start,
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(_PUBLISH_SNAP, return_value=_succeeded_publish_result()) as mock_pub,
        ):
            result = run_publish_runtime(
                conn, _SNAP_DATE, target_dir, zip_inputs, snapshot_id=snapshot_id
            )

        return result, mock_ensure, mock_start, mock_finish, mock_fail, mock_pub

    def test_returns_typed_result(self):
        result, *_ = self._run()
        assert isinstance(result, PublishRuntimeResult)

    def test_result_fields(self):
        result, *_ = self._run()
        assert result.data_source == _DS_ROW
        assert result.run_id == _RUN_ID
        assert result.snapshot_id == _SNAP_ID
        assert result.publish_result.succeeded is True

    def test_derives_snapshot_id_from_date(self):
        result, *_ = self._run()
        assert result.snapshot_id == _SNAP_DATE.isoformat()

    def test_explicit_snapshot_id_used(self):
        result, *_ = self._run(snapshot_id="custom-snap")
        assert result.snapshot_id == "custom-snap"

    def test_ensure_data_source_called(self):
        _, mock_ensure, *_ = self._run()
        mock_ensure.assert_called_once()

    def test_start_ingestion_run_called_with_export_type(self):
        _, _, mock_start, *_ = self._run()
        _conn, ds_id, run_type = mock_start.call_args[0]
        assert run_type == "export"
        assert ds_id == _DS_ROW["id"]

    def test_start_parameters_contain_snapshot_date(self):
        _, _, mock_start, *_ = self._run()
        params = mock_start.call_args[1]["parameters"]
        assert params["snapshot_date"] == _SNAP_DATE.isoformat()

    def test_publish_called_with_resolved_snapshot_id(self):
        _, *_, mock_pub = self._run()
        assert mock_pub.call_args[1]["snapshot_id"] == _SNAP_ID

    def test_finish_run_called_with_written_count(self):
        _, _, _, mock_finish, mock_fail, _ = self._run()
        mock_finish.assert_called_once()
        _conn, run_id, record_count = mock_finish.call_args[0]
        assert run_id == _RUN_ID
        assert record_count == 12
        mock_fail.assert_not_called()


# ---------------------------------------------------------------------------
# run_publish_runtime — publish pipeline raises
# ---------------------------------------------------------------------------


class TestRunPublishRuntimePublishException:
    def test_marks_run_failed_and_reraises(self, tmp_path: Path):
        conn = MagicMock()
        zip_inputs = MagicMock()
        boom = RuntimeError("disk full")
        target_dir = tmp_path / "publish"

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(_PUBLISH_SNAP, side_effect=boom),
        ):
            with pytest.raises(RuntimeError, match="disk full"):
                run_publish_runtime(conn, _SNAP_DATE, target_dir, zip_inputs)

        mock_fail.assert_called_once()
        _conn, run_id, msg = mock_fail.call_args[0]
        assert run_id == _RUN_ID
        assert "disk full" in msg
        mock_finish.assert_not_called()


# ---------------------------------------------------------------------------
# run_publish_runtime — publish returns succeeded=False
# ---------------------------------------------------------------------------


class TestRunPublishRuntimePublishFailure:
    def test_marks_run_failed_and_raises_on_verification_failures(self, tmp_path: Path):
        conn = MagicMock()
        zip_inputs = MagicMock()
        target_dir = tmp_path / "publish"

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(_PUBLISH_SNAP, return_value=_failed_publish_result(["bad hash", "missing file"])),
        ):
            with pytest.raises(RuntimeError, match="bad hash"):
                run_publish_runtime(conn, _SNAP_DATE, target_dir, zip_inputs)

        mock_fail.assert_called_once()
        _conn, run_id, msg = mock_fail.call_args[0]
        assert run_id == _RUN_ID
        assert "bad hash" in msg
        mock_finish.assert_not_called()


class TestRunPublishRuntimePromotion:
    def test_promotes_staged_tree_on_success(self, tmp_path: Path) -> None:
        conn = MagicMock()
        zip_inputs = MagicMock()
        target_dir = tmp_path / "publish"
        target_dir.mkdir()
        (target_dir / "old.json").write_text("old", encoding="utf-8")
        seen: dict[str, Path] = {}

        def _publish(*args, **kwargs):
            staging_dir = kwargs["target_dir"]
            seen["staging_dir"] = staging_dir
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "new.json").write_text("new", encoding="utf-8")
            return _succeeded_publish_result()

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_PUBLISH_SNAP, side_effect=_publish),
        ):
            run_publish_runtime(conn, _SNAP_DATE, target_dir, zip_inputs)

        assert seen["staging_dir"] != target_dir
        assert seen["staging_dir"].parent == target_dir.parent
        assert (target_dir / "new.json").read_text(encoding="utf-8") == "new"
        assert not (target_dir / "old.json").exists()

    def test_failed_publish_preserves_existing_target_tree(self, tmp_path: Path) -> None:
        conn = MagicMock()
        zip_inputs = MagicMock()
        target_dir = tmp_path / "publish"
        target_dir.mkdir()
        (target_dir / "old.json").write_text("old", encoding="utf-8")
        seen: dict[str, Path] = {}

        def _publish(*args, **kwargs):
            staging_dir = kwargs["target_dir"]
            seen["staging_dir"] = staging_dir
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "new.json").write_text("new", encoding="utf-8")
            return _failed_publish_result(["bad hash"])

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(_PUBLISH_SNAP, side_effect=_publish),
        ):
            with pytest.raises(RuntimeError, match="bad hash"):
                run_publish_runtime(conn, _SNAP_DATE, target_dir, zip_inputs)

        assert seen["staging_dir"] != target_dir
        assert (target_dir / "old.json").read_text(encoding="utf-8") == "old"
        assert not (target_dir / "new.json").exists()
        mock_fail.assert_called_once()
        mock_finish.assert_not_called()
