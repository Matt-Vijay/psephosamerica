"""Build a sector-rich roll-call corpus for a congress, with real bill ids.

Fetches House Clerk roll-calls for the given years (the only network boundary),
extracts each vote's real bill id (from ``legis-num``) and policy sectors (from the
``vote-desc`` text), and writes the rich JSONL the defection experiments consume:
``{bill_id, date, congress, sectors, votes:[[bioguide,party,state,choice],...]}``.

Built to produce the 113th-Congress corpus that joins to Track A's dense 113th
bill embeddings -- the corpus needed to run THE bill-content experiment for real.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.prediction.bill_sector import tag_bill_sectors
from src.runtime.house_clerk_corpus import fetch_house_rollcalls


def build(years: list[int], *, roll_max: int, out_path: Path) -> dict[str, int]:
    """Fetch + tag roll-calls for ``years`` and write the rich corpus."""
    written = 0
    real_bills = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for year in years:
            rollcalls = fetch_house_rollcalls(year=year, numbers=range(1, roll_max + 1))
            for rc in rollcalls:
                sectors = sorted(tag_bill_sectors(rc.description or rc.question))
                tail = rc.bill_id.split(":")[-1]
                if tail not in {"quorum", "adjourn", "unknown"}:
                    real_bills += 1
                row = {
                    "bill_id": rc.bill_id,
                    "date": rc.vote_date.isoformat(),
                    "congress": rc.congress,
                    "sectors": sectors,
                    "votes": [[v.bioguide_id, v.party, v.state, v.choice] for v in rc.votes],
                }
                handle.write(json.dumps(row) + "\n")
                written += 1
    return {"rollcalls": written, "with_real_bill": real_bills}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build a sector-rich roll-call corpus")
    parser.add_argument("--years", default="2013,2014", help="comma-separated years")
    parser.add_argument("--roll-max", type=int, default=700)
    parser.add_argument("--out", default="data/real/house_113_rich.jsonl")
    args = parser.parse_args(argv)

    years = [int(y) for y in args.years.split(",")]
    stats = build(years, roll_max=args.roll_max, out_path=Path(args.out))
    print(f"wrote {args.out}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
