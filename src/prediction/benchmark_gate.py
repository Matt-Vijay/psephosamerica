"""Brier/log-loss no-regression benchmark gate.

OVERALL_GOAL.md requires a CI gate where Brier and log-loss must not regress
on the benchmark slice without explicit approval, so that "benchmark metrics
never regress unobserved". This module is the enforceable core: it compares
the current per-slice metrics against a pinned baseline and decides whether
the run may proceed.

A run fails the gate when, for any baseline slice not explicitly approved for
regression:

* Brier score rises by more than ``tolerance``, or
* log-loss rises by more than ``tolerance``, or
* the slice is missing entirely (a silent coverage loss).

New slices are reported but never block. Improvements are reported so the pin
can be tightened deliberately. Approved regressions are still recorded for the
audit trail; they just do not block. The pinned baseline lives in the repo and
is updated only by an explicit, reviewed change -- that review *is* the
"explicit approval".
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Self

_METRICS = ("brier_score", "log_loss")


class BenchmarkSliceMetrics(BaseModel, frozen=True):
    """Brier and log-loss for one benchmark slice (jurisdiction/party/faction)."""

    slice_name: str = Field(min_length=1)
    brier_score: float = Field(ge=0.0, le=1.0)
    log_loss: float = Field(ge=0.0)
    sample_count: int = Field(default=0, ge=0)


class BenchmarkBaseline(BaseModel):
    """The pinned per-slice metrics a run must not regress against."""

    model_name: str = Field(min_length=1)
    slices: list[BenchmarkSliceMetrics] = Field(default_factory=list)

    @model_validator(mode="after")
    def _slice_names_are_unique(self) -> Self:
        names = [item.slice_name for item in self.slices]
        if len(set(names)) != len(names):
            raise ValueError("baseline slice_name values must be unique")
        return self


class BenchmarkRegression(BaseModel, frozen=True):
    """A single metric that worsened on a slice beyond tolerance."""

    slice_name: str
    metric: str
    baseline: float
    current: float
    delta: float
    approved: bool


class BenchmarkImprovement(BaseModel, frozen=True):
    """A single metric that improved on a slice (the pin could be tightened)."""

    slice_name: str
    metric: str
    baseline: float
    current: float
    delta: float


class BenchmarkGateResult(BaseModel):
    """The verdict of the no-regression gate over all slices."""

    passed: bool
    regressions: list[BenchmarkRegression] = Field(default_factory=list)
    improvements: list[BenchmarkImprovement] = Field(default_factory=list)
    missing_slices: list[str] = Field(default_factory=list)
    new_slices: list[str] = Field(default_factory=list)
    approved_slice_names: list[str] = Field(default_factory=list)


def evaluate_benchmark_gate(
    baseline: BenchmarkBaseline,
    current: list[BenchmarkSliceMetrics],
    *,
    tolerance: float,
    approved_regressions: set[str] | None = None,
) -> BenchmarkGateResult:
    """Compare current per-slice metrics against the pinned baseline.

    ``tolerance`` is the largest metric increase tolerated before a slice is a
    regression. ``approved_regressions`` is the set of slice names explicitly
    cleared to regress (the human approval); their regressions are reported but
    do not block.
    """
    if tolerance < 0.0:
        raise ValueError("tolerance must be non-negative")
    approved = approved_regressions or set()

    current_by_name = {item.slice_name: item for item in current}
    baseline_by_name = {item.slice_name: item for item in baseline.slices}

    regressions: list[BenchmarkRegression] = []
    improvements: list[BenchmarkImprovement] = []
    missing_slices = sorted(set(baseline_by_name) - set(current_by_name))
    new_slices = sorted(set(current_by_name) - set(baseline_by_name))

    blocking = bool(missing_slices)
    for slice_name in sorted(set(baseline_by_name) & set(current_by_name)):
        pinned = baseline_by_name[slice_name]
        observed = current_by_name[slice_name]
        slice_approved = slice_name in approved
        for metric in _METRICS:
            baseline_value = getattr(pinned, metric)
            current_value = getattr(observed, metric)
            delta = current_value - baseline_value
            if delta > tolerance:
                regressions.append(
                    BenchmarkRegression(
                        slice_name=slice_name,
                        metric=metric,
                        baseline=baseline_value,
                        current=current_value,
                        delta=delta,
                        approved=slice_approved,
                    )
                )
                if not slice_approved:
                    blocking = True
            elif delta < -tolerance:
                improvements.append(
                    BenchmarkImprovement(
                        slice_name=slice_name,
                        metric=metric,
                        baseline=baseline_value,
                        current=current_value,
                        delta=delta,
                    )
                )

    return BenchmarkGateResult(
        passed=not blocking,
        regressions=regressions,
        improvements=improvements,
        missing_slices=missing_slices,
        new_slices=new_slices,
        approved_slice_names=sorted(approved & set(baseline_by_name)),
    )
