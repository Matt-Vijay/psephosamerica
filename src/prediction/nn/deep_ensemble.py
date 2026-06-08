"""Bayesian deep ensemble over the cross-attention vote transformer (numpy).

OVERALL_GOAL.md asks for calibrated uncertainty via a Bayesian deep ensemble
across seeds (a practical approximation to the posterior predictive). Each
member is an independently-initialized :class:`VoteTransformer` trained on the
same data; differences in initialization send them to different minima, so the
spread of their predictions is the epistemic uncertainty that
``ServedPrediction``'s interval reports.

The point estimate is the ensemble mean; the interval is the central
``confidence_level`` band of the member probabilities (reusing the serving
layer's :func:`ensemble_probability_interval`). Deterministic given the seed
set, so reproducible from raw artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from src.prediction.nn.vote_transformer import (
    VoteTransformerExample,
    VoteTransformerParams,
    init_vote_transformer,
    train_vote_transformer,
    vote_transformer_forward,
)
from src.prediction.served_prediction import ensemble_probability_interval

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class TransformerEnsemble:
    """A trained Bayesian deep ensemble of vote transformers."""

    members: list[VoteTransformerParams]

    def predict(self, politician_tokens: Array, context_tokens: Array) -> list[float]:
        """Each member's yea probability for one (politician, bill) pair."""
        return [
            vote_transformer_forward(politician_tokens, context_tokens, member)[0]
            for member in self.members
        ]

    def predict_mean(self, politician_tokens: Array, context_tokens: Array) -> float:
        """The ensemble's point estimate (mean of member probabilities)."""
        probabilities = self.predict(politician_tokens, context_tokens)
        return sum(probabilities) / len(probabilities)

    def predict_interval(
        self,
        politician_tokens: Array,
        context_tokens: Array,
        *,
        confidence_level: float,
    ) -> tuple[float, float, float]:
        """Return ``(mean, lower, upper)`` over the member probabilities."""
        probabilities = self.predict(politician_tokens, context_tokens)
        mean = sum(probabilities) / len(probabilities)
        lower, upper = ensemble_probability_interval(
            probabilities, confidence_level=confidence_level
        )
        return mean, min(lower, mean), max(upper, mean)


def train_transformer_ensemble(
    examples: list[VoteTransformerExample],
    *,
    seeds: list[int],
    d_model: int,
    num_heads: int,
    d_hidden: int,
    epochs: int,
    learning_rate: float,
) -> TransformerEnsemble:
    """Train one independently-initialized transformer per seed on the same data."""
    if not seeds:
        raise ValueError("seeds must not be empty")
    members: list[VoteTransformerParams] = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        params = init_vote_transformer(
            d_model=d_model, num_heads=num_heads, d_hidden=d_hidden, rng=rng
        )
        trained, _history = train_vote_transformer(
            params, examples, epochs=epochs, learning_rate=learning_rate
        )
        members.append(trained)
    return TransformerEnsemble(members=members)
