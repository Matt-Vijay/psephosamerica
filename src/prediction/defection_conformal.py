"""Split-conformal coverage report for the defection head, per slice (v4 #7).

The defection head emits a calibrated `P(defect)`. Split-conformal wraps that into
a *set-valued* prediction with a finite-sample marginal coverage guarantee, and
this module reports the **empirical coverage per slice** so we can see where the
nominal 90% holds and where conditional coverage drifts (conformal guarantees the
*marginal* rate, not per-slice, so a slice that under-covers is a real, reportable
finding rather than a bug).

Method (standard split conformal for binary classification):

1. On a calibration split disjoint from training, score each example's
   nonconformity `s = 1 − p(true label)`.
2. Take the conformal threshold `q̂` = the `⌈(n+1)(1−α)⌉ / n` empirical quantile
   of those scores (the finite-sample-corrected `(1−α)` quantile).
3. On the test split, the prediction set for an example is
   `{ y ∈ {defect, loyal} : 1 − p(y) ≤ q̂ }`; coverage is the fraction of test
   examples whose *true* label lands in their set.

`α = 0.10` ⇒ nominal 90%. Pure Python; the head is the only model dependency.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from src.prediction.defection import PartyProfiles, defected, defection_features
from src.prediction.defection_head import DefectionHead
from src.runtime.cross_pressured_experiment import VoteRecord


def _quantile_threshold(scores: list[float], alpha: float) -> float:
    """Finite-sample-corrected (1−α) quantile used as the conformal threshold."""
    if not scores:
        return 1.0
    ordered = sorted(scores)
    n = len(ordered)
    rank = math.ceil((n + 1) * (1.0 - alpha))
    if rank >= n:
        return ordered[-1]
    if rank <= 0:
        return ordered[0]
    return ordered[rank - 1]


@dataclass(frozen=True)
class SliceCoverage:
    slice_name: str
    nominal: float
    empirical_coverage: float
    average_set_size: float
    sample_count: int

    def as_dict(self) -> dict[str, float | str | int]:
        return {
            "slice_name": self.slice_name,
            "nominal": self.nominal,
            "empirical_coverage": self.empirical_coverage,
            "average_set_size": self.average_set_size,
            "sample_count": self.sample_count,
        }


def _label_probs(head: DefectionHead, record: VoteRecord, profiles: PartyProfiles) -> dict[bool, float]:
    p_defect = head.probability(defection_features(record, profiles))
    return {True: p_defect, False: 1.0 - p_defect}


def conformal_coverage(
    head: DefectionHead,
    calibration: list[VoteRecord],
    test: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    alpha: float = 0.10,
    slicers: dict[str, Callable[[VoteRecord], bool]] | None = None,
    mondrian: bool = False,
) -> list[SliceCoverage]:
    """Per-slice empirical coverage + average set size at nominal (1−α).

    ``slicers`` maps a slice name to a predicate selecting test records; the
    ``overall`` slice is always reported. With ``mondrian=False`` a single
    threshold is fit on the full calibration split (the marginal guarantee), which
    can badly under-cover a minority slice. With ``mondrian=True`` each slice gets
    its **own** threshold from that slice's calibration points (class/slice-
    conditional conformal) -- the recalibration that restores per-slice coverage.
    """
    cal_scores = [
        1.0 - _label_probs(head, record, profiles)[defected(record)] for record in calibration
    ]
    marginal_q = _quantile_threshold(cal_scores, alpha)

    predicates: dict[str, Callable[[VoteRecord], bool]] = {"overall": lambda _r: True}
    if slicers:
        predicates.update(slicers)

    def _score(record: VoteRecord) -> float:
        return 1.0 - _label_probs(head, record, profiles)[defected(record)]

    results: list[SliceCoverage] = []
    for slice_name, predicate in predicates.items():
        members = [record for record in test if predicate(record)]
        if not members:
            continue
        if mondrian:
            slice_cal = [_score(r) for r in calibration if predicate(r)]
            q_hat = _quantile_threshold(slice_cal, alpha) if slice_cal else marginal_q
        else:
            q_hat = marginal_q
        covered = 0
        set_size_total = 0
        for record in members:
            probs = _label_probs(head, record, profiles)
            prediction_set = {y for y, p in probs.items() if (1.0 - p) <= q_hat}
            set_size_total += len(prediction_set)
            if defected(record) in prediction_set:
                covered += 1
        results.append(
            SliceCoverage(
                slice_name=slice_name,
                nominal=1.0 - alpha,
                empirical_coverage=covered / len(members),
                average_set_size=set_size_total / len(members),
                sample_count=len(members),
            )
        )
    return results
