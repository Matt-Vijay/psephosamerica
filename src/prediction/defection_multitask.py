"""Multi-task v2: dense auxiliaries -> transfer to defection AUC (v4 #4).

The blueprint's multi-task setting trains the shared representation on auxiliary
objectives derived from Track A's dense bills -- cosponsorship, committee
membership, and CRS policy-area stance -- and asks whether they *transfer* to the
defection-ranking head. In the numpy logistic setting "shared representation +
auxiliary heads" reduces to augmenting the defection features with the auxiliary
signals and measuring the AUC delta: a positive delta means the auxiliaries carry
information about defection beyond loyalty + divergence.

The auxiliary signals are **pluggable** (``AuxiliaryProvider``) because they live
on the dense bill records that Track A is still shipping. Until those land the
provider returns nothing for every (member, bill); coverage is 0 and the
transfer delta is ~0, reported honestly. The moment dense bills arrive, the same
harness reads ``cosponsor_count`` / ``committee`` / ``policy_area_stance`` off the
contract and the transfer becomes real -- no code change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.prediction.defection import (
    PartyProfiles,
    defected,
    defection_features,
)
from src.prediction.logistic import auc_of_rows, train_logistic_rows
from src.prediction.vote_record import VoteRecord

# name -> value, empty when the dense auxiliary is unavailable for this pair.
AuxiliaryProvider = Callable[[VoteRecord], dict[str, float]]

_AUX_FEATURES = ("aux_cosponsor", "aux_committee", "aux_policy_stance")
_BASE_FEATURES = ("loyalty_gap", "sector_divergence")


def no_auxiliaries(_record: VoteRecord) -> dict[str, float]:
    """Default provider: no dense auxiliary signals available (pre-Track-A-dense)."""
    return {}


def _augmented(
    record: VoteRecord, profiles: PartyProfiles, aux: AuxiliaryProvider
) -> dict[str, float]:
    features = dict(defection_features(record, profiles))
    for name, value in aux(record).items():
        features[f"aux_{name}"] = value
    return features


@dataclass(frozen=True)
class MultiTaskTransfer:
    base_auc: float
    multitask_auc: float
    delta_auc: float
    aux_coverage: float
    eval_pairs: int

    def as_dict(self) -> dict[str, object]:
        return {
            "base_auc": self.base_auc,
            "multitask_auc": self.multitask_auc,
            "delta_auc": self.delta_auc,
            "aux_coverage": self.aux_coverage,
            "eval_pairs": self.eval_pairs,
        }


def multitask_transfer(
    train: list[VoteRecord],
    eval_records: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    aux: AuxiliaryProvider = no_auxiliaries,
) -> MultiTaskTransfer:
    """Defection AUC with base features vs base + dense auxiliaries (the transfer)."""
    base_train = [(defection_features(r, profiles), defected(r)) for r in train]
    base_eval = [(defection_features(r, profiles), defected(r)) for r in eval_records]
    b_int, b_coef = train_logistic_rows(base_train, _BASE_FEATURES)
    base_auc = auc_of_rows(b_int, b_coef, _BASE_FEATURES, base_eval)

    all_features = (*_BASE_FEATURES, *_AUX_FEATURES)
    mt_train = [(_augmented(r, profiles, aux), defected(r)) for r in train]
    mt_eval = [(_augmented(r, profiles, aux), defected(r)) for r in eval_records]
    m_int, m_coef = train_logistic_rows(mt_train, all_features)
    mt_auc = auc_of_rows(m_int, m_coef, all_features, mt_eval)

    covered = sum(1 for r in eval_records if aux(r))
    coverage = covered / len(eval_records) if eval_records else 0.0
    return MultiTaskTransfer(
        base_auc=base_auc,
        multitask_auc=mt_auc,
        delta_auc=mt_auc - base_auc,
        aux_coverage=coverage,
        eval_pairs=len(eval_records),
    )
