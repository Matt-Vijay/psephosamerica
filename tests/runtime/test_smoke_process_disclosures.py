"""Tests for src/runtime/smoke_process_disclosures.py.

No live DB, no network, no filesystem writes.
All pipeline boundaries are patched at the smoke module's import path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.smoke_process_disclosures import smoke_process_disclosures

_MODULE = "src.runtime.smoke_process_disclosures"
_LOCAL_ROOT = Path("/tmp/test_artifacts")

_EXPECTED_KEYS = {
    "run_id",
    "source_slug",
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
    r.parse_result.processed_count = processed
    r.parse_result.succeeded_count = succeeded
    r.parse_result.failed_count = failed
    r.transform_count = transform_count
    r.load_result.load_summary.total_written = total_written
    r.load_result.load_summary.ok = load_ok
    return r


# ---------------------------------------------------------------------------
# Key shape
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_returns_dict_with_expected_keys(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert set(summary) == _EXPECTED_KEYS

    def test_returns_plain_dict_not_subclass(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert type(summary) is dict


# ---------------------------------------------------------------------------
# Value mapping
# ---------------------------------------------------------------------------


class TestValueMapping:
    def test_run_id_from_load_result(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(run_id=99),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["run_id"] == 99

    def test_source_slug_from_data_source(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(source_slug="house-load"),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["source_slug"] == "house-load"

    def test_parse_counts(self) -> None:
        result = _pipeline_result(processed=10, succeeded=7, failed=3)
        with patch(f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=result):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["parsed"] == 10
        assert summary["parse_succeeded"] == 7
        assert summary["parse_failed"] == 3

    def test_transformed_count(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(transform_count=4),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["transformed"] == 4

    def test_total_written_from_load_summary(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(total_written=12),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["total_written"] == 12

    def test_load_ok_true(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(load_ok=True),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["load_ok"] is True

    def test_load_ok_false_on_errors(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime",
            return_value=_pipeline_result(load_ok=False),
        ):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        assert summary["load_ok"] is False

    def test_zero_counts_are_valid(self) -> None:
        result = _pipeline_result(
            processed=0, succeeded=0, failed=0, transform_count=0, total_written=0
        )
        with patch(f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=result):
            summary = smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

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
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(conn, _LOCAL_ROOT)

        assert mock_run.call_args[0][0] is conn

    def test_local_root_forwarded(self) -> None:
        root = Path("/data/disclosures")
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), root)

        _, kwargs = mock_run.call_args
        assert kwargs["local_root"] == root

    def test_chamber_forwarded(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT, chamber="senate")

        _, kwargs = mock_run.call_args
        assert kwargs["chamber"] == "senate"

    def test_chamber_defaults_to_none(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        _, kwargs = mock_run.call_args
        assert kwargs.get("chamber") is None

    def test_limit_forwarded(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT, limit=10)

        _, kwargs = mock_run.call_args
        assert kwargs["limit"] == 10

    def test_limit_defaults_to_none(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        _, kwargs = mock_run.call_args
        assert kwargs.get("limit") is None

    def test_parser_name_forwarded(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT, parser_name="house_ocr_v2")

        _, kwargs = mock_run.call_args
        assert kwargs["parser_name"] == "house_ocr_v2"

    def test_parser_name_default(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        _, kwargs = mock_run.call_args
        assert kwargs["parser_name"] == "text_extract_v1"

    def test_parser_version_forwarded(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT, parser_version="2")

        _, kwargs = mock_run.call_args
        assert kwargs["parser_version"] == "2"

    def test_parser_version_default(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        _, kwargs = mock_run.call_args
        assert kwargs["parser_version"] == "1"

    def test_pipeline_called_exactly_once(self) -> None:
        with patch(
            f"{_MODULE}.run_disclosures_parse_load_runtime", return_value=_pipeline_result()
        ) as mock_run:
            smoke_process_disclosures(MagicMock(), _LOCAL_ROOT)

        mock_run.assert_called_once()
