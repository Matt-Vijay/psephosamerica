"""Tests for the calibration dashboard data contract.

OVERALL_GOAL.md wants dashboards showing calibration per jurisdiction / party /
faction with live Brier + log-loss, and the cross-pressured slice highlighted.
This builds the backend payload a frontend renders, from the per-slice metrics
the eval report already emits.
"""

from __future__ import annotations

import pytest

from src.prediction.benchmark_gate import BenchmarkSliceMetrics
from src.prediction.calibration_dashboard import build_calibration_dashboard


def _slice(name: str, brier: float, log_loss: float, count: int) -> BenchmarkSliceMetrics:
    return BenchmarkSliceMetrics(
        slice_name=name, brier_score=brier, log_loss=log_loss, sample_count=count
    )


def _slices() -> list[BenchmarkSliceMetrics]:
    return [
        _slice("overall", 0.14, 0.40, 4000),
        _slice("cross_pressured", 0.20, 0.55, 400),
        _slice("party:D", 0.12, 0.36, 2200),
        _slice("party:R", 0.16, 0.44, 1800),
        _slice("jurisdiction:us_congress", 0.14, 0.40, 4000),
        _slice("faction:freedom_caucus", 0.18, 0.50, 120),
    ]


def test_dashboard_groups_slices_by_dimension() -> None:
    dashboard = build_calibration_dashboard("per_member_signal_model", _slices())
    assert dashboard.model_name == "per_member_signal_model"
    assert {entry.key for entry in dashboard.sections["party"]} == {"D", "R"}
    assert [entry.key for entry in dashboard.sections["jurisdiction"]] == ["us_congress"]
    assert [entry.key for entry in dashboard.sections["faction"]] == ["freedom_caucus"]


def test_dashboard_highlights_overall_and_cross_pressured() -> None:
    dashboard = build_calibration_dashboard("per_member_signal_model", _slices())
    assert dashboard.overall is not None
    assert dashboard.overall.brier_score == 0.14
    assert dashboard.cross_pressured is not None
    assert dashboard.cross_pressured.log_loss == 0.55
    # The headline slices are not duplicated into the dimension sections.
    assert "party" in dashboard.sections
    assert all(
        entry.dimension != "overall" for section in dashboard.sections.values() for entry in section
    )


def test_dashboard_entries_carry_metrics_and_key() -> None:
    dashboard = build_calibration_dashboard("per_member_signal_model", _slices())
    democrat = next(entry for entry in dashboard.sections["party"] if entry.key == "D")
    assert democrat.brier_score == 0.12
    assert democrat.log_loss == 0.36
    assert democrat.sample_count == 2200
    assert democrat.dimension == "party"


def test_dashboard_sorts_entries_within_a_section() -> None:
    dashboard = build_calibration_dashboard(
        "m",
        [_slice("party:R", 0.16, 0.44, 1), _slice("party:D", 0.12, 0.36, 1)],
    )
    assert [entry.key for entry in dashboard.sections["party"]] == ["D", "R"]


def test_dashboard_handles_missing_overall_and_cross_pressured() -> None:
    dashboard = build_calibration_dashboard("m", [_slice("party:D", 0.12, 0.36, 1)])
    assert dashboard.overall is None
    assert dashboard.cross_pressured is None
    assert dashboard.sections["party"][0].key == "D"


def test_dashboard_rejects_unknown_slice_namespace() -> None:
    with pytest.raises(ValueError, match="unknown dashboard dimension"):
        build_calibration_dashboard("m", [_slice("region:west", 0.1, 0.3, 1)])
