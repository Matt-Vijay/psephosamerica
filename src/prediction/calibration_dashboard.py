"""Calibration dashboard data contract.

OVERALL_GOAL.md: dashboards showing calibration per jurisdiction / party /
faction with live Brier and log-loss, and the cross-pressured slice (the votes
anyone cares about) highlighted. This builds the backend payload a frontend
renders, from the ``benchmark_slices`` the eval report already emits -- so the
dashboard view is a pure projection of the benchmark, never a separate source
of truth.

Slice names are namespaced by the producer (``benchmark_slices.py``):
``overall``, ``cross_pressured``, ``party:<key>``, ``jurisdiction:<key>``,
``faction:<key>``. The headline ``overall`` and ``cross_pressured`` slices are
surfaced on their own; everything else is grouped into its dimension section.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.prediction.benchmark_gate import BenchmarkSliceMetrics

_OVERALL = "overall"
_CROSS_PRESSURED = "cross_pressured"
_DIMENSIONS = ("party", "jurisdiction", "faction")


class DashboardSlice(BaseModel, frozen=True):
    """One calibration cell for the dashboard."""

    dimension: str
    key: str
    brier_score: float
    log_loss: float
    sample_count: int


class CalibrationDashboard(BaseModel):
    """The per-dimension calibration view for one model."""

    model_name: str
    overall: DashboardSlice | None = None
    cross_pressured: DashboardSlice | None = None
    sections: dict[str, list[DashboardSlice]] = Field(default_factory=dict)


def _entry(dimension: str, key: str, metrics: BenchmarkSliceMetrics) -> DashboardSlice:
    return DashboardSlice(
        dimension=dimension,
        key=key,
        brier_score=metrics.brier_score,
        log_loss=metrics.log_loss,
        sample_count=metrics.sample_count,
    )


def build_calibration_dashboard(
    model_name: str,
    slices: list[BenchmarkSliceMetrics],
) -> CalibrationDashboard:
    """Project per-slice benchmark metrics into a grouped dashboard payload."""
    overall: DashboardSlice | None = None
    cross_pressured: DashboardSlice | None = None
    sections: dict[str, list[DashboardSlice]] = {dimension: [] for dimension in _DIMENSIONS}

    for metrics in slices:
        name = metrics.slice_name
        if name == _OVERALL:
            overall = _entry(_OVERALL, _OVERALL, metrics)
            continue
        if name == _CROSS_PRESSURED:
            cross_pressured = _entry(_CROSS_PRESSURED, _CROSS_PRESSURED, metrics)
            continue
        dimension, separator, key = name.partition(":")
        if not separator or dimension not in sections:
            raise ValueError(f"unknown dashboard dimension for slice: {name}")
        sections[dimension].append(_entry(dimension, key, metrics))

    for dimension in sections:
        sections[dimension].sort(key=lambda entry: entry.key)

    return CalibrationDashboard(
        model_name=model_name,
        overall=overall,
        cross_pressured=cross_pressured,
        sections=sections,
    )
