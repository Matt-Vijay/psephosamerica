"""Tests for the runnable benchmark-gate CLI.

Makes OVERALL_GOAL.md's "Brier and log-loss must not regress on the benchmark
slice without explicit approval" operationally enforceable: read an eval-report
artifact's per-slice metrics, compare against a pinned baseline, exit non-zero
on an unapproved regression.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.prediction_benchmark_gate import main


def _write_report(path: Path, slices: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"benchmark_slices": slices}), encoding="utf-8")


def _write_baseline(path: Path, slices: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"model_name": "per_member_signal_model", "slices": slices}),
        encoding="utf-8",
    )


def test_gate_passes_when_metrics_hold(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report, [{"slice_name": "overall", "brier_score": 0.14, "log_loss": 0.40}])
    _write_baseline(baseline, [{"slice_name": "overall", "brier_score": 0.14, "log_loss": 0.40}])

    exit_code = main(["--report", str(report), "--baseline", str(baseline), "--tolerance", "0.01"])
    assert exit_code == 0


def test_gate_fails_on_unapproved_regression(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report, [{"slice_name": "overall", "brier_score": 0.20, "log_loss": 0.40}])
    _write_baseline(baseline, [{"slice_name": "overall", "brier_score": 0.14, "log_loss": 0.40}])

    exit_code = main(["--report", str(report), "--baseline", str(baseline), "--tolerance", "0.01"])
    assert exit_code == 1


def test_gate_passes_regression_when_slice_is_approved(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report, [{"slice_name": "overall", "brier_score": 0.20, "log_loss": 0.40}])
    _write_baseline(baseline, [{"slice_name": "overall", "brier_score": 0.14, "log_loss": 0.40}])

    exit_code = main(
        [
            "--report",
            str(report),
            "--baseline",
            str(baseline),
            "--tolerance",
            "0.01",
            "--approve",
            "overall",
        ]
    )
    assert exit_code == 0


def test_update_baseline_pins_current_metrics(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    baseline = tmp_path / "baseline.json"
    _write_report(
        report,
        [
            {"slice_name": "overall", "brier_score": 0.13, "log_loss": 0.39, "sample_count": 100},
            {"slice_name": "party:D", "brier_score": 0.12, "log_loss": 0.37, "sample_count": 60},
        ],
    )

    exit_code = main(["--report", str(report), "--baseline", str(baseline), "--update-baseline"])
    assert exit_code == 0
    assert baseline.exists()

    # The freshly pinned baseline must now pass the gate against the same report.
    rerun = main(["--report", str(report), "--baseline", str(baseline), "--tolerance", "0.0"])
    assert rerun == 0


def test_missing_baseline_is_an_error_exit(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    _write_report(report, [{"slice_name": "overall", "brier_score": 0.14, "log_loss": 0.40}])
    exit_code = main(
        [
            "--report",
            str(report),
            "--baseline",
            str(tmp_path / "missing.json"),
            "--tolerance",
            "0.01",
        ]
    )
    assert exit_code == 2
