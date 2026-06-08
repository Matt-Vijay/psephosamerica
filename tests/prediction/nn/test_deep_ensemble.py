"""Tests for the Bayesian deep ensemble over the vote transformer.

OVERALL_GOAL.md: uncertainty via a Bayesian deep ensemble across seeds. Each
member is an independently-initialized cross-attention transformer trained on
the same data; their prediction spread is the epistemic uncertainty that the
served prediction's interval reports.
"""

from __future__ import annotations

import numpy as np

from src.prediction.nn.deep_ensemble import train_transformer_ensemble
from src.prediction.nn.vote_transformer import VoteTransformerExample

_CONTRAST = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])


def _dataset(seed: int, *, count: int = 50) -> list[VoteTransformerExample]:
    rng = np.random.default_rng(seed)
    examples: list[VoteTransformerExample] = []
    for _ in range(count):
        politician = rng.normal(size=(2, 8))
        context = rng.normal(size=(3, 8))
        is_yea = bool((politician @ _CONTRAST).sum() > 0.0)
        examples.append(
            VoteTransformerExample(
                politician_tokens=politician, context_tokens=context, is_yea=is_yea
            )
        )
    return examples


def _train(seeds: list[int]) -> object:
    return train_transformer_ensemble(
        _dataset(seed=1),
        seeds=seeds,
        d_model=8,
        num_heads=2,
        d_hidden=16,
        epochs=80,
        learning_rate=0.1,
    )


def test_ensemble_has_one_member_per_seed() -> None:
    ensemble = _train([0, 1, 2])
    assert len(ensemble.members) == 3  # type: ignore[attr-defined]


def test_ensemble_is_deterministic_for_a_seed_set() -> None:
    first = _train([0, 1])
    second = _train([0, 1])
    rng = np.random.default_rng(99)
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    assert first.predict(politician, context) == second.predict(politician, context)  # type: ignore[attr-defined]


def test_ensemble_members_disagree_so_interval_has_width() -> None:
    ensemble = _train([0, 1, 2, 3])
    rng = np.random.default_rng(7)
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    member_probabilities = ensemble.predict(politician, context)  # type: ignore[attr-defined]
    assert max(member_probabilities) - min(member_probabilities) > 1e-3


def test_predict_interval_brackets_the_mean_and_stays_in_unit_range() -> None:
    ensemble = _train([0, 1, 2, 3, 4])
    rng = np.random.default_rng(8)
    politician = rng.normal(size=(2, 8))
    context = rng.normal(size=(3, 8))
    mean, lower, upper = ensemble.predict_interval(  # type: ignore[attr-defined]
        politician, context, confidence_level=0.8
    )
    assert 0.0 <= lower <= mean <= upper <= 1.0
    assert np.isclose(mean, ensemble.predict_mean(politician, context))  # type: ignore[attr-defined]


def test_ensemble_mean_learns_the_separable_task() -> None:
    ensemble = _train([0, 1, 2])
    evaluation = _dataset(seed=1)
    correct = 0
    for example in evaluation:
        mean = ensemble.predict_mean(example.politician_tokens, example.context_tokens)  # type: ignore[attr-defined]
        if (mean >= 0.5) == example.is_yea:
            correct += 1
    assert correct / len(evaluation) >= 0.8
