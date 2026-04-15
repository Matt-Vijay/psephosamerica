"""Tests for src/runtime/recompute.py.

No live DB — all provenance and pipeline boundaries are mocked.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import LoadSummary, WarnErrorSummary, build_load_summary
from src.pipeline.recompute_run import RecomputeRunResult
from src.runtime.recompute import (
    RuntimeRecomputeResult,
    _null_issuer_sector_resolver,
    run_recompute_runtime,
)

# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_RUN_ID = 7
_DATA_SOURCE = {"id": 3, "slug": "recompute_internal", "name": "Open Pact Internal Recompute"}

_MODULE = "src.runtime.recompute"


def _empty_run_result() -> RecomputeRunResult:
    return RecomputeRunResult(rule_fires=[], evidence_cards=[], load_summary=None)


def _load_summary() -> LoadSummary:
    return build_load_summary([], warn_error=WarnErrorSummary(), run_id=_RUN_ID)


def _taxonomy() -> Any:
    return MagicMock()


# ---------------------------------------------------------------------------
# Null resolver
# ---------------------------------------------------------------------------


def test_null_issuer_sector_resolver_always_none() -> None:
    assert _null_issuer_sector_resolver("Acme Corp", "ACM") is None
    assert _null_issuer_sector_resolver("Unknown", None) is None


# ---------------------------------------------------------------------------
# Happy-path: all boundaries explicit
# ---------------------------------------------------------------------------


def test_run_recompute_runtime_returns_typed_result() -> None:
    conn = MagicMock()
    taxonomy = _taxonomy()
    run_result = _empty_run_result()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", return_value=run_result),
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run") as mock_fail,
    ):
        result = run_recompute_runtime(
            conn,
            _SNAPSHOT_DATE,
            taxonomy=taxonomy,
            issuer_sector_resolver=lambda n, t: None,
        )

    assert isinstance(result, RuntimeRecomputeResult)
    assert result.data_source is _DATA_SOURCE
    assert result.run_id == _RUN_ID
    assert result.recompute_result is run_result
    mock_fail.assert_not_called()


def test_provenance_calls_in_order() -> None:
    """ensure_data_source → start_ingestion_run → finish_ingestion_run."""
    conn = MagicMock()
    call_log: list[str] = []

    def _ensure(*_a, **_kw):
        call_log.append("ensure")
        return _DATA_SOURCE

    def _start(*_a, **_kw):
        call_log.append("start")
        return _RUN_ID

    def _finish(*_a, **_kw):
        call_log.append("finish")

    with (
        patch(f"{_MODULE}.ensure_data_source", side_effect=_ensure),
        patch(f"{_MODULE}.start_ingestion_run", side_effect=_start),
        patch(f"{_MODULE}.run_recompute", return_value=_empty_run_result()),
        patch(f"{_MODULE}.finish_ingestion_run", side_effect=_finish),
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    assert call_log == ["ensure", "start", "finish"]


# ---------------------------------------------------------------------------
# run_recompute is called with the correct keyword arguments
# ---------------------------------------------------------------------------


def test_run_recompute_receives_correct_kwargs() -> None:
    conn = MagicMock()
    taxonomy = _taxonomy()

    def resolver(name: str, ticker: str | None) -> str | None:
        return "finance"

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", return_value=_empty_run_result()) as mock_run,
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(
            conn,
            _SNAPSHOT_DATE,
            taxonomy=taxonomy,
            issuer_sector_resolver=resolver,
        )

    mock_run.assert_called_once_with(
        conn,
        recompute_run_id=_RUN_ID,
        snapshot_date=_SNAPSHOT_DATE,
        taxonomy=taxonomy,
        issuer_sector_resolver=resolver,
    )


# ---------------------------------------------------------------------------
# record_count passed to finish_ingestion_run
# ---------------------------------------------------------------------------


def test_finish_receives_combined_record_count() -> None:
    from src.rules.models import RuleFire

    conn = MagicMock()
    fires = [MagicMock(spec=RuleFire), MagicMock(spec=RuleFire)]
    cards = [MagicMock()]
    run_result = RecomputeRunResult(rule_fires=fires, evidence_cards=cards, load_summary=None)

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", return_value=run_result),
        patch(f"{_MODULE}.finish_ingestion_run") as mock_finish,
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    mock_finish.assert_called_once_with(conn, _RUN_ID, record_count=3)


# ---------------------------------------------------------------------------
# Failure: run_recompute raises → fail_ingestion_run called, exception re-raised
# ---------------------------------------------------------------------------


def test_pipeline_failure_marks_run_failed_and_reraises() -> None:
    conn = MagicMock()
    boom = RuntimeError("db exploded")

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", side_effect=boom),
        patch(f"{_MODULE}.finish_ingestion_run") as mock_finish,
        patch(f"{_MODULE}.fail_ingestion_run") as mock_fail,
    ):
        with pytest.raises(RuntimeError, match="db exploded"):
            run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    mock_fail.assert_called_once_with(conn, _RUN_ID, error_message="db exploded")
    mock_finish.assert_not_called()


# ---------------------------------------------------------------------------
# Default resolvers: taxonomy loaded lazily; null resolver used when absent
# ---------------------------------------------------------------------------


def test_null_resolver_used_when_issuer_resolver_omitted() -> None:
    """run_recompute should receive _null_issuer_sector_resolver by default."""
    conn = MagicMock()
    captured: dict = {}

    def _capture_run(conn, *, issuer_sector_resolver, **_kw):
        captured["resolver"] = issuer_sector_resolver
        return _empty_run_result()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", side_effect=_capture_run),
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    resolver = captured["resolver"]
    # The default resolver is deterministic and always returns None
    assert resolver("Anything", "TICK") is None


def test_taxonomy_loaded_from_data_root_when_none() -> None:
    """When taxonomy=None, load_taxonomy_runtime should be called once."""
    conn = MagicMock()
    fake_taxonomy = _taxonomy()
    captured: dict[str, Any] = {}

    def capture_run(conn: Any, **kwargs: Any) -> RecomputeRunResult:
        captured["taxonomy"] = kwargs["taxonomy"]
        return _empty_run_result()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", side_effect=capture_run),
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run"),
        patch("src.normalize.taxonomy_runtime.load_taxonomy_runtime", return_value=fake_taxonomy) as mock_load,
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE)

    mock_load.assert_called_once()
    assert captured["taxonomy"] is fake_taxonomy


# ---------------------------------------------------------------------------
# Source constants are correct
# ---------------------------------------------------------------------------


def test_ensure_data_source_called_with_canonical_slug() -> None:
    conn = MagicMock()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE) as mock_ensure,
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID),
        patch(f"{_MODULE}.run_recompute", return_value=_empty_run_result()),
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    args, kwargs = mock_ensure.call_args
    slug = kwargs.get("slug") or args[1]
    assert slug == "conflict-recompute"


def test_start_ingestion_run_called_with_recompute_type() -> None:
    conn = MagicMock()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value=_DATA_SOURCE),
        patch(f"{_MODULE}.start_ingestion_run", return_value=_RUN_ID) as mock_start,
        patch(f"{_MODULE}.run_recompute", return_value=_empty_run_result()),
        patch(f"{_MODULE}.finish_ingestion_run"),
        patch(f"{_MODULE}.fail_ingestion_run"),
    ):
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    args, kwargs = mock_start.call_args
    run_type = kwargs.get("run_type") or args[2]
    assert run_type == "recompute"
