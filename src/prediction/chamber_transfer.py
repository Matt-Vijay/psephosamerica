"""Cross-chamber / cross-corpus transfer for the defection head (v4 #5).

The defection head's signal is two global coefficients -- how strongly a member's
pre-cutoff loyalty gap and policy-area divergence predict a future defection --
on top of per-member profiles built from the *target* corpus. That makes a clean
zero-shot transfer test: do the coefficients learned on one chamber generalise to
another, where every member (and their profile) is unseen?

``transfer_report`` measures three points on the same held-out target window:

* **zero_shot** -- the *source*-trained head scored on target members using their
  own target pre-cutoff profiles (coefficients transferred, members unseen),
* **target_only** -- a head trained from scratch on the target corpus (the
  in-domain ceiling),
* **joint** -- a head trained on source + target pooled (the blueprint's joint
  setting),

and reports the **gap** = target_only AUC − zero_shot AUC: how much in-domain
data buys over pure coefficient transfer. The harness is corpus-agnostic, so it
runs House→Senate the moment a Senate corpus is ingested, and is exercised now as
a cross-congress House→House transfer (a real, available proxy).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.prediction.defection import (
    PartyProfiles,
    build_party_profiles,
    defected,
    defection_features,
    ranking_metrics,
)
from src.prediction.defection_head import DefectionHead, train_defection_head
from src.runtime.cross_pressured_experiment import VoteRecord


def _auc_on(
    head: DefectionHead, eval_records: list[VoteRecord], profiles: PartyProfiles
) -> float:
    scores = [head.probability(defection_features(r, profiles)) for r in eval_records]
    labels = [defected(r) for r in eval_records]
    return ranking_metrics(scores, labels).auc


@dataclass(frozen=True)
class TransferReport:
    source_label: str
    target_label: str
    zero_shot_auc: float
    target_only_auc: float
    joint_auc: float
    gap: float
    target_eval_pairs: int
    target_positives: int

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source_label,
            "target": self.target_label,
            "zero_shot_auc": self.zero_shot_auc,
            "target_only_auc": self.target_only_auc,
            "joint_auc": self.joint_auc,
            "gap": self.gap,
            "target_eval_pairs": self.target_eval_pairs,
            "target_positives": self.target_positives,
        }


def transfer_report(
    source_train: list[VoteRecord],
    target_train: list[VoteRecord],
    target_eval: list[VoteRecord],
    *,
    source_label: str = "source",
    target_label: str = "target",
) -> TransferReport:
    """Zero-shot vs target-only vs joint defection AUC on the target window."""
    source_profiles = build_party_profiles(source_train)
    source_head = train_defection_head(source_train, source_profiles)

    target_profiles = build_party_profiles(target_train)
    target_head = train_defection_head(target_train, target_profiles)

    joint_train = source_train + target_train
    joint_profiles = build_party_profiles(joint_train)
    joint_head = train_defection_head(joint_train, joint_profiles)

    # Zero-shot: source coefficients, target members' own profiles (unseen members).
    zero_shot = _auc_on(source_head, target_eval, target_profiles)
    target_only = _auc_on(target_head, target_eval, target_profiles)
    joint = _auc_on(joint_head, target_eval, joint_profiles)

    return TransferReport(
        source_label=source_label,
        target_label=target_label,
        zero_shot_auc=zero_shot,
        target_only_auc=target_only,
        joint_auc=joint,
        gap=target_only - zero_shot,
        target_eval_pairs=len(target_eval),
        target_positives=sum(1 for r in target_eval if defected(r)),
    )
