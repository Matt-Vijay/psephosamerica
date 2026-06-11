"""Discrete-time hazard model for bill stage transitions (v7 #1, additive head).

Markets price compound events -- a bill must *reach* the floor before any
defection head matters. This module prices that first link: P(stage advance by
date T) as a discrete-time survival model. Each bill contributes one row per
30-day period it is at risk; the hazard in period t is a logistic over period
dummies (a nonparametric baseline hazard) plus static bill covariates. Pure
vectorised numpy full-batch gradient descent -- no torch, no sklearn.

The event is whatever transition the caller labels (v1: first recorded floor
roll-call in the origin chamber); right-censoring (congress end / data end) is
handled exactly: censored bills contribute at-risk periods with no event row.
``predict_event_by`` composes per-period hazards into P(event by horizon), and
``concordance_index`` / ``calibration_at_horizon`` are the honest time-sliced
metrics (Harrell's C over comparable pairs; reliability among fully-observed
bills only).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

_CLAMP = 40.0


def _sigmoid(values: Array) -> Array:
    out: Array = 1.0 / (1.0 + np.exp(-np.clip(values, -_CLAMP, _CLAMP)))
    return out


@dataclass(frozen=True)
class HazardModel:
    """Fitted discrete-time hazard: period intercepts + covariate coefficients."""

    period_logits: Array  # (n_periods,) baseline log-odds of advancing in period t
    coefficients: Array  # (n_features,)
    feature_names: tuple[str, ...]
    period_days: int

    def hazards(self, features: Array) -> Array:
        """Per-period hazard matrix for ``features`` (n_bills, n_features) -> (n_bills, n_periods)."""
        raw = self.period_logits[None, :] + (features @ self.coefficients)[:, None]
        return _sigmoid(raw)

    def predict_event_between(
        self, features: Array, start_days: float, end_days: float
    ) -> Array:
        """P(event in (start, end] | no event by start) -- conditional survival.

        Prices a bill already ``start_days`` old: the periods it survived are
        conditioned away, not re-counted.
        """
        p_start = self.predict_event_by(features, start_days)
        p_end = self.predict_event_by(features, max(start_days, end_days))
        surv = np.maximum(1.0 - p_start, 1e-12)
        out: Array = np.clip((p_end - p_start) / surv, 0.0, 1.0)
        return out

    def predict_event_by(self, features: Array, horizon_days: float) -> Array:
        """P(event within ``horizon_days`` of t0) = 1 - prod_t (1 - h_t) over covered periods.

        A partial final period contributes a fractional exposure (linear in the
        log-survival of that period), so the horizon need not align to the grid.
        """
        h = self.hazards(features)
        n_periods = h.shape[1]
        full = int(min(n_periods, np.floor(horizon_days / self.period_days)))
        log_surv = np.log1p(-np.clip(h, 0.0, 1.0 - 1e-12))
        total = log_surv[:, :full].sum(axis=1)
        if full < n_periods:
            frac = (horizon_days - full * self.period_days) / self.period_days
            if frac > 0:
                total = total + frac * log_surv[:, full]
        out: Array = 1.0 - np.exp(total)
        return out


def build_person_period(
    features: Array,
    duration_days: Array,
    event_observed: npt.NDArray[np.bool_],
    *,
    period_days: int = 30,
    max_periods: int = 24,
) -> tuple[IntArray, Array, Array]:
    """Expand (bill, duration, event) into person-period training rows.

    Returns ``(period_index, row_features, labels)``: one row per period each
    bill was at risk, labelled 1 only in the event period. A bill whose duration
    exceeds the grid is censored at the grid end (its event, if any, falls
    outside the modelled window).
    """
    periods = np.minimum(np.maximum(duration_days, 0.0) // period_days, max_periods - 1).astype(
        np.int64
    )
    in_grid = duration_days < period_days * max_periods
    counts = periods + 1
    total = int(counts.sum())
    period_index = np.empty(total, dtype=np.int64)
    labels = np.zeros(total, dtype=np.float64)
    bill_index = np.empty(total, dtype=np.int64)
    pos = 0
    for i in range(features.shape[0]):
        c = int(counts[i])
        period_index[pos : pos + c] = np.arange(c)
        bill_index[pos : pos + c] = i
        if bool(event_observed[i]) and bool(in_grid[i]):
            labels[pos + c - 1] = 1.0
        pos += c
    return period_index, features[bill_index], labels


def fit_hazard(
    features: Array,
    duration_days: Array,
    event_observed: npt.NDArray[np.bool_],
    feature_names: tuple[str, ...],
    *,
    period_days: int = 30,
    max_periods: int = 24,
    learning_rate: float = 0.5,
    epochs: int = 3000,
    l2: float = 1e-4,
) -> HazardModel:
    """Fit the discrete-time hazard by vectorised full-batch gradient descent."""
    period_index, x, y = build_person_period(
        features, duration_days, event_observed, period_days=period_days, max_periods=max_periods
    )
    n = max(1, y.shape[0])
    rate = min(0.95, max(1e-4, float(y.mean()) if y.size else 1e-4))
    period_logits = np.full(max_periods, np.log(rate / (1.0 - rate)), dtype=np.float64)
    coef = np.zeros(x.shape[1], dtype=np.float64)
    one_hot = np.zeros((y.shape[0], max_periods), dtype=np.float64)
    one_hot[np.arange(y.shape[0]), period_index] = 1.0
    period_counts = np.maximum(one_hot.sum(axis=0), 1.0)
    for _ in range(epochs):
        raw = period_logits[period_index] + x @ coef
        err = _sigmoid(raw) - y
        period_logits -= learning_rate * (one_hot.T @ err) / period_counts
        coef -= learning_rate * ((x.T @ err) / n + l2 * coef)
    return HazardModel(
        period_logits=period_logits,
        coefficients=coef,
        feature_names=feature_names,
        period_days=period_days,
    )


def concordance_index(
    risk: Array, duration_days: Array, event_observed: npt.NDArray[np.bool_], *, chunk: int = 512
) -> float:
    """Harrell's C with right-censoring: P(risk_i > risk_j | t_i < t_j, event_i).

    Comparable pairs: i had the event, and j's (event or censoring) time is
    strictly later. Ties in risk count 0.5. Computed exactly in chunks.
    """
    concordant = 0.0
    comparable = 0
    event_idx = np.flatnonzero(event_observed)
    for start in range(0, event_idx.shape[0], chunk):
        idx = event_idx[start : start + chunk]
        later = duration_days[None, :] > duration_days[idx][:, None]  # (chunk, n)
        diff = risk[idx][:, None] - risk[None, :]
        concordant += float(((diff > 0) & later).sum()) + 0.5 * float(((diff == 0) & later).sum())
        comparable += int(later.sum())
    return concordant / comparable if comparable else 0.5


def calibration_at_horizon(
    predicted: Array,
    duration_days: Array,
    event_observed: npt.NDArray[np.bool_],
    *,
    horizon_days: float,
    bins: int = 10,
) -> dict[str, object]:
    """Reliability of P(event by horizon) among bills fully observed to the horizon.

    A bill censored before the horizon has an unknown outcome and is excluded
    (the honest restriction); events before the horizon and survivors observed
    past it are kept.
    """
    known = event_observed & (duration_days <= horizon_days)
    known |= duration_days >= horizon_days
    p = predicted[known]
    y = (event_observed[known] & (duration_days[known] <= horizon_days)).astype(np.float64)
    if p.size == 0:
        return {"n": 0, "brier": 0.0, "ece": 0.0, "base_rate": 0.0, "reliability": []}
    brier = float(np.mean((p - y) ** 2))
    order = np.argsort(p)
    reliability: list[dict[str, float]] = []
    ece = 0.0
    for chunk_idx in np.array_split(order, bins):
        if chunk_idx.size == 0:
            continue
        mean_p = float(p[chunk_idx].mean())
        mean_y = float(y[chunk_idx].mean())
        reliability.append({"predicted": mean_p, "observed": mean_y, "n": float(chunk_idx.size)})
        ece += abs(mean_p - mean_y) * chunk_idx.size / p.size
    return {
        "n": int(p.size),
        "brier": brier,
        "ece": float(ece),
        "base_rate": float(y.mean()),
        "reliability": reliability,
    }
