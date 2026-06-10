"""Run all non-bill defection signals on real data, report ΔAUC honestly (track 2).

Squeezes every non-bill signal we have into the defection head and reports each
one's ΔAUC against the base (loyalty + sector) head and the 0.7247 pin -- even
when it is zero or negative. The honest expectation, stated up front: the
vote-derived features already capture most member-level defection tendency, so
non-bill add-ons (hierarchical prior, statement engagement, donor independence)
are likely small; the real lift waits on Track A's dense bill content.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.prediction.defection import build_party_profiles, split_by_cutoff
from src.prediction.defection_external_signals import (
    donor_independence_provider,
    statement_corpus_coverage,
    statement_engagement_provider,
)
from src.prediction.defection_signals import (
    evaluate_signal,
    hierarchical_defection_prior,
)
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls

_PIN = 0.7247


def run(corpus: Path, *, cutoff: date, max_train: int = 200_000) -> dict[str, Any]:
    records = build_vote_records(load_rich_rollcalls(corpus))
    eval_end = max(r.vote_date for r in records)
    train, eval_records = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
    if len(train) > max_train:
        train = train[-max_train:]
    profiles = build_party_profiles(train)

    statements = Path("data/prepared/public-statement-sector-rows.jsonl")
    donor_profile = Path("data/prepared/donor_profile.json")

    signals = [
        (
            "hierarchical_prior",
            [hierarchical_defection_prior(train)],
            ("defection_prior",),
        ),
        (
            "statement_engagement",
            [statement_engagement_provider(statements, cutoff=cutoff)],
            ("statement_engagement",),
        ),
        (
            "donor_independence",
            [donor_independence_provider(donor_profile)],
            ("donor_independence",),
        ),
    ]

    evaluated = []
    for name, providers, names in signals:
        evaluated.append(
            evaluate_signal(
                train,
                eval_records,
                profiles,
                signal_name=name,
                providers=providers,
                extra_feature_names=names,
            )
        )

    # combined: all signals together
    combined = evaluate_signal(
        train,
        eval_records,
        profiles,
        signal_name="all_non_bill",
        providers=[
            hierarchical_defection_prior(train),
            statement_engagement_provider(statements, cutoff=cutoff),
            donor_independence_provider(donor_profile),
        ],
        extra_feature_names=("defection_prior", "statement_engagement", "donor_independence"),
    )
    evaluated.append(combined)
    results = [r.as_dict() for r in evaluated]

    beats_pin = combined.augmented_auc > _PIN + 0.005
    return {
        "cutoff": cutoff.isoformat(),
        "pin": _PIN,
        "base_auc": evaluated[0].base_auc,
        "signals": results,
        "best_augmented_auc": max(r.augmented_auc for r in evaluated),
        "beats_pin": beats_pin,
        "gates": {
            "statement_corpus_members": statement_corpus_coverage(statements),
            "donor_profile_present": donor_profile.exists(),
            "donor_note": (
                "Donor independence gated on a prepared data/prepared/donor_profile.json "
                "(raw FEC itcont is 1.6GB); absent -> neutral 0, reported honestly."
            ),
            "cosponsor_note": "Cosponsor-profile signal gated on Track A dense bill rows.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Non-bill defection signal experiment")
    parser.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--out", default="benchmarks/defection_signals.json")
    args = parser.parse_args(argv)

    report = run(Path(args.corpus), cutoff=date.fromisoformat(args.cutoff))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for sig in report["signals"]:
        print(f"{sig['signal_name']:22s} ΔAUC={sig['delta_auc']:+.4f} (aug={sig['augmented_auc']:.4f})")
    print(f"beats 0.7247 pin: {report['beats_pin']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
