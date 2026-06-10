"""Recalibrate the defection head: temperature / isotonic / conformal, per slice (v5 #6).

The defection head is tuned for ranking (AUC); its raw probabilities can be
miscalibrated, which matters for the forward registry's Brier and for served
probabilities. This fits temperature + isotonic maps on a held-out calibration
split and reports ECE + Brier per slice (overall, defection-prone) before/after,
plus split-conformal coverage (Mondrian per slice, target >= 0.85 on the
defection-prone slice). Vote-only here (the bill-content SOTA recalibration reruns
the same harness once Track A's contract export settles).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.calibration import (
    CalibrationExample,
    apply_temperature,
    expected_calibration_error,
    fit_global_temperature,
    fit_isotonic,
)
from src.prediction.defection import (
    build_party_profiles,
    defected,
    defection_features,
    is_defection_prone,
    split_by_cutoff,
)
from src.prediction.defection_conformal import conformal_coverage
from src.prediction.defection_head import train_defection_head
from src.prediction.vote_record import VoteRecord
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls


def _ece_brier(probs: list[float], labels: list[bool]) -> dict[str, float]:
    n = len(labels)
    if n == 0:
        return {"ece": 0.0, "brier": 0.0, "n": 0}
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(probs, labels)) / n
    return {"ece": expected_calibration_error(probs, labels), "brier": brier, "n": n}


def run(corpus: Path, *, cutoff: date) -> dict[str, Any]:
    records = build_vote_records(load_rich_rollcalls(corpus))
    eval_end = max(r.vote_date for r in records)
    train, eval_records = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
    train = train[-200_000:]
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles)

    half = len(eval_records) // 2
    cal, test = eval_records[:half], eval_records[half:]

    cal_examples = [
        CalibrationExample(
            member_id=r.member, probability_yea=head.probability(defection_features(r, profiles)),
            is_yea=defected(r),
        )
        for r in cal
    ]
    temperature = fit_global_temperature(cal_examples)
    isotonic = fit_isotonic(cal_examples)

    def slice_metrics(records_slice: list[VoteRecord], mapper: str) -> dict[str, float]:
        probs, labels = [], []
        for r in records_slice:
            p = head.probability(defection_features(r, profiles))
            if mapper == "temperature":
                p = apply_temperature(p, temperature)
            elif mapper == "isotonic":
                p = isotonic.calibrate(p)
            probs.append(p)
            labels.append(defected(r))
        return _ece_brier(probs, labels)

    prone_test = [r for r in test if is_defection_prone(r, profiles)]
    report: dict[str, Any] = {
        "cutoff": cutoff.isoformat(),
        "temperature": temperature,
        "calibration": {},
    }
    for mapper in ("raw", "temperature", "isotonic"):
        report["calibration"][mapper] = {
            "overall": slice_metrics(test, mapper),
            "defection_prone": slice_metrics(prone_test, mapper),
        }

    # conformal coverage per slice (marginal + Mondrian); target >= 0.85 on prone.
    cov_marginal = conformal_coverage(
        head, cal, test, profiles, alpha=0.10,
        slicers={"defection_prone": lambda r: is_defection_prone(r, profiles)},
    )
    cov_mondrian = conformal_coverage(
        head, cal, test, profiles, alpha=0.10, mondrian=True,
        slicers={"defection_prone": lambda r: is_defection_prone(r, profiles)},
    )
    report["conformal"] = {
        "marginal": {c.slice_name: c.empirical_coverage for c in cov_marginal},
        "mondrian": {c.slice_name: c.empirical_coverage for c in cov_mondrian},
    }
    prone_cov = report["conformal"]["mondrian"].get("defection_prone", 0.0)
    report["defection_prone_coverage_ok"] = prone_cov >= 0.85

    # pick the calibrator that minimises overall test ECE
    eces = {m: report["calibration"][m]["overall"]["ece"] for m in ("raw", "temperature", "isotonic")}
    report["best_calibrator"] = min(eces, key=lambda m: eces[m])
    report["ece_by_calibrator"] = eces
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Recalibrate the defection head")
    parser.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--out", default="benchmarks/defection_recalibration.json")
    args = parser.parse_args(argv)

    report = run(Path(args.corpus), cutoff=date.fromisoformat(args.cutoff))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"best_calibrator={report['best_calibrator']} "
        f"ECE raw={report['ece_by_calibrator']['raw']:.4f} "
        f"temp={report['ece_by_calibrator']['temperature']:.4f} "
        f"iso={report['ece_by_calibrator']['isotonic']:.4f} | "
        f"prone conformal coverage (Mondrian)={report['conformal']['mondrian'].get('defection_prone', 0):.3f} "
        f"(>=0.85: {report['defection_prone_coverage_ok']})"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
