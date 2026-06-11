"""Time-sliced confirmation-head validation on real Senate nominations (v7 #5 runner).

Consumes the question-preserving Senate backfill (``senate_113_119_rich_v2``):
"On the Nomination" roll-calls, train congresses <= 117, eval 118-119. Reports
AUC / Brier / accuracy against the party-line baseline and pins the result.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.confirmation_head import (
    ConfirmationVote,
    evaluate_confirmation_head,
    is_confirmation_question,
    train_confirmation_head,
)
from src.runtime.bill_content_experiment import _iter_records

_BINARY = {"yea", "nay"}


def load_confirmation_votes(corpus: Path) -> list[ConfirmationVote]:
    out: list[ConfirmationVote] = []
    for r in _iter_records(corpus):
        if not is_confirmation_question(str(r.get("question", ""))):
            continue
        raw_date = str(r.get("date") or "")
        congress = int(r.get("congress") or 0)
        if not raw_date or not congress:
            continue
        when = date.fromisoformat(raw_date)
        for member, party, _state, choice in r.get("votes") or []:
            if choice in _BINARY:
                out.append(
                    ConfirmationVote(
                        member=member,
                        party=party,
                        congress=congress,
                        vote_date=when,
                        is_yea=choice == "yea",
                    )
                )
    return out


def run(corpus: Path, *, train_max_congress: int = 117) -> dict[str, Any]:
    votes = load_confirmation_votes(corpus)
    train = [v for v in votes if v.congress <= train_max_congress]
    eval_votes = [v for v in votes if v.congress > train_max_congress]
    if not train or not eval_votes:
        return {"error": "empty slice", "train": len(train), "eval": len(eval_votes)}
    head = train_confirmation_head(train)
    metrics = evaluate_confirmation_head(head, eval_votes)
    return {
        "train_congresses": f"<= {train_max_congress}",
        "train_votes": len(train),
        "eval_congresses": "118-119",
        **metrics,
        "coefficients": {"intercept": head.intercept, **head.coefficients},
        "beats_party_line": bool(metrics["auc"] > metrics["auc_party_line"]),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Confirmation head time-sliced validation")
    p.add_argument("--corpus", default="data/real/senate_113_119_rich_v2.jsonl")
    p.add_argument("--out", default="benchmarks/confirmation_head.json")
    p.add_argument("--pin", default="benchmarks/confirmation_baseline.json")
    args = p.parse_args(argv)

    report = run(Path(args.corpus))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if "error" not in report:
        pin = {
            "slice": "confirmation-118-119",
            "auc": report["auc"],
            "auc_party_line": report["auc_party_line"],
            "brier": report["brier"],
            "eval_votes": report["eval_votes"],
            "tolerance": 0.005,
        }
        Path(args.pin).write_text(json.dumps(pin, indent=2), encoding="utf-8")
        print(
            f"confirmation AUC {report['auc']:.4f} vs party-line {report['auc_party_line']:.4f} "
            f"| Brier {report['brier']:.4f} (baseline {report['brier_party_line']:.4f}) "
            f"| acc {report['accuracy']:.4f} on {int(report['eval_votes'])} votes "
            f"| beats party line: {report['beats_party_line']}"
        )
    else:
        print(f"blocked: {report}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
