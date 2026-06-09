"""No-regression CI gate for the defection-ranking head (AUC, higher-is-better).

The Brier/log-loss gate in :mod:`benchmark_gate` is lower-is-better and cannot
express the defection product's metric: ranking who breaks with their party is
scored by ROC AUC, where *higher* is better. This module is the enforceable
floor for that metric. A run fails the gate when, for any pinned slice not
explicitly approved for regression:

* AUC falls by more than ``tolerance`` below the pinned baseline, or
* the slice is missing entirely (a silent coverage loss).

New slices are reported but never block; improvements are reported so the pin
can be tightened deliberately. The pinned baseline lives in the repo and moves
only by reviewed change -- that review *is* the explicit approval. The pinned
numbers are the real measured AUC on Track A's roll-call corpus (see
``benchmarks/defection_auc_baseline.json`` and ``benchmarks/SLICE_DEFINITION.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator


class DefectionSliceMetrics(BaseModel, frozen=True):
    """ROC AUC (and sample sizes) for one defection-ranking slice."""

    slice_name: str = Field(min_length=1)
    auc: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    positives: int = Field(default=0, ge=0)


class DefectionBaseline(BaseModel):
    """The pinned per-slice defection AUC a run must not regress against."""

    model_name: str = Field(min_length=1)
    cutoff: str | None = Field(
        default=None
    )  # strict no-leakage feature cutoff the pin was measured at
    slices: list[DefectionSliceMetrics] = Field(default_factory=list)

    @model_validator(mode="after")
    def _slice_names_are_unique(self) -> Self:
        names = [item.slice_name for item in self.slices]
        if len(set(names)) != len(names):
            raise ValueError("baseline slice_name values must be unique")
        return self

    @classmethod
    def load(cls, path: Path) -> DefectionBaseline:
        """Load a pinned baseline from JSON."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def dump(self, path: Path) -> None:
        """Write the baseline to JSON (stable key order, trailing newline)."""
        path.write_text(json.dumps(self.model_dump(), indent=2) + "\n", encoding="utf-8")


class DefectionRegression(BaseModel, frozen=True):
    """An AUC that dropped on a slice beyond tolerance."""

    slice_name: str
    baseline: float
    current: float
    delta: float
    approved: bool


class DefectionImprovement(BaseModel, frozen=True):
    """An AUC that rose on a slice (the pin could be tightened)."""

    slice_name: str
    baseline: float
    current: float
    delta: float


class DefectionGateResult(BaseModel):
    """The verdict of the AUC no-regression gate over all slices."""

    passed: bool
    regressions: list[DefectionRegression] = Field(default_factory=list)
    improvements: list[DefectionImprovement] = Field(default_factory=list)
    missing_slices: list[str] = Field(default_factory=list)
    new_slices: list[str] = Field(default_factory=list)
    approved_slice_names: list[str] = Field(default_factory=list)


def evaluate_defection_gate(
    baseline: DefectionBaseline,
    current: list[DefectionSliceMetrics],
    *,
    tolerance: float,
    approved_slice_names: frozenset[str] = frozenset(),
) -> DefectionGateResult:
    """Compare current defection AUC against the pinned baseline.

    Fails when any non-approved baseline slice drops AUC by more than
    ``tolerance`` or is missing from ``current``. New slices and improvements are
    reported but never block.
    """
    current_by_name = {item.slice_name: item for item in current}
    baseline_names = {item.slice_name for item in baseline.slices}
    regressions: list[DefectionRegression] = []
    improvements: list[DefectionImprovement] = []
    missing: list[str] = []
    passed = True
    for pinned in baseline.slices:
        observed = current_by_name.get(pinned.slice_name)
        if observed is None:
            missing.append(pinned.slice_name)
            if pinned.slice_name not in approved_slice_names:
                passed = False
            continue
        delta = observed.auc - pinned.auc
        if delta < -tolerance:
            approved = pinned.slice_name in approved_slice_names
            regressions.append(
                DefectionRegression(
                    slice_name=pinned.slice_name,
                    baseline=pinned.auc,
                    current=observed.auc,
                    delta=delta,
                    approved=approved,
                )
            )
            if not approved:
                passed = False
        elif delta > 0.0:
            improvements.append(
                DefectionImprovement(
                    slice_name=pinned.slice_name,
                    baseline=pinned.auc,
                    current=observed.auc,
                    delta=delta,
                )
            )
    new_slices = sorted(set(current_by_name) - baseline_names)
    return DefectionGateResult(
        passed=passed,
        regressions=regressions,
        improvements=improvements,
        missing_slices=missing,
        new_slices=new_slices,
        approved_slice_names=sorted(approved_slice_names),
    )
