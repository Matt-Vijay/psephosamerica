"""Tests for src/db/load_report.py — no database, no network."""

from __future__ import annotations

import pytest

from src.db.load_report import (
    TableWriteResult,
    WarnErrorSummary,
    LoadSummary,
    build_load_summary,
    merge_table_results,
    status_dict,
)


# ---------------------------------------------------------------------------
# TableWriteResult
# ---------------------------------------------------------------------------


class TestTableWriteResult:
    def test_defaults_are_zero(self) -> None:
        r = TableWriteResult(table="member")
        assert r.inserted == 0
        assert r.updated == 0
        assert r.skipped == 0
        assert r.rejected == 0

    def test_total_attempted(self) -> None:
        r = TableWriteResult(table="member", inserted=3, updated=1, skipped=2, rejected=1)
        assert r.total_attempted == 7

    def test_total_written(self) -> None:
        r = TableWriteResult(table="member", inserted=3, updated=1, skipped=2, rejected=1)
        assert r.total_written == 4

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError):
            TableWriteResult(table="member", inserted=-1)

    def test_frozen(self) -> None:
        r = TableWriteResult(table="member", inserted=1)
        with pytest.raises((AttributeError, TypeError)):
            r.inserted = 99  # type: ignore[misc]

    def test_all_counts_combined(self) -> None:
        r = TableWriteResult(table="t", inserted=10, updated=5, skipped=3, rejected=2)
        assert r.total_attempted == 20
        assert r.total_written == 15


# ---------------------------------------------------------------------------
# WarnErrorSummary
# ---------------------------------------------------------------------------


class TestWarnErrorSummary:
    def test_empty_by_default(self) -> None:
        w = WarnErrorSummary()
        assert w.warning_count == 0
        assert w.error_count == 0
        assert not w.has_errors
        assert not w.has_warnings

    def test_add_warning(self) -> None:
        w = WarnErrorSummary()
        w.add_warning("low confidence match")
        assert w.warning_count == 1
        assert w.has_warnings
        assert not w.has_errors

    def test_add_error(self) -> None:
        w = WarnErrorSummary()
        w.add_error("parse failed")
        assert w.error_count == 1
        assert w.has_errors

    def test_multiple_entries(self) -> None:
        w = WarnErrorSummary()
        for i in range(3):
            w.add_warning(f"warn {i}")
        w.add_error("critical")
        assert w.warning_count == 3
        assert w.error_count == 1

    def test_messages_are_stored(self) -> None:
        w = WarnErrorSummary()
        w.add_warning("alpha")
        w.add_error("beta")
        assert "alpha" in w.warnings
        assert "beta" in w.errors

    def test_independent_instances(self) -> None:
        """Mutable default should not bleed across instances."""
        a = WarnErrorSummary()
        b = WarnErrorSummary()
        a.add_warning("only in a")
        assert b.warning_count == 0


# ---------------------------------------------------------------------------
# merge_table_results
# ---------------------------------------------------------------------------


class TestMergeTableResults:
    def test_empty_sequence(self) -> None:
        m = merge_table_results([])
        assert m.inserted == 0
        assert m.table == "<merged>"

    def test_single_result(self) -> None:
        r = TableWriteResult(table="bill", inserted=5, updated=2)
        m = merge_table_results([r])
        assert m.inserted == 5
        assert m.updated == 2

    def test_multiple_results(self) -> None:
        results = [
            TableWriteResult(table="member", inserted=10, updated=1, skipped=2, rejected=0),
            TableWriteResult(table="bill", inserted=5, updated=0, skipped=1, rejected=1),
            TableWriteResult(table="vote_event", inserted=3, updated=3, skipped=0, rejected=0),
        ]
        m = merge_table_results(results)
        assert m.inserted == 18
        assert m.updated == 4
        assert m.skipped == 3
        assert m.rejected == 1

    def test_merged_table_name(self) -> None:
        results = [TableWriteResult(table="a"), TableWriteResult(table="b")]
        m = merge_table_results(results)
        assert m.table == "<merged>"


# ---------------------------------------------------------------------------
# build_load_summary
# ---------------------------------------------------------------------------


class TestBuildLoadSummary:
    def _make_results(self) -> list[TableWriteResult]:
        return [
            TableWriteResult(table="member", inserted=4, updated=1, skipped=0, rejected=1),
            TableWriteResult(table="committee", inserted=2, updated=0, skipped=1, rejected=0),
        ]

    def test_basic_aggregation(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.total_inserted == 6
        assert s.total_updated == 1
        assert s.total_skipped == 1
        assert s.total_rejected == 1

    def test_total_attempted(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.total_attempted == 9

    def test_total_written(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.total_written == 7

    def test_ok_when_no_errors(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.ok is True

    def test_not_ok_when_errors(self) -> None:
        we = WarnErrorSummary()
        we.add_error("something broke")
        s = build_load_summary(self._make_results(), warn_error=we)
        assert s.ok is False

    def test_run_id_stored(self) -> None:
        s = build_load_summary(self._make_results(), run_id=42)
        assert s.run_id == 42

    def test_run_id_defaults_none(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.run_id is None

    def test_table_results_preserved(self) -> None:
        results = self._make_results()
        s = build_load_summary(results)
        assert len(s.table_results) == 2
        assert s.table_results[0].table == "member"

    def test_empty_results(self) -> None:
        s = build_load_summary([])
        assert s.total_inserted == 0
        assert s.total_attempted == 0

    def test_default_warn_error_is_empty(self) -> None:
        s = build_load_summary(self._make_results())
        assert s.warn_error.warning_count == 0
        assert s.warn_error.error_count == 0


# ---------------------------------------------------------------------------
# status_dict
# ---------------------------------------------------------------------------


class TestStatusDict:
    def _make_summary(self, run_id: int = 1) -> LoadSummary:
        results = [
            TableWriteResult(table="member", inserted=3, updated=1, skipped=0, rejected=0),
            TableWriteResult(table="bill", inserted=7, updated=2, skipped=1, rejected=1),
        ]
        we = WarnErrorSummary()
        we.add_warning("fuzzy match used")
        return build_load_summary(results, warn_error=we, run_id=run_id)

    def test_top_level_keys(self) -> None:
        d = status_dict(self._make_summary())
        assert set(d.keys()) == {"run_id", "ok", "counts", "warnings", "errors", "tables"}

    def test_run_id(self) -> None:
        d = status_dict(self._make_summary(run_id=99))
        assert d["run_id"] == 99

    def test_ok_true(self) -> None:
        d = status_dict(self._make_summary())
        assert d["ok"] is True

    def test_ok_false_on_error(self) -> None:
        we = WarnErrorSummary()
        we.add_error("fatal")
        s = build_load_summary([], warn_error=we, run_id=1)
        assert status_dict(s)["ok"] is False

    def test_counts_shape(self) -> None:
        d = status_dict(self._make_summary())
        counts = d["counts"]
        assert set(counts.keys()) == {"inserted", "updated", "skipped", "rejected"}
        assert counts["inserted"] == 10
        assert counts["updated"] == 3
        assert counts["skipped"] == 1
        assert counts["rejected"] == 1

    def test_warning_and_error_counts(self) -> None:
        d = status_dict(self._make_summary())
        assert d["warnings"] == 1
        assert d["errors"] == 0

    def test_tables_list_length(self) -> None:
        d = status_dict(self._make_summary())
        assert len(d["tables"]) == 2

    def test_table_entry_shape(self) -> None:
        d = status_dict(self._make_summary())
        entry = d["tables"][0]
        assert set(entry.keys()) == {"table", "inserted", "updated", "skipped", "rejected"}
        assert entry["table"] == "member"

    def test_null_run_id(self) -> None:
        s = build_load_summary([])
        d = status_dict(s)
        assert d["run_id"] is None
