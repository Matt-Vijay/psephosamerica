"""Fit + evaluate the correlated yes-count PMF on real 118th roll-calls (v7 #2 runner).

Three strictly-ordered windows, no leakage:

* member yea-rates (conditioned on the member's party leaning yea or nay) are
  estimated on roll-calls up to ``rates_cutoff``;
* the shock scales (sigma_common, sigma_party) are fit by MLE on roll-calls in
  ``(rates_cutoff, fit_cutoff]``;
* both the independence baseline and the correlated mixture are scored on
  roll-calls after ``fit_cutoff`` -- mean CRPS, count log-likelihood, and
  central-interval coverage. The correlated model must beat independence.

Conditioning note (honest): each arm's per-member probability conditions on the
roll-call's realised party-lean direction -- identical conditioning for both
arms, so the measured lift is purely the correlation structure, not extra
information. A forward composer replaces the lean with its own estimate.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.vote_count_pmf import (
    RollCallCounts,
    correlated_count_pmf,
    count_log_likelihood,
    crps_count,
    fit_shock_scales,
    interval_coverage,
    poisson_binomial_pmf,
)
from src.runtime.bill_content_experiment import _iter_records

_BINARY = {"yea", "nay"}
_PARTY_SIGN = {"D": 1.0, "R": -1.0}
_MIN_MEMBERS = 50  # skip near-empty / quorum-style roll-calls


def _lean_yea(votes: list[list[str]], party: str) -> bool:
    yes = sum(1 for v in votes if v[1] == party and v[3] == "yea")
    total = sum(1 for v in votes if v[1] == party and v[3] in _BINARY)
    return yes * 2 >= total if total else True


def build_rollcall_counts(
    rollcalls: list[dict[str, Any]],
    rates: dict[tuple[str, bool], tuple[int, int]],
    party_rates: dict[tuple[str, bool], tuple[int, int]],
) -> list[RollCallCounts]:
    """Per-member yes-probabilities (smoothed pre-cutoff rates) + observed count."""
    out: list[RollCallCounts] = []
    for rc in rollcalls:
        votes = [v for v in rc.get("votes", []) if v[3] in _BINARY]
        if len(votes) < _MIN_MEMBERS:
            continue
        leans = {p: _lean_yea(votes, p) for p in {v[1] for v in votes}}
        probs = np.empty(len(votes), dtype=np.float64)
        signs = np.empty(len(votes), dtype=np.float64)
        for i, (member, party, _state, choice) in enumerate(votes):
            lean = leans.get(party, True)
            yes, n = rates.get((member, lean), (0, 0))
            if n == 0:
                yes, n = party_rates.get((party, lean), (0, 0))
            probs[i] = (yes + 1.0) / (n + 2.0)  # Laplace-smoothed
            signs[i] = _PARTY_SIGN.get(party, 0.0)
        observed = sum(1 for v in votes if v[3] == "yea")
        out.append(RollCallCounts(probabilities=probs, party_sign=signs, observed_yes=observed))
    return out


def _accumulate_rates(
    rollcalls: list[dict[str, Any]],
) -> tuple[dict[tuple[str, bool], tuple[int, int]], dict[tuple[str, bool], tuple[int, int]]]:
    member: dict[tuple[str, bool], list[int]] = defaultdict(lambda: [0, 0])
    party: dict[tuple[str, bool], list[int]] = defaultdict(lambda: [0, 0])
    for rc in rollcalls:
        votes = [v for v in rc.get("votes", []) if v[3] in _BINARY]
        if len(votes) < _MIN_MEMBERS:
            continue
        leans = {p: _lean_yea(votes, p) for p in {v[1] for v in votes}}
        for m, p, _s, choice in votes:
            lean = leans.get(p, True)
            yes = int(choice == "yea")
            member[(m, lean)][0] += yes
            member[(m, lean)][1] += 1
            party[(p, lean)][0] += yes
            party[(p, lean)][1] += 1
    return (
        {k: (v[0], v[1]) for k, v in member.items()},
        {k: (v[0], v[1]) for k, v in party.items()},
    )


def run(
    corpus: Path,
    *,
    rates_cutoff: date,
    fit_cutoff: date,
    eval_end: date,
    eval_nodes: int = 15,
) -> dict[str, Any]:
    rolls = sorted(
        (r for r in _iter_records(corpus) if r.get("date")), key=lambda r: str(r["date"])
    )
    rates_window = [r for r in rolls if date.fromisoformat(str(r["date"])) <= rates_cutoff]
    fit_window = [
        r for r in rolls if rates_cutoff < date.fromisoformat(str(r["date"])) <= fit_cutoff
    ]
    eval_window = [r for r in rolls if fit_cutoff < date.fromisoformat(str(r["date"])) <= eval_end]
    if not rates_window or not fit_window or not eval_window:
        return {
            "error": "empty window",
            "rates": len(rates_window),
            "fit": len(fit_window),
            "eval": len(eval_window),
        }

    member_rates, party_rates = _accumulate_rates(rates_window)
    fit_counts = build_rollcall_counts(fit_window, member_rates, party_rates)
    eval_counts = build_rollcall_counts(eval_window, member_rates, party_rates)

    sigma_common, sigma_party = fit_shock_scales(fit_counts)

    crps_ind: list[float] = []
    crps_cor: list[float] = []
    cover_ind = cover_cor = 0
    for rc in eval_counts:
        pmf_ind = poisson_binomial_pmf(rc.probabilities)
        pmf_cor = correlated_count_pmf(
            rc.probabilities,
            rc.party_sign,
            sigma_common=sigma_common,
            sigma_party=sigma_party,
            n_nodes=eval_nodes,
        )
        crps_ind.append(crps_count(pmf_ind, rc.observed_yes))
        crps_cor.append(crps_count(pmf_cor, rc.observed_yes))
        cover_ind += int(interval_coverage(pmf_ind, rc.observed_yes, level=0.9))
        cover_cor += int(interval_coverage(pmf_cor, rc.observed_yes, level=0.9))

    n_eval = len(eval_counts)
    ll_ind = count_log_likelihood(eval_counts, sigma_common=0.0, sigma_party=0.0)
    ll_cor = count_log_likelihood(eval_counts, sigma_common=sigma_common, sigma_party=sigma_party)
    return {
        "corpus": str(corpus),
        "windows": {
            "rates": f"<= {rates_cutoff}",
            "sigma_fit": f"({rates_cutoff}, {fit_cutoff}]",
            "eval": f"({fit_cutoff}, {eval_end}]",
        },
        "fit_rollcalls": len(fit_counts),
        "eval_rollcalls": n_eval,
        "sigma_common": sigma_common,
        "sigma_party": sigma_party,
        "crps_independence": float(np.mean(crps_ind)),
        "crps_correlated": float(np.mean(crps_cor)),
        "crps_improvement": float(np.mean(crps_ind) - np.mean(crps_cor)),
        "coverage90_independence": cover_ind / n_eval,
        "coverage90_correlated": cover_cor / n_eval,
        "loglik_independence": ll_ind,
        "loglik_correlated": ll_cor,
        "beats_independence": bool(np.mean(crps_cor) < np.mean(crps_ind) and ll_cor > ll_ind),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Correlated yes-count PMF fit + eval")
    p.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    p.add_argument("--rates-cutoff", default="2023-12-31")
    p.add_argument("--fit-cutoff", default="2024-04-20")
    p.add_argument("--eval-end", default="2024-12-31")
    p.add_argument("--out", default="benchmarks/count_pmf.json")
    p.add_argument("--pin", default="benchmarks/count_pmf_baseline.json")
    args = p.parse_args(argv)

    report = run(
        Path(args.corpus),
        rates_cutoff=date.fromisoformat(args.rates_cutoff),
        fit_cutoff=date.fromisoformat(args.fit_cutoff),
        eval_end=date.fromisoformat(args.eval_end),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if "error" not in report:
        pin = {
            "slice": "count-pmf-118",
            "crps_independence": report["crps_independence"],
            "crps_correlated": report["crps_correlated"],
            "coverage90_correlated": report["coverage90_correlated"],
            "sigma_common": report["sigma_common"],
            "sigma_party": report["sigma_party"],
            "eval_rollcalls": report["eval_rollcalls"],
            "tolerance": 0.005,
        }
        Path(args.pin).write_text(json.dumps(pin, indent=2), encoding="utf-8")
        print(
            f"sigma=({report['sigma_common']:.2f}, {report['sigma_party']:.2f}) | "
            f"CRPS {report['crps_independence']:.3f} -> {report['crps_correlated']:.3f} | "
            f"coverage90 {report['coverage90_independence']:.3f} -> {report['coverage90_correlated']:.3f} | "
            f"beats independence: {report['beats_independence']}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
