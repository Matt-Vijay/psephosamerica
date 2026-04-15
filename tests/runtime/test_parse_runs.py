"""Tests for src/runtime/parse_runs.py.

No live DB — provenance artifact helpers are mocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.parse_runs import ParseSessionResult, run_parse_session

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_CREATE = "src.runtime.parse_runs.create_parse_run"
_FINISH = "src.runtime.parse_runs.finish_parse_run"
_FAIL = "src.runtime.parse_runs.fail_parse_run"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RUN_ID = 11
_ARTIFACT_ID = 5
_PARSER_NAME = "senate_pdf"
_PARSER_VERSION = "1.0.0"


def _dict_result(
    page_count: int = 3,
    ocr_page_count: int = 1,
    confidence_summary: dict[str, Any] | None = None,
    **extra,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "page_count": page_count,
        "ocr_page_count": ocr_page_count,
        "confidence_summary": confidence_summary or {"avg": 0.95},
    }
    base.update(extra)
    return base


@dataclass
class _ObjResult:
    page_count: int | None = 4
    ocr_page_count: int | None = 2
    confidence_summary: dict[str, Any] | None = None
    payload: str = "parsed"

    def __post_init__(self):
        if self.confidence_summary is None:
            self.confidence_summary = {"avg": 0.80}


def _make_parse_fn(result):
    return lambda: result


def _patch_all(parse_fn=None):
    import contextlib

    if parse_fn is None:
        parse_fn = _make_parse_fn(_dict_result())

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(_CREATE, return_value=_RUN_ID) as mock_create,
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
        ):
            yield {"create": mock_create, "finish": mock_finish, "fail": mock_fail}

    return _ctx()


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_parse_session_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(_dict_result()),
            )
        assert isinstance(result, ParseSessionResult)

    def test_result_has_run_id(self):
        conn = MagicMock()
        with _patch_all():
            result = run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(_dict_result()),
            )
        assert result.run_id == _RUN_ID

    def test_result_carries_dict_parse_result(self):
        conn = MagicMock()
        payload = _dict_result(extra_field="abc")
        with _patch_all():
            result = run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(payload),
            )
        assert result.parse_result is payload

    def test_result_carries_object_parse_result(self):
        conn = MagicMock()
        obj = _ObjResult()
        with _patch_all():
            result = run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(obj),
            )
        assert result.parse_result is obj


# ---------------------------------------------------------------------------
# Lifecycle ordering
# ---------------------------------------------------------------------------


class TestLifecycleOrdering:
    def test_create_called_before_parse_fn(self):
        order: list[str] = []
        conn = MagicMock()

        def _parse():
            order.append("parse")
            return _dict_result()

        with (
            patch(_CREATE, side_effect=lambda *a, **k: order.append("create") or _RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_parse,
            )

        assert order.index("create") < order.index("parse")

    def test_finish_called_after_parse_fn(self):
        order: list[str] = []
        conn = MagicMock()

        def _parse():
            order.append("parse")
            return _dict_result()

        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH, side_effect=lambda *a, **k: order.append("finish")),
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_parse,
            )

        assert order.index("parse") < order.index("finish")

    def test_fail_not_called_on_success(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(_dict_result()),
            )
        mocks["fail"].assert_not_called()

    def test_finish_not_called_on_failure(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
        ):
            with pytest.raises(RuntimeError):
                run_parse_session(
                    conn,
                    source_artifact_id=_ARTIFACT_ID,
                    parser_name=_PARSER_NAME,
                    parser_version=_PARSER_VERSION,
                    parse_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                )
        mock_finish.assert_not_called()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_parse_error_marks_run_failed(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
        ):
            with pytest.raises(ValueError):
                run_parse_session(
                    conn,
                    source_artifact_id=_ARTIFACT_ID,
                    parser_name=_PARSER_NAME,
                    parser_version=_PARSER_VERSION,
                    parse_fn=lambda: (_ for _ in ()).throw(ValueError("bad pdf")),
                )
        mock_fail.assert_called_once_with(conn, _RUN_ID, "bad pdf")

    def test_parse_error_is_reraised(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
        ):
            with pytest.raises(ValueError, match="bad pdf"):
                run_parse_session(
                    conn,
                    source_artifact_id=_ARTIFACT_ID,
                    parser_name=_PARSER_NAME,
                    parser_version=_PARSER_VERSION,
                    parse_fn=lambda: (_ for _ in ()).throw(ValueError("bad pdf")),
                )

    def test_fail_receives_correct_run_id(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=42),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
        ):
            with pytest.raises(RuntimeError):
                run_parse_session(
                    conn,
                    source_artifact_id=_ARTIFACT_ID,
                    parser_name=_PARSER_NAME,
                    parser_version=_PARSER_VERSION,
                    parse_fn=lambda: (_ for _ in ()).throw(RuntimeError("oops")),
                )
        mock_fail.assert_called_once_with(conn, 42, "oops")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_create_receives_artifact_id_name_version(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID) as mock_create,
            patch(_FINISH),
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=7,
                parser_name="house_ocr",
                parser_version="2.1.0",
                parse_fn=_make_parse_fn(_dict_result()),
            )
        mock_create.assert_called_once()
        args = mock_create.call_args[0]
        assert args[1] == 7
        assert args[2] == "house_ocr"
        assert args[3] == "2.1.0"

    def test_create_receives_ingestion_run_id_when_provided(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID) as mock_create,
            patch(_FINISH),
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(_dict_result()),
                ingestion_run_id=99,
            )
        assert mock_create.call_args[1]["ingestion_run_id"] == 99

    def test_ingestion_run_id_defaults_to_none(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID) as mock_create,
            patch(_FINISH),
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(_dict_result()),
            )
        assert mock_create.call_args[1].get("ingestion_run_id") is None

    def test_finish_receives_metrics_from_dict_result(self):
        conn = MagicMock()
        summary = {"avg": 0.91}
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(
                    _dict_result(page_count=5, ocr_page_count=2, confidence_summary=summary)
                ),
            )
        mock_finish.assert_called_once_with(
            conn,
            _RUN_ID,
            page_count=5,
            ocr_page_count=2,
            confidence_summary=summary,
        )

    def test_finish_receives_metrics_from_object_result(self):
        conn = MagicMock()
        summary = {"avg": 0.78}
        obj = _ObjResult(page_count=6, ocr_page_count=3, confidence_summary=summary)
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(obj),
            )
        mock_finish.assert_called_once_with(
            conn,
            _RUN_ID,
            page_count=6,
            ocr_page_count=3,
            confidence_summary=summary,
        )

    def test_finish_tolerates_none_metrics(self):
        conn = MagicMock()
        with (
            patch(_CREATE, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
        ):
            run_parse_session(
                conn,
                source_artifact_id=_ARTIFACT_ID,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_fn=_make_parse_fn(
                    {"page_count": None, "ocr_page_count": None, "confidence_summary": None}
                ),
            )
        mock_finish.assert_called_once_with(
            conn,
            _RUN_ID,
            page_count=None,
            ocr_page_count=None,
            confidence_summary=None,
        )
