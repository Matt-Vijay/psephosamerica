"""Time-sliced validation of the stage-hazard model (v7 #1 runner).

Trains the discrete-time floor-vote hazard on bills introduced in congresses
<= 117 and evaluates on the 118th -- a pure forward slice (every 118th bill's
introduction postdates the whole training window). Reports Harrell's C-index of
the predicted one-year floor probability against observed (duration, event),
plus calibration (Brier / ECE / reliability deciles) of P(floor vote within 365
days) among 118th bills fully observed to the horizon, against an
intercept-only (baseline-hazard) model -- the covariates must beat it honestly.
Pins the result in benchmarks/stage_hazard_baseline.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.stage_hazard import (
    calibration_at_horizon,
    concordance_index,
    fit_hazard,
)
from src.runtime.bill_journey import (
    BillJourney,
    JourneyFeaturizer,
    load_bill_journeys,
    load_floor_events,
)

_HORIZON_DAYS = 365.0

_HOUSE_CORPORA = [
    "data/real/house_113_rich.jsonl",
    "data/real/house_115_117.jsonl",
    "data/real/house_118_rich.jsonl",
    "data/real/house_119_rich.jsonl",
]
_SENATE_CORPORA = ["data/real/senate_113_119_rich.jsonl"]


def _arrays(journeys: list[BillJourney]) -> tuple[np.ndarray, np.ndarray]:
    duration = np.asarray([j.duration_days for j in journeys], dtype=np.float64)
    event = np.asarray([j.event_observed for j in journeys], dtype=bool)
    return duration, event


def run(
    sidecar: Path,
    *,
    train_max_congress: int = 117,
    eval_congress: int = 118,
    house_corpora: list[Path] | None = None,
    senate_corpora: list[Path] | None = None,
) -> dict[str, Any]:
    house = load_floor_events(house_corpora or [Path(p) for p in _HOUSE_CORPORA])
    senate = load_floor_events(senate_corpora or [Path(p) for p in _SENATE_CORPORA])
    journeys = load_bill_journeys(sidecar, house, senate)
    train = [j for j in journeys if j.congress <= train_max_congress]
    eval_set = [j for j in journeys if j.congress == eval_congress]
    if not train or not eval_set:
        return {"error": "empty train or eval slice", "train": len(train), "eval": len(eval_set)}

    featurizer = JourneyFeaturizer.fit(train)
    x_train = featurizer.transform(train)
    x_eval = featurizer.transform(eval_set)
    dur_train, ev_train = _arrays(train)
    dur_eval, ev_eval = _arrays(eval_set)

    model = fit_hazard(x_train, dur_train, ev_train, featurizer.feature_names)
    baseline = fit_hazard(np.zeros((x_train.shape[0], 1)), dur_train, ev_train, ("intercept_only",))

    p_eval = model.predict_event_by(x_eval, _HORIZON_DAYS)
    p_base = baseline.predict_event_by(np.zeros((x_eval.shape[0], 1)), _HORIZON_DAYS)

    c_model = concordance_index(p_eval, dur_eval, ev_eval)
    c_base = concordance_index(p_base, dur_eval, ev_eval)
    cal = calibration_at_horizon(p_eval, dur_eval, ev_eval, horizon_days=_HORIZON_DAYS)
    cal_base = calibration_at_horizon(p_base, dur_eval, ev_eval, horizon_days=_HORIZON_DAYS)

    coef = dict(
        zip(
            featurizer.feature_names, (round(float(c), 4) for c in model.coefficients), strict=False
        )
    )
    top = sorted(coef.items(), key=lambda kv: -abs(kv[1]))[:12]
    return {
        "event": "first recorded floor roll-call in origin chamber",
        "train_congresses": f"<= {train_max_congress}",
        "eval_congress": eval_congress,
        "train_bills": len(train),
        "eval_bills": len(eval_set),
        "train_event_rate": float(ev_train.mean()),
        "eval_event_rate": float(ev_eval.mean()),
        "horizon_days": _HORIZON_DAYS,
        "c_index": c_model,
        "c_index_intercept_only": c_base,
        "c_index_lift": c_model - c_base,
        "calibration_365": cal,
        "calibration_365_intercept_only": {
            "brier": cal_base["brier"],
            "ece": cal_base["ece"],
        },
        "top_coefficients": dict(top),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Stage-hazard time-sliced validation")
    p.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_full.jsonl")
    p.add_argument("--out", default="benchmarks/stage_hazard.json")
    p.add_argument("--pin", default="benchmarks/stage_hazard_baseline.json")
    args = p.parse_args(argv)

    report = run(Path(args.sidecar))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if "error" not in report:
        pin = {
            "slice": "floor-vote-hazard-118",
            "c_index": report["c_index"],
            "c_index_intercept_only": report["c_index_intercept_only"],
            "brier_365": report["calibration_365"]["brier"],
            "ece_365": report["calibration_365"]["ece"],
            "eval_bills": report["eval_bills"],
            "tolerance": 0.005,
        }
        Path(args.pin).write_text(json.dumps(pin, indent=2), encoding="utf-8")
        print(
            f"C-index {report['c_index']:.4f} (intercept-only {report['c_index_intercept_only']:.4f}, "
            f"lift {report['c_index_lift']:+.4f}); Brier@365 {report['calibration_365']['brier']:.4f}, "
            f"ECE@365 {report['calibration_365']['ece']:.4f} on {report['eval_bills']} 118th bills"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
