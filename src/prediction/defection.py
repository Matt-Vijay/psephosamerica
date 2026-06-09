"""Honest, ex-ante defection modelling for the cross-pressure problem.

Last session's ``is_cross_pressured := (is_yea != party_lean)`` was defined from
the realized label, so any "improvement" on it was partly tautological: a model
that predicts the *opposite* of the party scores ~100% on that slice yet is
useless in production. v4 fixes both halves of the problem.

**Honest slice (ex ante).** A ``(member, bill)`` pair is *defection-prone* when,
using ONLY information available at the cutoff, the member's own record breaks
with their party on the bill's policy area. Loyalty is *directional*: the
fraction of the member's pre-cutoff votes cast WITH the party's majority lean,
within each of the bill's sectors. Defection-proneness is ``1 - loyalty``, taken
as the max over the bill's sectors (the member's least-loyal relevant sector); a
pair is in the slice when that proneness is at least ``tau``. Because the metric
is directional, a super-loyalist (loyalty 1.0) scores 0 and is never flagged --
only a member whose history actually points against the party enters the slice.
This is computable the morning before the vote and never reads the realized
outcome of the bill being scored -- so a lift on it is real.

**Honest product (ranking).** Rather than "accuracy on the slice" we score
``P(member defects from their party's majority)`` as a *ranking* task: rank all
eval ``(member, bill)`` pairs by predicted defection probability and report ROC
AUC + precision@k. AUC is invariant to the predict-opposite-of-party degeneracy
(it measures ordering, not which side of 0.5), so it cannot be gamed the way the
tautological accuracy could. Ranking who will break ranks is the actual product.

All features are pre-cutoff; the realized ``defected`` label is used only to
*score* eval pairs, never to define the slice or any feature.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from src.prediction.vote_record import VoteRecord

# A member needs at least this many pre-cutoff votes in a sector before we trust
# their sector yea-rate; below it we fall back to their overall rate.
_MIN_SECTOR_VOTES = 3
DEFAULT_TAU = 0.25


@dataclass(frozen=True)
class PartyProfiles:
    """Pre-cutoff party-loyalty rates used to derive ex-ante divergence features.

    Every rate here is computed strictly from votes at or before the cutoff, so
    the resulting features are knowable before the vote being scored happens.
    Loyalty is *directional*: the fraction of a member's votes cast WITH their
    party's majority lean, overall and within each policy sector. Defection-
    proneness is ``1 - loyalty`` -- so a super-loyalist (loyalty 1.0) is never
    flagged, only a member whose own record breaks with the party.
    """

    member_loyalty_rate: dict[str, float]
    member_sector_loyalty_rate: dict[tuple[str, str], float]
    member_sector_counts: dict[tuple[str, str], int]


def build_party_profiles(train: list[VoteRecord]) -> PartyProfiles:
    """Aggregate pre-cutoff member party-loyalty, overall and per sector."""
    member_loyalty: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    member_sector_loyalty: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for record in train:
        # party_alignment is +1 when the member's party leaned yea on that bill;
        # voting with that lean is loyalty, against it is a (historical) defection.
        voted_with_party = (record.is_yea and record.party_alignment > 0) or (
            not record.is_yea and record.party_alignment < 0
        )
        member_loyalty[record.member][0] += int(voted_with_party)
        member_loyalty[record.member][1] += 1
        for sector in record.sectors:
            member_sector_loyalty[(record.member, sector)][0] += int(voted_with_party)
            member_sector_loyalty[(record.member, sector)][1] += 1
    return PartyProfiles(
        member_loyalty_rate={k: y / n for k, (y, n) in member_loyalty.items() if n},
        member_sector_loyalty_rate={k: y / n for k, (y, n) in member_sector_loyalty.items() if n},
        member_sector_counts={k: n for k, (_y, n) in member_sector_loyalty.items() if n},
    )


def sector_divergence(record: VoteRecord, profiles: PartyProfiles) -> float:
    """Max ex-ante defection-proneness (``1 - party loyalty``) over the bill's sectors.

    Uses the member's sector loyalty when they have at least ``_MIN_SECTOR_VOTES``
    there, else their overall loyalty; a member with no history defaults to a high
    (0.9) loyalty so they are not flagged. Directional and purely pre-cutoff -- the
    scored bill's own outcome is never consulted, and a super-loyalist scores 0
    rather than being flagged for merely diverging from the party mean.
    """
    overall_loyalty = profiles.member_loyalty_rate.get(record.member, 0.9)
    gaps: list[float] = []
    for sector in record.sectors:
        member_key = (record.member, sector)
        if profiles.member_sector_counts.get(member_key, 0) >= _MIN_SECTOR_VOTES:
            loyalty = profiles.member_sector_loyalty_rate[member_key]
        else:
            loyalty = overall_loyalty
        gaps.append(1.0 - loyalty)
    return max(gaps) if gaps else 1.0 - overall_loyalty


def is_defection_prone(
    record: VoteRecord, profiles: PartyProfiles, *, tau: float = DEFAULT_TAU
) -> bool:
    """Whether a pair is in the honest ex-ante slice (divergence >= tau)."""
    return sector_divergence(record, profiles) >= tau


def defected(record: VoteRecord) -> bool:
    """Realized defection label (eval-only): voted against the party's lean."""
    return record.is_cross_pressured


def defection_features(record: VoteRecord, profiles: PartyProfiles) -> dict[str, float]:
    """Ex-ante features for the defection head (all pre-cutoff)."""
    loyalty = profiles.member_loyalty_rate.get(record.member, 0.9)
    return {
        "loyalty_gap": 1.0 - loyalty,
        "sector_divergence": sector_divergence(record, profiles),
    }


@dataclass(frozen=True)
class RankingMetrics:
    auc: float
    precision_at_10: float
    precision_at_50: float
    precision_at_100: float
    positives: int
    sample_count: int
    base_rate: float


def roc_auc(scores: list[float], labels: list[bool]) -> float:
    """ROC AUC via the Mann-Whitney rank-sum (ties get averaged ranks).

    Returns 0.5 when one class is absent (no ordering information). AUC measures
    only the ordering of scores, so it is immune to the predict-opposite-of-party
    degeneracy that inflated the old slice accuracy.
    """
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0  # 1-based, averaged over the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    rank_sum_positive = sum(ranks[i] for i in range(len(labels)) if labels[i])
    return (rank_sum_positive - positives * (positives + 1) / 2.0) / (positives * negatives)


def _precision_at_k(ranked_labels: list[bool], k: int) -> float:
    top = ranked_labels[:k]
    return sum(top) / len(top) if top else 0.0


def ranking_metrics(scores: list[float], labels: list[bool]) -> RankingMetrics:
    """AUC + precision@{10,50,100} for a defection ranking over eval pairs."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    ranked_labels = [labels[i] for i in order]
    positives = sum(labels)
    return RankingMetrics(
        auc=roc_auc(scores, labels),
        precision_at_10=_precision_at_k(ranked_labels, 10),
        precision_at_50=_precision_at_k(ranked_labels, 50),
        precision_at_100=_precision_at_k(ranked_labels, 100),
        positives=positives,
        sample_count=len(labels),
        base_rate=positives / len(labels) if labels else 0.0,
    )


def slice_prevalence(
    eval_records: list[VoteRecord], profiles: PartyProfiles, *, tau: float = DEFAULT_TAU
) -> dict[str, float]:
    """Prevalence + enrichment of the honest slice: does it concentrate defections?"""
    prone = [r for r in eval_records if is_defection_prone(r, profiles, tau=tau)]
    prone_defections = sum(1 for r in prone if defected(r))
    base_defections = sum(1 for r in eval_records if defected(r))
    n = len(eval_records)
    prone_rate = prone_defections / len(prone) if prone else 0.0
    base_rate = base_defections / n if n else 0.0
    return {
        "tau": tau,
        "eval_pairs": float(n),
        "defection_prone_pairs": float(len(prone)),
        "prevalence": len(prone) / n if n else 0.0,
        "defection_rate_in_slice": prone_rate,
        "defection_rate_overall": base_rate,
        "enrichment": prone_rate / base_rate if base_rate else 0.0,
    }


def split_by_cutoff(
    records: list[VoteRecord], *, cutoff: date, eval_end: date
) -> tuple[list[VoteRecord], list[VoteRecord]]:
    """Strict no-leakage split: train <= cutoff < eval <= eval_end."""
    train = [r for r in records if r.vote_date <= cutoff]
    eval_records = [r for r in records if cutoff < r.vote_date <= eval_end]
    return train, eval_records
