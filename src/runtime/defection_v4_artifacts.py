"""Regenerate all v4 defection artifacts on the real corpus (#4,#5,#7,#8,#9,#10).

One driver so the benchmark artifacts are reproducible from real roll-call data:
cross-congress transfer (#5), conformal coverage per slice (#7), multi-task
auxiliary transfer (#4), LLM stub stacking (#8), defection-watch HTML + AUC tiles
(#9), and the 10-seed Bayesian ensemble + content-addressed checkpoint (#10). All
under a strict no-leakage cutoff on the sector-rich 118th corpus, with the
cross-congress transfer using the flat 115-117 -> 118/119 corpora.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.api.defection_watch import (
    DefectionAucTile,
    render_defection_auc_tiles,
    render_defection_watch_html,
)
from src.prediction.chamber_transfer import transfer_report
from src.prediction.defection import (
    build_party_profiles,
    is_defection_prone,
    slice_prevalence,
    split_by_cutoff,
)
from src.prediction.defection_bayesian import (
    bootstrap_seed_ensemble,
    ensemble_auc,
    ensemble_payload,
    pin_checkpoint,
)
from src.prediction.defection_conformal import conformal_coverage
from src.prediction.defection_head import rank_defections, train_defection_head
from src.prediction.defection_multitask import multitask_transfer, no_auxiliaries
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls
from src.runtime.flat_corpus import build_vote_records_from_flat
from src.runtime.llm_defection_eval import anthropic_key_present, evaluate_llm_stack


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate v4 defection artifacts")
    parser.add_argument("--rich", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--source-flat", default="data/real/house_115_117.jsonl")
    parser.add_argument("--target-flat", default="data/real/house_118.jsonl")
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--max-train", type=int, default=200_000)
    parser.add_argument("--max-flat", type=int, default=400_000)
    args = parser.parse_args(argv)

    cutoff = date.fromisoformat(args.cutoff)
    records = build_vote_records(load_rich_rollcalls(Path(args.rich)))
    eval_end = max(r.vote_date for r in records)
    train, eval_records = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
    if len(train) > args.max_train:
        train = train[-args.max_train :]
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles)
    bench = Path("benchmarks")

    # #7 conformal coverage per slice (cal split = first half of eval).
    half = len(eval_records) // 2
    calibration, test = eval_records[:half], eval_records[half:]
    coverage = conformal_coverage(
        head,
        calibration,
        test,
        profiles,
        alpha=0.10,
        slicers={
            "defection_prone": lambda r: is_defection_prone(r, profiles, tau=0.25),
            "party_D": lambda r: r.party == "D",
            "party_R": lambda r: r.party == "R",
        },
    )
    _write(
        bench / "conformal_coverage.json",
        {"cutoff": args.cutoff, "alpha": 0.10, "slices": [c.as_dict() for c in coverage]},
    )

    # #10 10-seed Bayesian + content-addressed checkpoint.
    heads = bootstrap_seed_ensemble(train, profiles, n_seeds=10)
    auc = ensemble_auc(heads, eval_records, profiles)
    payload = ensemble_payload(heads, model_name="defection_head_bayes")
    digest, path = pin_checkpoint(Path("checkpoints"), payload)
    _write(
        bench / "defection_bayesian.json",
        {
            "cutoff": args.cutoff,
            **auc.as_dict(),
            "checkpoint_sha256": digest,
            "checkpoint_path": str(path),
        },
    )

    # #4 multi-task transfer (no dense auxiliaries yet -> coverage 0, delta ~0).
    mt = multitask_transfer(train, eval_records, profiles, aux=no_auxiliaries)
    _write(bench / "multitask_transfer.json", {"cutoff": args.cutoff, **mt.as_dict()})

    # #8 LLM stub stacking on the honest slice.
    honest_eval = [r for r in eval_records if is_defection_prone(r, profiles, tau=0.25)]
    stack = evaluate_llm_stack(head, calibration, honest_eval or eval_records, profiles)
    _write(
        bench / "llm_defection_stack.json",
        {
            "cutoff": args.cutoff,
            "anthropic_key_present": anthropic_key_present(),
            **stack.as_dict(),
        },
    )

    # #5 cross-congress transfer (flat corpora, loyalty-signal transfer).
    source = build_vote_records_from_flat(Path(args.source_flat), max_rows=args.max_flat)
    target_all = build_vote_records_from_flat(Path(args.target_flat), max_rows=args.max_flat)
    t_dates = sorted({r.vote_date for r in target_all})
    t_cut = t_dates[len(t_dates) // 2] if t_dates else cutoff
    t_train = [r for r in target_all if r.vote_date <= t_cut]
    t_eval = [r for r in target_all if r.vote_date > t_cut]
    transfer = transfer_report(
        source, t_train, t_eval, source_label="house_115_117", target_label="house_118"
    )
    _write(
        bench / "transfer_report.json",
        {
            "transfer": transfer.as_dict(),
            "note": (
                "Cross-congress loyalty-signal transfer (flat corpora, no sectors). "
                "Senate / sector-aware transfer awaits multi-congress sector ingest."
            ),
        },
    )

    # #9 defection-watch HTML + dashboard AUC tiles (latest window).
    ranked = rank_defections(head, eval_records, profiles, top_n=20, with_truth=True)
    watch_html = render_defection_watch_html(ranked, head, congress="118", top_n=20)
    (bench / "defection_watch.html").write_text(watch_html, encoding="utf-8")
    print("wrote benchmarks/defection_watch.html")
    tiles = [
        DefectionAucTile(
            key="118",
            auc=auc.mean_auc,
            sample_count=len(eval_records),
            positives=sum(1 for r in eval_records if r.is_cross_pressured),
        )
    ]
    (bench / "defection_auc_tiles.html").write_text(
        render_defection_auc_tiles(tiles, title="Defection AUC by congress"), encoding="utf-8"
    )
    print("wrote benchmarks/defection_auc_tiles.html")

    prevalence = slice_prevalence(eval_records, profiles, tau=0.25)
    print(
        f"conformal overall coverage={[c.empirical_coverage for c in coverage if c.slice_name == 'overall']} "
        f"| bayes AUC={auc.mean_auc:.3f}±{auc.std_auc:.3f} "
        f"| transfer gap={transfer.gap:.3f} | slice enrichment={prevalence['enrichment']:.2f}x"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
