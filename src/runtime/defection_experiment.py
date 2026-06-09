"""Run the honest defection experiment on real roll-call data.

Ties together v4's honest reframe end to end on a rich roll-call corpus:

* the ex-ante slice prevalence + enrichment (#1),
* the defection-ranking head's AUC + precision@k, per congress (#3),
* the bill-encoder / RAG before-vs-after on the *honest* slice (#2),

all under a strict no-leakage cutoff. The defaults read the committed rich
118th-House corpus; ``run`` is parameterised so the same harness runs on any
congress (and on the dense corpus once Track A's bills land).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.defection import (
    build_party_profiles,
    slice_prevalence,
    split_by_cutoff,
)
from src.prediction.defection_head import (
    evaluate_defection_head,
    train_defection_head,
)
from src.runtime.cross_pressured_experiment import (
    VoteRecord,
    build_vote_records,
    load_rich_rollcalls,
)
from src.runtime.defection_rag import run_rag_before_after


def _congress_of(rollcall: dict[str, Any]) -> int:
    return int(rollcall.get("congress", 0))


def split_records_by_congress(
    rollcalls: list[dict[str, Any]],
) -> dict[int, list[VoteRecord]]:
    """Build vote records grouped by congress for per-congress reporting."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rollcall in rollcalls:
        grouped[_congress_of(rollcall)].append(rollcall)
    return {congress: build_vote_records(rcs) for congress, rcs in grouped.items()}


def _cutoff_for(records: list[VoteRecord], train_fraction: float) -> tuple[date, date]:
    """Pick a chronological cutoff at ``train_fraction`` of the date range."""
    dates = sorted({r.vote_date for r in records})
    if not dates:
        today = date(2024, 1, 1)
        return today, today
    index = max(0, min(len(dates) - 1, int(len(dates) * train_fraction)))
    return dates[index], dates[-1]


def run(
    corpus: Path,
    *,
    train_fraction: float = 0.7,
    tau: float = 0.25,
    k_values: tuple[int, ...] = (4, 8, 16, 32),
    max_train: int | None = 200_000,
) -> dict[str, Any]:
    """Run the full honest defection experiment and return a JSON-able report."""
    rollcalls = load_rich_rollcalls(corpus)
    all_records = build_vote_records(rollcalls)
    cutoff, eval_end = _cutoff_for(all_records, train_fraction)
    train, eval_records = split_by_cutoff(all_records, cutoff=cutoff, eval_end=eval_end)
    if max_train is not None and len(train) > max_train:
        train = train[-max_train:]
    profiles = build_party_profiles(train)

    prevalence = slice_prevalence(eval_records, profiles, tau=tau)
    head = train_defection_head(train, profiles)
    overall_ranking = asdict(evaluate_defection_head(head, eval_records, profiles))

    # Per-congress AUC + precision@k (the product metric).
    per_congress: dict[str, Any] = {}
    by_congress = split_records_by_congress(rollcalls)
    for congress, records in sorted(by_congress.items()):
        c_train, c_eval = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
        if not c_eval:
            continue
        c_profiles = build_party_profiles(c_train) if c_train else profiles
        c_head = train_defection_head(c_train, c_profiles) if c_train else head
        per_congress[str(congress)] = asdict(evaluate_defection_head(c_head, c_eval, c_profiles))

    rag = run_rag_before_after(train, eval_records, profiles, tau=tau, k_values=k_values)

    return {
        "corpus": str(corpus),
        "cutoff": cutoff.isoformat(),
        "eval_end": eval_end.isoformat(),
        "train_pairs": len(train),
        "eval_pairs": len(eval_records),
        "slice_definition": {
            "name": "ex_ante_policy_divergence",
            "tau": tau,
            "description": (
                "A (member, bill) pair is defection-prone when the member's "
                "pre-cutoff yea-rate on the bill's policy area(s) diverges from "
                "their party's pre-cutoff yea-rate by at least tau. Features and "
                "slice membership use only pre-cutoff votes -- never the scored "
                "bill's realized outcome."
            ),
        },
        "slice_prevalence": prevalence,
        "defection_ranking_overall": overall_ranking,
        "defection_ranking_per_congress": per_congress,
        "rag_before_after": rag,
        "head": {"intercept": head.intercept, "coefficients": head.coefficients},
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Honest defection experiment")
    parser.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--out", default="benchmarks/defection_experiment.json")
    parser.add_argument("--tau", type=float, default=0.25)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    args = parser.parse_args(argv)

    report = run(Path(args.corpus), tau=args.tau, train_fraction=args.train_fraction)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    ranking = report["defection_ranking_overall"]
    prevalence = report["slice_prevalence"]
    print(
        f"slice prevalence={prevalence['prevalence']:.3f} "
        f"enrichment={prevalence['enrichment']:.2f}x | "
        f"defection AUC={ranking['auc']:.3f} "
        f"P@50={ranking['precision_at_50']:.3f}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
