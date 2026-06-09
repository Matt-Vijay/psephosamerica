"""Runnable benchmark-gate CLI over eval-report artifacts.

Makes OVERALL_GOAL.md's "Brier and log-loss must not regress on the benchmark
slice without explicit approval" operationally enforceable. It reads an eval
report's ``benchmark_slices`` (the per-jurisdiction / party / overall metrics
``build_prediction_eval_report`` now emits), compares them against a pinned
baseline JSON, and exits non-zero on any unapproved regression. ``--approve``
clears a named slice (the explicit human approval); ``--update-baseline``
re-pins the baseline from the current report.

CI wiring: once the pipeline produces a benchmark artifact, add a step
``python -m src.runtime.prediction_benchmark_gate --report <artifact>
--baseline benchmarks/prediction_baseline.json``. It is kept out of the shared
``ci.yml`` until that artifact exists so the gate never blocks on a missing
file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.prediction.benchmark_gate import (
    BenchmarkBaseline,
    BenchmarkSliceMetrics,
    evaluate_benchmark_gate,
)

_DEFAULT_TOLERANCE = 0.0


def slices_from_report(report: dict[str, object]) -> list[BenchmarkSliceMetrics]:
    """Parse the ``benchmark_slices`` array from an eval-report payload."""
    raw = report.get("benchmark_slices", [])
    if not isinstance(raw, list):
        raise ValueError("report benchmark_slices must be a list")
    return [BenchmarkSliceMetrics.model_validate(item) for item in raw]


def load_baseline(path: Path) -> BenchmarkBaseline:
    """Load a pinned baseline from JSON."""
    return BenchmarkBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def _slices_from_real_corpus(
    corpus_path: Path, *, window: str
) -> list[BenchmarkSliceMetrics] | None:
    """Recompute one window's per-slice metrics from a committed real vote corpus."""
    if not corpus_path.exists():
        return None
    from src.prediction.real_data_eval import evaluate_windows, load_vote_rows
    from src.runtime.real_benchmark import default_congress_windows

    rows = load_vote_rows(corpus_path)
    windows = [item for item in default_congress_windows() if item.name == window]
    result = evaluate_windows(rows, windows=windows)
    slices = result.get(window, {})
    if not slices:
        return None
    return [
        BenchmarkSliceMetrics(
            slice_name=metrics.slice_name,
            brier_score=metrics.brier_score,
            log_loss=metrics.log_loss,
            sample_count=metrics.sample_count,
        )
        for metrics in slices.values()
    ]


def _write_baseline(path: Path, slices: list[BenchmarkSliceMetrics], *, model_name: str) -> None:
    baseline = BenchmarkBaseline(model_name=model_name, slices=slices)
    path.write_text(baseline.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _format_result_lines(result: object) -> list[str]:
    lines: list[str] = []
    for regression in result.regressions:  # type: ignore[attr-defined]
        status = "APPROVED" if regression.approved else "BLOCKING"
        lines.append(
            f"  [{status}] {regression.slice_name} {regression.metric}: "
            f"{regression.baseline:.4f} -> {regression.current:.4f} (+{regression.delta:.4f})"
        )
    for missing in result.missing_slices:  # type: ignore[attr-defined]
        lines.append(f"  [BLOCKING] {missing}: slice missing from report (coverage loss)")
    for improvement in result.improvements:  # type: ignore[attr-defined]
        lines.append(
            f"  [improved] {improvement.slice_name} {improvement.metric}: "
            f"{improvement.baseline:.4f} -> {improvement.current:.4f}"
        )
    return lines


def main(argv: list[str]) -> int:
    """Run the benchmark gate; return 0 (pass), 1 (regression), or 2 (usage error)."""
    parser = argparse.ArgumentParser(description="Brier/log-loss no-regression benchmark gate")
    parser.add_argument("--report", help="path to the eval-report JSON artifact")
    parser.add_argument(
        "--from-synthetic",
        type=int,
        metavar="SEED",
        help="build benchmark slices from the deterministic synthetic House at SEED",
    )
    parser.add_argument(
        "--from-real-corpus",
        metavar="JSONL",
        help="recompute slices from a committed real vote corpus (deterministic)",
    )
    parser.add_argument(
        "--window",
        default="118th-h2",
        help="window name to gate when using --from-real-corpus",
    )
    parser.add_argument("--baseline", required=True, help="path to the pinned baseline JSON")
    parser.add_argument("--tolerance", type=float, default=_DEFAULT_TOLERANCE)
    parser.add_argument(
        "--approve",
        action="append",
        default=[],
        help="slice name cleared to regress (repeatable)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="re-pin the baseline from the current report and exit",
    )
    parser.add_argument(
        "--model-name",
        default="per_member_signal_model",
        help="model name recorded when pinning a baseline",
    )
    args = parser.parse_args(argv)

    sources = [args.report, args.from_synthetic, args.from_real_corpus]
    if sum(source is not None for source in sources) != 1:
        print(
            "benchmark-gate: provide exactly one of --report / --from-synthetic / --from-real-corpus"
        )
        return 2

    baseline_path = Path(args.baseline)
    if args.from_synthetic is not None:
        from src.runtime.synthetic_benchmark import build_synthetic_benchmark

        current = build_synthetic_benchmark(seed=args.from_synthetic)
    elif args.from_real_corpus is not None:
        real_slices = _slices_from_real_corpus(Path(args.from_real_corpus), window=args.window)
        if real_slices is None:
            print(f"benchmark-gate: corpus/window produced no slices: {args.from_real_corpus}")
            return 2
        current = real_slices
    else:
        report_path = Path(args.report)
        if not report_path.exists():
            print(f"benchmark-gate: report not found: {report_path}")
            return 2
        report = json.loads(report_path.read_text(encoding="utf-8"))
        current = slices_from_report(report)

    if args.update_baseline:
        _write_baseline(baseline_path, current, model_name=args.model_name)
        print(f"benchmark-gate: pinned {len(current)} slices to {baseline_path}")
        return 0

    if not baseline_path.exists():
        print(f"benchmark-gate: baseline not found: {baseline_path}")
        return 2

    baseline = load_baseline(baseline_path)
    result = evaluate_benchmark_gate(
        baseline,
        current,
        tolerance=args.tolerance,
        approved_regressions=set(args.approve),
    )
    for line in _format_result_lines(result):
        print(line)
    if result.passed:
        print("benchmark-gate: PASS")
        return 0
    print("benchmark-gate: FAIL (unapproved Brier/log-loss regression)")
    return 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
