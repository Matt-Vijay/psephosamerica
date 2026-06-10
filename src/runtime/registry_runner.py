"""Freeze + score the forward prediction registry on real 119th-House data (v5 #2).

``freeze``: trains the defection head strictly on 119th votes at/before the cutoff
and emits content-hashed predictions for the post-cutoff target window -- the
pre-registration that is committed *before* scoring. ``score``: joins realized
outcomes and reports Brier / accuracy / AUC, preserving the frozen hash.

The committed frozen file is the credibility artifact: its sha256 + git timestamp
prove the predictions predate their outcomes. The same loop runs on truly-future
windows (cutoff = latest data, target = upcoming floor votes) as new votes land.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.prediction.prediction_registry import (
    RegistryFile,
    freeze_registry,
    score_registry,
)
from src.prediction.vote_record import VoteRecord
from src.runtime.flat_corpus import build_linked_flat_votes


def _load_votes(corpus: Path, *, rich: bool) -> list[tuple[VoteRecord, str]]:
    """Load (record, bill_id) pairs. Rich corpora carry real sectors (stronger head)."""
    if not rich:
        return build_linked_flat_votes(corpus)
    from src.runtime.bill_content_experiment import build_linked_votes
    from src.runtime.cross_pressured_experiment import load_rich_rollcalls

    return [
        (lv.record, lv.bill_id)
        for lv in build_linked_votes(load_rich_rollcalls(corpus), None)
    ]


def _load(path: Path) -> RegistryFile:
    from src.prediction.prediction_registry import Citation, FrozenPrediction

    raw = json.loads(path.read_text(encoding="utf-8"))
    preds = [
        FrozenPrediction(
            member=p["member"], party=p["party"], state=p["state"], bill_id=p["bill_id"],
            target_vote_date=p["target_vote_date"], p_defect=p["p_defect"],
            predicted_defect=p["predicted_defect"], counterfactual=p["counterfactual"],
            citations=[Citation(**c) for c in p["citations"]],
            actual_defect=p.get("actual_defect"),
        )
        for p in raw["predictions"]
    ]
    return RegistryFile(
        frozen_at_cutoff=raw["frozen_at_cutoff"], target_window_end=raw["target_window_end"],
        model_name=raw["model_name"], content_sha256=raw["content_sha256"],
        predictions=preds, scored=raw.get("scored", False), metrics=raw.get("metrics", {}),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Forward prediction registry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freeze")
    f.add_argument("--corpus", default="data/real/house_119_rich.jsonl")
    f.add_argument("--rich", action="store_true", default=True)
    f.add_argument("--flat", dest="rich", action="store_false")
    f.add_argument("--cutoff", default="2025-09-30")
    f.add_argument("--target-end", default="2025-12-18")
    f.add_argument("--max", type=int, default=200)
    f.add_argument("--out", default="benchmarks/prediction_registry.json")

    s = sub.add_parser("score")
    s.add_argument("--corpus", default="data/real/house_119_rich.jsonl")
    s.add_argument("--rich", action="store_true", default=True)
    s.add_argument("--flat", dest="rich", action="store_false")
    s.add_argument("--registry", default="benchmarks/prediction_registry.json")
    s.add_argument("--out", default="benchmarks/prediction_registry_scored.json")

    args = parser.parse_args(argv)

    if args.cmd == "freeze":
        votes = _load_votes(Path(args.corpus), rich=args.rich)
        reg = freeze_registry(
            votes, cutoff=date.fromisoformat(args.cutoff),
            target_end=date.fromisoformat(args.target_end), max_predictions=args.max,
        )
        Path(args.out).write_text(json.dumps(reg.to_dict(), indent=2), encoding="utf-8")
        print(
            f"FROZEN {len(reg.predictions)} predictions, cutoff {reg.frozen_at_cutoff} -> "
            f"{reg.target_window_end}, sha256 {reg.content_sha256[:16]}; wrote {args.out}"
        )
        return 0

    votes = _load_votes(Path(args.corpus), rich=args.rich)
    reg = _load(Path(args.registry))
    scored = score_registry(reg, votes)
    Path(args.out).write_text(json.dumps(scored.to_dict(), indent=2), encoding="utf-8")
    m = scored.metrics
    print(
        f"SCORED: resolved={int(m.get('resolved', 0))} pending={int(m.get('pending', 0))} "
        f"brier={m.get('brier', float('nan')):.4f} acc={m.get('accuracy', float('nan')):.4f} "
        f"auc={m.get('auc', float('nan')):.4f} base_rate={m.get('base_rate', float('nan')):.4f}; "
        f"hash preserved={scored.content_sha256 == reg.content_sha256}; wrote {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
