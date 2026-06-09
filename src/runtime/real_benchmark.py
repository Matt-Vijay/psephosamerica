"""Real-data benchmark runner + CLI over a House vote corpus.

Loads a JSONL vote corpus (``real_data_eval.VoteRow`` rows from Track A or the
House Clerk feed), evaluates the per-member model over rolling strict-cutoff
congress windows, and writes a JSON report of the first real Brier / log-loss /
accuracy / ECE per slice (all / cross_pressured / per-party / per-state) for
each window. Optionally pins a real-data baseline (one window's slices) for the
no-regression gate.

Pure orchestration over the offline-tested ``evaluate_windows``; the only IO is
reading the corpus and writing the report/baseline JSON.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from src.prediction.benchmark_gate import BenchmarkBaseline, BenchmarkSliceMetrics
from src.prediction.real_data_eval import EvalWindow, SliceMetrics, evaluate_windows, load_vote_rows


def default_congress_windows() -> list[EvalWindow]:
    """Rolling strict-cutoff windows spanning recent congresses (115th-118th + current).

    Each window trains on everything up to ``train_end`` and evaluates on votes
    inside the window, so calibration drift across windows is visible.
    """
    return [
        EvalWindow("115th", date(2017, 1, 2), date(2017, 1, 3), date(2018, 12, 31)),
        EvalWindow("116th", date(2019, 1, 2), date(2019, 1, 3), date(2020, 12, 31)),
        EvalWindow("117th", date(2021, 1, 2), date(2021, 1, 3), date(2022, 12, 31)),
        EvalWindow("118th-h1", date(2023, 6, 30), date(2023, 7, 1), date(2023, 12, 31)),
        EvalWindow("118th-h2", date(2024, 3, 31), date(2024, 4, 1), date(2025, 1, 2)),
        EvalWindow("119th", date(2025, 6, 30), date(2025, 7, 1), date(2026, 12, 31)),
    ]


def _slice_to_dict(metrics: SliceMetrics) -> dict[str, float | int | str]:
    return {
        "slice_name": metrics.slice_name,
        "brier_score": metrics.brier_score,
        "log_loss": metrics.log_loss,
        "accuracy": metrics.accuracy,
        "ece": metrics.ece,
        "sample_count": metrics.sample_count,
    }


def build_report(
    result: dict[str, dict[str, SliceMetrics]],
) -> dict[str, object]:
    """Serialize the per-window per-slice metrics into a JSON-ready report."""
    windows = {
        window: {name: _slice_to_dict(metrics) for name, metrics in slices.items()}
        for window, slices in result.items()
        if slices
    }
    return {"windows": windows, "window_count": len(windows)}


def baseline_from_window(
    result: dict[str, dict[str, SliceMetrics]],
    *,
    window: str,
    model_name: str = "per_member_signal_model",
) -> BenchmarkBaseline:
    """Project one window's slices into a pinnable benchmark baseline."""
    slices = result.get(window, {})
    return BenchmarkBaseline(
        model_name=model_name,
        slices=[
            BenchmarkSliceMetrics(
                slice_name=metrics.slice_name,
                brier_score=metrics.brier_score,
                log_loss=metrics.log_loss,
                sample_count=metrics.sample_count,
            )
            for metrics in slices.values()
        ],
    )


def main(argv: list[str]) -> int:
    """Run the real-data benchmark; write the report and (optionally) pin a baseline."""
    parser = argparse.ArgumentParser(
        description="Real-data per-member benchmark over a vote corpus"
    )
    parser.add_argument("--corpus", required=True, help="JSONL vote corpus path")
    parser.add_argument("--report", required=True, help="output report JSON path")
    parser.add_argument("--pin-baseline", help="output baseline JSON path (one window)")
    parser.add_argument("--baseline-window", default="118th-h2")
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"real-benchmark: corpus not found: {corpus_path}")
        return 2

    rows = load_vote_rows(corpus_path)
    result = evaluate_windows(rows, windows=default_congress_windows())
    report = build_report(result)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"real-benchmark: wrote {report['window_count']} windows to {args.report}")

    if args.pin_baseline:
        baseline = baseline_from_window(result, window=args.baseline_window)
        Path(args.pin_baseline).write_text(
            baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(f"real-benchmark: pinned {len(baseline.slices)} slices to {args.pin_baseline}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
