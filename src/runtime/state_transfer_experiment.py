"""Federal -> state zero-shot defection transfer on real CA votes (v7 #6).

The blueprint's scaling bet: the defection signal learned on Congress should
transfer to officials the model has never seen, in a legislature it has never
seen. Track A's CA ingest landed real Assembly/Senate roll-calls (2012-2026)
in Track B's rich 4-tuple format, so the bet is now testable: train the
defection head on US House votes, evaluate zero-shot on CA floor votes
(unseen members, unseen chamber, different party labels -- DEM/REP get their
own profiles), against the CA-trained and joint heads via the same
``chamber_transfer`` harness that showed House->Senate is lossless.

Floor votes only (``location_code`` contains FLOOR; the CA emit is already
floor-only, so the filter is a guard). HONEST DEGENERACY NOTE: the CA rows
carry no sectors, so ``sector_divergence`` is identically zero and the head
reduces to the single loyalty-gap feature -- any head with a positive
loyalty-gap coefficient produces the SAME ranking, which is why zero-shot,
CA-trained and joint report the exact same AUC. The zero-shot number is real
(the federal head ranks unseen-state defections far above chance with no CA
training); the zero gap is NOT the lossless-transfer evidence House->Senate
gave -- it needs CA bill sectors (taggable from CA bill titles in the contract)
to become a discriminating comparison.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.prediction.chamber_transfer import transfer_report
from src.runtime.bill_content_experiment import _iter_records
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls


def load_ca_floor_rollcalls(corpus: Path) -> list[dict[str, Any]]:
    """CA rich roll-calls restricted to floor votes, date-sorted."""
    rolls = [
        r
        for r in _iter_records(corpus)
        if "FLOOR" in str(r.get("location_code", "")).upper() and r.get("date")
    ]
    rolls.sort(key=lambda r: str(r["date"]))
    return rolls


def run(
    federal_corpus: Path,
    state_corpus: Path,
    *,
    train_fraction: float = 0.7,
    max_source: int = 200_000,
    max_target_train: int = 200_000,
) -> dict[str, Any]:
    source = build_vote_records(load_rich_rollcalls(federal_corpus))[-max_source:]
    state_rolls = load_ca_floor_rollcalls(state_corpus)
    if not state_rolls:
        return {"error": "no CA floor roll-calls", "state_corpus": str(state_corpus)}
    records = build_vote_records(state_rolls)
    dates = sorted({r.vote_date for r in records})
    cutoff = dates[max(0, min(len(dates) - 1, int(len(dates) * train_fraction)))]
    target_train = [r for r in records if r.vote_date <= cutoff][-max_target_train:]
    target_eval = [r for r in records if r.vote_date > cutoff]
    report = transfer_report(
        source,
        target_train,
        target_eval,
        source_label="US House (federal floor votes)",
        target_label="California Legislature (floor votes)",
    )
    return {
        "federal_corpus": str(federal_corpus),
        "state_corpus": str(state_corpus),
        "state_floor_rollcalls": len(state_rolls),
        "state_cutoff": cutoff.isoformat(),
        "source_pairs": len(source),
        "target_train_pairs": len(target_train),
        "transfer": report.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Federal -> CA zero-shot defection transfer")
    p.add_argument("--federal", default="data/real/house_118_rich.jsonl")
    p.add_argument("--state", default="data/real/ca_2013_2025_rich.jsonl")
    p.add_argument("--out", default="benchmarks/state_transfer.json")
    p.add_argument("--pin", default="benchmarks/state_transfer_baseline.json")
    args = p.parse_args(argv)

    result = run(Path(args.federal), Path(args.state))
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if "error" not in result:
        t = result["transfer"]
        pin = {
            "slice": "federal-to-ca-floor",
            "zero_shot_auc": t["zero_shot_auc"],
            "target_only_auc": t["target_only_auc"],
            "joint_auc": t["joint_auc"],
            "eval_pairs": t["target_eval_pairs"],
            "tolerance": 0.005,
        }
        Path(args.pin).write_text(json.dumps(pin, indent=2), encoding="utf-8")
        print(
            f"federal->CA zero-shot AUC {t['zero_shot_auc']:.4f} | CA-trained "
            f"{t['target_only_auc']:.4f} | joint {t['joint_auc']:.4f} | gap {t['gap']:+.4f} "
            f"on {t['target_eval_pairs']} eval pairs"
        )
    else:
        print(f"blocked: {result}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
