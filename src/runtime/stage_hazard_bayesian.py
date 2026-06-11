"""10-seed bootstrap uncertainty on the stage-hazard pin (v7 #7).

Resamples the <=117th training bills with replacement ten times, refits the
discrete-time hazard each time, and reports the 118th C-index mean +/- std plus
the per-seed spread -- is the 0.7885 pin a property of the data or of one
draw? Content-addressed checkpoint of the seed coefficients, matching the
bill-content Bayesian convention.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.stage_hazard import concordance_index, fit_hazard
from src.runtime.bill_journey import JourneyFeaturizer, load_bill_journeys, load_floor_events
from src.runtime.stage_hazard_experiment import (
    _HORIZON_DAYS,
    _HOUSE_CORPORA,
    _SENATE_CORPORA,
    _arrays,
)


def run(sidecar: Path, *, n_seeds: int = 10) -> dict[str, Any]:
    house = load_floor_events([Path(p) for p in _HOUSE_CORPORA])
    senate = load_floor_events([Path(p) for p in _SENATE_CORPORA])
    journeys = load_bill_journeys(sidecar, house, senate)
    train = [j for j in journeys if j.congress <= 117]
    eval_set = [j for j in journeys if j.congress == 118]
    featurizer = JourneyFeaturizer.fit(train)
    x_train = featurizer.transform(train)
    x_eval = featurizer.transform(eval_set)
    dur_train, ev_train = _arrays(train)
    dur_eval, ev_eval = _arrays(eval_set)

    n = x_train.shape[0]
    cs: list[float] = []
    coefs: list[dict[str, float]] = []
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=n)
        model = fit_hazard(x_train[idx], dur_train[idx], ev_train[idx], featurizer.feature_names)
        p = model.predict_event_by(x_eval, _HORIZON_DAYS)
        cs.append(concordance_index(p, dur_eval, ev_eval))
        coefs.append(
            {name: float(c) for name, c in zip(featurizer.feature_names, model.coefficients)}
        )
    arr = np.asarray(cs)

    payload = {"model": "stage_hazard", "seeds": coefs}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    ckpt = Path("checkpoints") / "sha256" / digest[:2] / digest[2:4] / f"{digest}.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "n_seeds": n_seeds,
        "mean_c_index": float(arr.mean()),
        "std_c_index": float(arr.std()),
        "per_seed_c_index": cs,
        "eval_bills": len(eval_set),
        "checkpoint_sha256": digest,
        "checkpoint_path": str(ckpt),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="10-seed bootstrap on the stage-hazard pin")
    p.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_full.jsonl")
    p.add_argument("--out", default="benchmarks/stage_hazard_bayesian.json")
    args = p.parse_args(argv)

    report = run(Path(args.sidecar))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"stage-hazard C-index {report['mean_c_index']:.4f} ± {report['std_c_index']:.4f} "
        f"({report['n_seeds']} bootstrap seeds); checkpoint {report['checkpoint_sha256'][:16]}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
