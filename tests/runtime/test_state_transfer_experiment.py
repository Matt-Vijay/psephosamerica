"""Tests for the federal -> state transfer runner."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.state_transfer_experiment import load_ca_floor_rollcalls, run


def _row(date: str, location: str = "AFLOOR") -> dict[str, object]:
    return {
        "bill_id": f"ca:2023-2024:AB{date[-2:]}",
        "date": date,
        "location_code": location,
        "sectors": [],
        "votes": [
            ["MemA", "DEM", "CA", "yea"],
            ["MemB", "DEM", "CA", "yea"],
            ["MemC", "REP", "CA", "nay"],
            ["MemD", "REP", "CA", "yea"],
        ],
    }


def test_floor_filter_keeps_floor_and_drops_committee(tmp_path: Path) -> None:
    path = tmp_path / "ca.jsonl"
    rows = [_row("2023-01-10"), _row("2023-01-11", location="AJUD"), _row("2023-01-12", "SFLOOR")]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    kept = load_ca_floor_rollcalls(path)
    assert [r["date"] for r in kept] == ["2023-01-10", "2023-01-12"]


def test_run_reports_transfer_on_tiny_fixture(tmp_path: Path) -> None:
    federal = tmp_path / "federal.jsonl"
    fed_rows = [
        {
            "bill_id": f"us_congress:118:hr-{i}",
            "date": f"2023-02-{i:02d}",
            "congress": 118,
            "sectors": ["health"],
            "votes": [
                ["F1", "D", "CA", "yea"],
                ["F2", "D", "NY", "yea"],
                ["F3", "R", "TX", "nay"],
                ["F4", "R", "OH", "yea"],
            ],
        }
        for i in range(1, 11)
    ]
    federal.write_text("\n".join(json.dumps(r) for r in fed_rows) + "\n", encoding="utf-8")
    state = tmp_path / "ca.jsonl"
    state_rows = [_row(f"2023-03-{i:02d}") for i in range(1, 11)]
    state.write_text("\n".join(json.dumps(r) for r in state_rows) + "\n", encoding="utf-8")
    report = run(federal, state)
    assert "transfer" in report
    assert report["transfer"]["target_eval_pairs"] > 0
    assert 0.0 <= report["transfer"]["zero_shot_auc"] <= 1.0


def test_empty_state_corpus_is_an_error(tmp_path: Path) -> None:
    federal = tmp_path / "federal.jsonl"
    federal.write_text("", encoding="utf-8")
    state = tmp_path / "ca.jsonl"
    state.write_text("", encoding="utf-8")
    assert run(federal, state)["error"] == "no CA floor roll-calls"
