"""Tests for the live continuous-learning runner (offline, no sleeps)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from src.prediction.continuous_learning import DatedExample
from src.runtime.continuous_learning_live import main, plan_live_tick


def _corpus() -> list[DatedExample]:
    return [
        DatedExample(
            example=("M", index % 2 == 0), vote_date=date(2025, 1, 1) + timedelta(days=index)
        )
        for index in range(120)
    ]


def test_first_tick_is_full_and_observes_cdc(tmp_path: Path) -> None:
    cdc = tmp_path / "deltas.jsonl"
    cdc.write_text("\n".join(["{}"] * 5) + "\n", encoding="utf-8")
    tick = plan_live_tick(
        _corpus(),
        cdc,
        tick=0,
        as_of=date(2025, 3, 1),
        last_full_retrain=None,
        last_incremental_at=None,
        full_cadence_days=90,
    )
    assert tick.kind == "full"
    assert tick.cdc_delta_count == 5
    assert tick.example_count > 0


def test_tick_is_idempotent(tmp_path: Path) -> None:
    cdc = tmp_path / "deltas.jsonl"
    cdc.write_text("{}\n", encoding="utf-8")
    args = dict(
        tick=2,
        as_of=date(2025, 4, 1),
        last_full_retrain=date(2025, 3, 1),
        last_incremental_at=date(2025, 3, 20),
        full_cadence_days=90,
    )
    first = plan_live_tick(_corpus(), cdc, **args)  # type: ignore[arg-type]
    second = plan_live_tick(_corpus(), cdc, **args)  # type: ignore[arg-type]
    assert first == second


def test_main_writes_ticks_and_advances_cutoff(tmp_path: Path) -> None:
    corpus = tmp_path / "votes.jsonl"
    with corpus.open("w", encoding="utf-8") as fh:
        for index in range(60):
            fh.write(
                json.dumps(
                    {
                        "member_bioguide_id": "M",
                        "canonical_bill_id": "b",
                        "vote_option": "yea" if index % 2 else "nay",
                        "vote_date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
                        "party": "D",
                        "state": "CA",
                        "jurisdiction_id": "us_congress",
                        "signals": {},
                        "is_cross_pressured": False,
                    }
                )
                + "\n"
            )
    cdc = tmp_path / "deltas.jsonl"
    cdc.write_text("{}\n{}\n", encoding="utf-8")
    report = tmp_path / "report.jsonl"
    code = main(
        [
            "--corpus",
            str(corpus),
            "--cdc",
            str(cdc),
            "--report",
            str(report),
            "--ticks",
            "4",
            "--interval-seconds",
            "0",
            "--start",
            "2025-01-10",
            "--step-days",
            "10",
            "--full-cadence-days",
            "90",
        ]
    )
    assert code == 0
    ticks = [json.loads(line) for line in report.read_text().splitlines()]
    assert len(ticks) == 4
    assert ticks[0]["kind"] == "full"
    # cutoff advances monotonically
    assert ticks[0]["as_of"] < ticks[-1]["as_of"]
    assert all(t["cdc_delta_count"] == 2 for t in ticks)
