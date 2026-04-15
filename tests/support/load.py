"""Builders for LoadSummary and WarnErrorSummary.

These are the most-reused building blocks across test_output.py and
any test that needs to construct runtime result types.
"""

from __future__ import annotations

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)


def warn_error(warnings: int = 0, errors: int = 0) -> WarnErrorSummary:
    """Build a WarnErrorSummary with *n* dummy warning/error strings."""
    we = WarnErrorSummary()
    for i in range(warnings):
        we.add_warning(f"warn {i}")
    for i in range(errors):
        we.add_error(f"err {i}")
    return we


def load_summary(
    tables: list[TableWriteResult] | None = None,
    warn_error_summary: WarnErrorSummary | None = None,
    run_id: int = 42,
) -> LoadSummary:
    """Build a LoadSummary from optional table results and warn/error."""
    return build_load_summary(
        tables or [],
        warn_error=warn_error_summary or WarnErrorSummary(),
        run_id=run_id,
    )
