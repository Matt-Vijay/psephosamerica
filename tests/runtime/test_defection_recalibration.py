"""Tests for defection-head recalibration (temp/isotonic/conformal per slice)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.defection_recalibration import run


def _rich(path: Path) -> None:
    rolls = []
    for i in range(120):
        kind = "energy_utilities" if i % 2 else "health"
        # defector D breaks party on energy; loyalists hold
        rolls.append(
            {
                "bill_id": f"us_congress:118:hr-{i}",
                "date": f"2024-{1 + i % 11:02d}-15",
                "congress": 118,
                "sectors": [kind],
                "votes": [
                    ["D", "R", "TX", "yea" if kind == "energy_utilities" else "nay"],
                    ["L1", "R", "TX", "nay"],
                    ["L2", "R", "TX", "nay"],
                    ["L3", "R", "TX", "nay"],
                ],
            }
        )
    path.write_text("\n".join(json.dumps(r) for r in rolls), encoding="utf-8")


def test_recalibration_reports_calibrators_and_coverage(tmp_path: Path) -> None:
    corpus = tmp_path / "rich.jsonl"
    _rich(corpus)
    report = run(corpus, cutoff=date(2024, 7, 1))
    assert set(report["calibration"]) == {"raw", "temperature", "isotonic"}
    for mapper in ("raw", "temperature", "isotonic"):
        assert "ece" in report["calibration"][mapper]["overall"]
    assert report["best_calibrator"] in {"raw", "temperature", "isotonic"}
    assert "marginal" in report["conformal"] and "mondrian" in report["conformal"]
    assert isinstance(report["defection_prone_coverage_ok"], bool)
    assert report["temperature"] > 0.0
