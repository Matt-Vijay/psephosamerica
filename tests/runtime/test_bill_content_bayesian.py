"""Test for the bill-content SOTA 10-seed Bayesian + checkpoint."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.runtime.bill_content_bayesian import run


def _records(path: Path) -> None:
    rows = [
        {
            "entity_type": "bill",
            "canonical_id": f"us_congress:118:hr-{i}",
            "dossier_embedding": [0.1 * (i % 5), 0.2, 0.3 * (i % 3)],
        }
        for i in range(40)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _rich(path: Path) -> None:
    rolls = []
    for i in range(40):
        energy = i % 2 == 0
        rolls.append(
            {
                "bill_id": f"us_congress:118:hr-{i}",
                "date": f"2024-{1 + i % 11:02d}-15",
                "congress": 118,
                "sectors": ["energy_utilities" if energy else "health"],
                "votes": [
                    ["D", "R", "TX", "yea" if energy else "nay"],
                    ["L1", "R", "TX", "nay"],
                    ["L2", "R", "TX", "nay"],
                ],
            }
        )
    path.write_text("\n".join(json.dumps(r) for r in rolls), encoding="utf-8")


def test_bayesian_reports_band_and_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rich = tmp_path / "rich.jsonl"
    records = tmp_path / "records.jsonl"
    _rich(rich)
    _records(records)
    monkeypatch.chdir(tmp_path)  # checkpoints/ written under cwd; restored after this test
    report = run(
        rich,
        records,
        cutoff=date(2024, 7, 1),
        eval_end=date(2024, 12, 31),
        k=4,
        projection_dim=2,
        n_seeds=4,
    )
    assert report["n_seeds"] == 4 and len(report["per_seed_auc"]) == 4
    assert 0.0 <= report["mean_auc"] <= 1.0 and report["std_auc"] >= 0.0
    assert Path(report["checkpoint_path"]).exists()
