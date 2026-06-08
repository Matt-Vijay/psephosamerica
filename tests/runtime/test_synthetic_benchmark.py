"""Tests for the deterministic synthetic benchmark.

This produces the benchmark slices the CI no-regression gate runs against,
without a database or Track A: the controlled-experiment House from
OVERALL_GOAL.md, scored by the per-member model. Determinism is essential --
the gate compares these metrics to a committed pinned baseline -- so identical
seeds must yield identical metrics.
"""

from __future__ import annotations

from src.runtime.synthetic_benchmark import build_synthetic_benchmark


def test_synthetic_benchmark_has_overall_and_cross_pressured_slices() -> None:
    slices = {item.slice_name: item for item in build_synthetic_benchmark(seed=20260608)}
    assert "overall" in slices
    assert "cross_pressured" in slices
    assert slices["cross_pressured"].sample_count > 0


def test_synthetic_benchmark_is_deterministic() -> None:
    first = build_synthetic_benchmark(seed=20260608)
    second = build_synthetic_benchmark(seed=20260608)
    assert [(s.slice_name, s.brier_score, s.log_loss) for s in first] == [
        (s.slice_name, s.brier_score, s.log_loss) for s in second
    ]


def test_synthetic_benchmark_metrics_are_in_valid_ranges() -> None:
    for item in build_synthetic_benchmark(seed=20260608):
        assert 0.0 <= item.brier_score <= 1.0
        assert item.log_loss >= 0.0
        assert item.sample_count > 0


def test_cross_pressured_slice_is_harder_than_overall() -> None:
    slices = {item.slice_name: item for item in build_synthetic_benchmark(seed=20260608)}
    # The cross-pressured slice is the hard one; its Brier should exceed overall.
    assert slices["cross_pressured"].brier_score > slices["overall"].brier_score


def test_gate_from_synthetic_passes_against_freshly_pinned_baseline(tmp_path: object) -> None:
    from pathlib import Path

    from src.runtime.prediction_benchmark_gate import main

    baseline = Path(tmp_path) / "baseline.json"  # type: ignore[arg-type]
    # Pin from the synthetic run, then gate the same deterministic run at zero
    # tolerance: it must pass exactly. A model change that worsened the metrics
    # would make this exit non-zero in CI.
    assert (
        main(["--from-synthetic", "20260608", "--baseline", str(baseline), "--update-baseline"])
        == 0
    )
    assert (
        main(["--from-synthetic", "20260608", "--baseline", str(baseline), "--tolerance", "0.0"])
        == 0
    )


def test_gate_from_synthetic_detects_a_regression(tmp_path: object) -> None:
    from pathlib import Path

    from src.runtime.prediction_benchmark_gate import main

    baseline = Path(tmp_path) / "baseline.json"  # type: ignore[arg-type]
    # Pin a baseline far better than the synthetic run can achieve; the gate must
    # flag the (apparent) regression and exit non-zero.
    baseline.write_text(
        '{"model_name": "per_member_signal_model", "slices": '
        '[{"slice_name": "overall", "brier_score": 0.01, "log_loss": 0.05}]}',
        encoding="utf-8",
    )
    assert (
        main(["--from-synthetic", "20260608", "--baseline", str(baseline), "--tolerance", "0.01"])
        == 1
    )
