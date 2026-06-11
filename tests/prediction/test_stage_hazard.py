"""Tests for the discrete-time stage-hazard model."""

from __future__ import annotations

import numpy as np

from src.prediction.stage_hazard import (
    build_person_period,
    calibration_at_horizon,
    concordance_index,
    fit_hazard,
)

_NAMES = ("fast_track",)


def _synthetic(n: int = 400, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Half the bills are fast-track (event ~60d), half slow (mostly censored)."""
    rng = np.random.default_rng(seed)
    fast = np.zeros((n, 1))
    fast[: n // 2, 0] = 1.0
    duration = np.where(
        fast[:, 0] == 1.0,
        rng.integers(20, 120, size=n),
        rng.integers(500, 700, size=n),
    ).astype(np.float64)
    event = np.where(fast[:, 0] == 1.0, True, rng.random(n) < 0.05)
    return fast, duration, event


def test_person_period_labels_event_in_final_period() -> None:
    x = np.ones((2, 1))
    duration = np.array([65.0, 100.0])
    event = np.array([True, False])
    period_index, rows, labels = build_person_period(x, duration, event, period_days=30)
    # bill 0: periods 0,1,2 with event in period 2; bill 1: periods 0..3, no event
    assert period_index.tolist() == [0, 1, 2, 0, 1, 2, 3]
    assert labels.tolist() == [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert rows.shape == (7, 1)


def test_fit_separates_fast_from_slow_and_ranks_them() -> None:
    x, duration, event = _synthetic()
    model = fit_hazard(x, duration, event, _NAMES)
    assert model.coefficients[0] > 0.5  # fast-track raises the hazard
    # One binary feature -> two risk values; within-group pairs are ties (0.5
    # each), so even perfect group separation caps C below ~0.84 here.
    p = model.predict_event_by(x, 365.0)
    assert concordance_index(p, duration, event) > 0.8
    assert float(p[:200].mean()) > 0.6 > float(p[200:].mean())


def test_predict_event_by_is_monotone_in_horizon() -> None:
    x, duration, event = _synthetic()
    model = fit_hazard(x, duration, event, _NAMES)
    p90 = model.predict_event_by(x, 90.0)
    p365 = model.predict_event_by(x, 365.0)
    assert bool(np.all(p365 >= p90))
    assert bool(np.all((p90 >= 0.0) & (p365 <= 1.0)))


def test_calibration_excludes_bills_censored_before_horizon() -> None:
    predicted = np.array([0.5, 0.5, 0.5])
    duration = np.array([100.0, 100.0, 400.0])
    event = np.array([True, False, False])  # second bill censored at day 100 < horizon
    cal = calibration_at_horizon(predicted, duration, event, horizon_days=365.0)
    assert cal["n"] == 2  # the early-censored bill's outcome is unknown
    assert cal["base_rate"] == 0.5


def test_concordance_handles_no_events() -> None:
    risk = np.array([0.9, 0.1])
    duration = np.array([10.0, 20.0])
    event = np.array([False, False])
    assert concordance_index(risk, duration, event) == 0.5
