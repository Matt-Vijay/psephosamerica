"""Tests for src/db/writer.py — no network, no real DB.

The psycopg connection is replaced with a lightweight mock so every test
stays pure-Python and offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import LoadSummary, TableWriteResult, WarnErrorSummary
from src.db.writer import write_table_batch, write_table_batches


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn():
    """Return a mock psycopg-compatible connection."""
    return MagicMock()


# ---------------------------------------------------------------------------
# write_table_batch — empty rows
# ---------------------------------------------------------------------------


class TestWriteTableBatchEmpty:
    def test_empty_rows_returns_zero_result(self, mock_conn):
        result = write_table_batch(mock_conn, table="member", rows=[])
        assert isinstance(result, TableWriteResult)
        assert result.table == "member"
        assert result.inserted == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert result.rejected == 0

    def test_empty_rows_no_db_call(self, mock_conn):
        with patch("src.db.writer.execute_many") as mock_exec:
            write_table_batch(mock_conn, table="member", rows=[])
            mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# write_table_batch — insert mode
# ---------------------------------------------------------------------------


class TestWriteTableBatchInsert:
    def test_insert_counts_all_as_inserted(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "last_name": "Smith"}]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(mock_conn, table="member", rows=rows, mode="insert")
        assert result.inserted == 1
        assert result.skipped == 0

    def test_insert_multi_row_count(self, mock_conn):
        rows = [{"slug": f"member-{i}", "chamber": "house"} for i in range(5)]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(mock_conn, table="member", rows=rows, mode="insert")
        assert result.inserted == 5

    def test_insert_sql_has_no_conflict_clause(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "chamber": "house"}]
        captured: list[str] = []

        def capture(conn, sql, params, *, commit):
            captured.append(sql)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(mock_conn, table="member", rows=rows, mode="insert")

        assert captured
        assert "ON CONFLICT" not in captured[0]
        assert 'INSERT INTO "member"' in captured[0]

    def test_insert_passes_correct_params(self, mock_conn):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        captured_params: list = []

        def capture(conn, sql, params, *, commit):
            captured_params.extend(params)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(mock_conn, table="t", rows=rows, mode="insert")

        assert [1, 2] in captured_params
        assert [3, 4] in captured_params

    def test_insert_adapts_dict_and_list_params_as_jsonb(self, mock_conn):
        rows = [{"payload": {"a": 1}, "items": [1, 2]}]
        captured_params: list = []

        def capture(conn, sql, params, *, commit):
            captured_params.extend(params)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(mock_conn, table="t", rows=rows, mode="insert")

        assert captured_params
        assert [type(value).__name__ for value in captured_params[0]] == ["Jsonb", "Jsonb"]


# ---------------------------------------------------------------------------
# write_table_batch — upsert mode
# ---------------------------------------------------------------------------


class TestWriteTableBatchUpsert:
    def test_upsert_counts_as_inserted(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "chamber": "house"}]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(
                mock_conn,
                table="member",
                rows=rows,
                conflict_columns=["bioguide_id"],
                mode="upsert",
            )
        assert result.inserted == 1
        assert result.skipped == 0

    def test_upsert_sql_contains_on_conflict_do_update(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "last_name": "Smith", "chamber": "senate"}]
        captured: list[str] = []

        def capture(conn, sql, params, *, commit):
            captured.append(sql)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(
                mock_conn,
                table="member",
                rows=rows,
                conflict_columns=["bioguide_id"],
                mode="upsert",
            )

        assert 'ON CONFLICT ("bioguide_id") DO UPDATE SET' in captured[0]

    def test_upsert_multi_row_count(self, mock_conn):
        rows = [{"slug": f"s{i}", "last_name": f"Name{i}"} for i in range(3)]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(
                mock_conn,
                table="member",
                rows=rows,
                conflict_columns=["slug"],
                mode="upsert",
            )
        assert result.inserted == 3

    def test_upsert_empty_rows_no_db_call(self, mock_conn):
        with patch("src.db.writer.execute_many") as mock_exec:
            write_table_batch(
                mock_conn,
                table="member",
                rows=[],
                conflict_columns=["slug"],
                mode="upsert",
            )
            mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# write_table_batch — ignore mode
# ---------------------------------------------------------------------------


class TestWriteTableBatchIgnore:
    def test_ignore_counts_as_skipped(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "chamber": "house"}]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(
                mock_conn,
                table="member",
                rows=rows,
                conflict_columns=["bioguide_id"],
                mode="ignore",
            )
        assert result.skipped == 1
        assert result.inserted == 0

    def test_ignore_sql_contains_do_nothing(self, mock_conn):
        rows = [{"bioguide_id": "A000001", "chamber": "senate"}]
        captured: list[str] = []

        def capture(conn, sql, params, *, commit):
            captured.append(sql)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(
                mock_conn,
                table="member",
                rows=rows,
                conflict_columns=["bioguide_id"],
                mode="ignore",
            )

        assert "DO NOTHING" in captured[0]
        assert "DO UPDATE" not in captured[0]

    def test_ignore_no_conflict_columns_uses_bare_do_nothing(self, mock_conn):
        rows = [{"slug": "alice"}]
        captured: list[str] = []

        def capture(conn, sql, params, *, commit):
            captured.append(sql)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batch(mock_conn, table="member", rows=rows, mode="ignore")

        assert "ON CONFLICT DO NOTHING" in captured[0]

    def test_ignore_multi_row_skipped_count(self, mock_conn):
        rows = [{"slug": f"m{i}"} for i in range(7)]
        with patch("src.db.writer.execute_many"):
            result = write_table_batch(mock_conn, table="member", rows=rows, mode="ignore")
        assert result.skipped == 7


# ---------------------------------------------------------------------------
# write_table_batch — invalid mode
# ---------------------------------------------------------------------------


class TestWriteTableBatchInvalidMode:
    def test_invalid_mode_raises(self, mock_conn):
        rows = [{"slug": "alice"}]
        with pytest.raises(ValueError, match="mode"):
            write_table_batch(mock_conn, table="member", rows=rows, mode="replace")

    def test_invalid_mode_empty_rows_still_raises(self, mock_conn):
        # Validation should happen before the empty-row early-return
        with pytest.raises(ValueError, match="mode"):
            write_table_batch(mock_conn, table="member", rows=[], mode="bogus")


# ---------------------------------------------------------------------------
# write_table_batches
# ---------------------------------------------------------------------------


class TestWriteTableBatches:
    def test_returns_load_summary(self, mock_conn):
        batches = [
            {
                "table": "member",
                "rows": [{"bioguide_id": "A1", "chamber": "house"}],
                "mode": "insert",
            },
        ]
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, batches)
        assert isinstance(summary, LoadSummary)

    def test_aggregate_inserted_across_tables(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "Fin"}, {"name": "Jud"}], "mode": "insert"},
        ]
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, batches)
        assert summary.total_inserted == 3

    def test_empty_batch_in_sequence_skipped(self, mock_conn):
        batches = [
            {"table": "member", "rows": [], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "Ways"}], "mode": "insert"},
        ]
        call_count = []

        def capture(conn, sql, params, *, commit):
            call_count.append(1)

        with patch("src.db.writer.execute_many", side_effect=capture):
            summary = write_table_batches(mock_conn, batches)

        # Only one execute_many call — the empty member batch is skipped
        assert len(call_count) == 1
        assert summary.total_inserted == 1

    def test_table_results_length_matches_batches(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "bill", "rows": [], "mode": "insert"},
            {
                "table": "committee",
                "rows": [{"name": "X"}],
                "mode": "upsert",
                "conflict_columns": ["name"],
            },
        ]
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, batches)
        assert len(summary.table_results) == 3

    def test_run_id_propagated(self, mock_conn):
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, [], run_id=42)
        assert summary.run_id == 42

    def test_warn_error_propagated(self, mock_conn):
        we = WarnErrorSummary()
        we.add_warning("test warning")
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, [], warn_error=we)
        assert summary.warn_error.warning_count == 1

    def test_empty_batches_list_returns_zero_summary(self, mock_conn):
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, [])
        assert summary.total_inserted == 0
        assert summary.total_skipped == 0
        assert summary.ok is True

    def test_mixed_modes_aggregate(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}, {"slug": "b"}], "mode": "insert"},
            {
                "table": "bill",
                "rows": [{"title": "X"}],
                "conflict_columns": ["title"],
                "mode": "ignore",
            },
        ]
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, batches)
        assert summary.total_inserted == 2
        assert summary.total_skipped == 1

    def test_default_mode_is_insert(self, mock_conn):
        # No 'mode' key in batch — should default to insert
        batches = [{"table": "member", "rows": [{"slug": "z"}]}]
        captured: list[str] = []

        def capture(conn, sql, params, *, commit):
            captured.append(sql)

        with patch("src.db.writer.execute_many", side_effect=capture):
            summary = write_table_batches(mock_conn, batches)

        assert "ON CONFLICT" not in captured[0]
        assert summary.total_inserted == 1

    def test_table_results_named_correctly(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "x"}], "mode": "insert"},
        ]
        with patch("src.db.writer.execute_many"):
            summary = write_table_batches(mock_conn, batches)
        names = [r.table for r in summary.table_results]
        assert names == ["member", "committee"]

    def test_default_batches_commit_once_after_all_tables_succeed(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "x"}], "mode": "insert"},
        ]
        commits: list[bool] = []

        def capture(conn, sql, params, *, commit):
            commits.append(commit)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batches(mock_conn, batches)

        assert commits == [False, False]
        mock_conn.commit.assert_called_once_with()
        mock_conn.rollback.assert_not_called()

    def test_default_batches_commit_failure_rolls_back(self, mock_conn):
        batches = [{"table": "member", "rows": [{"slug": "a"}], "mode": "insert"}]
        mock_conn.commit.side_effect = RuntimeError("commit failed")

        with (
            patch("src.db.writer.execute_many"),
            pytest.raises(RuntimeError, match="commit failed"),
        ):
            write_table_batches(mock_conn, batches)

        mock_conn.commit.assert_called_once_with()
        mock_conn.rollback.assert_called_once_with()

    def test_default_batches_roll_back_without_partial_commit_when_later_batch_fails(
        self, mock_conn
    ):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "x"}], "mode": "insert"},
        ]
        commits: list[bool] = []

        def capture(conn, sql, params, *, commit):
            commits.append(commit)
            if len(commits) == 2:
                raise RuntimeError("committee failed")

        with (
            patch("src.db.writer.execute_many", side_effect=capture),
            pytest.raises(RuntimeError, match="committee failed"),
        ):
            write_table_batches(mock_conn, batches)

        assert commits == [False, False]
        mock_conn.commit.assert_not_called()
        mock_conn.rollback.assert_called_once_with()

    def test_commit_false_defers_all_batch_commits(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "x"}], "mode": "insert"},
        ]
        commits: list[bool] = []

        def capture(conn, sql, params, *, commit):
            commits.append(commit)

        with patch("src.db.writer.execute_many", side_effect=capture):
            write_table_batches(mock_conn, batches, commit=False)

        assert commits == [False, False]

    def test_commit_false_later_batch_failure_does_not_commit_prior_batch(self, mock_conn):
        batches = [
            {"table": "member", "rows": [{"slug": "a"}], "mode": "insert"},
            {"table": "committee", "rows": [{"name": "x"}], "mode": "insert"},
        ]
        commits: list[bool] = []

        def capture(conn, sql, params, *, commit):
            commits.append(commit)
            if len(commits) == 2:
                raise RuntimeError("committee failed")

        with (
            patch("src.db.writer.execute_many", side_effect=capture),
            pytest.raises(RuntimeError, match="committee failed"),
        ):
            write_table_batches(mock_conn, batches, commit=False)

        assert commits == [False, False]
        mock_conn.commit.assert_not_called()
