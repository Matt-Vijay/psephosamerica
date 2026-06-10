"""Pre-registration registry: a forward, content-hashed defection track record (v5 #2).

The credibility milestone. Before a target window's votes are scored, we freeze a
model (trained strictly on votes at/before a cutoff), emit predictions for every
selected post-cutoff (member, bill) pair -- probability + citations + the
counterfactual flip that would most change it -- and **content-hash** the frozen
file so the predictions cannot be retroactively edited. When the outcomes land, the
scoring runner joins them and reports Brier / accuracy / AUC.

This is genuinely forward: the model never sees a target-window vote (strict
cutoff), and the hash + commit timestamp prove the predictions predate scoring.
The same freeze→commit→score loop runs on truly-future windows (cutoff = today,
target = upcoming floor votes) the moment new outcomes arrive.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from src.prediction.defection import (
    build_party_profiles,
    defected,
    defection_features,
    is_defection_prone,
)
from src.prediction.defection_head import DefectionHead, train_defection_head
from src.prediction.vote_record import VoteRecord

_FACTOR_LABEL = {
    "loyalty_gap": "the member breaks with party overall",
    "sector_divergence": "the member breaks with party on this policy area",
}


@dataclass(frozen=True)
class Citation:
    kind: str
    ref: str
    detail: str


@dataclass(frozen=True)
class FrozenPrediction:
    member: str
    party: str
    state: str
    bill_id: str
    target_vote_date: str
    p_defect: float
    predicted_defect: bool
    counterfactual: str
    citations: list[Citation] = field(default_factory=list)
    actual_defect: bool | None = None  # filled by scoring; None while pending

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _counterfactual(head: DefectionHead, features: dict[str, float]) -> str:
    contributions = head.contributions(features)
    if not contributions:
        return "no ranked factors"
    name, value = max(contributions.items(), key=lambda kv: kv[1])
    if value <= 0:
        return "already at the party line on every factor"
    return f"if {_FACTOR_LABEL.get(name, name)} were neutralised, P(defect) drops most"


def _citations(record: VoteRecord, bill_id: str, features: dict[str, float]) -> list[Citation]:
    cites = [
        Citation(kind="rollcall_bill", ref=bill_id, detail="bill under vote"),
        Citation(
            kind="member_history",
            ref=record.member,
            detail=f"pre-cutoff loyalty_gap={features.get('loyalty_gap', 0.0):.3f}, "
            f"sector_divergence={features.get('sector_divergence', 0.0):.3f}",
        ),
    ]
    if record.sectors:
        cites.append(Citation(kind="policy_sectors", ref=",".join(record.sectors), detail="bill policy areas"))
    return cites


def content_hash(predictions: list[FrozenPrediction], *, cutoff: str, target_end: str) -> str:
    """sha256 over the frozen predictions (excluding any later-filled actuals)."""
    payload = {
        "cutoff": cutoff,
        "target_end": target_end,
        "predictions": [
            {
                "member": p.member,
                "bill_id": p.bill_id,
                "target_vote_date": p.target_vote_date,
                "p_defect": round(p.p_defect, 9),
                "predicted_defect": p.predicted_defect,
            }
            for p in predictions
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class RegistryFile:
    frozen_at_cutoff: str
    target_window_end: str
    model_name: str
    content_sha256: str
    predictions: list[FrozenPrediction]
    scored: bool = False
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frozen_at_cutoff": self.frozen_at_cutoff,
            "target_window_end": self.target_window_end,
            "model_name": self.model_name,
            "content_sha256": self.content_sha256,
            "scored": self.scored,
            "metrics": self.metrics,
            "predictions": [p.to_dict() for p in self.predictions],
        }


def freeze_registry(
    votes: list[tuple[VoteRecord, str]],
    *,
    cutoff: date,
    target_end: date,
    max_predictions: int = 200,
    prone_first: bool = True,
) -> RegistryFile:
    """Train on <= cutoff, emit frozen predictions for the (cutoff, target_end] window.

    ``votes`` is a list of (record, bill_id) pairs (VoteRecord carries no bill id).
    """
    train = [r for r, _b in votes if r.vote_date <= cutoff]
    target = [(r, b) for r, b in votes if cutoff < r.vote_date <= target_end]
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles)

    # Prioritise defection-prone pairs (the interesting "who breaks ranks" calls),
    # then fill with the rest, deterministically (by member+bill+date).
    target.sort(key=lambda rb: (rb[0].member, rb[1], rb[0].vote_date.isoformat()))
    if prone_first:
        target.sort(key=lambda rb: not is_defection_prone(rb[0], profiles))
    selected = target[:max_predictions]

    predictions: list[FrozenPrediction] = []
    for r, bill_id in selected:
        features = defection_features(r, profiles)
        p = head.probability(features)
        predictions.append(
            FrozenPrediction(
                member=r.member,
                party=r.party,
                state=r.state,
                bill_id=bill_id,
                target_vote_date=r.vote_date.isoformat(),
                p_defect=p,
                predicted_defect=p >= 0.5,
                counterfactual=_counterfactual(head, features),
                citations=_citations(r, bill_id, features),
            )
        )
    digest = content_hash(predictions, cutoff=cutoff.isoformat(), target_end=target_end.isoformat())
    return RegistryFile(
        frozen_at_cutoff=cutoff.isoformat(),
        target_window_end=target_end.isoformat(),
        model_name="defection_head_logreg",
        content_sha256=digest,
        predictions=predictions,
    )


def score_registry(registry: RegistryFile, votes: list[tuple[VoteRecord, str]]) -> RegistryFile:
    """Join frozen predictions to realized outcomes; fill actuals + compute metrics."""
    truth: dict[tuple[str, str, str], bool] = {}
    for r, bill_id in votes:
        truth[(r.member, bill_id, r.vote_date.isoformat())] = defected(r)

    scored_preds: list[FrozenPrediction] = []
    resolved: list[tuple[float, bool]] = []
    for p in registry.predictions:
        actual = truth.get((p.member, p.bill_id, p.target_vote_date))
        scored_preds.append(
            FrozenPrediction(
                member=p.member, party=p.party, state=p.state, bill_id=p.bill_id,
                target_vote_date=p.target_vote_date, p_defect=p.p_defect,
                predicted_defect=p.predicted_defect, counterfactual=p.counterfactual,
                citations=p.citations, actual_defect=actual,
            )
        )
        if actual is not None:
            resolved.append((p.p_defect, actual))

    metrics: dict[str, float] = {"resolved": float(len(resolved)), "pending": float(len(registry.predictions) - len(resolved))}
    if resolved:
        n = len(resolved)
        metrics["brier"] = sum((pp - (1.0 if y else 0.0)) ** 2 for pp, y in resolved) / n
        metrics["accuracy"] = sum(1 for pp, y in resolved if (pp >= 0.5) == y) / n
        from src.prediction.defection import roc_auc

        metrics["auc"] = roc_auc([pp for pp, _ in resolved], [y for _, y in resolved])
        metrics["base_rate"] = sum(1 for _p, y in resolved if y) / n
    return RegistryFile(
        frozen_at_cutoff=registry.frozen_at_cutoff,
        target_window_end=registry.target_window_end,
        model_name=registry.model_name,
        content_sha256=registry.content_sha256,
        predictions=scored_preds,
        scored=True,
        metrics=metrics,
    )
