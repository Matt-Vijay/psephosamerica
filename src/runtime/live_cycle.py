"""The live incremental cycle: one tick keeps Track B's feeds fresh (v7 #8).

A tick runs the three resumable, idempotent incremental runners — each a no-op
when nothing new exists upstream:

1. **Senate top-up**: new roll-calls through the (fixed) vote menu into the
   rich file, then unseen roll-calls into the vote-edge feed,
2. **Market snapshot**: current prices + any new/changed congress markets into
   the markets sidecar (delta-announced),

and reports what changed. ``run_loop`` repeats ticks on an interval — the
"cron LIVE" loop. Each stage is injectable for tests; a stage failure is
contained (logged in the report, the others still run), so one flaky upstream
never kills the cycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.runtime.market_ingest import snapshot_markets
from src.runtime.senate_vote_edges import export_senate_vote_edges
from src.runtime.senate_votes_backfill import backfill_senate_votes

RICH_PATH = "data/real/senate_113_119_rich.jsonl"
SIDECAR_PATH = "data/exports/govinfo_bills/bill_content.jsonl"
CORPUS_DIR = "data/exports/contract_records"
EDGES_PATH = "data/exports/govinfo_bills/senate_vote_edges.jsonl"
MARKETS_DIR = "data/exports/markets"


def _senate_stage() -> dict[str, Any]:
    progress = backfill_senate_votes(
        congresses=[119], out_path=RICH_PATH, content_sidecar=SIDECAR_PATH, sessions=(1, 2)
    )
    report = export_senate_vote_edges(
        rich_path=RICH_PATH, corpus_directory=CORPUS_DIR, out_path=EDGES_PATH
    )
    return {"new_rollcalls": progress.votes_new, "new_edges": report.edges_written}


def _markets_stage() -> dict[str, Any]:
    report = snapshot_markets(out_directory=MARKETS_DIR, include_closed=False)
    return {
        "markets": report.markets_total,
        "linked": report.markets_linked,
        "prices_new": report.prices_written,
        "deltas": report.deltas_written,
    }


STAGES: dict[str, Callable[[], dict[str, Any]]] = {
    "senate": _senate_stage,
    "markets": _markets_stage,
}


@dataclass(frozen=True)
class TickReport:
    """One live-cycle tick's outcome per stage."""

    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_tick(
    stages: dict[str, Callable[[], dict[str, Any]]] | None = None,
) -> TickReport:
    """Run every stage, containing per-stage failures."""
    active = stages if stages is not None else STAGES
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for name, stage in active.items():
        try:
            results[name] = stage()
        except Exception as exc:  # noqa: BLE001 - one stage must not kill the cycle
            errors[name] = f"{type(exc).__name__}: {exc}"
    return TickReport(results=results, errors=errors)


def run_loop(
    *,
    ticks: int,
    interval_seconds: float,
    stages: dict[str, Callable[[], dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
) -> list[TickReport]:
    """Run ``ticks`` cycles, ``interval_seconds`` apart, reporting each."""
    reports: list[TickReport] = []
    for index in range(ticks):
        if index > 0:
            sleep(interval_seconds)
        report = run_tick(stages)
        reports.append(report)
        summary = "; ".join(f"{name}={result}" for name, result in report.results.items())
        if report.errors:
            summary += " | ERRORS " + "; ".join(
                f"{name}: {message}" for name, message in report.errors.items()
            )
        emit(f"tick {index + 1}/{ticks}: {summary}")
    return reports


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Live incremental cycle (Senate + markets)")
    parser.add_argument("--ticks", type=int, default=5)
    parser.add_argument("--interval", type=float, default=900.0)
    args = parser.parse_args(argv)
    reports = run_loop(ticks=args.ticks, interval_seconds=args.interval)
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
