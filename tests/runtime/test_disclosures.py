"""Tests for src/runtime/disclosures.py.

No live DB — provenance store and pipeline boundary are both mocked.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    FinancialDisclosurePayload,
    OutsidePositionSidecar,
)
from src.runtime.disclosures import (
    DisclosuresLoadRuntimeResult,
    run_disclosures_load_runtime,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_ENSURE = "src.runtime.disclosures.ensure_data_source"
_START = "src.runtime.disclosures.start_ingestion_run"
_FINISH = "src.runtime.disclosures.finish_ingestion_run"
_FAIL = "src.runtime.disclosures.fail_ingestion_run"
_LOAD = "src.runtime.disclosures.run_disclosures_load"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RUN_ID = 7
_DS_ROW: dict[str, Any] = {"id": 3, "slug": "financial-disclosures"}


def _disclosure() -> FinancialDisclosurePayload:
    return FinancialDisclosurePayload(
        member_bioguide_id="A000001",
        chamber="senate",
        filing_year=2024,
        filing_type="annual",
        amendment_number=0,
        is_amended=False,
        filed_at=date(2024, 5, 15),
    )


def _transform_result() -> DisclosureTransformResult:
    return DisclosureTransformResult(disclosure=_disclosure())


def _summary(inserted: int = 1) -> LoadSummary:
    return build_load_summary(
        [TableWriteResult(table="financial_disclosure", inserted=inserted)],
        warn_error=WarnErrorSummary(),
    )


def _sidecar() -> OutsidePositionSidecar:
    return OutsidePositionSidecar(
        line_number=1,
        owner_type="self",
        entity_name="Gamma LLC",
    )


def _patch_all(
    summary: LoadSummary | None = None,
    sidecars: tuple[OutsidePositionSidecar, ...] = (),
):
    """Return a context manager stack for the happy-path mock set."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(_ENSURE, return_value=_DS_ROW) as mock_ensure,
            patch(_START, return_value=_RUN_ID) as mock_start,
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(_LOAD, return_value=(summary or _summary(), sidecars)) as mock_load,
        ):
            yield {
                "ensure": mock_ensure,
                "start": mock_start,
                "finish": mock_finish,
                "fail": mock_fail,
                "load": mock_load,
            }

    return _ctx()


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_typed_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_load_runtime(conn, [])

        assert isinstance(result, DisclosuresLoadRuntimeResult)

    def test_result_contains_data_source(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_load_runtime(conn, [])

        assert result.data_source == _DS_ROW

    def test_result_contains_run_id(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_load_runtime(conn, [])

        assert result.run_id == _RUN_ID

    def test_result_contains_load_summary(self):
        conn = MagicMock()
        summary = _summary(inserted=5)
        with _patch_all(summary=summary):
            result = run_disclosures_load_runtime(conn, [])

        assert result.load_summary is summary

    def test_result_contains_sidecars(self):
        conn = MagicMock()
        sidecar = _sidecar()
        with _patch_all(sidecars=(sidecar,)):
            result = run_disclosures_load_runtime(conn, [])

        assert result.sidecars == (sidecar,)

    def test_empty_sidecars_when_none(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_load_runtime(conn, [])

        assert result.sidecars == ()


# ---------------------------------------------------------------------------
# Provenance step ordering
# ---------------------------------------------------------------------------


class TestProvenanceOrdering:
    def test_ensure_called_before_start(self):
        call_order: list[str] = []
        conn = MagicMock()

        with (
            patch(_ENSURE, side_effect=lambda *a, **k: call_order.append("ensure") or _DS_ROW),
            patch(_START, side_effect=lambda *a, **k: call_order.append("start") or _RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())),
        ):
            run_disclosures_load_runtime(conn, [])

        assert call_order.index("ensure") < call_order.index("start")

    def test_start_called_before_load(self):
        call_order: list[str] = []
        conn = MagicMock()

        with (
            patch(_ENSURE, side_effect=lambda *a, **k: call_order.append("ensure") or _DS_ROW),
            patch(_START, side_effect=lambda *a, **k: call_order.append("start") or _RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, side_effect=lambda *a, **k: call_order.append("load") or (_summary(), ())),
        ):
            run_disclosures_load_runtime(conn, [])

        assert call_order.index("start") < call_order.index("load")

    def test_finish_called_after_load(self):
        call_order: list[str] = []
        conn = MagicMock()

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH, side_effect=lambda *a, **k: call_order.append("finish")),
            patch(_FAIL),
            patch(_LOAD, side_effect=lambda *a, **k: call_order.append("load") or (_summary(), ())),
        ):
            run_disclosures_load_runtime(conn, [])

        assert call_order.index("load") < call_order.index("finish")

    def test_fail_not_called_on_success(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_load_runtime(conn, [])

        mocks["fail"].assert_not_called()

    def test_finish_not_called_on_failure(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
            patch(_LOAD, side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError):
                run_disclosures_load_runtime(conn, [])

        mock_finish.assert_not_called()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_pipeline_error_marks_run_failed(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_LOAD, side_effect=ValueError("bad data")),
        ):
            with pytest.raises(ValueError):
                run_disclosures_load_runtime(conn, [])

        mock_fail.assert_called_once_with(conn, _RUN_ID, "bad data")

    def test_pipeline_error_rolls_back_uncommitted_load_before_marking_failed(self):
        conn = MagicMock()
        order: list[str] = []
        conn.rollback.side_effect = lambda: order.append("rollback")

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL, side_effect=lambda *a, **k: order.append("fail")),
            patch(_LOAD, side_effect=ValueError("bad data")),
        ):
            with pytest.raises(ValueError):
                run_disclosures_load_runtime(conn, [])

        assert order == ["rollback", "fail"]

    def test_finish_error_rolls_back_uncommitted_load(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH, side_effect=RuntimeError("finish failed")),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())),
        ):
            with pytest.raises(RuntimeError, match="finish failed"):
                run_disclosures_load_runtime(conn, [])

        conn.rollback.assert_called_once()

    def test_pipeline_error_is_reraised(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, side_effect=ValueError("bad data")),
        ):
            with pytest.raises(ValueError, match="bad data"):
                run_disclosures_load_runtime(conn, [])

    def test_fail_receives_run_id(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=99),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_LOAD, side_effect=RuntimeError("oops")),
        ):
            with pytest.raises(RuntimeError):
                run_disclosures_load_runtime(conn, [])

        mock_fail.assert_called_once_with(conn, 99, "oops")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_start_receives_data_source_id(self):
        conn = MagicMock()
        ds_row = {"id": 42, "slug": "financial-disclosures"}
        with (
            patch(_ENSURE, return_value=ds_row),
            patch(_START, return_value=_RUN_ID) as mock_start,
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())),
        ):
            run_disclosures_load_runtime(conn, [])

        assert mock_start.call_args[0][1] == 42

    def test_start_uses_ingest_run_type(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID) as mock_start,
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())),
        ):
            run_disclosures_load_runtime(conn, [])

        assert mock_start.call_args[0][2] == "ingest"

    def test_load_receives_results_and_run_id(self):
        conn = MagicMock()
        results = [_transform_result()]
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())) as mock_load,
        ):
            run_disclosures_load_runtime(conn, results)

        args, kwargs = mock_load.call_args
        assert args[0] is results
        assert args[1] is conn
        assert kwargs.get("run_id") == _RUN_ID
        assert kwargs.get("commit") is False

    def test_finish_receives_total_written(self):
        conn = MagicMock()
        summary = _summary(inserted=3)
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
            patch(_LOAD, return_value=(summary, ())),
        ):
            run_disclosures_load_runtime(conn, [])

        mock_finish.assert_called_once_with(conn, _RUN_ID, summary.total_written)

    def test_load_receives_lookup_bundle_loader(self):
        """load_lookup_bundle is passed as lookup_loader (not called directly here)."""
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_LOAD, return_value=(_summary(), ())) as mock_load,
        ):
            run_disclosures_load_runtime(conn, [])

        from src.db.runtime_lookups import load_lookup_bundle

        _, kwargs = mock_load.call_args
        assert kwargs.get("lookup_loader") is load_lookup_bundle
