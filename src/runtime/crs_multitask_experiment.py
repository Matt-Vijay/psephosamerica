"""Multi-task v2 on the now-real CRS policy-area edges (v5 #7).

v4's multi-task gave ΔAUC=0 because the auxiliary signals weren't in the contract.
Track A's govinfo sidecar now ships **CRS policy_area + subjects** per bill
(`bill_content.jsonl`), joined to votes via the contract's canonical_id →
external_id map. This adds a *CRS-authoritative* policy-area auxiliary to the
defection head -- the member's pre-cutoff defection rate within the bill's CRS
policy area (analogous to the keyword ``sector_divergence`` but from CRS's own
taxonomy) -- and reports the transfer ΔAUC honestly, even if 0.

Runs on the 113th (where CRS coverage has landed: 906/1204 roll-calls); strict
cutoff (the policy-area defection rates come only from pre-cutoff votes).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles, defected, defection_features, ranking_metrics
from src.prediction.vote_record import VoteRecord
from src.runtime.bill_content_experiment import _iter_records, normalize_bill_key
from src.runtime.cross_pressured_experiment import load_rich_rollcalls

_LOGIT_CLAMP = 40.0
_MIN_POLICY_VOTES = 3


def load_crs_policy_map(records_path: Path, content_path: Path) -> dict[str, str]:
    """bill_key -> CRS policy_area, joining bill_content (canonical_id) via the contract."""
    canon2key: dict[str, str] = {}
    for r in _iter_records(records_path):
        if r.get("entity_type") != "bill":
            continue
        cid = str(r.get("canonical_id", ""))
        for ext in r.get("external_ids") or []:
            key = normalize_bill_key(str(ext))
            if key:
                canon2key[cid] = key
                break
    out: dict[str, str] = {}
    if content_path.exists():
        with content_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                key = canon2key.get(str(row.get("canonical_id", "")))
                pa = row.get("policy_area")
                if key and pa:
                    out[key] = str(pa)
    return out


def _train(rows: list[tuple[dict[str, float], bool]], names: tuple[str, ...]) -> tuple[float, dict[str, float]]:
    if not rows:
        return 0.0, {n: 0.0 for n in names}
    rate = min(0.95, max(0.05, sum(1 for _f, y in rows if y) / len(rows)))
    intercept = math.log(rate / (1.0 - rate))
    coef = {n: 0.0 for n in names}
    scale = 1.0 / len(rows)
    for _ in range(300):
        d_int = 0.0
        d_coef = {n: 0.0 for n in names}
        for f, y in rows:
            raw = intercept + sum(coef[n] * f.get(n, 0.0) for n in names)
            pred = 1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, raw))))
            err = pred - (1.0 if y else 0.0)
            d_int += err
            for n in names:
                d_coef[n] += err * f.get(n, 0.0)
        intercept -= 0.3 * d_int * scale
        for n in names:
            coef[n] -= 0.3 * (d_coef[n] * scale + 0.01 * coef[n])
    return intercept, coef


def _auc(intercept: float, coef: dict[str, float], names: tuple[str, ...], rows: list[tuple[dict[str, float], bool]]) -> float:
    scores = [
        1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, intercept + sum(coef[n] * f.get(n, 0.0) for n in names)))))
        for f, _y in rows
    ]
    return ranking_metrics(scores, [y for _f, y in rows]).auc


def run(rich_corpus: Path, *, records_path: Path, content_path: Path, cutoff: date) -> dict[str, Any]:
    policy_map = load_crs_policy_map(records_path, content_path)
    rolls = load_rich_rollcalls(rich_corpus)

    # (record, bill_key) with CRS policy area attached where available
    linked: list[tuple[VoteRecord, str | None]] = []
    from src.runtime.bill_content_experiment import build_linked_votes

    for lv in build_linked_votes(rolls, None):
        key = normalize_bill_key(lv.bill_id)
        linked.append((lv.record, policy_map.get(key) if key else None))

    train = [(r, pa) for r, pa in linked if r.vote_date <= cutoff]
    eval_records = [(r, pa) for r, pa in linked if r.vote_date > cutoff]
    profiles = build_party_profiles([r for r, _ in train])

    # per-(member, CRS policy_area) pre-cutoff defection rate + member overall rate
    mp: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    mo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, pa in train:
        d = int(defected(r))
        mo[r.member][0] += d
        mo[r.member][1] += 1
        if pa:
            mp[(r.member, pa)][0] += d
            mp[(r.member, pa)][1] += 1

    def crs_feature(r: VoteRecord, pa: str | None) -> float:
        overall = mo[r.member][0] / mo[r.member][1] if mo.get(r.member, [0, 0])[1] else 0.1
        if pa and mp.get((r.member, pa), [0, 0])[1] >= _MIN_POLICY_VOTES:
            rate = mp[(r.member, pa)][0] / mp[(r.member, pa)][1]
            return rate - overall  # deviation: defects MORE than usual on this CRS area
        return 0.0

    base_names = ("loyalty_gap", "sector_divergence")
    crs_names = (*base_names, "crs_policy_divergence")
    base_train = [(defection_features(r, profiles), defected(r)) for r, _ in train]
    base_eval = [(defection_features(r, profiles), defected(r)) for r, _ in eval_records]
    b_int, b_coef = _train(base_train, base_names)
    base_auc = _auc(b_int, b_coef, base_names, base_eval)

    crs_train = [({**defection_features(r, profiles), "crs_policy_divergence": crs_feature(r, pa)}, defected(r)) for r, pa in train]
    crs_eval = [({**defection_features(r, profiles), "crs_policy_divergence": crs_feature(r, pa)}, defected(r)) for r, pa in eval_records]
    c_int, c_coef = _train(crs_train, crs_names)
    crs_auc = _auc(c_int, c_coef, crs_names, crs_eval)

    covered = sum(1 for _r, pa in eval_records if pa)
    return {
        "cutoff": cutoff.isoformat(),
        "eval_pairs": len(eval_records),
        "crs_policy_coverage": covered / len(eval_records) if eval_records else 0.0,
        "base_auc": base_auc,
        "crs_multitask_auc": crs_auc,
        "delta_auc": crs_auc - base_auc,
        "crs_coefficient": c_coef.get("crs_policy_divergence", 0.0),
        "distinct_policy_areas": len({pa for _r, pa in linked if pa}),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="CRS policy-area multi-task")
    parser.add_argument("--rich", default="data/real/house_113_rich.jsonl")
    parser.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    parser.add_argument("--content", default="data/exports/govinfo_bills/bill_content.jsonl")
    parser.add_argument("--cutoff", default="2014-01-01")
    parser.add_argument("--out", default="benchmarks/crs_multitask.json")
    args = parser.parse_args(argv)

    report = run(Path(args.rich), records_path=Path(args.records), content_path=Path(args.content), cutoff=date.fromisoformat(args.cutoff))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"CRS coverage={report['crs_policy_coverage']:.2f} "
        f"base_auc={report['base_auc']:.4f} +crs={report['crs_multitask_auc']:.4f} "
        f"ΔAUC={report['delta_auc']:+.4f} (coef={report['crs_coefficient']:+.3f}, "
        f"{report['distinct_policy_areas']} policy areas)"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
