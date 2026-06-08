"""Tests for the Brier/log-loss no-regression benchmark gate.

OVERALL_GOAL.md definition-of-done: "benchmark metrics never regress
unobserved" and a CI gate where "Brier and log-loss must not regress on the
benchmark slice without explicit approval". This gate compares the current
per-slice metrics against a pinned baseline and fails on any unapproved
regression beyond tolerance, on a dropped slice (coverage loss), or on a
metric that silently disappears.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.prediction.benchmark_gate import (
    BenchmarkBaseline,
    BenchmarkSliceMetrics,
    evaluate_benchmark_gate,
)


def _baseline() -> BenchmarkBaseline:
    return BenchmarkBaseline(
        model_name="per_member_signal_model",
        slices=[
            BenchmarkSliceMetrics(
                slice_name="cross_pressured", brier_score=0.20, log_loss=0.55, sample_count=400
            ),
            BenchmarkSliceMetrics(
                slice_name="overall", brier_score=0.14, log_loss=0.40, sample_count=4000
            ),
        ],
    )


def _slice(name: str, brier: float, log_loss: float, count: int = 400) -> BenchmarkSliceMetrics:
    return BenchmarkSliceMetrics(
        slice_name=name, brier_score=brier, log_loss=log_loss, sample_count=count
    )


def test_gate_passes_when_metrics_hold_or_improve() -> None:
    current = [
        _slice("cross_pressured", 0.20, 0.55),
        _slice("overall", 0.12, 0.36, 4000),  # improved
    ]
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=1e-6)
    assert result.passed
    assert result.regressions == []
    assert any(improvement.slice_name == "overall" for improvement in result.improvements)


def test_gate_fails_on_brier_regression_beyond_tolerance() -> None:
    current = [
        _slice("cross_pressured", 0.23, 0.55),  # brier worse by 0.03
        _slice("overall", 0.14, 0.40, 4000),
    ]
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=0.005)
    assert not result.passed
    assert [regression.slice_name for regression in result.regressions] == ["cross_pressured"]
    assert result.regressions[0].metric == "brier_score"
    assert result.regressions[0].delta == pytest.approx(0.03)


def test_gate_fails_on_log_loss_regression() -> None:
    current = [
        _slice("cross_pressured", 0.20, 0.62),  # log loss worse by 0.07
        _slice("overall", 0.14, 0.40, 4000),
    ]
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=0.01)
    assert not result.passed
    metrics = {regression.metric for regression in result.regressions}
    assert metrics == {"log_loss"}


def test_gate_tolerates_small_regression_within_tolerance() -> None:
    current = [
        _slice("cross_pressured", 0.203, 0.553),
        _slice("overall", 0.14, 0.40, 4000),
    ]
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=0.01)
    assert result.passed


def test_gate_allows_regression_only_with_explicit_approval() -> None:
    current = [
        _slice("cross_pressured", 0.25, 0.60),
        _slice("overall", 0.14, 0.40, 4000),
    ]
    blocked = evaluate_benchmark_gate(_baseline(), current, tolerance=0.005)
    assert not blocked.passed

    approved = evaluate_benchmark_gate(
        _baseline(), current, tolerance=0.005, approved_regressions={"cross_pressured"}
    )
    assert approved.passed
    # The regression is still reported for the audit trail, just not blocking.
    assert {regression.slice_name for regression in approved.regressions} == {"cross_pressured"}
    assert all(regression.approved for regression in approved.regressions)
    assert approved.approved_slice_names == ["cross_pressured"]


def test_gate_fails_when_a_baseline_slice_is_missing() -> None:
    current = [_slice("cross_pressured", 0.20, 0.55)]  # "overall" dropped
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=0.01)
    assert not result.passed
    assert result.missing_slices == ["overall"]


def test_gate_reports_new_slices_without_failing() -> None:
    current = [
        _slice("cross_pressured", 0.20, 0.55),
        _slice("overall", 0.14, 0.40, 4000),
        _slice("state_legislature", 0.18, 0.50),
    ]
    result = evaluate_benchmark_gate(_baseline(), current, tolerance=0.01)
    assert result.passed
    assert result.new_slices == ["state_legislature"]


def test_benchmark_baseline_rejects_duplicate_slices() -> None:
    with pytest.raises(ValidationError):
        BenchmarkBaseline(
            model_name="per_member_signal_model",
            slices=[_slice("overall", 0.1, 0.3), _slice("overall", 0.2, 0.4)],
        )


def test_benchmark_gate_rejects_negative_tolerance() -> None:
    with pytest.raises(ValueError, match="tolerance"):
        evaluate_benchmark_gate(_baseline(), [], tolerance=-0.01)
