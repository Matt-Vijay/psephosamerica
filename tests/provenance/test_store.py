"""Tests for provenance.store — DB helpers for data sources and ingestion runs.

All DB boundaries are mocked; no live Postgres required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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

    def test_inserts_with_correct_params(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[self._ROW]):
            ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        _, sql, params = mock_exec.call_args[0]
        assert "ON CONFLICT" in sql
        assert params[0] == "congress-api"
        assert params[1] == "Congress.gov API"
        assert params[2] == "official"

    def test_passes_base_url(self):
        conn = MagicMock()
        row = {**self._ROW, "base_url": "https://api.congress.gov"}
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[row]):
            ensure_data_source(
                conn, "congress-api", "Congress.gov API", "official",
                base_url="https://api.congress.gov",
            )

        _, _sql, params = mock_exec.call_args[0]
        assert params[3] == "https://api.congress.gov"

    def test_base_url_defaults_to_none(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec, patch(_FETCH_ALL, return_value=[self._ROW]):
            ensure_data_source(conn, "congress-api", "Congress.gov API", "official")

        _, _sql, params = mock_exec.call_args[0]
        assert params[3] is None

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

    def test_commits_after_insert(self):
        conn, _cur = _conn_for_insert(7)
        start_ingestion_run(conn, data_source_id=1, run_type="ingest")
        conn.commit.assert_called_once()

    def test_sql_sets_running_status(self):
        conn, cur = _conn_for_insert(8)
        start_ingestion_run(conn, data_source_id=1, run_type="ingest")
        sql = cur.execute.call_args[0][0]
        assert "'running'" in sql

    def test_sql_includes_returning_id(self):
        conn, cur = _conn_for_insert(9)
        start_ingestion_run(conn, data_source_id=1, run_type="recompute")
        sql = cur.execute.call_args[0][0]
        assert "RETURNING id" in sql

    def test_empty_parameters_serialised_as_empty_object(self):
        conn, cur = _conn_for_insert(10)
        start_ingestion_run(conn, data_source_id=3, run_type="ingest")
        params = cur.execute.call_args[0][1]
        assert params[3] == "{}"

    def test_parameters_serialised_as_json(self):
        conn, cur = _conn_for_insert(11)
        start_ingestion_run(conn, data_source_id=3, run_type="export", parameters={"cycle": 2024})
        params = cur.execute.call_args[0][1]
        assert json.loads(params[3]) == {"cycle": 2024}

    def test_data_source_id_and_run_type_passed(self):
        conn, cur = _conn_for_insert(12)
        start_ingestion_run(conn, data_source_id=5, run_type="recompute")
        params = cur.execute.call_args[0][1]
        assert params[0] == 5
        assert params[1] == "recompute"


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
        assert params[1] == 500

    def test_passes_run_id(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=500)

        _, _sql, params = mock_exec.call_args[0]
        assert params[3] == 10

    def test_zero_record_count_accepted(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=11, record_count=0)

        _, _sql, params = mock_exec.call_args[0]
        assert params[1] == 0

    def test_sets_finished_at(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            finish_ingestion_run(conn, run_id=10, record_count=1)

        _, sql, params = mock_exec.call_args[0]
        assert "finished_at" in sql
        assert params[0] is not None


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
        assert params[1] == "timeout"

    def test_passes_run_id(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=20, error_message="timeout")

        _, _sql, params = mock_exec.call_args[0]
        assert params[3] == 20

    def test_preserves_full_error_message(self):
        conn = MagicMock()
        msg = "Connection refused: postgresql://localhost:5432/openpact"
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=21, error_message=msg)

        _, _sql, params = mock_exec.call_args[0]
        assert params[1] == msg

    def test_sets_finished_at(self):
        conn = MagicMock()
        with patch(_EXECUTE_ONE) as mock_exec:
            fail_ingestion_run(conn, run_id=22, error_message="err")

        _, sql, params = mock_exec.call_args[0]
        assert "finished_at" in sql
        assert params[0] is not None
