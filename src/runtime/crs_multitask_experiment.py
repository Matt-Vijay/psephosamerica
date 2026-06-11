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
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles, defected, defection_features
from src.prediction.logistic import auc_of_rows, train_logistic_rows
from src.prediction.vote_record import VoteRecord
from src.runtime.bill_content_experiment import _iter_records, normalize_bill_key
from src.runtime.cross_pressured_experiment import load_rich_rollcalls

_MIN_POLICY_VOTES = 3


def _canonical_to_key(records_path: Path) -> dict[str, str]:
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
    return canon2key


def load_crs_policy_map(records_path: Path, content_path: Path) -> dict[str, str]:
    """bill_key -> CRS policy_area, joining bill_content (canonical_id) via the contract."""
    canon2key = _canonical_to_key(records_path)
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


def load_crs_subjects_map(records_path: Path, content_path: Path) -> dict[str, tuple[str, ...]]:
    """bill_key -> CRS subjects tuple (finer-grained than policy_area)."""
    canon2key = _canonical_to_key(records_path)
    out: dict[str, tuple[str, ...]] = {}
    if content_path.exists():
        with content_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                key = canon2key.get(str(row.get("canonical_id", "")))
                subjects = row.get("subjects") or []
                if key and subjects:
                    out[key] = tuple(str(s) for s in subjects)
    return out


def run(
    rich_corpus: Path, *, records_path: Path, content_path: Path, cutoff: date
) -> dict[str, Any]:
    policy_map = load_crs_policy_map(records_path, content_path)
    subjects_map = load_crs_subjects_map(records_path, content_path)
    rolls = load_rich_rollcalls(rich_corpus)

    from src.runtime.bill_content_experiment import build_linked_votes

    # (record, policy_area, subjects) with CRS attached where available
    linked: list[tuple[VoteRecord, str | None, tuple[str, ...]]] = []
    for lv in build_linked_votes(rolls, None):
        key = normalize_bill_key(lv.bill_id)
        linked.append(
            (
                lv.record,
                policy_map.get(key) if key else None,
                subjects_map.get(key, ()) if key else (),
            )
        )

    train = [t for t in linked if t[0].vote_date <= cutoff]
    eval_records = [t for t in linked if t[0].vote_date > cutoff]
    profiles = build_party_profiles([t[0] for t in train])

    # per-(member, policy/subject) pre-cutoff defection rate + member overall rate
    mp: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    ms: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    mo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, pa, subs in train:
        d = int(defected(r))
        mo[r.member][0] += d
        mo[r.member][1] += 1
        if pa:
            mp[(r.member, pa)][0] += d
            mp[(r.member, pa)][1] += 1
        for s in subs:
            ms[(r.member, s)][0] += d
            ms[(r.member, s)][1] += 1

    def _overall(member: str) -> float:
        return mo[member][0] / mo[member][1] if mo.get(member, [0, 0])[1] else 0.1

    def policy_feat(r: VoteRecord, pa: str | None) -> float:
        if pa and mp.get((r.member, pa), [0, 0])[1] >= _MIN_POLICY_VOTES:
            return mp[(r.member, pa)][0] / mp[(r.member, pa)][1] - _overall(r.member)
        return 0.0

    def subject_feat(r: VoteRecord, subs: tuple[str, ...]) -> float:
        devs = [
            ms[(r.member, s)][0] / ms[(r.member, s)][1] - _overall(r.member)
            for s in subs
            if ms.get((r.member, s), [0, 0])[1] >= _MIN_POLICY_VOTES
        ]
        return max(devs) if devs else 0.0  # member's most defection-prone subject on this bill

    base_names = ("loyalty_gap", "sector_divergence")
    arms: dict[
        str,
        tuple[
            tuple[str, ...], Callable[[VoteRecord, str | None, tuple[str, ...]], dict[str, float]]
        ],
    ] = {
        "base": (base_names, lambda r, pa, subs: {}),
        "policy": (
            (*base_names, "crs_policy_divergence"),
            lambda r, pa, subs: {"crs_policy_divergence": policy_feat(r, pa)},
        ),
        "policy_subjects": (
            (*base_names, "crs_policy_divergence", "crs_subject_divergence"),
            lambda r, pa, subs: {
                "crs_policy_divergence": policy_feat(r, pa),
                "crs_subject_divergence": subject_feat(r, subs),
            },
        ),
    }
    results: dict[str, Any] = {}
    base_auc = 0.0
    for name, (names, extra) in arms.items():
        tr = [
            ({**defection_features(r, profiles), **extra(r, pa, subs)}, defected(r))
            for r, pa, subs in train
        ]
        ev = [
            ({**defection_features(r, profiles), **extra(r, pa, subs)}, defected(r))
            for r, pa, subs in eval_records
        ]
        i, c = train_logistic_rows(tr, names)
        auc = auc_of_rows(i, c, names, ev)
        if name == "base":
            base_auc = auc
        results[name] = {
            "auc": auc,
            "delta_vs_base": auc - base_auc,
            "coef": {k: c.get(k, 0.0) for k in names if k not in base_names},
        }

    covered = sum(1 for _r, pa, _s in eval_records if pa)
    best = max(results, key=lambda a: results[a]["auc"])
    return {
        "cutoff": cutoff.isoformat(),
        "eval_pairs": len(eval_records),
        "crs_policy_coverage": covered / len(eval_records) if eval_records else 0.0,
        "base_auc": base_auc,
        "arms": results,
        "best_arm": best,
        "best_auc": results[best]["auc"],
        "delta_auc": results[best]["auc"] - base_auc,
        "distinct_policy_areas": len({pa for _r, pa, _s in linked if pa}),
        "distinct_subjects": len({s for _r, _p, subs in linked for s in subs}),
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

    report = run(
        Path(args.rich),
        records_path=Path(args.records),
        content_path=Path(args.content),
        cutoff=date.fromisoformat(args.cutoff),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"CRS coverage={report['crs_policy_coverage']:.2f} base_auc={report['base_auc']:.4f}")
    for name, arm in report["arms"].items():
        print(f"  {name:16s} auc={arm['auc']:.4f} Δvs_base={arm['delta_vs_base']:+.4f}")
    print(
        f"best={report['best_arm']} ΔAUC={report['delta_auc']:+.4f} "
        f"({report['distinct_policy_areas']} policy areas, {report['distinct_subjects']} subjects)"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
