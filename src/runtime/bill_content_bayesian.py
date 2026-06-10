"""10-seed Bayesian uncertainty on the bill-content SOTA + checkpoint (v5 #9).

The bill-content model (loyalty + sector + dense bill-RAG + projection, the 0.7707
118th SOTA) is deterministic given its data; epistemic uncertainty comes from data
resampling. The expensive part is the RAG retrieval, so we compute the SOTA feature
rows ONCE (at the pinned best k), then bootstrap-resample the training rows 10× and
refit the (cheap) logistic each time, reporting AUC mean ± std. The ensemble is
content-addressed and pinned. Now unblocked: Track A's contract export settled at
full coverage.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.defection import build_party_profiles, defected, defection_features, ranking_metrics
from src.runtime.bill_content_experiment import (
    _MemberBillStore,
    _projection,
    build_linked_votes,
    load_bill_embedding_map,
)
from src.runtime.cross_pressured_experiment import load_rich_rollcalls

_LOGIT_CLAMP = 40.0
_FEATURES = ("loyalty_gap", "sector_divergence", "bill_rag_signal")


def _fit(rows: list[tuple[dict[str, float], bool]], names: tuple[str, ...], idx: np.ndarray) -> tuple[float, dict[str, float]]:
    sample = [rows[int(i)] for i in idx]
    rate = min(0.95, max(0.05, sum(1 for _f, y in sample if y) / len(sample)))
    intercept = math.log(rate / (1.0 - rate))
    coef = {n: 0.0 for n in names}
    scale = 1.0 / len(sample)
    for _ in range(300):
        d_int = 0.0
        d_coef = {n: 0.0 for n in names}
        for f, y in sample:
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
    scores = [1.0 / (1.0 + math.exp(-max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, intercept + sum(coef[n] * f.get(n, 0.0) for n in names))))) for f, _y in rows]
    return ranking_metrics(scores, [y for _f, y in rows]).auc


def run(rich: Path, records: Path, *, cutoff: date, eval_end: date, k: int = 32, projection_dim: int = 16, n_seeds: int = 10, max_train: int = 200_000) -> dict[str, Any]:
    emap = load_bill_embedding_map(records, field="dossier_embedding")
    linked = build_linked_votes(load_rich_rollcalls(rich), emap)
    train = [lv for lv in linked if lv.record.vote_date <= cutoff][-max_train:]
    eval_lv = [lv for lv in linked if cutoff < lv.record.vote_date <= eval_end]
    profiles = build_party_profiles([lv.record for lv in train])

    stores: dict[str, _MemberBillStore] = defaultdict(_MemberBillStore)
    for lv in train:
        if lv.bill_embedding is not None:
            stores[lv.record.member].add(lv.bill_embedding, defected(lv.record))
    sample = next((lv.bill_embedding for lv in train if lv.bill_embedding is not None), None)
    proj = _projection(sample.shape[0], projection_dim) if sample is not None else None
    proj_names = tuple(f"bill_proj_{i}" for i in range(projection_dim)) if proj is not None else ()
    names = (*_FEATURES, *proj_names)

    def feats(lv: Any) -> dict[str, float]:
        base = dict(defection_features(lv.record, profiles))
        st = stores.get(lv.record.member)
        base["bill_rag_signal"] = st.signals(lv.bill_embedding, (k,))[k] if (st and lv.bill_embedding is not None) else 0.0
        if proj is not None and lv.bill_embedding is not None:
            pv = lv.bill_embedding @ proj
            for i, n in enumerate(proj_names):
                base[n] = float(pv[i])
        return base

    train_rows = [(feats(lv), defected(lv.record)) for lv in train]
    eval_rows = [(feats(lv), defected(lv.record)) for lv in eval_lv]

    n = len(train_rows)
    aucs: list[float] = []
    coefs: list[dict[str, float]] = []
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=n)
        i, c = _fit(train_rows, names, idx)
        aucs.append(_auc(i, c, names, eval_rows))
        coefs.append({"intercept": i, **c})
    arr = np.asarray(aucs)

    payload = {"model": "bill_content_sota", "k": k, "projection_dim": projection_dim, "seeds": coefs}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    ckpt = Path("checkpoints") / "sha256" / digest[:2] / digest[2:4] / f"{digest}.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "cutoff": cutoff.isoformat(), "k": k, "n_seeds": n_seeds,
        "mean_auc": float(arr.mean()), "std_auc": float(arr.std()),
        "per_seed_auc": aucs, "eval_pairs": len(eval_rows),
        "checkpoint_sha256": digest, "checkpoint_path": str(ckpt),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="10-seed Bayesian on the bill-content SOTA")
    p.add_argument("--rich", default="data/real/house_118_rich.jsonl")
    p.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    p.add_argument("--cutoff", default="2024-04-20")
    p.add_argument("--eval-end", default="2024-12-31")
    p.add_argument("--out", default="benchmarks/bill_content_bayesian.json")
    args = p.parse_args(argv)

    report = run(Path(args.rich), Path(args.records), cutoff=date.fromisoformat(args.cutoff), eval_end=date.fromisoformat(args.eval_end))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"bill-content SOTA AUC {report['mean_auc']:.4f} ± {report['std_auc']:.4f} (10 seeds); checkpoint {report['checkpoint_sha256'][:16]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
