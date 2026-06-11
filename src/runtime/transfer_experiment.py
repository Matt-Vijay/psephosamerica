"""Run the cross-chamber / cross-congress defection transfer (v4 #5).

Builds vote records from a rich roll-call corpus, splits them by congress, trains
the defection head on a *source* set of congresses, and measures zero-shot /
target-only / joint defection AUC on a held-out *target* congress. The same
``chamber_transfer`` harness also runs the real House→Senate transfer against the
``senate_corpus`` ingest (result pinned in ``benchmarks/house_senate_transfer.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.prediction.chamber_transfer import transfer_report
from src.runtime.cross_pressured_experiment import (
    VoteRecord,
    load_rich_rollcalls,
)
from src.runtime.defection_experiment import split_records_by_congress


def _split_target(
    records: list[VoteRecord], train_fraction: float
) -> tuple[list[VoteRecord], list[VoteRecord]]:
    dates = sorted({r.vote_date for r in records})
    if not dates:
        return [], []
    cutoff = dates[max(0, min(len(dates) - 1, int(len(dates) * train_fraction)))]
    train = [r for r in records if r.vote_date <= cutoff]
    evaluation = [r for r in records if r.vote_date > cutoff]
    return train, evaluation


def run(
    corpus: Path,
    *,
    target_congress: int | None = None,
    train_fraction: float = 0.7,
    max_source: int | None = 200_000,
) -> dict[str, Any]:
    """Train on all-but-target congresses, transfer to the target congress."""
    rollcalls = load_rich_rollcalls(corpus)
    by_congress = split_records_by_congress(rollcalls)
    congresses = sorted(by_congress)
    if not congresses:
        return {"corpus": str(corpus), "error": "empty corpus"}
    target = target_congress if target_congress is not None else congresses[-1]
    if target not in by_congress:
        return {
            "corpus": str(corpus),
            "error": f"target congress {target} not in corpus (have {congresses})",
        }
    source_records: list[VoteRecord] = []
    for congress in congresses:
        if congress != target:
            source_records.extend(by_congress[congress])
    if max_source is not None and len(source_records) > max_source:
        source_records = source_records[-max_source:]

    target_train, target_eval = _split_target(by_congress[target], train_fraction)
    report = transfer_report(
        source_records,
        target_train,
        target_eval,
        source_label=f"congresses {[c for c in congresses if c != target]}",
        target_label=f"congress {target}",
    )
    return {
        "corpus": str(corpus),
        "target_congress": target,
        "source_congresses": [c for c in congresses if c != target],
        "source_pairs": len(source_records),
        "transfer": report.as_dict(),
        "senate_status": (
            "Senate roll-calls are ingested via src/runtime/senate_corpus.py "
            "(data/real/senate_119_rich.jsonl); the House->Senate transfer result "
            "lives in benchmarks/house_senate_transfer.json. This run reports the "
            "cross-congress transfer for the given corpus."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Cross-chamber defection transfer")
    parser.add_argument("--corpus", default="data/real/house_5congress_sample.jsonl")
    parser.add_argument("--target-congress", type=int, default=None)
    parser.add_argument("--out", default="benchmarks/transfer_report.json")
    args = parser.parse_args(argv)

    report = run(Path(args.corpus), target_congress=args.target_congress)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    transfer = report.get("transfer", {})
    print(
        f"target=congress {report.get('target_congress')} | "
        f"zero-shot AUC={transfer.get('zero_shot_auc', 0):.3f} "
        f"target-only={transfer.get('target_only_auc', 0):.3f} "
        f"joint={transfer.get('joint_auc', 0):.3f} "
        f"gap={transfer.get('gap', 0):.3f}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
