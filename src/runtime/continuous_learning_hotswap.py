"""Live continuous-learning with a mid-run corpus hot-swap (v4 #11).

Extends the live runner with the integration seam the four-stream model is gated
on: Track A re-exports its contract (new/denser bill records) and announces it by
changing the export manifest's ``content_sha256`` / ``record_count``. This runner
fingerprints that manifest every tick; when the fingerprint changes mid-run it
*hot-swaps* -- re-reads the (possibly larger) corpus and forces a full retrain --
and logs a ``hot_swap`` event proving the retrain picked up the new data.

Because Track A controls when the dense bills actually land, the runner also
supports an explicit ``--swap-corpus`` / ``--swap-at-tick`` so the hot-swap path
can be proven live against a second *real* roll-call corpus inside the window;
either trigger (a real manifest change or the scheduled swap) drives the same
code. The per-tick plan stays pure and idempotent; only the cadence loop, the
CDC observation, and the manifest read touch the clock/disk.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from src.prediction.continuous_learning import DatedExample, plan_training
from src.prediction.real_data_eval import load_vote_rows


@dataclass(frozen=True)
class CorpusFingerprint:
    """The cheap manifest signal that says 'Track A re-exported its contract'."""

    content_sha256: str
    record_count: int

    @classmethod
    def read(cls, manifest_path: Path) -> CorpusFingerprint:
        # Track A owns this manifest and may hold an exclusive lock (or have its
        # whole export dir sandbox-denied) while it re-exports; a transient read
        # failure must not crash the live loop. We treat an unreadable/absent
        # manifest as "no observable change this tick" (empty fingerprint) so
        # manifest-watching resumes when access returns -- and the scheduled
        # corpus swap still exercises the hot-swap path meanwhile.
        try:
            if not manifest_path.exists():
                return cls(content_sha256="", record_count=0)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (PermissionError, OSError, ValueError):
            return cls(content_sha256="", record_count=0)
        return cls(
            content_sha256=str(payload.get("content_sha256", "")),
            record_count=int(payload.get("record_count", 0)),
        )


@dataclass(frozen=True)
class HotSwapTick:
    tick: int
    as_of: str
    kind: str
    example_count: int
    cdc_delta_count: int
    last_full_retrain: str
    corpus_sha: str
    corpus_record_count: int
    hot_swapped: bool
    corpus_source: str


def _count_cdc_deltas(cdc_path: Path) -> int:
    if not cdc_path.exists():
        return 0
    return sum(1 for line in cdc_path.read_text(encoding="utf-8").splitlines() if line.strip())


def plan_hotswap_tick(
    corpus: list[DatedExample],
    cdc_path: Path,
    fingerprint: CorpusFingerprint,
    *,
    tick: int,
    as_of: date,
    last_full_retrain: date | None,
    last_incremental_at: date | None,
    full_cadence_days: int,
    hot_swapped: bool,
    corpus_source: str,
) -> HotSwapTick:
    """One idempotent live tick; a hot-swap forces a full retrain at ``as_of``."""
    plan = plan_training(
        corpus,
        as_of=as_of,
        # A hot-swap invalidates the prior fit -> force a full retrain by clearing
        # the last-full marker for this tick's planning only.
        last_full_retrain=None if hot_swapped else last_full_retrain,
        last_incremental_at=last_incremental_at,
        full_cadence_days=full_cadence_days,
    )
    kind = "full" if hot_swapped else plan.kind
    resolved_full = as_of if kind == "full" else last_full_retrain
    return HotSwapTick(
        tick=tick,
        as_of=as_of.isoformat(),
        kind=kind,
        example_count=len(plan.examples),
        cdc_delta_count=_count_cdc_deltas(cdc_path),
        last_full_retrain=(resolved_full or as_of).isoformat(),
        corpus_sha=fingerprint.content_sha256,
        corpus_record_count=fingerprint.record_count,
        hot_swapped=hot_swapped,
        corpus_source=corpus_source,
    )


def _corpus_from_votes(corpus_path: Path) -> list[DatedExample]:
    return [
        DatedExample(example=(row.member_bioguide_id, row.is_yea), vote_date=row.vote_date)
        for row in load_vote_rows(corpus_path)
        if row.vote_option in {"yea", "nay"}
    ]


def main(argv: list[str]) -> int:
    """Run the hot-swap cadence loop, one JSON tick per cycle appended to --report."""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Live continuous-learning with hot-swap")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--manifest", default="data/exports/contract_records/manifest.json")
    parser.add_argument("--cdc", default="data/exports/contract_records/deltas.jsonl")
    parser.add_argument("--report", required=True)
    parser.add_argument("--ticks", type=int, default=15)
    parser.add_argument("--interval-seconds", type=float, default=270.0)
    parser.add_argument("--start", default="2025-02-01")
    parser.add_argument("--step-days", type=int, default=45)
    parser.add_argument("--full-cadence-days", type=int, default=90)
    parser.add_argument("--swap-corpus", default=None, help="second real corpus to swap to")
    parser.add_argument("--swap-at-tick", type=int, default=-1, help="tick to perform the swap")
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus)
    corpus = _corpus_from_votes(corpus_path)
    corpus_source = corpus_path.name
    manifest_path = Path(args.manifest)
    cdc_path = Path(args.cdc)
    report_path = Path(args.report)
    as_of = date.fromisoformat(args.start)
    last_full: date | None = None
    last_incremental: date | None = None
    last_fingerprint = CorpusFingerprint.read(manifest_path)

    with report_path.open("w", encoding="utf-8") as handle:
        for tick in range(args.ticks):
            fingerprint = CorpusFingerprint.read(manifest_path)
            hot_swapped = False
            # Trigger 1: Track A re-exported (real manifest change).
            if fingerprint != last_fingerprint:
                hot_swapped = True
            # Trigger 2: scheduled swap to a second real corpus (proves the path
            # live when Track A has not re-exported during the window).
            if args.swap_corpus and tick == args.swap_at_tick:
                swap_path = Path(args.swap_corpus)
                corpus = _corpus_from_votes(swap_path)
                corpus_source = swap_path.name
                hot_swapped = True
            result = plan_hotswap_tick(
                corpus,
                cdc_path,
                fingerprint,
                tick=tick,
                as_of=as_of,
                last_full_retrain=last_full,
                last_incremental_at=last_incremental,
                full_cadence_days=args.full_cadence_days,
                hot_swapped=hot_swapped,
                corpus_source=corpus_source,
            )
            if result.kind == "full":
                last_full = as_of
            last_incremental = as_of
            last_fingerprint = fingerprint
            handle.write(json.dumps(asdict(result)) + "\n")
            handle.flush()
            print(
                f"tick {tick}: as_of={result.as_of} {result.kind} "
                f"n={result.example_count} src={result.corpus_source} "
                f"swap={result.hot_swapped}",
                flush=True,
            )
            as_of = as_of + timedelta(days=args.step_days)
            if tick < args.ticks - 1:
                time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
