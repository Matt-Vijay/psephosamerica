"""Confirmation head: P(senator votes yea on a nomination) (v7 #5, additive head).

Senate executive-calendar votes are a distinct regime from legislation: the
dominant signal is whether the senator's party matches the nominating
president's, and the member-specific residual is how often that senator crosses
on confirmations specifically. The head is the shared logistic over

* ``party_match`` -- senator party == president party for that congress;
* ``conf_rate_match`` -- the senator's PRE-CUTOFF confirmation yea-rate in the
  same party_match regime, Laplace-shrunk, falling back to the party-level rate
  for senators with no confirmation history.

Trained on "On the Nomination" roll-calls up to a cutoff congress and scored
forward (AUC / Brier / accuracy) against the party-line baseline (predict yea
iff party_match) -- the head must beat that baseline to earn its keep.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from src.prediction.defection import ranking_metrics
from src.prediction.logistic import score_row, train_logistic_rows

# Nominating president's party by congress (public record).
PRESIDENT_PARTY = {113: "D", 114: "D", 115: "R", 116: "R", 117: "D", 118: "D", 119: "R"}

_FEATURES = ("party_match", "conf_rate_match")


@dataclass(frozen=True)
class ConfirmationVote:
    """One senator's vote on one 'On the Nomination' roll-call."""

    member: str
    party: str
    congress: int
    vote_date: date
    is_yea: bool

    @property
    def party_match(self) -> bool:
        return self.party == PRESIDENT_PARTY.get(self.congress, "?")


def is_confirmation_question(question: str) -> bool:
    """Final confirmation votes only (cloture-on-nomination is a separate regime)."""
    q = question.strip().lower()
    return q.startswith("on the nomination")


@dataclass(frozen=True)
class ConfirmationHead:
    intercept: float
    coefficients: dict[str, float]
    member_rates: dict[tuple[str, bool], tuple[int, int]]
    party_rates: dict[tuple[str, bool], tuple[int, int]]

    def features(self, vote: ConfirmationVote) -> dict[str, float]:
        match = vote.party_match
        yes, n = self.member_rates.get((vote.member, match), (0, 0))
        if n == 0:
            yes, n = self.party_rates.get((vote.party, match), (0, 0))
        return {
            "party_match": 1.0 if match else 0.0,
            "conf_rate_match": (yes + 1.0) / (n + 2.0),
        }

    def probability(self, vote: ConfirmationVote) -> float:
        return score_row(self.intercept, self.coefficients, _FEATURES, self.features(vote))


def train_confirmation_head(train: list[ConfirmationVote]) -> ConfirmationHead:
    member: dict[tuple[str, bool], list[int]] = defaultdict(lambda: [0, 0])
    party: dict[tuple[str, bool], list[int]] = defaultdict(lambda: [0, 0])
    for v in train:
        member[(v.member, v.party_match)][0] += int(v.is_yea)
        member[(v.member, v.party_match)][1] += 1
        party[(v.party, v.party_match)][0] += int(v.is_yea)
        party[(v.party, v.party_match)][1] += 1
    head = ConfirmationHead(
        intercept=0.0,
        coefficients=dict.fromkeys(_FEATURES, 0.0),
        member_rates={k: (c[0], c[1]) for k, c in member.items()},
        party_rates={k: (c[0], c[1]) for k, c in party.items()},
    )
    rows = [(head.features(v), v.is_yea) for v in train]
    intercept, coefficients = train_logistic_rows(rows, _FEATURES)
    return ConfirmationHead(
        intercept=intercept,
        coefficients=coefficients,
        member_rates=head.member_rates,
        party_rates=head.party_rates,
    )


def evaluate_confirmation_head(
    head: ConfirmationHead, eval_votes: list[ConfirmationVote]
) -> dict[str, float]:
    """AUC / Brier / accuracy vs the party-line baseline on a forward window."""
    if not eval_votes:
        return {"eval_votes": 0.0}
    probs = [head.probability(v) for v in eval_votes]
    labels = [v.is_yea for v in eval_votes]
    baseline = [1.0 if v.party_match else 0.0 for v in eval_votes]
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(probs, labels)) / len(labels)
    base_brier = sum((b - (1.0 if y else 0.0)) ** 2 for b, y in zip(baseline, labels)) / len(labels)
    accuracy = sum(1 for p, y in zip(probs, labels) if (p >= 0.5) == y) / len(labels)
    base_acc = sum(1 for b, y in zip(baseline, labels) if (b >= 0.5) == y) / len(labels)
    return {
        "eval_votes": float(len(labels)),
        "auc": ranking_metrics(probs, labels).auc,
        "auc_party_line": ranking_metrics(baseline, labels).auc,
        "brier": brier,
        "brier_party_line": base_brier,
        "accuracy": accuracy,
        "accuracy_party_line": base_acc,
    }
