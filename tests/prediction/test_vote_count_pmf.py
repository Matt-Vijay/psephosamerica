"""Tests for the correlated yes-count PMF."""

from __future__ import annotations

import math

import numpy as np

from src.prediction.vote_count_pmf import (
    RollCallCounts,
    correlated_count_pmf,
    crps_count,
    fit_shock_scales,
    interval_coverage,
    poisson_binomial_pmf,
)


def test_poisson_binomial_matches_binomial() -> None:
    pmf = poisson_binomial_pmf(np.full(10, 0.5))
    expected = np.array([math.comb(10, k) for k in range(11)]) / 1024.0
    assert np.allclose(pmf, expected)


def test_zero_sigma_recovers_independence() -> None:
    rng = np.random.default_rng(3)
    p = rng.uniform(0.1, 0.9, size=30)
    signs = np.where(np.arange(30) % 2 == 0, 1.0, -1.0)
    ind = poisson_binomial_pmf(p)
    cor = correlated_count_pmf(p, signs, sigma_common=0.0, sigma_party=0.0)
    assert np.allclose(ind, cor)


def test_common_shock_fattens_tails() -> None:
    p = np.full(100, 0.5)
    signs = np.ones(100)
    ind = poisson_binomial_pmf(p)
    cor = correlated_count_pmf(p, signs, sigma_common=1.0, sigma_party=0.0)
    # variance of the count must increase under a shared shock
    ks = np.arange(101)
    var_ind = float((ind * ks**2).sum() - (ind * ks).sum() ** 2)
    var_cor = float((cor * ks**2).sum() - (cor * ks).sum() ** 2)
    assert var_cor > 2.0 * var_ind
    assert np.isclose(cor.sum(), 1.0)


def test_fit_recovers_positive_sigma_on_shocked_data() -> None:
    rng = np.random.default_rng(11)
    rollcalls = []
    p = np.full(80, 0.5)
    signs = np.where(np.arange(80) < 40, 1.0, -1.0)
    for _ in range(60):
        z = rng.standard_normal()
        shifted = 1.0 / (1.0 + np.exp(-(0.0 + 1.2 * z * signs)))
        observed = int((rng.random(80) < shifted).sum())
        rollcalls.append(RollCallCounts(probabilities=p, party_sign=signs, observed_yes=observed))
    sigma_common, sigma_party = fit_shock_scales(rollcalls)
    assert sigma_party >= 0.75  # the polarised shock is detected
    assert sigma_common <= 0.5


def test_crps_rewards_concentration_at_truth() -> None:
    sharp = np.zeros(11)
    sharp[5] = 1.0
    flat = np.full(11, 1.0 / 11.0)
    assert crps_count(sharp, 5) < crps_count(flat, 5)


def test_interval_coverage() -> None:
    pmf = poisson_binomial_pmf(np.full(20, 0.5))
    assert interval_coverage(pmf, 10, level=0.9)
    assert not interval_coverage(pmf, 20, level=0.9)
