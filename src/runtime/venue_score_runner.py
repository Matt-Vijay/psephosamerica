"""Produce a real venue-score artifact from roll-call data (track 3).

Builds the venue index from a rich roll-call corpus, ranks the venue(s) by P(pass)
for each policy sector with Wilson intervals + roll-call citations, and attaches
the top defection-watch marginal members per venue -- the two halves of the
marginal-vote product in one artifact. With the House-118 corpus the venue set is
a single jurisdiction, so the useful output is the per-sector pass profile (which
policy areas pass, with evidence); the ranking generalises to many venues the
moment Track A lands other chambers/committees and state legislatures.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.api.venue_score import Venue, build_venue_index
from src.prediction.bill_sector import sector_keywords
from src.prediction.defection import build_party_profiles, split_by_cutoff
from src.prediction.defection_head import rank_defections, train_defection_head
from src.runtime.cross_pressured_experiment import build_vote_records, load_rich_rollcalls


def run(corpus: Path, *, cutoff: date) -> dict[str, Any]:
    rollcalls = load_rich_rollcalls(corpus)
    index = build_venue_index(rollcalls)

    # defection-watch marginal members per venue (here: the single House venue).
    records = build_vote_records(rollcalls)
    eval_end = max(r.vote_date for r in records)
    train, eval_records = split_by_cutoff(records, cutoff=cutoff, eval_end=eval_end)
    train = train[-200_000:]
    profiles = build_party_profiles(train)
    head = train_defection_head(train, profiles)
    ranked = rank_defections(head, eval_records, profiles, top_n=5)
    congresses = {rc.get("congress", "?") for rc in rollcalls}
    venue_key = Venue(jurisdiction=f"us_congress:{sorted(congresses)[0]}", chamber="house").key()
    marginal = {venue_key: [d.member for d in ranked]}

    per_sector: dict[str, Any] = {}
    for sector in sorted(sector_keywords()):
        scores = index.score([sector], marginal_members_by_venue=marginal, top_n=5)
        if scores:
            per_sector[sector] = scores[0].as_dict()

    # an example bill profile (energy permitting) ranking across venues
    example = [s.as_dict() for s in index.score(["energy_utilities"], marginal_members_by_venue=marginal)]
    return {
        "cutoff": cutoff.isoformat(),
        "venues": sorted({Venue(jurisdiction=f"us_congress:{c}", chamber="house").label() for c in congresses}),
        "per_sector_pass_profile": per_sector,
        "example_ranking_energy": example,
        "note": (
            "Single-jurisdiction (House) corpus -> the ranking degenerates to one "
            "venue; the per-sector pass profile is the useful output. Multi-venue "
            "ranking (other chambers, committees, state legislatures) activates as "
            "Track A lands those jurisdictions -- the Venue key already carries "
            "chamber/committee fields."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Venue-score artifact")
    parser.add_argument("--corpus", default="data/real/house_118_rich.jsonl")
    parser.add_argument("--cutoff", default="2024-04-20")
    parser.add_argument("--out", default="benchmarks/venue_score.json")
    args = parser.parse_args(argv)

    report = run(Path(args.corpus), cutoff=date.fromisoformat(args.cutoff))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    top = sorted(
        report["per_sector_pass_profile"].items(),
        key=lambda kv: kv[1]["p_pass"],
        reverse=True,
    )[:3]
    print("top pass-rate sectors (House):", [(k, round(v["p_pass"], 3)) for k, v in top])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
