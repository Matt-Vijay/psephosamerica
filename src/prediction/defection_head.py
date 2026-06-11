"""Defection-ranking head: P(member defects from party) as a ranking model.

Ranking who will break with their party on a given bill is the product. The head
is a small logistic regression over the ex-ante features in ``defection.py``
(member loyalty gap + bill-policy-area divergence), trained on pre-cutoff
``(member, bill)`` defection labels and scored on a held-out window by ROC AUC
and precision@k. Pure Python full-batch gradient descent -- no torch -- matching
``per_member_model``; the realized defection label is consumed only as the
training target and the eval-scoring truth, never as a feature.

A trained head also exposes ``rank`` so the explorer can surface the most
defection-likely members for the live congress with cited contributions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.prediction.defection import (
    PartyProfiles,
    RankingMetrics,
    build_party_profiles,
    defected,
    defection_features,
    ranking_metrics,
    split_by_cutoff,
)
from src.prediction.logistic import clamped_sigmoid, train_logistic_rows
from src.prediction.vote_record import VoteRecord

_FEATURES = ("loyalty_gap", "sector_divergence")


@dataclass(frozen=True)
class DefectionHead:
    intercept: float
    coefficients: dict[str, float]

    def probability(self, features: dict[str, float]) -> float:
        # Sum over the head's OWN coefficients, not the module's base-feature
        # constant: heads trained with extra features (e.g. rag_signal) must not
        # silently drop those coefficients at scoring time.
        raw = self.intercept + sum(
            coefficient * features.get(name, 0.0)
            for name, coefficient in self.coefficients.items()
        )
        return clamped_sigmoid(raw)

    def contributions(self, features: dict[str, float]) -> dict[str, float]:
        """Per-feature logit contributions, for explorer attribution."""
        return {
            name: coefficient * features.get(name, 0.0)
            for name, coefficient in self.coefficients.items()
        }


def train_defection_head(
    train: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> DefectionHead:
    """Fit the logistic defection head on pre-cutoff defection labels."""
    rows = [(defection_features(r, profiles), defected(r)) for r in train]
    intercept, coefficients = train_logistic_rows(
        rows, _FEATURES, learning_rate=learning_rate, epochs=epochs, l2=l2
    )
    return DefectionHead(intercept=intercept, coefficients=coefficients)


@dataclass(frozen=True)
class RankedDefection:
    member: str
    party: str
    state: str
    probability: float
    features: dict[str, float] = field(default_factory=dict)
    actual_defected: bool | None = None


def evaluate_defection_head(
    head: DefectionHead, eval_records: list[VoteRecord], profiles: PartyProfiles
) -> RankingMetrics:
    """Score the head on a held-out window by AUC + precision@k."""
    scores = [head.probability(defection_features(r, profiles)) for r in eval_records]
    labels = [defected(r) for r in eval_records]
    return ranking_metrics(scores, labels)


def rank_defections(
    head: DefectionHead,
    records: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    top_n: int = 20,
    with_truth: bool = False,
) -> list[RankedDefection]:
    """Rank records by predicted defection probability, highest first."""
    ranked = [
        RankedDefection(
            member=r.member,
            party=r.party,
            state=r.state,
            probability=head.probability(defection_features(r, profiles)),
            features=defection_features(r, profiles),
            actual_defected=defected(r) if with_truth else None,
        )
        for r in records
    ]
    ranked.sort(key=lambda d: d.probability, reverse=True)
    return ranked[:top_n]


def defection_ranking_over_window(
    records: list[VoteRecord],
    *,
    cutoff: date,
    eval_end: date,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> RankingMetrics:
    """End-to-end strict-cutoff defection ranking: split, fit, score one window.

    Trains profiles + the logistic head on votes at or before ``cutoff`` and
    scores defection ranking on ``(cutoff, eval_end]`` -- the single reproducible
    measurement the AUC gate pins. Returns zeroed metrics when the window is
    empty (no eval pairs), never raising.
    """
    train, eval_records = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles, learning_rate=learning_rate, epochs=epochs, l2=l2)
    return evaluate_defection_head(head, eval_records, profiles)
