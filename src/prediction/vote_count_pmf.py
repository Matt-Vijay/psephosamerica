"""Correlated yes-count distribution over a roll-call (v7 #2, additive head).

Markets settle on the full count ("does it clear 60?"), not on per-member
marginals -- and member errors are correlated: a leadership deal or a poison
pill moves a whole party at once. Independence (the Poisson-binomial over
per-member probabilities) understates tail mass exactly where pivots live.

This module prices the count three ways:

* ``poisson_binomial_pmf`` -- the exact independence PMF by dynamic-programming
  convolution (O(n^2), vectorised numpy).
* ``correlated_count_pmf`` -- a latent-shock mixture: member logits are shifted
  by a common shock and a party-polarised shock (Democrats +, Republicans -),
  integrated over Gauss-Hermite nodes. Each node is an independence PMF; the
  mixture is the correlated PMF. sigma = 0 recovers independence exactly.
* ``fit_shock_scales`` -- maximum-likelihood (sigma_common, sigma_party) over
  historical roll-calls by coarse-to-fine grid search; no scipy, no torch.

Scoring: ``crps_count`` (the proper score for count distributions) and central
``interval_coverage`` -- the honest comparison vs the independence baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

_CLAMP = 40.0


def _sigmoid(values: Array) -> Array:
    out: Array = 1.0 / (1.0 + np.exp(-np.clip(values, -_CLAMP, _CLAMP)))
    return out


def _logit(p: Array) -> Array:
    q = np.clip(p, 1e-9, 1.0 - 1e-9)
    out: Array = np.log(q / (1.0 - q))
    return out


def poisson_binomial_pmf(probabilities: Array) -> Array:
    """Exact PMF of the sum of independent Bernoullis (length n+1)."""
    out: Array = _batched_poisson_binomial(probabilities[None, :])[0]
    return out


def _batched_poisson_binomial(prob_matrix: Array) -> Array:
    """Exact independence PMFs for a batch of probability vectors.

    One dynamic-programming pass shared across the batch (the quadrature nodes
    of the mixture), so the correlated PMF costs one DP, not nodes^2 of them.
    """
    m, n = prob_matrix.shape
    pmf = np.zeros((m, n + 1), dtype=np.float64)
    pmf[:, 0] = 1.0
    for i in range(n):
        p = prob_matrix[:, i : i + 1]
        pmf[:, 1 : i + 2] = pmf[:, 1 : i + 2] * (1.0 - p) + pmf[:, : i + 1] * p
        pmf[:, 0] *= (1.0 - p)[:, 0]
    return pmf


def _hermite_nodes(n_nodes: int) -> tuple[Array, Array]:
    """Gauss-Hermite nodes/weights rescaled for a standard normal expectation."""
    nodes, weights = np.polynomial.hermite_e.hermegauss(n_nodes)
    return nodes.astype(np.float64), (weights / weights.sum()).astype(np.float64)


def correlated_count_pmf(
    probabilities: Array,
    party_sign: Array,
    *,
    sigma_common: float,
    sigma_party: float,
    n_nodes: int = 15,
) -> Array:
    """Latent-shock mixture PMF of the yes count.

    ``party_sign`` is +1/-1 (0 for independents/unknown): the party shock moves
    the two parties in opposite directions while the common shock moves all
    members together. With both sigmas 0 this is exactly the independence PMF.
    All quadrature nodes share one batched DP pass.
    """
    if sigma_common == 0.0 and sigma_party == 0.0:
        return poisson_binomial_pmf(probabilities)
    base = _logit(probabilities)
    nodes, weights = _hermite_nodes(n_nodes)
    zc = np.repeat(nodes, n_nodes)  # (n_nodes^2,)
    zp = np.tile(nodes, n_nodes)
    w = np.repeat(weights, n_nodes) * np.tile(weights, n_nodes)
    shifted = _sigmoid(
        base[None, :] + sigma_common * zc[:, None] + sigma_party * zp[:, None] * party_sign[None, :]
    )
    pmf: Array = w @ _batched_poisson_binomial(shifted)
    normalised: Array = pmf / pmf.sum()
    return normalised


@dataclass(frozen=True)
class RollCallCounts:
    """One historical roll-call: per-member yes-probabilities and the outcome."""

    probabilities: Array
    party_sign: Array
    observed_yes: int


def count_log_likelihood(
    rollcalls: list[RollCallCounts],
    *,
    sigma_common: float,
    sigma_party: float,
    n_nodes: int = 15,
) -> float:
    total = 0.0
    for rc in rollcalls:
        pmf = correlated_count_pmf(
            rc.probabilities,
            rc.party_sign,
            sigma_common=sigma_common,
            sigma_party=sigma_party,
            n_nodes=n_nodes,
        )
        k = min(max(rc.observed_yes, 0), pmf.shape[0] - 1)
        total += math.log(max(float(pmf[k]), 1e-300))
    return total


def fit_shock_scales(
    rollcalls: list[RollCallCounts],
    *,
    coarse: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5),
    refine_step: float = 0.1,
    n_nodes: int = 9,
) -> tuple[float, float]:
    """Coarse-to-fine grid MLE of (sigma_common, sigma_party)."""
    best = (0.0, 0.0)
    best_ll = -math.inf
    for sc in coarse:
        for sp in coarse:
            ll = count_log_likelihood(rollcalls, sigma_common=sc, sigma_party=sp, n_nodes=n_nodes)
            if ll > best_ll:
                best_ll, best = ll, (sc, sp)
    for sc in (best[0] - refine_step, best[0], best[0] + refine_step):
        for sp in (best[1] - refine_step, best[1], best[1] + refine_step):
            if sc < 0 or sp < 0 or (sc, sp) == best:
                continue
            ll = count_log_likelihood(rollcalls, sigma_common=sc, sigma_party=sp, n_nodes=n_nodes)
            if ll > best_ll:
                best_ll, best = ll, (sc, sp)
    return best


def crps_count(pmf: Array, observed: int) -> float:
    """CRPS of a count PMF vs the observed count (sum of squared CDF errors)."""
    cdf = np.cumsum(pmf)
    step = (np.arange(pmf.shape[0]) >= observed).astype(np.float64)
    return float(np.sum((cdf - step) ** 2))


def interval_coverage(pmf: Array, observed: int, *, level: float = 0.9) -> bool:
    """Is the observed count inside the central ``level`` interval of the PMF?"""
    cdf = np.cumsum(pmf)
    lo_target = (1.0 - level) / 2.0
    lo = int(np.searchsorted(cdf, lo_target, side="left"))
    hi = int(np.searchsorted(cdf, 1.0 - lo_target, side="left"))
    return lo <= observed <= hi
