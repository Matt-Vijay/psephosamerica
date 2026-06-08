"""Tests for the stated-position-to-vote-stance transfer head.

OVERALL_GOAL.md's thin-record mechanism #2: train a head from declared
positions (questionnaires, debate answers, campaign statements) to vote
stance, so an official with declared positions but no votes still gets a
prediction. The transfer is learned on (declared_position, realized_vote)
pairs and applied to voteless officials from their declared positions alone.
"""

from __future__ import annotations

import random

from src.prediction.stated_position_transfer import (
    StatedPositionExample,
    predict_stance,
    train_stated_position_transfer,
)


def _dataset(seed: int, *, count: int = 400) -> list[StatedPositionExample]:
    rng = random.Random(seed)
    examples: list[StatedPositionExample] = []
    for _ in range(count):
        energy = rng.uniform(-1.0, 1.0)
        labor = rng.uniform(-1.0, 1.0)
        # A yea vote follows a positive declared stance on energy (the true signal),
        # with labor as a weak distractor and some noise.
        score = 2.0 * energy + 0.3 * labor
        is_yea = rng.random() < 1.0 / (1.0 + pow(2.718281828, -score))
        examples.append(
            StatedPositionExample(
                declared_positions={"energy": energy, "labor": labor}, is_yea=is_yea
            )
        )
    return examples


def test_transfer_head_learns_declared_position_signal() -> None:
    model = train_stated_position_transfer(_dataset(1))
    # The energy coefficient (true driver) should dominate the labor distractor.
    assert model.coefficients["energy"] > model.coefficients["labor"]
    assert model.coefficients["energy"] > 0.0


def test_predict_stance_is_higher_for_supportive_declarations() -> None:
    model = train_stated_position_transfer(_dataset(2))
    supportive = predict_stance(model, {"energy": 0.9, "labor": 0.0})
    opposed = predict_stance(model, {"energy": -0.9, "labor": 0.0})
    assert supportive > 0.6
    assert opposed < 0.4


def test_transfer_head_classifies_holdout_well() -> None:
    model = train_stated_position_transfer(_dataset(3))
    holdout = _dataset(4)
    correct = sum(
        1
        for example in holdout
        if (predict_stance(model, example.declared_positions) >= 0.5) == example.is_yea
    )
    assert correct / len(holdout) >= 0.7


def test_predict_stance_ignores_unknown_declared_issues() -> None:
    model = train_stated_position_transfer(_dataset(5))
    # An issue the head never saw contributes nothing.
    base = predict_stance(model, {"energy": 0.5})
    with_unknown = predict_stance(model, {"energy": 0.5, "unseen_issue": 5.0})
    assert base == with_unknown


def test_empty_training_set_yields_neutral_head() -> None:
    model = train_stated_position_transfer([])
    assert predict_stance(model, {"energy": 1.0}) == 0.5
