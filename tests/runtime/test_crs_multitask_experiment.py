"""Tests for the CRS policy-area multi-task experiment."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.crs_multitask_experiment import load_crs_policy_map, run


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    # contract: canonical_id -> external_id (bill key)
    records = tmp_path / "records.jsonl"
    recs = []
    for i in range(60):
        recs.append(
            {
                "entity_type": "bill",
                "canonical_id": f"cb-{i}",
                "external_ids": [f"congress:113-hr-{i}"],
            }
        )
    records.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    # bill_content: canonical_id -> policy_area (energy bills get a distinct CRS area)
    content = tmp_path / "bill_content.jsonl"
    cont = [
        {
            "canonical_id": f"cb-{i}",
            "policy_area": "Energy" if i % 2 == 0 else "Health",
            "subjects": [],
        }
        for i in range(60)
    ]
    content.write_text("\n".join(json.dumps(r) for r in cont), encoding="utf-8")
    # rich rollcalls: defector D breaks party on Energy (even-index) bills
    rich = tmp_path / "rich.jsonl"
    rolls = []
    for i in range(60):
        energy = i % 2 == 0
        rolls.append(
            {
                "bill_id": f"us_congress:113:hr-{i}",
                "date": f"2013-{1 + i % 11:02d}-15",
                "congress": 113,
                "sectors": [],
                "votes": [
                    ["D", "R", "TX", "yea" if energy else "nay"],
                    ["L1", "R", "TX", "nay"],
                    ["L2", "R", "TX", "nay"],
                ],
            }
        )
    rich.write_text("\n".join(json.dumps(r) for r in rolls), encoding="utf-8")
    return rich, records, content


def test_load_crs_policy_map_joins(tmp_path: Path) -> None:
    rich, records, content = _setup(tmp_path)
    m = load_crs_policy_map(records, content)
    assert m.get("113:hr:0") == "Energy"
    assert m.get("113:hr:1") == "Health"


def test_crs_multitask_reports_delta(tmp_path: Path) -> None:
    rich, records, content = _setup(tmp_path)
    report = run(rich, records_path=records, content_path=content, cutoff=date(2013, 7, 1))
    assert report["crs_policy_coverage"] > 0.5
    assert report["distinct_policy_areas"] == 2
    assert set(report["arms"]) == {"base", "policy", "policy_subjects"}
    # the CRS-area defection signal is real here, so the best arm should not hurt
    assert report["best_auc"] >= report["base_auc"] - 1e-6
    assert "delta_auc" in report and report["best_arm"] in {"base", "policy", "policy_subjects"}
