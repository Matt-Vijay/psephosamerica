"""Tests for the hyperparameter sweep harness."""

from __future__ import annotations

import numpy as np

from src.prediction.nn.vote_transformer import VoteTransformerExample
from src.runtime.hpo import HpoConfig, default_grid, sweep

_CONTRAST = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])


def _examples(seed: int, n: int) -> list[VoteTransformerExample]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        politician = rng.normal(size=(2, 8))
        context = rng.normal(size=(3, 8))
        out.append(
            VoteTransformerExample(
                politician_tokens=politician,
                context_tokens=context,
                is_yea=bool((politician @ _CONTRAST).sum() > 0),
            )
        )
    return out


def test_sweep_ranks_configs_best_first() -> None:
    train = _examples(1, 60)
    evaluation = _examples(2, 30)
    configs = [
        HpoConfig(d_model=8, num_heads=2, d_hidden=16, learning_rate=0.1, epochs=40),
        HpoConfig(d_model=8, num_heads=2, d_hidden=16, learning_rate=0.01, epochs=40),
    ]
    results = sweep(train, evaluation, configs)
    assert len(results) == 2
    # sorted best-first by accuracy then brier
    assert results[0].accuracy >= results[1].accuracy
    for r in results:
        assert 0.0 <= r.accuracy <= 1.0
        assert 0.0 <= r.brier_score <= 1.0


def test_default_grid_is_valid() -> None:
    grid = default_grid()
    assert len(grid) == 8
    for config in grid:
        assert config.d_model % config.num_heads == 0
