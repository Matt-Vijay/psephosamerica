"""Watch Track A's contract for dense, vote-linked bill rows landing at scale.

The defection AUC is gated on dense bill content. Track A announces a re-export by
changing the contract manifest's ``content_sha256``; the payload we care about is
*vote-linkable* bill rows -- bill records carrying both a ``dossier_embedding`` and
external ids / a canonical id that join to the roll-call corpus' ``bill_id``. This
module counts those and decides when the bill-content experiment should fire
(default threshold 5,000 linked bills), so the watcher can trigger automatically
the moment the data lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THRESHOLD = 5_000


@dataclass(frozen=True)
class LinkageStatus:
    total_bills: int
    with_embedding: int
    with_policy_area: int
    vote_linkable: int  # has embedding AND a join key (external_ids or us_congress canonical id)

    def as_dict(self) -> dict[str, int]:
        return {
            "total_bills": self.total_bills,
            "with_embedding": self.with_embedding,
            "with_policy_area": self.with_policy_area,
            "vote_linkable": self.vote_linkable,
        }


def _is_vote_linkable(record: dict[str, object]) -> bool:
    if not record.get("dossier_embedding"):
        return False
    if record.get("external_ids"):
        return True
    canonical = str(record.get("canonical_id", ""))
    # roll-call bill ids look like "us_congress:118:hr1234"; a canonical id in that
    # namespace is directly joinable to the vote corpus.
    return canonical.startswith("us_congress:")


def count_linked_bills(records_path: Path) -> LinkageStatus:
    """Count bill rows by embedding / policy-area / vote-linkability."""
    from src.runtime.bill_content_experiment import _iter_records

    total = emb = policy = linkable = 0
    for record in _iter_records(records_path):
        if record.get("entity_type") != "bill":
            continue
        total += 1
        if record.get("dossier_embedding"):
            emb += 1
        dossier = record.get("dossier_json") or {}
        if isinstance(dossier, dict) and dossier.get("policy_area"):
            policy += 1
        if _is_vote_linkable(record):
            linkable += 1
    return LinkageStatus(
        total_bills=total, with_embedding=emb, with_policy_area=policy, vote_linkable=linkable
    )


def manifest_sha(manifest_path: Path) -> str:
    """Track A's export content hash (empty string when unreadable)."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("content_sha256", ""))


def should_trigger(status: LinkageStatus, *, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """Fire the bill-content experiment once vote-linkable dense bills reach scale."""
    return status.vote_linkable >= threshold


def main(argv: list[str] | None = None) -> int:
    """Poll the contract; on >= threshold vote-linkable dense bills, fire the experiment.

    Exits 0 the moment it triggers (after running the bill-content experiment and
    writing the report) so the launching task notifies the operator to review the
    ΔAUC and re-pin. Exits 2 if the poll budget is exhausted without the data
    landing (still logging the latest linkage so the gate is visible).
    """
    import argparse
    import time
    from datetime import date

    from src.runtime.bill_content_experiment import (
        load_bill_embedding_map,
        run_bill_content_experiment,
    )
    from src.runtime.cross_pressured_experiment import load_rich_rollcalls

    parser = argparse.ArgumentParser(description="Watch Track A for dense vote-linked bills")
    parser.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    parser.add_argument("--manifest", default="data/exports/contract_records/manifest.json")
    parser.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--out", default="benchmarks/bill_content_experiment_live.json")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--eval-end", default="2024-12-31")
    parser.add_argument("--poll-seconds", type=float, default=270.0)
    parser.add_argument("--max-polls", type=int, default=200)
    parser.add_argument(
        "--seen-sha",
        default="",
        help="manifest sha already processed; the watcher fires only on a DIFFERENT export",
    )
    parser.add_argument(
        "--semantic-ab",
        action="store_true",
        help="on trigger, run the semantic-vs-hash-vs-concat A/B (v5 #1) instead of the plain experiment",
    )
    args = parser.parse_args(argv)

    records_path = Path(args.records)
    last_sha = ""
    prev_signal = -1
    for poll in range(args.max_polls):
        status = count_linked_bills(records_path)
        sha = manifest_sha(Path(args.manifest))
        # In semantic-ab mode the quantity that must be present + settled is the
        # SEMANTIC embedding count (the hash count settles first and would fire early).
        if args.semantic_ab:
            from src.runtime.bill_content_experiment import count_embedding_field

            signal = count_embedding_field(records_path, "semantic_embedding")
        else:
            signal = status.vote_linkable
        if sha != last_sha:
            print(f"poll {poll}: manifest {sha[:12]} | {status.as_dict()} | signal={signal}", flush=True)
            last_sha = sha
        # Only fire on a re-export we have not already processed, and only once the
        # gating count has SETTLED (unchanged since the previous poll) -- Track A
        # rewrites records.jsonl in place over several passes, so firing on the
        # first sighting would run on a half-written file.
        already_seen = bool(args.seen_sha) and sha == args.seen_sha
        stable = signal == prev_signal
        prev_signal = signal
        if not already_seen and stable and signal >= args.threshold:
            print(f"TRIGGER: {status.vote_linkable} vote-linkable dense bills >= {args.threshold}", flush=True)
            if args.semantic_ab:
                from src.runtime.semantic_ab_experiment import run as run_semantic_ab

                report = run_semantic_ab(
                    rich_corpus=Path(args.corpus),
                    records_path=records_path,
                    cutoff=date.fromisoformat(args.cutoff),
                    eval_end=date.fromisoformat(args.eval_end),
                )
                report["linkage"] = status.as_dict()
                Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(f"semantic A/B: {report['decision']}; wrote {args.out}", flush=True)
                return 0
            rolls = load_rich_rollcalls(Path(args.corpus))
            emap = load_bill_embedding_map(records_path)
            report = run_bill_content_experiment(
                rolls,
                emap,
                cutoff=date.fromisoformat(args.cutoff),
                eval_end=date.fromisoformat(args.eval_end),
                synthetic=False,
            )
            report["linkage"] = status.as_dict()
            Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"bill-content AUC={report['best_auc']:.4f} (base {report['base_auc']:.4f}, "
                f"delta {report['delta_vs_base']:+.4f}); beats 0.7247 pin: {report['beats_pin']}; "
                f"wrote {args.out}",
                flush=True,
            )
            return 0
        if poll < args.max_polls - 1:
            time.sleep(args.poll_seconds)
    final = count_linked_bills(records_path)
    print(f"poll budget exhausted; vote-linkable dense bills still {final.vote_linkable}", flush=True)
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
