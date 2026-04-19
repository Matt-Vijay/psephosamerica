"""Canonical score semantics shared across recompute, feed, and snapshots."""

from __future__ import annotations

from typing import Any

from src.rules.models import Severity

BASELINE_DIMENSION = "conflict_of_interest_risk"
BASELINE_SCORE = 100.0

_SEVERITY_SCORE_DELTAS: dict[str, float] = {
    Severity.low.value: -1.0,
    Severity.medium.value: -2.0,
    Severity.high.value: -4.0,
    Severity.critical.value: -8.0,
}


def baseline_dimension_scores() -> dict[str, float]:
    return {BASELINE_DIMENSION: round(BASELINE_SCORE, 2)}


def normalize_dimension_scores(raw: Any) -> dict[str, float]:
    """Return canonical dimension scores.

    Legacy rows may encode a baseline snapshot as ``{}`` or ``None``.
    Normalize those cases to the explicit v1 baseline dimension so recovery
    diffs remain visible and downstream consumers see one baseline model.
    """
    if raw is None:
        return baseline_dimension_scores()

    scores = {str(key): float(value) for key, value in dict(raw).items()}
    if scores:
        return scores
    return baseline_dimension_scores()


def severity_score_delta(
    severity: Severity | str,
    *,
    overrides: dict[str, float] | None = None,
) -> float:
    key = severity.value if isinstance(severity, Severity) else str(severity).strip().lower()
    mapping = {**_SEVERITY_SCORE_DELTAS, **(overrides or {})}
    if not key:
        raise ValueError("severity is required when score_delta is not provided")
    if key not in mapping:
        raise ValueError(f"unknown severity: {severity!r}")
    return float(mapping[key])
