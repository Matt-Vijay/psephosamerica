"""Offline tests for the cross-pressured experiment.

A synthetic rich corpus where defections are sector-consistent (a subset of
members always vote against their party on one sector) lets us verify that the
sector_lean signal recovers those defections — the experiment's mechanism.
"""

from __future__ import annotations

from datetime import date

from src.runtime.cross_pressured_experiment import (
    build_vote_records,
    run_experiment,
)


def _rollcall(bill: str, day: int, sectors: list[str], votes: list[list[str]]) -> dict[str, object]:
    return {
        "bill_id": bill,
        "date": f"2024-{1 + day // 28:02d}-{1 + day % 28:02d}",
        "congress": 118,
        "sectors": sectors,
        "votes": votes,
    }


def _corpus() -> list[dict[str, object]]:
    rolls: list[dict[str, object]] = []
    # 8 D members; D2,D3 are "energy defectors" who always vote nay on energy even
    # when the party leans yea. R members vote the party line.
    for day in range(40):
        energy = day % 2 == 0
        votes = []
        for i in range(8):
            m = f"D{i}"
            if energy and i in (2, 3):
                choice = "nay"  # defect on energy
            else:
                choice = "yea"  # D party line = yea
            votes.append([m, "D", "CA", choice])
        for i in range(8):
            votes.append([f"R{i}", "R", "TX", "nay"])  # R party line = nay
        rolls.append(_rollcall(f"bill{day}", day, ["energy_utilities"] if energy else [], votes))
    return rolls


def test_build_vote_records_flags_cross_pressure() -> None:
    records = build_vote_records(_corpus())
    # D2 on an energy bill defects (D leans yea, D2 votes nay).
    energy_d2 = [r for r in records if r.member == "D2" and "energy_utilities" in r.sectors]
    assert energy_d2 and all(r.is_cross_pressured for r in energy_d2)


def test_sector_signal_lifts_cross_pressured_accuracy() -> None:
    result = run_experiment(_corpus(), cutoff=date(2024, 1, 25), eval_end=date(2024, 12, 31))
    before = result["before"].get("cross_pressured")
    after = result["after"].get("cross_pressured")
    assert before is not None and after is not None
    # Party-only is wrong on every defection; the sector signal recovers them.
    assert before.accuracy < 0.2
    assert after.accuracy > before.accuracy
