"""Tests for the shared feature-dict logistic trainer/scorer."""

from __future__ import annotations

from src.prediction.logistic import (
    auc_of_rows,
    clamped_sigmoid,
    score_row,
    train_logistic_rows,
)

_NAMES = ("a", "b")


def _separable_rows() -> list[tuple[dict[str, float], bool]]:
    return [({"a": 1.0, "b": 0.0}, True) for _ in range(20)] + [
        ({"a": -1.0, "b": 0.0}, False) for _ in range(20)
    ]


def test_clamped_sigmoid_is_overflow_safe_and_monotone() -> None:
    assert clamped_sigmoid(-1e9) == clamped_sigmoid(-1e6)  # clamped, no OverflowError
    assert 0.0 < clamped_sigmoid(-1e9) < clamped_sigmoid(0.0) < clamped_sigmoid(1e9) <= 1.0
    assert clamped_sigmoid(0.0) == 0.5


def test_train_on_separable_rows_ranks_perfectly() -> None:
    rows = _separable_rows()
    intercept, coef = train_logistic_rows(rows, _NAMES)
    assert coef["a"] > 0.0
    assert auc_of_rows(intercept, coef, _NAMES, rows) == 1.0


def test_empty_rows_yield_constant_half_head() -> None:
    intercept, coef = train_logistic_rows([], _NAMES)
    assert intercept == 0.0
    assert all(v == 0.0 for v in coef.values())
    assert score_row(intercept, coef, _NAMES, {"a": 5.0}) == 0.5


def test_score_row_ignores_features_outside_names() -> None:
    intercept, coef = train_logistic_rows(_separable_rows(), _NAMES)
    base = score_row(intercept, coef, _NAMES, {"a": 1.0})
    with_extra = score_row(intercept, coef, _NAMES, {"a": 1.0, "unrelated": 99.0})
    assert base == with_extra
