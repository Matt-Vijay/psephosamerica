"""Test for the combined bill-RAG + CRS SOTA experiment."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.combined_sota_experiment import run


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    records = tmp_path / "records.jsonl"
    content = tmp_path / "bill_content.jsonl"
    rich = tmp_path / "rich.jsonl"
    recs, cont, rolls = [], [], []
    for i in range(60):
        energy = i % 2 == 0
        cid = f"cb-{i}"
        recs.append({"entity_type": "bill", "canonical_id": cid, "external_ids": [f"congress:118-hr-{i}"], "dossier_embedding": [0.1 * (i % 5), 0.2, 0.3 * (i % 3)]})
        cont.append({"canonical_id": cid, "policy_area": "Energy" if energy else "Health", "subjects": []})
        rolls.append({
            "bill_id": f"us_congress:118:hr-{i}", "date": f"2024-{1 + i % 11:02d}-15",
            "congress": 118, "sectors": ["energy_utilities" if energy else "health"],
            "votes": [["D", "R", "TX", "yea" if energy else "nay"], ["L1", "R", "TX", "nay"], ["L2", "R", "TX", "nay"]],
        })
    records.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    content.write_text("\n".join(json.dumps(r) for r in cont), encoding="utf-8")
    rich.write_text("\n".join(json.dumps(r) for r in rolls), encoding="utf-8")
    return rich, records, content


def test_combined_reports_four_arms(tmp_path: Path) -> None:
    rich, records, content = _setup(tmp_path)
    r = run(rich, records, content, cutoff=date(2024, 7, 1), eval_end=date(2024, 12, 31), k=4, projection_dim=2)
    assert set(r["aucs"]) == {"base", "bill_rag", "bill_rag_crs", "crs_only"}
    for auc in r["aucs"].values():
        assert 0.0 <= auc <= 1.0
    assert "stacks" in r and isinstance(r["stacks"], bool)
    assert r["best_arm"] in r["aucs"]
