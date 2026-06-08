"""Per-slice benchmark metric rollups.

OVERALL_GOAL.md wants calibration and accuracy reported per jurisdiction /
party / faction, with the Brier/log-loss no-regression gate applied per slice.
The eval report already produces scored ``PredictionBacktestPredictionPayload``
rows -- each carrying its party, jurisdiction, Brier score and log-loss -- so
the per-slice rollups are a pure aggregation over those rows.

This is the bridge between the model output and ``benchmark_gate``: compute the
slices, then hand them to ``evaluate_benchmark_gate``. Slice keys are namespaced
(``overall``, ``party:D``, ``jurisdiction:us_congress``) so a single flat list
covers every cut a dashboard or the gate cares about.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.prediction.backtest import PredictionBacktestPredictionPayload
from src.prediction.benchmark_gate import (
    BenchmarkBaseline,
    BenchmarkGateResult,
    BenchmarkSliceMetrics,
    evaluate_benchmark_gate,
)

_OVERALL_SLICE = "overall"


def _is_evaluated(prediction: PredictionBacktestPredictionPayload) -> bool:
    return (
        prediction.skipped_reason is None
        and prediction.correct is not None
        and prediction.brier_score is not None
        and prediction.log_loss is not None
    )


def _slice_metrics(
    slice_name: str, predictions: list[PredictionBacktestPredictionPayload]
) -> BenchmarkSliceMetrics | None:
    briers = [item.brier_score for item in predictions if item.brier_score is not None]
    losses = [item.log_loss for item in predictions if item.log_loss is not None]
    if not briers or not losses:
        return None
    return BenchmarkSliceMetrics(
        slice_name=slice_name,
        brier_score=sum(briers) / len(briers),
        log_loss=sum(losses) / len(losses),
        sample_count=len(predictions),
    )


def compute_benchmark_slices(
    predictions: Iterable[PredictionBacktestPredictionPayload],
    *,
    by_party: bool = True,
    by_jurisdiction: bool = True,
) -> list[BenchmarkSliceMetrics]:
    """Roll up Brier/log-loss over evaluated predictions into named slices.

    Only non-skipped binary predictions contribute. The ``overall`` slice is
    always present; per-party and per-jurisdiction slices are added when the
    corresponding dimension is requested and known. Results are sorted with
    ``overall`` first, then alphabetically.
    """
    evaluated = [item for item in predictions if _is_evaluated(item)]

    grouped: dict[str, list[PredictionBacktestPredictionPayload]] = {_OVERALL_SLICE: evaluated}
    if by_party:
        for item in evaluated:
            if item.party:
                grouped.setdefault(f"party:{item.party}", []).append(item)
    if by_jurisdiction:
        for item in evaluated:
            grouped.setdefault(f"jurisdiction:{item.jurisdiction_id}", []).append(item)

    metrics = [
        slice_metrics
        for slice_name, members in grouped.items()
        if (slice_metrics := _slice_metrics(slice_name, members)) is not None
    ]
    metrics.sort(key=lambda item: (item.slice_name != _OVERALL_SLICE, item.slice_name))
    return metrics


def evaluate_predictions_against_baseline(
    predictions: Iterable[PredictionBacktestPredictionPayload],
    baseline: BenchmarkBaseline,
    *,
    tolerance: float,
    approved_regressions: set[str] | None = None,
    by_party: bool = True,
    by_jurisdiction: bool = True,
) -> BenchmarkGateResult:
    """Compute per-slice metrics from predictions and run the no-regression gate."""
    current = compute_benchmark_slices(
        predictions, by_party=by_party, by_jurisdiction=by_jurisdiction
    )
    return evaluate_benchmark_gate(
        baseline,
        current,
        tolerance=tolerance,
        approved_regressions=approved_regressions,
    )
