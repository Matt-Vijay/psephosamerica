"""Tests for src/runtime/smoke_congress_local.py.

No live DB, no network, no filesystem writes.
run_congress_archive_load is mocked at the smoke_congress_local import path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.db.load_report import WarnErrorSummary, build_load_summary
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.smoke_congress_local import smoke_congress_local

_MODULE = "src.runtime.smoke_congress_local"

_ARCHIVE = Path("/data/congress")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _options(congress: int = 119) -> CongressLoadOptions:
    return CongressLoadOptions(congress=congress)


def _load_summary(
    *,
    run_id: int = 1,
    inserted: int = 10,
    updated: int = 2,
    skipped: int = 1,
    rejected: int = 0,
    warnings: int = 0,
    errors: int = 0,
):
    from src.db.load_report import TableWriteResult

    we = WarnErrorSummary()
    for _ in range(warnings):
        we.add_warning("warn")
    for _ in range(errors):
        we.add_error("err")
    tr = TableWriteResult(
        table="member",
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        rejected=rejected,
    )
    return build_load_summary([tr], warn_error=we, run_id=run_id)


def _make_result(
    *,
    run_id: int = 1,
    source_slug: str = "congress-core",
    inserted: int = 10,
    updated: int = 2,
    skipped: int = 1,
    rejected: int = 0,
    warnings: int = 0,
    errors: int = 0,
):
    result = MagicMock()
    result.run_id = run_id
    result.data_source = {"slug": source_slug, "id": 1}
    result.load_summary = _load_summary(
        run_id=run_id,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        rejected=rejected,
        warnings=warnings,
        errors=errors,
    )
    return result


def _run(
    conn=None,
    *,
    archive: Path = _ARCHIVE,
    options: CongressLoadOptions | None = None,
    run_result=None,
):
    conn = conn or MagicMock()
    opts = options or _options()
    result = run_result if run_result is not None else _make_result()
    with patch(f"{_MODULE}.run_congress_archive_load", return_value=result) as mock_load:
        summary = smoke_congress_local(conn, archive, opts)
    return summary, mock_load, conn


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestSmokeCongressLocalStructure:
    def test_returns_nine_top_level_keys(self) -> None:
        summary, *_ = _run()
        assert set(summary) == {
            "run_id",
            "source_slug",
            "inserted",
            "updated",
            "skipped",
            "rejected",
            "ok",
            "warnings",
            "errors",
        }

    def test_ok_is_bool(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["ok"], bool)

    def test_run_id_is_int(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["run_id"], int)

    def test_source_slug_is_str(self) -> None:
        summary, *_ = _run()
        assert isinstance(summary["source_slug"], str)


# ---------------------------------------------------------------------------
# Argument forwarding
# ---------------------------------------------------------------------------


class TestArgumentForwarding:
    def test_conn_forwarded(self) -> None:
        conn = MagicMock()
        _, mock_load, _ = _run(conn=conn)
        assert mock_load.call_args[0][0] is conn

    def test_archive_forwarded(self) -> None:
        archive = Path("/custom/archive")
        _, mock_load, _ = _run(archive=archive)
        assert mock_load.call_args[0][1] == archive

    def test_options_forwarded(self) -> None:
        opts = _options(congress=118)
        _, mock_load, _ = _run(options=opts)
        assert mock_load.call_args[0][2] is opts


# ---------------------------------------------------------------------------
# Value forwarding
# ---------------------------------------------------------------------------


class TestValueForwarding:
    def test_run_id_from_result(self) -> None:
        result = _make_result(run_id=42)
        summary, *_ = _run(run_result=result)
        assert summary["run_id"] == 42

    def test_source_slug_from_result(self) -> None:
        result = _make_result(source_slug="congress-core")
        summary, *_ = _run(run_result=result)
        assert summary["source_slug"] == "congress-core"

    def test_inserted_from_load_summary(self) -> None:
        result = _make_result(inserted=55)
        summary, *_ = _run(run_result=result)
        assert summary["inserted"] == 55

    def test_updated_from_load_summary(self) -> None:
        result = _make_result(updated=7)
        summary, *_ = _run(run_result=result)
        assert summary["updated"] == 7

    def test_skipped_from_load_summary(self) -> None:
        result = _make_result(skipped=3)
        summary, *_ = _run(run_result=result)
        assert summary["skipped"] == 3

    def test_rejected_from_load_summary(self) -> None:
        result = _make_result(rejected=2)
        summary, *_ = _run(run_result=result)
        assert summary["rejected"] == 2

    def test_ok_true_when_no_errors(self) -> None:
        result = _make_result(errors=0)
        summary, *_ = _run(run_result=result)
        assert summary["ok"] is True

    def test_ok_false_when_errors_present(self) -> None:
        result = _make_result(errors=1)
        summary, *_ = _run(run_result=result)
        assert summary["ok"] is False

    def test_warnings_count(self) -> None:
        result = _make_result(warnings=3)
        summary, *_ = _run(run_result=result)
        assert summary["warnings"] == 3

    def test_errors_count(self) -> None:
        result = _make_result(errors=2)
        summary, *_ = _run(run_result=result)
        assert summary["errors"] == 2

    def test_zero_counts(self) -> None:
        result = _make_result(inserted=0, updated=0, skipped=0, rejected=0)
        summary, *_ = _run(run_result=result)
        assert summary["inserted"] == 0
        assert summary["updated"] == 0
        assert summary["skipped"] == 0
        assert summary["rejected"] == 0


# ---------------------------------------------------------------------------
# JSON contract: full summary is operator-safe JSON
# ---------------------------------------------------------------------------


class TestJsonContract:
    def test_summary_is_json_serializable(self) -> None:
        summary, *_ = _run()
        raw = json.dumps(summary)
        obj = json.loads(raw)
        assert set(obj) == {
            "run_id",
            "source_slug",
            "inserted",
            "updated",
            "skipped",
            "rejected",
            "ok",
            "warnings",
            "errors",
        }

    def test_all_values_are_primitives(self) -> None:
        """Operator JSON must contain only str, int, bool, None — no objects."""
        summary, *_ = _run()
        for key, value in summary.items():
            assert isinstance(value, (str, int, bool, type(None))), (
                f"{key} has type {type(value).__name__}, expected primitive"
            )
