"""Property/fuzz tests: the eval-window planner only emits cutoff-safe windows.

Every planned window must satisfy the strict temporal ordering that prevents
leakage, and the planner must not crash on any reasonable year/window inputs.
Seeded stdlib randomness (no hypothesis offline).
"""

from __future__ import annotations

import random

import pytest

from src.prediction.window_plan import build_prediction_eval_window_plan


def test_planner_emits_only_cutoff_safe_windows() -> None:
    rng = random.Random(2024)
    for _ in range(400):
        start = rng.randint(2008, 2026)
        end = start + rng.randint(0, 6)
        train_years = rng.randint(1, 4)
        label_years = rng.randint(1, 3)

        plan = build_prediction_eval_window_plan(
            start_label_year=start,
            end_label_year=end,
            train_years=train_years,
            label_years=label_years,
        )

        for w in plan.windows:
            assert (
                w.training_feature_cutoff
                < w.train_start
                <= w.train_end
                <= w.feature_cutoff
                < w.label_start
                <= w.label_end
            )
        # Windows are ordered by ascending label window.
        starts = [w.label_start for w in plan.windows]
        assert starts == sorted(starts)


def test_planner_rejects_inverted_year_range() -> None:
    with pytest.raises(ValueError):
        build_prediction_eval_window_plan(start_label_year=2026, end_label_year=2020)


def test_planner_rejects_nonpositive_window_spans() -> None:
    with pytest.raises(ValueError):
        build_prediction_eval_window_plan(start_label_year=2022, end_label_year=2024, train_years=0)
    with pytest.raises(ValueError):
        build_prediction_eval_window_plan(start_label_year=2022, end_label_year=2024, label_years=0)
