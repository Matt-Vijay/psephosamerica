"""Hyperparameter sweep for the vote transformer on a real validation set.

Trains the four-stream/vote transformer under each config and ranks them by
held-out accuracy (tie-broken by Brier). The full blueprint grid (dim ×
heads × depth × lr × L2 = 243 configs) is infeasible for the pure-numpy trainer
in a 2–3h budget; this sweeps the knobs the current single-block trainer exposes
(``d_model``, ``num_heads``, ``d_hidden``, ``learning_rate``) over a coarse grid.
Depth (stacked blocks) and explicit weight-decay are not yet wired into the
``vote_transformer`` SGD and are reported as out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.prediction.nn.vote_transformer import (
    VoteTransformerExample,
    init_vote_transformer,
    train_vote_transformer,
    vote_transformer_forward,
)


@dataclass(frozen=True)
class HpoConfig:
    d_model: int
    num_heads: int
    d_hidden: int
    learning_rate: float
    epochs: int = 60


@dataclass(frozen=True)
class HpoResult:
    config: HpoConfig
    accuracy: float
    brier_score: float


def _evaluate(params: object, examples: list[VoteTransformerExample]) -> tuple[float, float]:
    correct = 0
    brier = 0.0
    for example in examples:
        probability, _ = vote_transformer_forward(
            example.politician_tokens,
            example.context_tokens,
            params,  # type: ignore[arg-type]
        )
        target = 1.0 if example.is_yea else 0.0
        correct += int((probability >= 0.5) == example.is_yea)
        brier += (probability - target) ** 2
    n = len(examples)
    return correct / n, brier / n


def sweep(
    train: list[VoteTransformerExample],
    evaluation: list[VoteTransformerExample],
    configs: list[HpoConfig],
    *,
    seed: int = 0,
) -> list[HpoResult]:
    """Train each config and return results ranked best-first (accuracy, then Brier)."""
    results: list[HpoResult] = []
    for index, config in enumerate(configs):
        rng = np.random.default_rng(seed + index)
        params = init_vote_transformer(
            d_model=config.d_model,
            num_heads=config.num_heads,
            d_hidden=config.d_hidden,
            rng=rng,
        )
        trained, _history = train_vote_transformer(
            params, train, epochs=config.epochs, learning_rate=config.learning_rate
        )
        accuracy, brier = _evaluate(trained, evaluation)
        results.append(HpoResult(config=config, accuracy=accuracy, brier_score=brier))
    results.sort(key=lambda r: (-r.accuracy, r.brier_score))
    return results


def default_grid() -> list[HpoConfig]:
    """A coarse, tractable grid over the trainer's exposed knobs."""
    grid: list[HpoConfig] = []
    for d_model in (16, 32):
        for num_heads in (2, 4):
            for learning_rate in (0.05, 0.1):
                grid.append(
                    HpoConfig(
                        d_model=d_model,
                        num_heads=num_heads,
                        d_hidden=d_model * 2,
                        learning_rate=learning_rate,
                    )
                )
    return grid
