"""Non-bill defection signals + an honest ΔAUC harness (v4 squeeze, track 2).

Until Track A's dense bill content lands, the only way to move the defection AUC
is *non-bill* signal. This module adds candidate signals as first-class defection
features and measures each one's ΔAUC against the base head honestly -- a signal
that adds nothing reports 0.0, and we keep it out of the pin.

Signals implemented here:

* **Hierarchical per-member defection prior** -- the member's pre-cutoff defection
  rate, empirical-Bayes shrunk member→party→global so a thin-record member borrows
  strength from their party and the chamber. This is *not* redundant with
  ``loyalty_gap``: loyalty_gap is the raw member rate (noisy for few votes),
  whereas the shrunk prior stabilises low-count members toward their group.

External-data signals (donor / statement / cosponsor profiles) are expressed as
the same ``SignalProvider`` interface so they drop in without touching the head;
their providers live in ``defection_external_signals`` and are gated on the
relevant corpus being present.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from src.prediction.defection import PartyProfiles, defected, defection_features, ranking_metrics
from src.prediction.vote_record import VoteRecord

# A signal provider maps a record to zero or more named features (pre-cutoff only).
SignalProvider = Callable[[VoteRecord], dict[str, float]]
_BASE_FEATURES = ("loyalty_gap", "sector_divergence")
_LOGIT_CLAMP = 40.0


def hierarchical_defection_prior(
    train: list[VoteRecord], *, member_strength: float = 20.0, party_strength: float = 50.0
) -> SignalProvider:
    """Empirical-Bayes shrunk defection rate: member → party → global.

    ``member_strength`` is the pseudo-count pulling a member toward their party
    rate; ``party_strength`` pulls a party toward the global rate. A member with
    many votes keeps their own rate; a thin-record member is shrunk toward the
    group -- the hierarchical prior the blueprint calls for.
    """
    member_def: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    party_def: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    global_def = [0, 0]
    for r in train:
        d = int(defected(r))
        member_def[r.member][0] += d
        member_def[r.member][1] += 1
        party_def[r.party][0] += d
        party_def[r.party][1] += 1
        global_def[0] += d
        global_def[1] += 1
    global_rate = global_def[0] / global_def[1] if global_def[1] else 0.06
    party_rate = {
        p: (c[0] + party_strength * global_rate) / (c[1] + party_strength)
        for p, c in party_def.items()
    }

    def provider(record: VoteRecord) -> dict[str, float]:
        base = party_rate.get(record.party, global_rate)
        c = member_def.get(record.member)
        if c is None or c[1] == 0:
            shrunk = base
        else:
            shrunk = (c[0] + member_strength * base) / (c[1] + member_strength)
        return {"defection_prior": shrunk}

    return provider


def _augmented_features(
    record: VoteRecord, profiles: PartyProfiles, providers: list[SignalProvider]
) -> dict[str, float]:
    features = dict(defection_features(record, profiles))
    for provider in providers:
        features.update(provider(record))
    return features


def _train_logistic(
    rows: list[tuple[dict[str, float], bool]],
    feature_names: tuple[str, ...],
    *,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> tuple[float, dict[str, float]]:
    if not rows:
        return 0.0, {n: 0.0 for n in feature_names}
    positives = sum(1 for _f, y in rows if y)
    rate = min(0.95, max(0.05, positives / len(rows)))
    intercept = math.log(rate / (1.0 - rate))
    coef = {n: 0.0 for n in feature_names}
    scale = 1.0 / len(rows)
    for _ in range(epochs):
        d_int = 0.0
        d_coef = {n: 0.0 for n in feature_names}
        for features, label in rows:
            raw = intercept + sum(coef[n] * features.get(n, 0.0) for n in feature_names)
            pred = 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))
            err = pred - (1.0 if label else 0.0)
            d_int += err
            for n in feature_names:
                d_coef[n] += err * features.get(n, 0.0)
        intercept -= learning_rate * d_int * scale
        for n in feature_names:
            coef[n] -= learning_rate * (d_coef[n] * scale + l2 * coef[n])
    return intercept, coef


def _auc(
    intercept: float,
    coef: dict[str, float],
    feature_names: tuple[str, ...],
    rows: list[tuple[dict[str, float], bool]],
) -> float:
    scores = []
    for features, _label in rows:
        raw = intercept + sum(coef[n] * features.get(n, 0.0) for n in feature_names)
        scores.append(1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw)))))
    return ranking_metrics(scores, [y for _f, y in rows]).auc


@dataclass(frozen=True)
class SignalResult:
    signal_name: str
    base_auc: float
    augmented_auc: float
    delta_auc: float
    coefficients: dict[str, float]
    eval_pairs: int

    def as_dict(self) -> dict[str, object]:
        return {
            "signal_name": self.signal_name,
            "base_auc": self.base_auc,
            "augmented_auc": self.augmented_auc,
            "delta_auc": self.delta_auc,
            "coefficients": self.coefficients,
            "eval_pairs": self.eval_pairs,
        }


def evaluate_signal(
    train: list[VoteRecord],
    eval_records: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    signal_name: str,
    providers: list[SignalProvider],
    extra_feature_names: tuple[str, ...],
) -> SignalResult:
    """Train base vs base+signal heads; report ΔAUC honestly (even if 0)."""
    base_train = [(defection_features(r, profiles), defected(r)) for r in train]
    base_eval = [(defection_features(r, profiles), defected(r)) for r in eval_records]
    b_int, b_coef = _train_logistic(base_train, _BASE_FEATURES)
    base_auc = _auc(b_int, b_coef, _BASE_FEATURES, base_eval)

    names = (*_BASE_FEATURES, *extra_feature_names)
    aug_train = [(_augmented_features(r, profiles, providers), defected(r)) for r in train]
    aug_eval = [(_augmented_features(r, profiles, providers), defected(r)) for r in eval_records]
    a_int, a_coef = _train_logistic(aug_train, names)
    aug_auc = _auc(a_int, a_coef, names, aug_eval)

    return SignalResult(
        signal_name=signal_name,
        base_auc=base_auc,
        augmented_auc=aug_auc,
        delta_auc=aug_auc - base_auc,
        coefficients=a_coef,
        eval_pairs=len(eval_records),
    )
