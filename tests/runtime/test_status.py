"""Tests for src/runtime/status.py.

No live DB — fetch_all is mocked at the module boundary.
Covers: SQL is issued with correct params, return values are forwarded,
        and runtime_status_dict assembles the dict correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.runtime.status import (
    current_data_sources,
    latest_ingestion_runs,
    latest_parse_runs,
    latest_source_artifacts,
    runtime_status_dict,
)

_FETCH_ALL = "src.runtime.status.fetch_all"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_INGESTION_ROW = {
    "id": 1,
    "run_type": "ingest",
    "status": "succeeded",
    "started_at": None,
    "finished_at": None,
    "record_count": 10,
    "error_message": None,
    "parameters": {},
    "data_source_slug": "congress-gov-api",
    "data_source_name": "Congress.gov API",
}

_PARSE_ROW = {
    "id": 2,
    "parser_name": "senate_pdf",
    "parser_version": "1.0",
    "status": "succeeded",
    "started_at": None,
    "finished_at": None,
    "page_count": 5,
    "ocr_page_count": 0,
    "error_message": None,
}

_SOURCE_ARTIFACT_ROW = {
    "id": 10,
    "artifact_kind": "pdf",
    "source_url": "https://efdsearch.senate.gov/search/view/annual/abc123/",
    "storage_uri": "r2://psephosamerica-raw/senate/annual/abc123.pdf",
    "sha256": "a" * 64,
    "mime_type": "application/pdf",
    "fetched_at": None,
    "is_immutable": True,
    "source_record_id": "abc123",
    "created_at": None,
    "data_source_slug": "senate-efdsearch",
    "data_source_name": "Senate EFD Search",
}

_DATA_SOURCE_ROW = {
    "id": 3,
    "slug": "congress-gov-api",
    "name": "Congress.gov API",
    "source_kind": "official",
    "base_url": "https://api.congress.gov/v3",
    "active": True,
    "created_at": None,
    "updated_at": None,
}


# ---------------------------------------------------------------------------
# latest_ingestion_runs
# ---------------------------------------------------------------------------


class TestLatestIngestionRuns:
    def test_returns_fetch_all_result(self):
        conn = MagicMock()
        rows = [_INGESTION_ROW]

        with patch(_FETCH_ALL, return_value=rows):
            result = latest_ingestion_runs(conn)

        assert result is rows

    def test_default_limit_passed_as_param(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_ingestion_runs(conn)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 20}

    def test_custom_limit_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_ingestion_runs(conn, limit=5)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 5}

    def test_conn_forwarded_to_fetch_all(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_ingestion_runs(conn)

        passed_conn = mock_fetch.call_args.args[0]
        assert passed_conn is conn

    def test_sql_references_ingestion_run(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_ingestion_runs(conn)

        _conn, sql, _params = mock_fetch.call_args.args
        assert "ingestion_run" in sql.lower()

    def test_empty_result_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]):
            result = latest_ingestion_runs(conn)

        assert result == []


# ---------------------------------------------------------------------------
# latest_parse_runs
# ---------------------------------------------------------------------------


class TestLatestParseRuns:
    def test_returns_fetch_all_result(self):
        conn = MagicMock()
        rows = [_PARSE_ROW]

        with patch(_FETCH_ALL, return_value=rows):
            result = latest_parse_runs(conn)

        assert result is rows

    def test_default_limit_passed_as_param(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_parse_runs(conn)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 20}

    def test_custom_limit_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_parse_runs(conn, limit=3)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 3}

    def test_sql_references_parse_run(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_parse_runs(conn)

        _conn, sql, _params = mock_fetch.call_args.args
        assert "parse_run" in sql.lower()

    def test_conn_forwarded_to_fetch_all(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_parse_runs(conn)

        passed_conn = mock_fetch.call_args.args[0]
        assert passed_conn is conn


# ---------------------------------------------------------------------------
# current_data_sources
# ---------------------------------------------------------------------------


class TestCurrentDataSources:
    def test_returns_fetch_all_result(self):
        conn = MagicMock()
        rows = [_DATA_SOURCE_ROW]

        with patch(_FETCH_ALL, return_value=rows):
            result = current_data_sources(conn)

        assert result is rows

    def test_no_limit_param(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            current_data_sources(conn)

        args = mock_fetch.call_args.args
        # fetch_all(conn, sql) — no params arg for this query
        assert len(args) == 2

    def test_sql_filters_active(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            current_data_sources(conn)

        _conn, sql = mock_fetch.call_args.args
        assert "active" in sql.lower()

    def test_sql_references_data_source(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            current_data_sources(conn)

        _conn, sql = mock_fetch.call_args.args
        assert "data_source" in sql.lower()

    def test_conn_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            current_data_sources(conn)

        passed_conn = mock_fetch.call_args.args[0]
        assert passed_conn is conn


# ---------------------------------------------------------------------------
# runtime_status_dict  (pure — no DB)
# ---------------------------------------------------------------------------


class TestRuntimeStatusDict:
    def test_keys_present(self):
        result = runtime_status_dict([], [], [])
        assert "ingestion_runs" in result
        assert "parse_runs" in result
        assert "data_sources" in result
        assert "summary" in result

    def test_rows_forwarded_verbatim(self):
        ing = [_INGESTION_ROW]
        par = [_PARSE_ROW]
        ds = [_DATA_SOURCE_ROW]

        result = runtime_status_dict(ing, par, ds)

        assert result["ingestion_runs"] is ing
        assert result["parse_runs"] is par
        assert result["data_sources"] is ds

    def test_summary_counts_match_list_lengths(self):
        result = runtime_status_dict(
            [_INGESTION_ROW, _INGESTION_ROW],
            [_PARSE_ROW],
            [_DATA_SOURCE_ROW, _DATA_SOURCE_ROW, _DATA_SOURCE_ROW],
        )

        assert result["summary"]["ingestion_run_count"] == 2
        assert result["summary"]["parse_run_count"] == 1
        assert result["summary"]["data_source_count"] == 3

    def test_empty_inputs_produce_zero_counts(self):
        result = runtime_status_dict([], [], [])

        assert result["summary"]["ingestion_run_count"] == 0
        assert result["summary"]["parse_run_count"] == 0
        assert result["summary"]["data_source_count"] == 0

    def test_exact_top_level_keys_without_artifacts(self):
        result = runtime_status_dict([], [], [])
        assert set(result.keys()) == {"ingestion_runs", "parse_runs", "data_sources", "summary"}

    def test_exact_top_level_keys_with_artifacts(self):
        result = runtime_status_dict([], [], [], source_artifacts=[])
        assert set(result.keys()) == {
            "ingestion_runs",
            "parse_runs",
            "data_sources",
            "source_artifacts",
            "summary",
        }

    def test_exact_summary_keys_without_artifacts(self):
        result = runtime_status_dict([], [], [])
        assert set(result["summary"].keys()) == {
            "ingestion_run_count",
            "parse_run_count",
            "data_source_count",
        }

    def test_exact_summary_keys_with_artifacts(self):
        result = runtime_status_dict([], [], [], source_artifacts=[])
        assert set(result["summary"].keys()) == {
            "ingestion_run_count",
            "parse_run_count",
            "data_source_count",
            "source_artifact_count",
        }

    def test_pure_no_side_effects(self):
        ing = [_INGESTION_ROW]
        par = [_PARSE_ROW]
        ds = [_DATA_SOURCE_ROW]

        # Calling twice with same inputs must produce identical results.
        r1 = runtime_status_dict(ing, par, ds)
        r2 = runtime_status_dict(ing, par, ds)

        assert r1 == r2

    def test_source_artifacts_omitted_by_default(self):
        result = runtime_status_dict([], [], [])

        assert "source_artifacts" not in result
        assert "source_artifact_count" not in result["summary"]

    def test_source_artifacts_included_when_passed(self):
        arts = [_SOURCE_ARTIFACT_ROW]
        result = runtime_status_dict([], [], [], source_artifacts=arts)

        assert result["source_artifacts"] is arts

    def test_source_artifact_count_in_summary(self):
        arts = [_SOURCE_ARTIFACT_ROW, _SOURCE_ARTIFACT_ROW]
        result = runtime_status_dict([], [], [], source_artifacts=arts)

        assert result["summary"]["source_artifact_count"] == 2

    def test_source_artifact_empty_list_included(self):
        result = runtime_status_dict([], [], [], source_artifacts=[])

        assert result["source_artifacts"] == []
        assert result["summary"]["source_artifact_count"] == 0

    def test_existing_counts_unaffected_by_source_artifacts(self):
        result = runtime_status_dict(
            [_INGESTION_ROW],
            [_PARSE_ROW],
            [_DATA_SOURCE_ROW],
            source_artifacts=[_SOURCE_ARTIFACT_ROW],
        )

        assert result["summary"]["ingestion_run_count"] == 1
        assert result["summary"]["parse_run_count"] == 1
        assert result["summary"]["data_source_count"] == 1
        assert result["summary"]["source_artifact_count"] == 1


# ---------------------------------------------------------------------------
# latest_source_artifacts
# ---------------------------------------------------------------------------


class TestLatestSourceArtifacts:
    def test_returns_fetch_all_result(self):
        conn = MagicMock()
        rows = [_SOURCE_ARTIFACT_ROW]

        with patch(_FETCH_ALL, return_value=rows):
            result = latest_source_artifacts(conn)

        assert result is rows

    def test_default_limit_passed_as_param(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_source_artifacts(conn)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 20}

    def test_custom_limit_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_source_artifacts(conn, limit=7)

        _conn, sql, params = mock_fetch.call_args.args
        assert params == {"limit": 7}

    def test_conn_forwarded_to_fetch_all(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_source_artifacts(conn)

        passed_conn = mock_fetch.call_args.args[0]
        assert passed_conn is conn

    def test_sql_references_source_artifact(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_source_artifacts(conn)

        _conn, sql, _params = mock_fetch.call_args.args
        assert "source_artifact" in sql.lower()

    def test_sql_joins_data_source(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]) as mock_fetch:
            latest_source_artifacts(conn)

        _conn, sql, _params = mock_fetch.call_args.args
        assert "data_source" in sql.lower()

    def test_empty_result_forwarded(self):
        conn = MagicMock()

        with patch(_FETCH_ALL, return_value=[]):
            result = latest_source_artifacts(conn)

        assert result == []
