"""Live continuous-learning runner against Track A's delta-CDC.

Drives the continuous-learning planner on a wall-clock cadence: every tick it
observes Track A's delta-CDC (``contract_records/deltas.jsonl``), advances the
``as_of`` cutoff over the real vote timeline, and plans a retrain (full on
cadence, otherwise incremental over newly-resolved votes), logging the cutoff
advance and retrain kind/count per tick. The per-tick decision is pure and
idempotent (a re-fired tick yields the identical plan); only the cadence loop
and the CDC observation touch the clock/disk.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from src.prediction.continuous_learning import DatedExample, plan_training
from src.prediction.real_data_eval import load_vote_rows


@dataclass(frozen=True)
class LiveTick:
    tick: int
    as_of: str
    kind: str
    example_count: int
    cdc_delta_count: int
    last_full_retrain: str


def _count_cdc_deltas(cdc_path: Path) -> int:
    if not cdc_path.exists():
        return 0
    return sum(1 for line in cdc_path.read_text(encoding="utf-8").splitlines() if line.strip())


def plan_live_tick(
    corpus: list[DatedExample],
    cdc_path: Path,
    *,
    tick: int,
    as_of: date,
    last_full_retrain: date | None,
    last_incremental_at: date | None,
    full_cadence_days: int,
) -> LiveTick:
    """One idempotent live tick: observe the CDC, plan the retrain at ``as_of``."""
    plan = plan_training(
        corpus,
        as_of=as_of,
        last_full_retrain=last_full_retrain,
        last_incremental_at=last_incremental_at,
        full_cadence_days=full_cadence_days,
    )
    resolved_full = as_of if plan.kind == "full" else last_full_retrain
    return LiveTick(
        tick=tick,
        as_of=as_of.isoformat(),
        kind=plan.kind,
        example_count=len(plan.examples),
        cdc_delta_count=_count_cdc_deltas(cdc_path),
        last_full_retrain=(resolved_full or as_of).isoformat(),
    )


def _corpus_from_votes(corpus_path: Path) -> list[DatedExample]:
    return [
        DatedExample(example=(row.member_bioguide_id, row.is_yea), vote_date=row.vote_date)
        for row in load_vote_rows(corpus_path)
        if row.vote_option in {"yea", "nay"}
    ]


def main(argv: list[str]) -> int:
    """Run the live cadence loop, appending one JSON tick per cycle to --report."""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Live continuous-learning runner")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--cdc", default="data/exports/contract_records/deltas.jsonl")
    parser.add_argument("--report", required=True)
    parser.add_argument("--ticks", type=int, default=7)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--start", default="2025-02-01")
    parser.add_argument("--step-days", type=int, default=45)
    parser.add_argument("--full-cadence-days", type=int, default=90)
    args = parser.parse_args(argv)

    corpus = _corpus_from_votes(Path(args.corpus))
    cdc_path = Path(args.cdc)
    report_path = Path(args.report)
    as_of = date.fromisoformat(args.start)
    last_full: date | None = None
    last_incremental: date | None = None

    with report_path.open("w", encoding="utf-8") as handle:
        for tick in range(args.ticks):
            result = plan_live_tick(
                corpus,
                cdc_path,
                tick=tick,
                as_of=as_of,
                last_full_retrain=last_full,
                last_incremental_at=last_incremental,
                full_cadence_days=args.full_cadence_days,
            )
            if result.kind == "full":
                last_full = as_of
            last_incremental = as_of
            handle.write(json.dumps(asdict(result)) + "\n")
            handle.flush()
            print(
                f"tick {tick}: as_of={result.as_of} {result.kind} n={result.example_count}",
                flush=True,
            )
            as_of = as_of + timedelta(days=args.step_days)
            if tick < args.ticks - 1:
                time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
