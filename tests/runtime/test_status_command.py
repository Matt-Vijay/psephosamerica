"""Tests for src/runtime/status_command.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.runtime.status_command import get_runtime_status, get_runtime_status_summary

_MOD = "src.runtime.status_command"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_INGESTION_ROW = {
    "id": 1,
    "run_type": "ingest",
    "status": "succeeded",
    "started_at": None,
}
_PARSE_ROW = {"id": 2, "parser_name": "senate_pdf", "status": "succeeded"}
_DATA_SOURCE_ROW = {"id": 3, "slug": "congress-gov-api", "active": True}
_ARTIFACT_ROW = {
    "id": 10,
    "artifact_kind": "pdf",
    "storage_uri": "r2://disclosures/abc.pdf",
    "sha256": "a" * 64,
    "fetched_at": None,
    "ingestion_run_id": 1,
    "data_source_slug": "efdsearch-senate-gov",
}


def _patched(
    ingestion_rows=None,
    parse_rows=None,
    data_source_rows=None,
    artifact_rows=None,
):
    return (
        patch(f"{_MOD}.latest_ingestion_runs", return_value=ingestion_rows or []),
        patch(f"{_MOD}.latest_parse_runs", return_value=parse_rows or []),
        patch(f"{_MOD}.current_data_sources", return_value=data_source_rows or []),
        patch(f"{_MOD}.latest_source_artifacts", return_value=artifact_rows or []),
    )


# ---------------------------------------------------------------------------
# get_runtime_status
# ---------------------------------------------------------------------------


class TestGetRuntimeStatus:
    def test_calls_all_four_fetchers(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as mock_ds, p_art as mock_art:
            get_runtime_status(conn)

        mock_ing.assert_called_once()
        mock_par.assert_called_once()
        mock_ds.assert_called_once()
        mock_art.assert_called_once()

    def test_conn_forwarded_to_all_fetchers(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as mock_ds, p_art as mock_art:
            get_runtime_status(conn)

        assert mock_ing.call_args.args[0] is conn
        assert mock_par.call_args.args[0] is conn
        assert mock_ds.call_args.args[0] is conn
        assert mock_art.call_args.args[0] is conn

    def test_default_limit_forwarded_to_row_fetchers(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as _, p_art as mock_art:
            get_runtime_status(conn)

        assert mock_ing.call_args.kwargs["limit"] == 20
        assert mock_par.call_args.kwargs["limit"] == 20
        assert mock_art.call_args.kwargs["limit"] == 20

    def test_custom_limit_forwarded(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as _, p_art as mock_art:
            get_runtime_status(conn, limit=5)

        assert mock_ing.call_args.kwargs["limit"] == 5
        assert mock_par.call_args.kwargs["limit"] == 5
        assert mock_art.call_args.kwargs["limit"] == 5

    def test_result_contains_required_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert "ingestion_runs" in result
        assert "parse_runs" in result
        assert "data_sources" in result
        assert "source_artifacts" in result
        assert "summary" in result

    def test_fetched_rows_appear_in_result(self):
        conn = MagicMock()
        ing = [_INGESTION_ROW]
        par = [_PARSE_ROW]
        ds = [_DATA_SOURCE_ROW]
        art = [_ARTIFACT_ROW]
        p_ing, p_par, p_ds, p_art = _patched(ing, par, ds, art)

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert result["ingestion_runs"] == ing
        assert result["parse_runs"] == par
        assert result["data_sources"] == ds
        assert result["source_artifacts"] == art

    def test_summary_counts_match_fetched_rows(self):
        conn = MagicMock()
        ing = [_INGESTION_ROW, _INGESTION_ROW]
        par = [_PARSE_ROW]
        ds = [_DATA_SOURCE_ROW, _DATA_SOURCE_ROW, _DATA_SOURCE_ROW]
        art = [_ARTIFACT_ROW, _ARTIFACT_ROW]
        p_ing, p_par, p_ds, p_art = _patched(ing, par, ds, art)

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert result["summary"]["ingestion_run_count"] == 2
        assert result["summary"]["parse_run_count"] == 1
        assert result["summary"]["data_source_count"] == 3
        assert result["summary"]["source_artifact_count"] == 2

    def test_empty_sources_produce_zero_counts(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert result["summary"]["ingestion_run_count"] == 0
        assert result["summary"]["parse_run_count"] == 0
        assert result["summary"]["data_source_count"] == 0
        assert result["summary"]["source_artifact_count"] == 0

    def test_returns_dict(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert isinstance(result, dict)

    def test_exact_top_level_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert set(result.keys()) == {
            "ingestion_runs", "parse_runs", "data_sources",
            "source_artifacts", "summary",
        }

    def test_exact_summary_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status(conn)

        assert set(result["summary"].keys()) == {
            "ingestion_run_count", "parse_run_count",
            "data_source_count", "source_artifact_count",
        }


# ---------------------------------------------------------------------------
# get_runtime_status_summary
# ---------------------------------------------------------------------------


class TestGetRuntimeStatusSummary:
    def test_returns_compact_status_shape(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched(
            [_INGESTION_ROW],
            [_PARSE_ROW],
            [_DATA_SOURCE_ROW],
            [_ARTIFACT_ROW],
        )

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert "summary" in result
        assert "latest_ingestion_run" in result
        assert "latest_artifact" in result
        assert "ingestion_runs" not in result
        assert "parse_runs" not in result
        assert "data_sources" not in result
        assert "source_artifacts" not in result

    def test_counts_match_fetched_rows(self):
        conn = MagicMock()
        ing = [_INGESTION_ROW, _INGESTION_ROW]
        par = [_PARSE_ROW]
        ds = [_DATA_SOURCE_ROW]
        art = [_ARTIFACT_ROW, _ARTIFACT_ROW, _ARTIFACT_ROW]
        p_ing, p_par, p_ds, p_art = _patched(ing, par, ds, art)

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert result["summary"]["ingestion_run_count"] == 2
        assert result["summary"]["parse_run_count"] == 1
        assert result["summary"]["data_source_count"] == 1
        assert result["summary"]["source_artifact_count"] == 3

    def test_latest_artifact_present_when_artifacts_exist(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched(artifact_rows=[_ARTIFACT_ROW])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        la = result["latest_artifact"]
        assert la is not None
        assert la["id"] == _ARTIFACT_ROW["id"]
        assert la["artifact_kind"] == _ARTIFACT_ROW["artifact_kind"]
        assert la["data_source"] == _ARTIFACT_ROW["data_source_slug"]

    def test_latest_artifact_none_when_no_artifacts(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert result["latest_artifact"]["id"] is None
        assert result["latest_artifact"]["artifact_kind"] is None

    def test_latest_artifact_is_first_row(self):
        conn = MagicMock()
        first = {**_ARTIFACT_ROW, "id": 99}
        second = {**_ARTIFACT_ROW, "id": 50}
        p_ing, p_par, p_ds, p_art = _patched(artifact_rows=[first, second])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert result["latest_artifact"]["id"] == 99

    def test_latest_ingestion_run_present_when_runs_exist(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched(ingestion_rows=[_INGESTION_ROW])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        lr = result["latest_ingestion_run"]
        assert lr is not None
        assert lr["id"] == _INGESTION_ROW["id"]
        assert lr["run_type"] == _INGESTION_ROW["run_type"]
        assert lr["status"] == _INGESTION_ROW["status"]
        assert lr["data_source"] is None
        assert lr["run_type"] == _INGESTION_ROW["run_type"]

    def test_latest_ingestion_run_none_when_no_runs(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert result["latest_ingestion_run"]["id"] is None
        assert result["latest_ingestion_run"]["status"] is None

    def test_latest_ingestion_run_compact_no_full_row(self):
        """latest_ingestion_run carries a subset of fields, not the full row."""
        conn = MagicMock()
        row = {**_INGESTION_ROW, "record_count": 500, "error_message": None, "parameters": {}}
        p_ing, p_par, p_ds, p_art = _patched(ingestion_rows=[row])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        lr = result["latest_ingestion_run"]
        assert "record_count" not in lr
        assert "error_message" not in lr

    def test_default_limit_forwarded(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as _, p_art as mock_art:
            get_runtime_status_summary(conn)

        assert mock_ing.call_args.kwargs["limit"] == 20
        assert mock_par.call_args.kwargs["limit"] == 20
        assert mock_art.call_args.kwargs["limit"] == 20

    def test_custom_limit_forwarded(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing as mock_ing, p_par as mock_par, p_ds as _, p_art as mock_art:
            get_runtime_status_summary(conn, limit=7)

        assert mock_ing.call_args.kwargs["limit"] == 7
        assert mock_par.call_args.kwargs["limit"] == 7
        assert mock_art.call_args.kwargs["limit"] == 7

    def test_returns_dict(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert isinstance(result, dict)

    def test_empty_sources_produce_zero_counts(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert result["summary"]["ingestion_run_count"] == 0
        assert result["summary"]["parse_run_count"] == 0
        assert result["summary"]["data_source_count"] == 0
        assert result["summary"]["source_artifact_count"] == 0

    def test_exact_top_level_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched()

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert set(result.keys()) == {"summary", "latest_ingestion_run", "latest_artifact"}

    def test_latest_ingestion_run_exact_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched(ingestion_rows=[_INGESTION_ROW])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert set(result["latest_ingestion_run"].keys()) == {
            "id", "run_type", "status", "data_source",
        }

    def test_latest_artifact_exact_keys(self):
        conn = MagicMock()
        p_ing, p_par, p_ds, p_art = _patched(artifact_rows=[_ARTIFACT_ROW])

        with p_ing, p_par, p_ds, p_art:
            result = get_runtime_status_summary(conn)

        assert set(result["latest_artifact"].keys()) == {
            "id", "artifact_kind", "data_source",
        }
