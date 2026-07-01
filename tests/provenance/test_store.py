"""Tests for provenance.store — DB helpers for data sources and ingestion runs.

All DB boundaries are mocked; no live Postgres required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)

_EXECUTE_ONE = "src.provenance.store.execute_one"
_FETCH_ALL = "src.provenance.store.fetch_all"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn_for_insert(row_id: int):
    """Return a mock conn whose cursor yields a RETURNING row with *row_id*."""
    cur = MagicMock()
    cur.fetchone.return_value = {"id": row_id}
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# ensure_data_source
# ---------------------------------------------------------------------------


class TestEnsureDataSource:
    _ROW = {
        "id": 1,
        "slug": "congress-api",
        "name": "Congress.gov API",
        "source_kind": "official",
        "base_url": None,
        "active": True,
    }

    def test_returns_fetched_row(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE), patch(_FETCH_ALL, return_value=[self._ROW]) as mock_fetch:
            result = ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        assert result == self._ROW
        mock_fetch.assert_called_once()

    def test_upsert_includes_all_field_values(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[self._ROW]):
            ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        _, _sql, params = mock_exec.call_args[0]
        assert "congress-api" in params
        assert "Congress.gov API" in params
        assert "official" in params

    def test_passes_base_url(self):
        conn = MagicMock()
        row = {**self._ROW, "base_url": "https://api.congress.gov"}
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[row]):
            ensure_data_source(
                conn,
                "congress-api",
                "Congress.gov API",
                "official",
                base_url="https://api.congress.gov",
            )

        _, _sql, params = mock_exec.call_args[0]
        assert "https://api.congress.gov" in params

    def test_base_url_defaults_to_none(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[self._ROW]):
            ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        _, _sql, params = mock_exec.call_args[0]
        assert None in params

    def test_returns_existing_row_on_conflict(self):
        """ON CONFLICT DO NOTHING means the pre-existing row is returned."""
        conn = MagicMock()
        existing = {**self._ROW, "id": 99}
        with patch(_EXECUTE_ONE), patch(_FETCH_ALL, return_value=[existing]):
            result = ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        assert result["id"] == 99


# ---------------------------------------------------------------------------
# start_ingestion_run
# ---------------------------------------------------------------------------


class TestStartIngestionRun:
    def test_returns_new_run_id(self):
        conn, _cur = _conn_for_insert(42)
        run_id = start_ingestion_run(conn, data_source_id=1, run_type="ingest")
        assert run_id == 42

    def test_rejects_boolean_returning_id(self):
        conn, _cur = _conn_for_insert(True)

        with pytest.raises(TypeError, match="expected integer id"):
            start_ingestion_run(conn, data_source_id=1, run_type="ingest")

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_commits_after_insert(self):
        conn, _cur = _conn_for_insert(7)
        start_ingestion_run(conn, data_source_id=1, run_type="ingest")
        conn.commit.assert_called_once()

    def test_commit_false_defers_insert_commit(self):
        conn, _cur = _conn_for_insert(7)
        start_ingestion_run(conn, data_source_id=1, run_type="ingest", commit=False)
        conn.commit.assert_not_called()

    def test_sql_sets_running_status(self):
        conn, cur = _conn_for_insert(8)
        start_ingestion_run(conn, data_source_id=1, run_type="ingest")
        sql = cur.execute.call_args[0][0]
        assert "'running'" in sql

    def test_parameters_serialised_as_json(self):
        conn, cur = _conn_for_insert(11)
        start_ingestion_run(conn, data_source_id=3, run_type="export", parameters={"cycle": 2024})
        params = cur.execute.call_args[0][1]
        # The JSON-serialised parameters appear somewhere in the param tuple
        json_values = [p for p in params if isinstance(p, str)]
        parsed = [json.loads(v) for v in json_values if v.startswith("{")]
        assert {"cycle": 2024} in parsed

    def test_empty_parameters_serialised_as_empty_object(self):
        conn, cur = _conn_for_insert(10)
        start_ingestion_run(conn, data_source_id=3, run_type="ingest")
        params = cur.execute.call_args[0][1]
        assert "{}" in params

    def test_data_source_id_and_run_type_in_params(self):
        conn, cur = _conn_for_insert(12)
        start_ingestion_run(conn, data_source_id=5, run_type="recompute")
        params = cur.execute.call_args[0][1]
        assert 5 in params
        assert "recompute" in params

    def test_rolls_back_when_insert_returning_id_is_missing(self):
        conn, cur = _conn_for_insert(12)
        cur.fetchone.return_value = None

        with pytest.raises(ValueError, match="RETURNING id produced no row"):
            start_ingestion_run(conn, data_source_id=5, run_type="ingest")

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_commit_false_does_not_roll_back_missing_returning_id(self):
        conn, cur = _conn_for_insert(12)
        cur.fetchone.return_value = None

        with pytest.raises(ValueError, match="RETURNING id produced no row"):
            start_ingestion_run(
                conn,
                data_source_id=5,
                run_type="ingest",
                commit=False,
            )

        conn.rollback.assert_not_called()
        conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# finish_ingestion_run
# ---------------------------------------------------------------------------


class TestFinishIngestionRun:
    def test_sets_succeeded_status(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=500)

        _, sql, _ = mock_exec.call_args[0]
        assert "'succeeded'" in sql

    def test_passes_record_count(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=500)

        _, _sql, params = mock_exec.call_args[0]
        assert 500 in params

    def test_passes_run_id(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=500)

        _, _sql, params = mock_exec.call_args[0]
        assert 10 in params

    def test_zero_record_count_accepted(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=11, record_count=0)

        _, _sql, params = mock_exec.call_args[0]
        assert 0 in params

    def test_sets_finished_at(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=1)

        _, sql, params = mock_exec.call_args[0]
        assert "finished_at" in sql
        # At least one non-None datetime in params
        non_none = [p for p in params if p is not None]
        assert len(non_none) >= 1

    def test_commit_false_forwarded(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=1, commit=False)

        assert mock_exec.call_args.kwargs["commit"] is False


# ---------------------------------------------------------------------------
# fail_ingestion_run
# ---------------------------------------------------------------------------


class TestFailIngestionRun:
    def test_sets_failed_status(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=20, error_message="timeout")

        _, sql, _ = mock_exec.call_args[0]
        assert "'failed'" in sql

    def test_passes_error_message(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=20, error_message="timeout")

        _, _sql, params = mock_exec.call_args[0]
        assert "timeout" in params

    def test_passes_run_id(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=20, error_message="timeout")

        _, _sql, params = mock_exec.call_args[0]
        assert 20 in params

    def test_preserves_full_error_message(self):
        conn = MagicMock()
        msg = "Connection refused: postgresql://localhost:5432/psephosamerica"
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=21, error_message=msg)

        _, _sql, params = mock_exec.call_args[0]
        assert msg in params

    def test_sets_finished_at(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=22, error_message="err")

        _, sql, params = mock_exec.call_args[0]
        assert "finished_at" in sql
        non_none = [p for p in params if p is not None]
        assert len(non_none) >= 1

    def test_commit_false_forwarded(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=22, error_message="err", commit=False)

        assert mock_exec.call_args.kwargs["commit"] is False
