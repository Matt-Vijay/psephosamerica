"""Tests for the curriculum training schedule.

OVERALL_GOAL.md's recipe: easy partisan votes first, then mid, then close
votes at a higher learning rate, with a final fine-tune on the cross-pressured
slice only. Difficulty derives from the roll-call margin, so this needs no
Track A data; it orders and stages the existing trainable transformer.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.prediction.nn.curriculum import (
    CurriculumExample,
    build_curriculum,
    difficulty_from_margin,
    train_with_curriculum,
)
from src.prediction.nn.vote_transformer import init_vote_transformer, vote_transformer_forward

_CONTRAST = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])


def test_difficulty_from_margin_peaks_at_a_tied_vote() -> None:
    assert difficulty_from_margin(yea_count=50, total=100) == pytest.approx(1.0)
    assert difficulty_from_margin(yea_count=90, total=100) == pytest.approx(0.2)
    assert difficulty_from_margin(yea_count=100, total=100) == pytest.approx(0.0)


def test_difficulty_from_margin_rejects_empty_vote() -> None:
    with pytest.raises(ValueError):
        difficulty_from_margin(yea_count=0, total=0)


def _example(difficulty: float, *, cross_pressured: bool = False) -> CurriculumExample:
    rng = np.random.default_rng(int(difficulty * 1000) + (1 if cross_pressured else 0))
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    is_yea = bool((politician @ _CONTRAST).sum() > 0.0)
    return CurriculumExample(
        politician_tokens=politician,
        context_tokens=context,
        is_yea=is_yea,
        difficulty=difficulty,
        is_cross_pressured=cross_pressured,
    )


def test_build_curriculum_orders_stages_easy_to_hard_with_rising_learning_rate() -> None:
    examples = [_example(difficulty=d / 10.0) for d in range(10)]
    # Mark the two hardest as cross-pressured for the final stage.
    examples += [_example(difficulty=0.95, cross_pressured=True) for _ in range(2)]

    stages = build_curriculum(
        examples,
        stage_count=3,
        epochs_per_stage=5,
        base_learning_rate=0.05,
        learning_rate_growth=2.0,
        fine_tune_epochs=5,
        fine_tune_learning_rate=0.02,
    )

    difficulty_stages = [stage for stage in stages if stage.name != "cross_pressured_fine_tune"]
    mean_difficulties = [
        sum(item.difficulty for item in stage.examples) / len(stage.examples)
        for stage in difficulty_stages
    ]
    assert mean_difficulties == sorted(mean_difficulties)  # easy -> hard
    learning_rates = [stage.learning_rate for stage in difficulty_stages]
    assert learning_rates == sorted(learning_rates)  # rising LR
    # The final stage fine-tunes on the cross-pressured slice only.
    assert stages[-1].name == "cross_pressured_fine_tune"
    assert all(item.is_cross_pressured for item in stages[-1].examples)


def test_build_curriculum_drops_empty_cross_pressured_stage() -> None:
    examples = [_example(difficulty=d / 5.0) for d in range(5)]
    stages = build_curriculum(examples, stage_count=2, epochs_per_stage=3, base_learning_rate=0.05)
    assert all(stage.name != "cross_pressured_fine_tune" for stage in stages)


def test_train_with_curriculum_runs_every_stage_and_learns() -> None:
    rng = np.random.default_rng(0)
    examples = [_example(difficulty=(index % 10) / 10.0) for index in range(40)]
    examples += [_example(difficulty=0.95, cross_pressured=True) for _ in range(6)]
    stages = build_curriculum(
        examples,
        stage_count=3,
        epochs_per_stage=40,
        base_learning_rate=0.1,
        fine_tune_epochs=20,
        fine_tune_learning_rate=0.05,
    )
    params = init_vote_transformer(d_model=8, num_heads=2, d_hidden=16, rng=rng)

    trained, stage_history = train_with_curriculum(params, stages)

    assert len(stage_history) == len(stages)
    assert all(history for history in stage_history)  # every stage ran at least one epoch

    correct = 0
    for example in examples:
        probability, _ = vote_transformer_forward(
            example.politician_tokens, example.context_tokens, trained
        )
        if (probability >= 0.5) == example.is_yea:
            correct += 1
    assert correct / len(examples) >= 0.75
