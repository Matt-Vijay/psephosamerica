"""Shared feature-dict logistic regression for Track B heads and experiments.

One implementation of the numpy-free full-batch gradient-descent trainer that was
previously duplicated across seven modules (``defection_head``,
``defection_multitask``, ``defection_signals``, ``defection_rag`` and the
bill-content / combined-SOTA / CRS-multitask experiments). The numerics are
exactly those of the duplicated originals -- lr 0.3, 300 epochs, L2 0.01, logit
clamp 40, base-rate intercept init -- so every pinned benchmark number is
unchanged by the consolidation.
"""

from __future__ import annotations

import math

from src.prediction.defection import ranking_metrics

LOGIT_CLAMP = 40.0

FeatureRows = list[tuple[dict[str, float], bool]]


def clamped_sigmoid(value: float) -> float:
    """Overflow-safe sigmoid; the clamp matches every prior inline copy."""
    return 1.0 / (1.0 + math.exp(-max(-LOGIT_CLAMP, min(LOGIT_CLAMP, value))))


def train_logistic_rows(
    rows: FeatureRows,
    feature_names: tuple[str, ...],
    *,
    learning_rate: float = 0.3,
    epochs: int = 300,
    l2: float = 0.01,
) -> tuple[float, dict[str, float]]:
    """Fit ``(intercept, coefficients)`` by full-batch GD on feature-dict rows."""
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
            err = clamped_sigmoid(raw) - (1.0 if label else 0.0)
            d_int += err
            for n in feature_names:
                d_coef[n] += err * features.get(n, 0.0)
        intercept -= learning_rate * d_int * scale
        for n in feature_names:
            coef[n] -= learning_rate * (d_coef[n] * scale + l2 * coef[n])
    return intercept, coef


def score_row(
    intercept: float,
    coefficients: dict[str, float],
    feature_names: tuple[str, ...],
    features: dict[str, float],
) -> float:
    """Calibrated probability for one feature dict under a trained head."""
    raw = intercept + sum(coefficients[n] * features.get(n, 0.0) for n in feature_names)
    return clamped_sigmoid(raw)


def auc_of_rows(
    intercept: float,
    coefficients: dict[str, float],
    feature_names: tuple[str, ...],
    rows: FeatureRows,
) -> float:
    """Defection-ranking ROC AUC of a trained head over labelled feature rows."""
    scores = [score_row(intercept, coefficients, feature_names, f) for f, _y in rows]
    return ranking_metrics(scores, [y for _f, y in rows]).auc
