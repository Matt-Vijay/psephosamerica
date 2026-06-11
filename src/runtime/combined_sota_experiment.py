"""Do bill-RAG and CRS policy-area stack? — the combined 118th SOTA (v5 next-gap).

Two independent real wins this session: dense bill-RAG + projection (+0.064 over
base, the 0.7707 SOTA) and the CRS-policy-area defection signal (+0.071 on the
113th). Now that the contract has settled with full CRS coverage on the 118th, this
asks whether they are complementary — base vs bill-RAG+projection (the SOTA) vs
bill-RAG+projection+CRS — on the 118th pin. If +CRS stacks on top of bill-RAG it is
a new SOTA. Strict cutoff; every signal pre-cutoff.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles, defected, defection_features
from src.prediction.logistic import auc_of_rows, train_logistic_rows
from src.runtime.bill_content_experiment import (
    _MemberBillStore,
    _projection,
    build_linked_votes,
    load_bill_embedding_map,
    normalize_bill_key,
)
from src.runtime.crs_multitask_experiment import load_crs_policy_map
from src.runtime.cross_pressured_experiment import load_rich_rollcalls

_MIN_POLICY_VOTES = 3


def run(rich: Path, records: Path, content: Path, *, cutoff: date, eval_end: date, k: int = 32, projection_dim: int = 16, max_train: int = 200_000) -> dict[str, Any]:
    emap = load_bill_embedding_map(records, field="dossier_embedding")
    policy_map = load_crs_policy_map(records, content)
    linked = build_linked_votes(load_rich_rollcalls(rich), emap)
    train = [lv for lv in linked if lv.record.vote_date <= cutoff][-max_train:]
    eval_lv = [lv for lv in linked if cutoff < lv.record.vote_date <= eval_end]
    profiles = build_party_profiles([lv.record for lv in train])

    stores: dict[str, _MemberBillStore] = defaultdict(_MemberBillStore)
    mp: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    mo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for lv in train:
        if lv.bill_embedding is not None:
            stores[lv.record.member].add(lv.bill_embedding, defected(lv.record))
        d = int(defected(lv.record))
        mo[lv.record.member][0] += d
        mo[lv.record.member][1] += 1
        pa = policy_map.get(normalize_bill_key(lv.bill_id) or "")
        if pa:
            mp[(lv.record.member, pa)][0] += d
            mp[(lv.record.member, pa)][1] += 1

    sample = next((lv.bill_embedding for lv in train if lv.bill_embedding is not None), None)
    proj = _projection(sample.shape[0], projection_dim) if sample is not None else None
    proj_names = tuple(f"bill_proj_{i}" for i in range(projection_dim)) if proj is not None else ()

    def crs_feat(lv: Any) -> float:
        pa = policy_map.get(normalize_bill_key(lv.bill_id) or "")
        overall = mo[lv.record.member][0] / mo[lv.record.member][1] if mo.get(lv.record.member, [0, 0])[1] else 0.1
        if pa and mp.get((lv.record.member, pa), [0, 0])[1] >= _MIN_POLICY_VOTES:
            return mp[(lv.record.member, pa)][0] / mp[(lv.record.member, pa)][1] - overall
        return 0.0

    def feats(lv: Any) -> dict[str, float]:
        base = dict(defection_features(lv.record, profiles))
        st = stores.get(lv.record.member)
        base["bill_rag_signal"] = st.signals(lv.bill_embedding, (k,))[k] if (st and lv.bill_embedding is not None) else 0.0
        if proj is not None and lv.bill_embedding is not None:
            pv = lv.bill_embedding @ proj
            for i, n in enumerate(proj_names):
                base[n] = float(pv[i])
        base["crs_policy_divergence"] = crs_feat(lv)
        return base

    tr = [(feats(lv), defected(lv.record)) for lv in train]
    ev = [(feats(lv), defected(lv.record)) for lv in eval_lv]

    arms = {
        "base": ("loyalty_gap", "sector_divergence"),
        "bill_rag": ("loyalty_gap", "sector_divergence", "bill_rag_signal", *proj_names),
        "bill_rag_crs": ("loyalty_gap", "sector_divergence", "bill_rag_signal", *proj_names, "crs_policy_divergence"),
        "crs_only": ("loyalty_gap", "sector_divergence", "crs_policy_divergence"),
    }
    results: dict[str, float] = {}
    for name, names in arms.items():
        i, c = train_logistic_rows(tr, names)
        results[name] = auc_of_rows(i, c, names, ev)
    best = max(results, key=lambda a: results[a])
    return {
        "cutoff": cutoff.isoformat(), "eval_pairs": len(ev), "k": k,
        "aucs": results,
        "bill_rag_vs_base": results["bill_rag"] - results["base"],
        "crs_only_vs_base": results["crs_only"] - results["base"],
        "combined_vs_bill_rag": results["bill_rag_crs"] - results["bill_rag"],
        "best_arm": best, "best_auc": results[best],
        "stacks": results["bill_rag_crs"] > max(results["bill_rag"], results["crs_only"]) + 0.002,
        "pin": 0.7707, "beats_pin": results[best] > 0.7707 + 0.005,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Combined bill-RAG + CRS SOTA experiment")
    p.add_argument("--rich", default="data/real/house_118_rich.jsonl")
    p.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    p.add_argument("--content", default="data/exports/govinfo_bills/bill_content.jsonl")
    p.add_argument("--cutoff", default="2024-04-20")
    p.add_argument("--eval-end", default="2024-12-31")
    p.add_argument("--out", default="benchmarks/combined_sota.json")
    args = p.parse_args(argv)

    r = run(Path(args.rich), Path(args.records), Path(args.content), cutoff=date.fromisoformat(args.cutoff), eval_end=date.fromisoformat(args.eval_end))
    Path(args.out).write_text(json.dumps(r, indent=2), encoding="utf-8")
    for name, auc in r["aucs"].items():
        print(f"  {name:14s} {auc:.4f}")
    print(f"combined vs bill_rag: {r['combined_vs_bill_rag']:+.4f} | stacks={r['stacks']} | best={r['best_arm']} {r['best_auc']:.4f} | beats 0.7707 pin: {r['beats_pin']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
