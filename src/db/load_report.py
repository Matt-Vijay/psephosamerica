"""Load reporting models and helpers for batch write summaries.

Pure in-memory types — no database calls.

Provides:
  TableWriteResult      — counts for one table's write pass
  WarnErrorSummary      — collected warnings and errors across a load
  LoadSummary           — aggregated report across all tables in a run
  merge_table_results() — combine a sequence of TableWriteResults
  build_load_summary()  — construct a LoadSummary from raw results + warnings
  status_dict()         — compact dict for the /status endpoint
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableWriteResult:
    """Outcome of writing one batch to a single canonical table."""

    table: str
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0

    def __post_init__(self) -> None:
        for attr in ("inserted", "updated", "skipped", "rejected"):
            val = getattr(self, attr)
            if val < 0:
                raise ValueError(f"{attr} must be >= 0, got {val}")

    @property
    def total_attempted(self) -> int:
        return self.inserted + self.updated + self.skipped + self.rejected

    @property
    def total_written(self) -> int:
        return self.inserted + self.updated


@dataclass
class WarnErrorSummary:
    """Warnings and errors collected during an ingestion or recompute pass."""

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


@dataclass(frozen=True)
class LoadSummary:
    """Aggregated load/recompute summary across all tables in one run."""

    run_id: int | None
    table_results: tuple[TableWriteResult, ...]
    warn_error: WarnErrorSummary

    # Aggregate totals (computed at build time, not derived on each access)
    total_inserted: int
    total_updated: int
    total_skipped: int
    total_rejected: int

    @property
    def total_attempted(self) -> int:
        return self.total_inserted + self.total_updated + self.total_skipped + self.total_rejected

    @property
    def total_written(self) -> int:
        return self.total_inserted + self.total_updated

    @property
    def ok(self) -> bool:
        """True when there are no errors."""
        return not self.warn_error.has_errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def merge_table_results(results: Sequence[TableWriteResult]) -> TableWriteResult:
    """Combine multiple TableWriteResults (same or different tables) into one.

    The merged result's ``table`` is set to ``"<merged>"`` to signal that it
    spans multiple tables.
    """
    return TableWriteResult(
        table="<merged>",
        inserted=sum(r.inserted for r in results),
        updated=sum(r.updated for r in results),
        skipped=sum(r.skipped for r in results),
        rejected=sum(r.rejected for r in results),
    )


def build_load_summary(
    table_results: Sequence[TableWriteResult],
    warn_error: WarnErrorSummary | None = None,
    run_id: int | None = None,
) -> LoadSummary:
    """Construct a LoadSummary from a sequence of per-table results."""
    if warn_error is None:
        warn_error = WarnErrorSummary()

    merged = merge_table_results(table_results)

    return LoadSummary(
        run_id=run_id,
        table_results=tuple(table_results),
        warn_error=warn_error,
        total_inserted=merged.inserted,
        total_updated=merged.updated,
        total_skipped=merged.skipped,
        total_rejected=merged.rejected,
    )


def status_dict(summary: LoadSummary) -> dict:
    """Return a compact dict suitable for serialisation into the /status endpoint.

    Shape::

        {
            "run_id": 42,
            "ok": true,
            "counts": {"inserted": 10, "updated": 2, "skipped": 0, "rejected": 1},
            "warnings": 3,
            "errors": 0,
            "tables": [
                {"table": "member", "inserted": 5, "updated": 1, "skipped": 0, "rejected": 0},
                ...
            ]
        }
    """
    return {
        "run_id": summary.run_id,
        "ok": summary.ok,
        "counts": {
            "inserted": summary.total_inserted,
            "updated": summary.total_updated,
            "skipped": summary.total_skipped,
            "rejected": summary.total_rejected,
        },
        "warnings": summary.warn_error.warning_count,
        "errors": summary.warn_error.error_count,
        "tables": [
            {
                "table": r.table,
                "inserted": r.inserted,
                "updated": r.updated,
                "skipped": r.skipped,
                "rejected": r.rejected,
            }
            for r in summary.table_results
        ],
    }
