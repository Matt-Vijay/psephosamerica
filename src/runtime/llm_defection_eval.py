"""LLM-forecaster defection eval + stacking weights (v4 #8).

The blueprint stacks a frontier-LLM forecaster with the model on a held-out
window. With ``ANTHROPIC_API_KEY`` set, the real member is
``src.prediction.anthropic_forecaster.AnthropicForecaster`` (httpx, no SDK); with
no key we extend the *stub* path: a deterministic stub "forecaster" that reasons
over the same ex-ante features the head sees, scored on honest-slice votes, and
**stacked** with the defection head by a one-parameter logit blend fit on a
held-out split. We report the stub's standalone AUC, the stacked AUC, and the
learned stacking weight (how much the ensemble leans on the LLM vs the head).

Self-contained on purpose: the stacking blend is a tiny local logistic so the
eval runs with no network and no key, and the real LLM simply replaces the stub's
probability function when a key is present.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass

from src.prediction.defection import (
    PartyProfiles,
    defected,
    defection_features,
    ranking_metrics,
)
from src.prediction.defection_head import DefectionHead
from src.runtime.cross_pressured_experiment import VoteRecord

# A defection forecaster maps ex-ante features -> P(defect).
DefectionForecaster = Callable[[dict[str, float]], float]
_LOGIT_CLAMP = 40.0


def stub_llm_forecaster(features: dict[str, float]) -> float:
    """Deterministic stub: an 'LLM' that weighs divergence over raw loyalty gap.

    Stands in for the real Anthropic forecaster when no key is set. It reasons
    over the same ex-ante signals but with a different emphasis than the head
    (divergence-led), so stacking has something non-redundant to learn.
    """
    raw = (
        -2.2 + 2.5 * features.get("sector_divergence", 0.0) + 1.0 * features.get("loyalty_gap", 0.0)
    )
    return 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))


@dataclass(frozen=True)
class StackingResult:
    head_auc: float
    llm_auc: float
    stacked_auc: float
    llm_weight: float
    head_weight: float
    eval_pairs: int
    used_real_llm: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "head_auc": self.head_auc,
            "llm_auc": self.llm_auc,
            "stacked_auc": self.stacked_auc,
            "llm_weight": self.llm_weight,
            "head_weight": self.head_weight,
            "eval_pairs": self.eval_pairs,
            "used_real_llm": self.used_real_llm,
        }


def _fit_stack(
    head_probs: list[float],
    llm_probs: list[float],
    labels: list[bool],
    *,
    epochs: int = 400,
    learning_rate: float = 0.3,
) -> tuple[float, float, float]:
    """Fit logit(stack) = b + w_h*logit(head) + w_l*logit(llm); return (b, w_h, w_l)."""

    def _logit(p: float) -> float:
        p = min(1.0 - 1e-9, max(1e-9, p))
        return math.log(p / (1.0 - p))

    hs = [_logit(p) for p in head_probs]
    ls = [_logit(p) for p in llm_probs]
    b, w_h, w_l = 0.0, 0.5, 0.5
    n = len(labels)
    if n == 0:
        return b, w_h, w_l
    scale = 1.0 / n
    for _ in range(epochs):
        d_b = d_h = d_l = 0.0
        for i in range(n):
            raw = b + w_h * hs[i] + w_l * ls[i]
            pred = 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))
            err = pred - (1.0 if labels[i] else 0.0)
            d_b += err
            d_h += err * hs[i]
            d_l += err * ls[i]
        b -= learning_rate * d_b * scale
        w_h -= learning_rate * d_h * scale
        w_l -= learning_rate * d_l * scale
    return b, w_h, w_l


def evaluate_llm_stack(
    head: DefectionHead,
    fit_records: list[VoteRecord],
    eval_records: list[VoteRecord],
    profiles: PartyProfiles,
    *,
    forecaster: DefectionForecaster | None = None,
) -> StackingResult:
    """Stack the head with an LLM defection forecaster; report AUCs + weights.

    ``fit_records`` learns the stacking blend (held out from the head's training);
    ``eval_records`` scores it. ``forecaster`` defaults to the stub; pass the real
    Anthropic-backed forecaster when a key is available.
    """
    llm = forecaster or stub_llm_forecaster
    used_real = forecaster is not None

    def _feats(r: VoteRecord) -> dict[str, float]:
        return defection_features(r, profiles)

    fit_head = [head.probability(_feats(r)) for r in fit_records]
    fit_llm = [llm(_feats(r)) for r in fit_records]
    fit_labels = [defected(r) for r in fit_records]
    b, w_h, w_l = _fit_stack(fit_head, fit_llm, fit_labels)

    def _logit(p: float) -> float:
        p = min(1.0 - 1e-9, max(1e-9, p))
        return math.log(p / (1.0 - p))

    eval_head = [head.probability(_feats(r)) for r in eval_records]
    eval_llm = [llm(_feats(r)) for r in eval_records]
    labels = [defected(r) for r in eval_records]
    stacked = [
        1.0
        / (
            1.0
            + math.exp(
                -max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, b + w_h * _logit(h) + w_l * _logit(lp)))
            )
        )
        for h, lp in zip(eval_head, eval_llm)
    ]
    return StackingResult(
        head_auc=ranking_metrics(eval_head, labels).auc,
        llm_auc=ranking_metrics(eval_llm, labels).auc,
        stacked_auc=ranking_metrics(stacked, labels).auc,
        llm_weight=w_l,
        head_weight=w_h,
        eval_pairs=len(eval_records),
        used_real_llm=used_real,
    )


def anthropic_key_present() -> bool:
    """Whether the real LLM forecaster path is available."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
