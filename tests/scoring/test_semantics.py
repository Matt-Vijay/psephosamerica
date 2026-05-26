"""Unit tests for the canonical score-semantics helpers in src/scoring/semantics.py."""

from __future__ import annotations

import pytest

from src.rules.models import Severity
from src.scoring.semantics import (
    BASELINE_DIMENSION,
    BASELINE_SCORE,
    baseline_dimension_scores,
    normalize_dimension_scores,
    severity_score_delta,
)


def test_baseline_dimension_scores_returns_single_v1_baseline() -> None:
    assert baseline_dimension_scores() == {BASELINE_DIMENSION: round(BASELINE_SCORE, 2)}


def test_normalize_dimension_scores_treats_none_as_baseline() -> None:
    assert normalize_dimension_scores(None) == baseline_dimension_scores()


def test_normalize_dimension_scores_treats_empty_mapping_as_baseline() -> None:
    assert normalize_dimension_scores({}) == baseline_dimension_scores()


def test_normalize_dimension_scores_coerces_keys_and_values() -> None:
    assert normalize_dimension_scores({1: "5", "x": 2}) == {"1": 5.0, "x": 2.0}


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.low, -1.0),
        (Severity.medium, -2.0),
        (Severity.high, -4.0),
        (Severity.critical, -8.0),
        ("HIGH", -4.0),  # string is normalised (stripped + lowercased)
        ("  medium  ", -2.0),
    ],
)
def test_severity_score_delta_maps_known_severities(severity: object, expected: float) -> None:
    assert severity_score_delta(severity) == expected  # type: ignore[arg-type]


def test_severity_score_delta_applies_overrides() -> None:
    assert severity_score_delta("low", overrides={"low": -10.0}) == -10.0


def test_severity_score_delta_rejects_blank_severity() -> None:
    with pytest.raises(ValueError, match="severity is required"):
        severity_score_delta("")


def test_severity_score_delta_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="unknown severity"):
        severity_score_delta("nonsense")
