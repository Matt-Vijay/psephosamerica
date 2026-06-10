"""Semantic vs hash vs concat bill-embedding A/B on the 118th pin (v5 #1).

Track A is shipping ``semantic_embedding`` (MiniLM-384 over full bill text + CRS
summaries); the current SOTA (0.7707) used the title-only hash ``dossier_embedding``.
This reruns the bill-content experiment three ways on the 118th pin and reports
ΔAUC vs 0.7707, so we can decide whether to re-pin and whether to escalate to API
embeddings:

* **hash**  — dossier_embedding (the current SOTA input),
* **semantic** — semantic_embedding (full text + CRS),
* **concat** — both, concatenated.

If semantic ≤ hash we report it honestly (the diagnosis — pooling, truncation,
leakage — is left to follow-up, but the comparison itself is the decision input).
Gated on the semantic field being present; until then this is a no-op that the
watcher fires once Track A's export lands.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.runtime.bill_content_experiment import (
    count_embedding_field,
    load_bill_embedding_map,
    run_bill_content_experiment,
)
from src.runtime.cross_pressured_experiment import load_rich_rollcalls

_PIN = 0.7707


def run(
    *,
    rich_corpus: Path,
    records_path: Path,
    cutoff: date,
    eval_end: date,
    k_values: tuple[int, ...] = (8, 16, 32, 64),
    projection_dim: int = 16,
) -> dict[str, Any]:
    rolls = load_rich_rollcalls(rich_corpus)
    semantic_n = count_embedding_field(records_path, "semantic_embedding")
    arms: dict[str, Any] = {}
    variants: list[tuple[str, str, str | None]] = [("hash", "dossier_embedding", None)]
    if semantic_n > 0:
        variants += [
            ("semantic", "semantic_embedding", None),
            ("concat", "dossier_embedding", "semantic_embedding"),
        ]
    for name, field, concat in variants:
        emap = load_bill_embedding_map(records_path, field=field, concat_with=concat)
        report = run_bill_content_experiment(
            rolls, emap, cutoff=cutoff, eval_end=eval_end,
            k_values=k_values, projection_dim=projection_dim, synthetic=False,
        )
        arms[name] = {
            "best_auc": report["best_auc"],
            "best_k": report["best_k"],
            "best_variant": report["best_variant"],
            "delta_vs_base": report["delta_vs_base"],
            "delta_vs_pin": report["best_auc"] - _PIN,
            "vote_linked_bills": report["vote_linked_bills"],
        }
    best_arm = max(arms, key=lambda a: arms[a]["best_auc"]) if arms else None
    beats_pin = best_arm is not None and arms[best_arm]["best_auc"] > _PIN + 0.005
    return {
        "pin": _PIN,
        "semantic_available": semantic_n > 0,
        "semantic_bill_count": semantic_n,
        "arms": arms,
        "best_arm": best_arm,
        "beats_pin": beats_pin,
        "decision": (
            "semantic embeddings not yet exported by Track A; ran hash arm only"
            if semantic_n == 0
            else (
                f"best arm '{best_arm}' "
                + ("BEATS" if beats_pin else "does not beat")
                + " the 0.7707 pin"
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Semantic vs hash vs concat A/B")
    parser.add_argument("--rich", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--eval-end", default="2024-12-31")
    parser.add_argument("--out", default="benchmarks/semantic_ab.json")
    args = parser.parse_args(argv)

    report = run(
        rich_corpus=Path(args.rich),
        records_path=Path(args.records),
        cutoff=date.fromisoformat(args.cutoff),
        eval_end=date.fromisoformat(args.eval_end),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, arm in report["arms"].items():
        print(f"{name:9s} best_auc={arm['best_auc']:.4f} Δvs_pin={arm['delta_vs_pin']:+.4f}")
    print(report["decision"])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
