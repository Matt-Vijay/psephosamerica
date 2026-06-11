"""10-seed bootstrap uncertainty on the count-PMF pin (v7 #7).

Resamples the sigma-fit window's roll-calls with replacement ten times, refits
(sigma_common, sigma_party) each time, and scores eval CRPS + coverage with
each draw -- is the (1.5, 2.2) pin stable under resampling? Content-addressed
checkpoint of the seed sigmas.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.vote_count_pmf import (
    correlated_count_pmf,
    crps_count,
    fit_shock_scales,
    interval_coverage,
)
from src.runtime.bill_content_experiment import _iter_records
from src.runtime.count_pmf_experiment import _accumulate_rates, build_rollcall_counts


def run(
    corpus: Path,
    *,
    rates_cutoff: date,
    fit_cutoff: date,
    eval_end: date,
    n_seeds: int = 10,
) -> dict[str, Any]:
    rolls = sorted(
        (r for r in _iter_records(corpus) if r.get("date")), key=lambda r: str(r["date"])
    )
    rates_window = [r for r in rolls if date.fromisoformat(str(r["date"])) <= rates_cutoff]
    fit_window = [
        r for r in rolls if rates_cutoff < date.fromisoformat(str(r["date"])) <= fit_cutoff
    ]
    eval_window = [r for r in rolls if fit_cutoff < date.fromisoformat(str(r["date"])) <= eval_end]
    member_rates, party_rates = _accumulate_rates(rates_window)
    fit_counts = build_rollcall_counts(fit_window, member_rates, party_rates)
    eval_counts = build_rollcall_counts(eval_window, member_rates, party_rates)

    sigmas: list[tuple[float, float]] = []
    crps: list[float] = []
    coverage: list[float] = []
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        sample = [fit_counts[int(i)] for i in rng.integers(0, len(fit_counts), len(fit_counts))]
        sc, sp = fit_shock_scales(sample)
        sigmas.append((sc, sp))
        seed_crps = [
            crps_count(
                correlated_count_pmf(
                    rc.probabilities, rc.party_sign, sigma_common=sc, sigma_party=sp
                ),
                rc.observed_yes,
            )
            for rc in eval_counts
        ]
        seed_cover = [
            interval_coverage(
                correlated_count_pmf(
                    rc.probabilities, rc.party_sign, sigma_common=sc, sigma_party=sp
                ),
                rc.observed_yes,
                level=0.9,
            )
            for rc in eval_counts
        ]
        crps.append(float(np.mean(seed_crps)))
        coverage.append(float(np.mean(seed_cover)))

    payload = {"model": "count_pmf", "seed_sigmas": sigmas}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    ckpt = Path("checkpoints") / "sha256" / digest[:2] / digest[2:4] / f"{digest}.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    crps_arr = np.asarray(crps)
    cov_arr = np.asarray(coverage)
    return {
        "n_seeds": n_seeds,
        "seed_sigmas": sigmas,
        "mean_crps": float(crps_arr.mean()),
        "std_crps": float(crps_arr.std()),
        "mean_coverage90": float(cov_arr.mean()),
        "std_coverage90": float(cov_arr.std()),
        "eval_rollcalls": len(eval_counts),
        "checkpoint_sha256": digest,
        "checkpoint_path": str(ckpt),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="10-seed bootstrap on the count-PMF pin")
    p.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    p.add_argument("--rates-cutoff", default="2023-12-31")
    p.add_argument("--fit-cutoff", default="2024-04-20")
    p.add_argument("--eval-end", default="2024-12-31")
    p.add_argument("--out", default="benchmarks/count_pmf_bayesian.json")
    args = p.parse_args(argv)

    report = run(
        Path(args.corpus),
        rates_cutoff=date.fromisoformat(args.rates_cutoff),
        fit_cutoff=date.fromisoformat(args.fit_cutoff),
        eval_end=date.fromisoformat(args.eval_end),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"count-PMF CRPS {report['mean_crps']:.3f} ± {report['std_crps']:.3f}, "
        f"coverage90 {report['mean_coverage90']:.3f} ± {report['std_coverage90']:.3f} "
        f"({report['n_seeds']} seeds); checkpoint {report['checkpoint_sha256'][:16]}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
