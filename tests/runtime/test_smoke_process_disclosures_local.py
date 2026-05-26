"""Tests for src/runtime/smoke_process_disclosures_local.py.

No live DB, no network, no filesystem writes.
run_disclosures_bundle_process is patched at the smoke module's import path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.smoke_process_disclosures_local import smoke_process_disclosures_local

_MODULE = "src.runtime.smoke_process_disclosures_local"
_LOCAL_ROOT = Path("/tmp/test_bundle_artifacts")

_EXPECTED_KEYS = {
    "run_id",
    "source_slug",
    "staged",
    "mirrored",
    "parsed",
    "parse_succeeded",
    "parse_failed",
    "transformed",
    "total_written",
    "load_ok",
}


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _pipeline_result(
    *,
    run_id: int = 1,
    source_slug: str = "disclosure-load",
    staged: int = 3,
    mirrored: int = 3,
    processed: int = 3,
    succeeded: int = 2,
    failed: int = 1,
    transform_count: int = 2,
    total_written: int = 5,
    load_ok: bool = True,
) -> MagicMock:
    r = MagicMock()
    r.load_result.run_id = run_id
    r.load_result.data_source = {"id": 7, "slug": source_slug}
    r.stage_result.staged_count = staged
    r.stage_result.mirrored_count = mirrored
    r.parse_result.processed_count = processed
    r.parse_result.succeeded_count = succeeded
    r.parse_result.failed_count = failed
    r.transform_count = transform_count
    r.load_result.load_summary.total_written = total_written
    r.load_result.load_summary.ok = load_ok
    return r


def _run(
    conn=None,
    bundle=None,
    *,
    local_root: Path | None = _LOCAL_ROOT,
    run_result=None,
):
    conn = conn or MagicMock()
    bundle = bundle or MagicMock()
    result = run_result if run_result is not None else _pipeline_result()
    with patch(f"{_MODULE}.run_disclosures_bundle_process", return_value=result) as mock_run:
        summary = smoke_process_disclosures_local(conn, bundle, local_root=local_root)
    return summary, mock_run, conn, bundle


# ---------------------------------------------------------------------------
# Key shape
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_returns_dict_with_expected_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary) == _EXPECTED_KEYS

    def test_returns_plain_dict_not_subclass(self) -> None:
        summary, *_ = _run()
        assert type(summary) is dict


# ---------------------------------------------------------------------------
# Value mapping
# ---------------------------------------------------------------------------


class TestValueMapping:
    def test_run_id_from_load_result(self) -> None:
        result = _pipeline_result(run_id=42)
        summary, *_ = _run(run_result=result)
        assert summary["run_id"] == 42

    def test_source_slug_from_data_source(self) -> None:
        result = _pipeline_result(source_slug="house-disclosures")
        summary, *_ = _run(run_result=result)
        assert summary["source_slug"] == "house-disclosures"

    def test_staged_from_stage_result(self) -> None:
        result = _pipeline_result(staged=5)
        summary, *_ = _run(run_result=result)
        assert summary["staged"] == 5

    def test_mirrored_from_stage_result(self) -> None:
        result = _pipeline_result(mirrored=4)
        summary, *_ = _run(run_result=result)
        assert summary["mirrored"] == 4

    def test_mirrored_zero_when_no_local_root(self) -> None:
        result = _pipeline_result(mirrored=0)
        summary, *_ = _run(local_root=None, run_result=result)
        assert summary["mirrored"] == 0

    def test_parse_counts(self) -> None:
        result = _pipeline_result(processed=10, succeeded=7, failed=3)
        summary, *_ = _run(run_result=result)
        assert summary["parsed"] == 10
        assert summary["parse_succeeded"] == 7
        assert summary["parse_failed"] == 3

    def test_transformed_count(self) -> None:
        result = _pipeline_result(transform_count=4)
        summary, *_ = _run(run_result=result)
        assert summary["transformed"] == 4

    def test_total_written_from_load_summary(self) -> None:
        result = _pipeline_result(total_written=12)
        summary, *_ = _run(run_result=result)
        assert summary["total_written"] == 12

    def test_load_ok_true(self) -> None:
        result = _pipeline_result(load_ok=True)
        summary, *_ = _run(run_result=result)
        assert summary["load_ok"] is True

    def test_load_ok_false_on_errors(self) -> None:
        result = _pipeline_result(load_ok=False)
        summary, *_ = _run(run_result=result)
        assert summary["load_ok"] is False

    def test_zero_counts_are_valid(self) -> None:
        result = _pipeline_result(
            staged=0,
            mirrored=0,
            processed=0,
            succeeded=0,
            failed=0,
            transform_count=0,
            total_written=0,
        )
        summary, *_ = _run(run_result=result)
        assert summary["staged"] == 0
        assert summary["mirrored"] == 0
        assert summary["parsed"] == 0
        assert summary["parse_succeeded"] == 0
        assert summary["parse_failed"] == 0
        assert summary["transformed"] == 0
        assert summary["total_written"] == 0


# ---------------------------------------------------------------------------
# Argument forwarding
# ---------------------------------------------------------------------------


class TestArgForwarding:
    def test_conn_forwarded_as_first_positional_arg(self) -> None:
        conn = MagicMock()
        _, mock_run, *_ = _run(conn=conn)
        assert mock_run.call_args[0][0] is conn

    def test_bundle_forwarded_as_second_positional_arg(self) -> None:
        bundle = MagicMock()
        _, mock_run, _, _ = _run(bundle=bundle)
        assert mock_run.call_args[0][1] is bundle

    def test_local_root_forwarded(self) -> None:
        root = Path("/data/disclosures")
        _, mock_run, *_ = _run(local_root=root)
        _, kwargs = mock_run.call_args
        assert kwargs["local_root"] == root

    def test_local_root_none_forwarded(self) -> None:
        _, mock_run, *_ = _run(local_root=None)
        _, kwargs = mock_run.call_args
        assert kwargs["local_root"] is None

    def test_pipeline_called_exactly_once(self) -> None:
        _, mock_run, *_ = _run()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# JSON contract: full summary is operator-safe JSON
# ---------------------------------------------------------------------------


class TestJsonContract:
    def test_summary_is_json_serializable(self) -> None:
        summary, *_ = _run()
        raw = json.dumps(summary)
        obj = json.loads(raw)
        assert set(obj) == _EXPECTED_KEYS

    def test_all_values_are_primitives(self) -> None:
        """Operator JSON must contain only str, int, bool, None — no objects."""
        summary, *_ = _run()
        for key, value in summary.items():
            assert isinstance(value, (str, int, bool, type(None))), (
                f"{key} has type {type(value).__name__}, expected primitive"
            )
