"""Tests for src/db/load_executor.py — no network, no real DB.

All writer and resolver boundaries are mocked so every test is pure Python.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_executor import Resolvers, execute_load_plan
from src.db.load_report import LoadSummary, TableWriteResult, WarnErrorSummary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn():
    return MagicMock()


def _make_op(
    table: str,
    rows: list[dict] | None = None,
    conflict_columns: list[str] | None = None,
    mode: str = "upsert",
) -> dict[str, Any]:
    op: dict[str, Any] = {"table": table, "rows": rows or [], "mode": mode}
    if conflict_columns is not None:
        op["conflict_columns"] = conflict_columns
    return op


# Patch target for write_table_batch used inside the executor
_PATCH = "src.db.load_executor.write_table_batch"


# ---------------------------------------------------------------------------
# Hint-table skipping
# ---------------------------------------------------------------------------


class TestHintTableSkipping:
    def test_hint_table_not_passed_to_writer(self, mock_conn):
        ops = [_make_op("_fec_linkage_hint", [{"fec_candidate_id": "H1", "fec_committee_id": "C1"}])]
        with patch(_PATCH) as mock_write:
            execute_load_plan(mock_conn, ops)
        mock_write.assert_not_called()

    def test_hint_table_excluded_from_summary(self, mock_conn):
        ops = [_make_op("_fec_linkage_hint", [{"x": 1}])]
        with patch(_PATCH):
            summary = execute_load_plan(mock_conn, ops)
        assert len(summary.table_results) == 0
        assert summary.total_inserted == 0
        assert summary.total_skipped == 0

    def test_canonical_table_passes_through_alongside_hint(self, mock_conn):
        ops = [
            _make_op("member", [{"bioguide_id": "A000001"}], ["bioguide_id"]),
            _make_op("_fec_linkage_hint", [{"fec_candidate_id": "H1"}]),
        ]
        with patch(_PATCH, return_value=TableWriteResult(table="member", inserted=1)) as mock_write:
            summary = execute_load_plan(mock_conn, ops)
        mock_write.assert_called_once()
        assert len(summary.table_results) == 1

    def test_multiple_hint_tables_all_skipped(self, mock_conn):
        ops = [
            _make_op("_hint_a", [{"x": 1}]),
            _make_op("_hint_b", [{"y": 2}]),
        ]
        with patch(_PATCH) as mock_write:
            execute_load_plan(mock_conn, ops)
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# FK resolution — hint key stripping
# ---------------------------------------------------------------------------


class TestHintKeyStripping:
    def test_hint_keys_stripped_when_no_resolvers(self, mock_conn):
        rows = [{"bioguide_id": "A1", "_some_hint": "ignored"}]
        ops = [_make_op("member", rows, ["bioguide_id"])]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        assert captured
        assert "_some_hint" not in captured[0][0]
        assert captured[0][0]["bioguide_id"] == "A1"

    def test_canonical_keys_preserved(self, mock_conn):
        rows = [{"bioguide_id": "B1", "chamber": "house", "_bioguide_id": "B1"}]
        ops = [_make_op("member", rows, ["bioguide_id"])]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        row = captured[0][0]
        assert "bioguide_id" in row
        assert "_bioguide_id" not in row
        assert row["chamber"] == "house"

    def test_hint_key_without_resolver_silently_dropped(self, mock_conn):
        rows = [{"title": "A Bill", "_unknown_hint": "some_value"}]
        ops = [_make_op("bill", rows, ["title"])]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops, resolvers={})

        assert "_unknown_hint" not in captured[0][0]
        assert "title" in captured[0][0]


# ---------------------------------------------------------------------------
# FK resolution — resolver functions
# ---------------------------------------------------------------------------


class TestFKResolution:
    def test_single_resolver_fills_fk_column(self, mock_conn):
        member_map = {"P000197": 42}
        resolvers: Resolvers = {
            "_bioguide_id": ("member_id", lambda row: member_map.get(row["_bioguide_id"]))
        }
        rows = [{"congress": 118, "chamber": "house", "_bioguide_id": "P000197"}]
        ops = [_make_op("member_term", rows, ["member_id", "congress", "chamber"])]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table, inserted=len(rows))

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops, resolvers=resolvers)

        row = captured[0][0]
        assert row["member_id"] == 42
        assert "_bioguide_id" not in row

    def test_resolver_returning_none_sets_column_to_none(self, mock_conn):
        resolvers: Resolvers = {
            "_bioguide_id": ("member_id", lambda row: None),
        }
        rows = [{"congress": 119, "_bioguide_id": "UNKNOWN"}]
        ops = [_make_op("member_term", rows)]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops, resolvers=resolvers)

        row = captured[0][0]
        assert row["member_id"] is None
        assert "_bioguide_id" not in row

    def test_multi_key_resolver_receives_full_row(self, mock_conn):
        # committee_id resolved from _committee_code + _congress together
        committee_map = {("HSWM00", 118): 99}

        def resolve_committee(row: dict) -> int | None:
            return committee_map.get((row["_committee_code"], row["_congress"]))

        resolvers: Resolvers = {
            "_committee_code": ("committee_id", resolve_committee),
        }
        rows = [{"role": "member", "_committee_code": "HSWM00", "_congress": 118}]
        ops = [_make_op("committee_membership", rows)]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table, inserted=len(rows))

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops, resolvers=resolvers)

        row = captured[0][0]
        assert row["committee_id"] == 99
        assert "_committee_code" not in row
        # _congress was a hint-only key with no resolver — stripped
        assert "_congress" not in row

    def test_multiple_resolvers_on_same_row(self, mock_conn):
        member_map = {"S000148": 7}
        committee_map = {"SSFI00": 15}
        resolvers: Resolvers = {
            "_bioguide_id": ("member_id", lambda row: member_map.get(row["_bioguide_id"])),
            "_committee_code": ("committee_id", lambda row: committee_map.get(row["_committee_code"])),
        }
        rows = [{"role": "chair", "_bioguide_id": "S000148", "_committee_code": "SSFI00"}]
        ops = [_make_op("committee_membership", rows)]
        captured: list[list[dict]] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured.append(rows)
            return TableWriteResult(table=table, inserted=len(rows))

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops, resolvers=resolvers)

        row = captured[0][0]
        assert row["member_id"] == 7
        assert row["committee_id"] == 15
        assert "_bioguide_id" not in row
        assert "_committee_code" not in row

    def test_resolver_called_per_row(self, mock_conn):
        call_count = [0]
        member_map = {"A1": 1, "A2": 2}

        def resolve(row: dict) -> int | None:
            call_count[0] += 1
            return member_map.get(row["_bioguide_id"])

        resolvers: Resolvers = {"_bioguide_id": ("member_id", resolve)}
        rows = [{"_bioguide_id": "A1"}, {"_bioguide_id": "A2"}]
        ops = [_make_op("member_term", rows)]

        with patch(_PATCH, return_value=TableWriteResult(table="member_term")):
            execute_load_plan(mock_conn, ops, resolvers=resolvers)

        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


class TestSummaryAggregation:
    def test_returns_load_summary(self, mock_conn):
        ops = [_make_op("member", [{"bioguide_id": "A1"}], ["bioguide_id"])]
        with patch(_PATCH, return_value=TableWriteResult(table="member", inserted=1)):
            result = execute_load_plan(mock_conn, ops)
        assert isinstance(result, LoadSummary)

    def test_total_inserted_aggregated(self, mock_conn):
        ops = [
            _make_op("member", [{"bioguide_id": "A1"}, {"bioguide_id": "A2"}]),
            _make_op("committee", [{"name": "Judiciary"}]),
        ]
        side_effects = [
            TableWriteResult(table="member", inserted=2),
            TableWriteResult(table="committee", inserted=1),
        ]
        with patch(_PATCH, side_effect=side_effects):
            summary = execute_load_plan(mock_conn, ops)
        assert summary.total_inserted == 3

    def test_run_id_embedded_in_summary(self, mock_conn):
        ops = [_make_op("member", [])]
        with patch(_PATCH, return_value=TableWriteResult(table="member")):
            summary = execute_load_plan(mock_conn, ops, run_id=99)
        assert summary.run_id == 99

    def test_warn_error_propagated(self, mock_conn):
        we = WarnErrorSummary()
        we.add_warning("test warning")
        ops = []
        summary = execute_load_plan(mock_conn, ops, warn_error=we)
        assert summary.warn_error.warning_count == 1

    def test_fresh_warn_error_created_when_omitted(self, mock_conn):
        ops = []
        summary = execute_load_plan(mock_conn, ops)
        assert isinstance(summary.warn_error, WarnErrorSummary)
        assert summary.warn_error.warning_count == 0
        assert summary.warn_error.error_count == 0

    def test_empty_operations_returns_zero_summary(self, mock_conn):
        summary = execute_load_plan(mock_conn, [])
        assert summary.total_inserted == 0
        assert summary.total_skipped == 0
        assert summary.total_rejected == 0
        assert summary.ok is True
        assert summary.run_id is None

    def test_table_results_order_preserved(self, mock_conn):
        ops = [
            _make_op("member", [{"slug": "a"}]),
            _make_op("committee", [{"name": "X"}]),
            _make_op("bill", [{"title": "Y"}]),
        ]
        side_effects = [
            TableWriteResult(table="member", inserted=1),
            TableWriteResult(table="committee", inserted=1),
            TableWriteResult(table="bill", inserted=1),
        ]
        with patch(_PATCH, side_effect=side_effects):
            summary = execute_load_plan(mock_conn, ops)
        names = [r.table for r in summary.table_results]
        assert names == ["member", "committee", "bill"]

    def test_hint_tables_not_counted_in_total(self, mock_conn):
        ops = [
            _make_op("_fec_linkage_hint", [{"x": 1}, {"x": 2}]),
            _make_op("fec_committee", [{"fec_committee_id": "C1"}]),
        ]
        with patch(_PATCH, return_value=TableWriteResult(table="fec_committee", inserted=1)):
            summary = execute_load_plan(mock_conn, ops)
        assert summary.total_inserted == 1
        assert len(summary.table_results) == 1


# ---------------------------------------------------------------------------
# Writer boundary — correct args forwarded
# ---------------------------------------------------------------------------


class TestWriterBoundary:
    def test_table_name_forwarded(self, mock_conn):
        ops = [_make_op("vote_event", [{"chamber": "house"}], ["chamber"])]
        captured_kwargs: list[dict] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured_kwargs.append({"table": table, "conflict_columns": conflict_columns, "mode": mode})
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        assert captured_kwargs[0]["table"] == "vote_event"

    def test_conflict_columns_forwarded(self, mock_conn):
        ops = [_make_op("vote_event", [{"chamber": "house"}], ["chamber", "congress"], mode="upsert")]
        captured_kwargs: list[dict] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured_kwargs.append({"conflict_columns": conflict_columns})
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        assert captured_kwargs[0]["conflict_columns"] == ["chamber", "congress"]

    def test_mode_forwarded(self, mock_conn):
        ops = [_make_op("member", [{"slug": "x"}], mode="ignore")]
        captured_modes: list[str] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured_modes.append(mode)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        assert captured_modes[0] == "ignore"

    def test_default_mode_is_insert_when_omitted(self, mock_conn):
        op = {"table": "member", "rows": [{"slug": "z"}]}  # no "mode" key
        captured_modes: list[str] = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            captured_modes.append(mode)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, [op])

        assert captured_modes[0] == "insert"

    def test_empty_rows_still_calls_writer(self, mock_conn):
        ops = [_make_op("member", [])]
        with patch(_PATCH, return_value=TableWriteResult(table="member")) as mock_write:
            execute_load_plan(mock_conn, ops)
        mock_write.assert_called_once()

    def test_conn_passed_through(self, mock_conn):
        ops = [_make_op("member", [{"slug": "a"}])]
        received_conn = []

        def capture(conn, *, table, rows, conflict_columns, mode):
            received_conn.append(conn)
            return TableWriteResult(table=table)

        with patch(_PATCH, side_effect=capture):
            execute_load_plan(mock_conn, ops)

        assert received_conn[0] is mock_conn
