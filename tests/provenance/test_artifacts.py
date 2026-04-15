"""Tests for provenance.artifacts — source artifact and parse run DB helpers.

All DB boundaries are mocked; no live database is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.provenance.artifacts import (
    create_source_artifact,
    create_parse_run,
    finish_parse_run,
    fail_parse_run,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SHA256 = "a" * 64


def _make_conn(fetchone_id: int = 1, fetchall_rows: list | None = None):
    """Return a mock psycopg connection wired for typical INSERT/SELECT flows."""
    conn = MagicMock()
    cursor_cm = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = {"id": fetchone_id}
    cur.fetchall.return_value = fetchall_rows or []
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm
    return conn, cur


# ---------------------------------------------------------------------------
# create_source_artifact
# ---------------------------------------------------------------------------


class TestCreateSourceArtifact:
    ARTIFACT_ROW = {
        "id": 7,
        "data_source_id": 3,
        "artifact_kind": "pdf",
        "storage_uri": "raw/senate/2025-06-01/aaaaaaaa/disclosure.pdf",
        "sha256": SHA256,
        "source_url": None,
        "mime_type": None,
        "fetched_at": FIXED_NOW,
        "source_record_id": None,
        "ingestion_run_id": None,
        "is_immutable": True,
        "created_at": FIXED_NOW,
        "updated_at": FIXED_NOW,
    }

    def _make_conn_for_artifact(self):
        conn, cur = _make_conn(fetchone_id=7, fetchall_rows=[self.ARTIFACT_ROW])
        return conn, cur

    def test_returns_artifact_row_dict(self):
        conn, cur = self._make_conn_for_artifact()
        with patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            row = create_source_artifact(
                conn,
                data_source_id=3,
                artifact_kind="pdf",
                storage_uri="raw/senate/2025-06-01/aaaaaaaa/disclosure.pdf",
                sha256=SHA256,
            )
        assert row["id"] == 7
        assert row["artifact_kind"] == "pdf"
        assert row["sha256"] == SHA256

    def test_commits_after_insert(self):
        conn, cur = self._make_conn_for_artifact()
        with patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            create_source_artifact(
                conn,
                data_source_id=3,
                artifact_kind="xml",
                storage_uri="raw/fec/2025-06-01/aaaaaaaa/cm.xml",
                sha256=SHA256,
            )
        conn.commit.assert_called()

    def test_optional_fields_default_to_none(self):
        conn, cur = self._make_conn_for_artifact()
        with patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            create_source_artifact(
                conn,
                data_source_id=1,
                artifact_kind="json",
                storage_uri="raw/src/2025-06-01/aaaaaaaa/data.json",
                sha256=SHA256,
            )
        params = cur.execute.call_args_list[0][0][1]
        # ingestion_run_id, source_url, mime_type, source_record_id default to None
        none_count = sum(1 for p in params if p is None)
        assert none_count >= 4

    def test_fetched_at_defaults_to_utcnow(self):
        conn, cur = self._make_conn_for_artifact()
        with patch("src.provenance.artifacts._utcnow", return_value=FIXED_NOW), \
             patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            create_source_artifact(
                conn,
                data_source_id=1,
                artifact_kind="pdf",
                storage_uri="raw/senate/2025-06-01/aaaaaaaa/x.pdf",
                sha256=SHA256,
            )
        params = cur.execute.call_args_list[0][0][1]
        assert FIXED_NOW in params

    def test_explicit_fetched_at_is_passed_through(self):
        conn, cur = self._make_conn_for_artifact()
        explicit_ts = datetime(2025, 1, 15, 8, 30, tzinfo=timezone.utc)
        with patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            create_source_artifact(
                conn,
                data_source_id=1,
                artifact_kind="pdf",
                storage_uri="raw/senate/2025-06-01/aaaaaaaa/y.pdf",
                sha256=SHA256,
                fetched_at=explicit_ts,
            )
        params = cur.execute.call_args_list[0][0][1]
        assert explicit_ts in params

    def test_ingestion_run_id_forwarded(self):
        conn, cur = self._make_conn_for_artifact()
        with patch("src.provenance.artifacts.fetch_all", return_value=[self.ARTIFACT_ROW]):
            create_source_artifact(
                conn,
                data_source_id=1,
                artifact_kind="csv",
                storage_uri="raw/fec/2025-06-01/aaaaaaaa/z.csv",
                sha256=SHA256,
                ingestion_run_id=42,
            )
        params = cur.execute.call_args_list[0][0][1]
        assert 42 in params


# ---------------------------------------------------------------------------
# create_parse_run
# ---------------------------------------------------------------------------


class TestCreateParseRun:
    def test_returns_new_run_id(self):
        conn, cur = _make_conn(fetchone_id=99)
        run_id = create_parse_run(conn, 7, "disclosure-pdf", "0.3.1")
        assert run_id == 99

    def test_status_is_running(self):
        conn, cur = _make_conn(fetchone_id=1)
        create_parse_run(conn, 7, "disclosure-pdf", "0.3.1")
        sql = cur.execute.call_args[0][0]
        assert "'running'" in sql

    def test_started_at_is_set(self):
        conn, cur = _make_conn(fetchone_id=1)
        with patch("src.provenance.artifacts._utcnow", return_value=FIXED_NOW):
            create_parse_run(conn, 7, "disclosure-pdf", "0.3.1")
        params = cur.execute.call_args[0][1]
        assert FIXED_NOW in params

    def test_ingestion_run_id_forwarded(self):
        conn, cur = _make_conn(fetchone_id=1)
        create_parse_run(conn, 7, "disclosure-pdf", "0.3.1", ingestion_run_id=55)
        params = cur.execute.call_args[0][1]
        assert 55 in params

    def test_ingestion_run_id_defaults_to_none(self):
        conn, cur = _make_conn(fetchone_id=1)
        create_parse_run(conn, 7, "disclosure-pdf", "0.3.1")
        params = cur.execute.call_args[0][1]
        assert None in params

    def test_commits(self):
        conn, cur = _make_conn(fetchone_id=1)
        create_parse_run(conn, 7, "disclosure-pdf", "0.3.1")
        conn.commit.assert_called()


# ---------------------------------------------------------------------------
# finish_parse_run
# ---------------------------------------------------------------------------


class TestFinishParseRun:
    def test_sets_succeeded_status(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            finish_parse_run(conn, run_id=10)
        sql = mock_exec.call_args[0][1]
        assert "succeeded" in sql

    def test_sets_finished_at(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec, \
             patch("src.provenance.artifacts._utcnow", return_value=FIXED_NOW):
            finish_parse_run(conn, run_id=10)
        params = mock_exec.call_args[0][2]
        assert FIXED_NOW in params

    def test_page_count_forwarded(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            finish_parse_run(conn, run_id=10, page_count=12, ocr_page_count=3)
        params = mock_exec.call_args[0][2]
        assert 12 in params
        assert 3 in params

    def test_confidence_summary_serialised(self):
        conn, cur = _make_conn()
        summary = {"mean": 0.91, "low_pages": 2}
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            finish_parse_run(conn, run_id=10, confidence_summary=summary)
        params = mock_exec.call_args[0][2]
        assert '{"mean": 0.91, "low_pages": 2}' in params

    def test_confidence_summary_defaults_to_empty(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            finish_parse_run(conn, run_id=10)
        params = mock_exec.call_args[0][2]
        assert "{}" in params

    def test_run_id_is_in_params(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            finish_parse_run(conn, run_id=77)
        params = mock_exec.call_args[0][2]
        assert 77 in params


# ---------------------------------------------------------------------------
# fail_parse_run
# ---------------------------------------------------------------------------


class TestFailParseRun:
    def test_sets_failed_status(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            fail_parse_run(conn, run_id=10, error_message="OCR timeout")
        sql = mock_exec.call_args[0][1]
        assert "failed" in sql

    def test_error_message_in_params(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            fail_parse_run(conn, run_id=10, error_message="OCR timeout")
        params = mock_exec.call_args[0][2]
        assert "OCR timeout" in params

    def test_sets_finished_at(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec, \
             patch("src.provenance.artifacts._utcnow", return_value=FIXED_NOW):
            fail_parse_run(conn, run_id=10, error_message="boom")
        params = mock_exec.call_args[0][2]
        assert FIXED_NOW in params

    def test_run_id_is_in_params(self):
        conn, cur = _make_conn()
        with patch("src.provenance.artifacts.execute_one") as mock_exec:
            fail_parse_run(conn, run_id=88, error_message="bad input")
        params = mock_exec.call_args[0][2]
        assert 88 in params
