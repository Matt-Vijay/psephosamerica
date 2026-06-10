"""Tests for the semantic vs hash vs concat bill-embedding A/B."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.bill_content_experiment import count_embedding_field, load_bill_embedding_map
from src.runtime.semantic_ab_experiment import run


def _records(path: Path, *, semantic: bool) -> None:
    rows = []
    for i in range(3):
        row = {
            "entity_type": "bill",
            "canonical_id": f"us_congress:118:hr-{i}",
            "dossier_embedding": [0.1 * i, 0.2],
        }
        if semantic:
            row["semantic_embedding"] = [0.3, 0.4, 0.5 * i]
        rows.append(row)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _rich(path: Path) -> None:
    rolls = []
    for i in range(20):
        rolls.append({
            "bill_id": f"us_congress:118:hr-{i % 3}", "date": f"2024-{1 + i % 9:02d}-15",
            "congress": 118, "sectors": ["health"],
            "votes": [["A", "R", "TX", "yea" if i % 2 else "nay"], ["B", "D", "CA", "nay"]],
        })
    path.write_text("\n".join(json.dumps(r) for r in rolls), encoding="utf-8")


def test_count_embedding_field(tmp_path: Path) -> None:
    rec = tmp_path / "records.jsonl"
    _records(rec, semantic=False)
    assert count_embedding_field(rec, "dossier_embedding") == 3
    assert count_embedding_field(rec, "semantic_embedding") == 0


def test_loader_field_and_concat(tmp_path: Path) -> None:
    rec = tmp_path / "records.jsonl"
    _records(rec, semantic=True)
    hash_map = load_bill_embedding_map(rec, field="dossier_embedding")
    sem_map = load_bill_embedding_map(rec, field="semantic_embedding")
    concat_map = load_bill_embedding_map(rec, field="dossier_embedding", concat_with="semantic_embedding")
    assert any(v.shape[0] == 2 for v in hash_map.values())
    assert any(v.shape[0] == 3 for v in sem_map.values())
    assert any(v.shape[0] == 5 for v in concat_map.values())  # 2 + 3 concatenated


def test_ab_runs_hash_only_when_semantic_absent(tmp_path: Path) -> None:
    rec = tmp_path / "records.jsonl"
    rich = tmp_path / "rich.jsonl"
    _records(rec, semantic=False)
    _rich(rich)
    report = run(rich_corpus=rich, records_path=rec, cutoff=date(2024, 5, 1), eval_end=date(2024, 12, 31), k_values=(4,), projection_dim=0)
    assert report["semantic_available"] is False
    assert set(report["arms"]) == {"hash"}
    assert "not yet exported" in report["decision"]


def test_ab_runs_all_arms_when_semantic_present(tmp_path: Path) -> None:
    rec = tmp_path / "records.jsonl"
    rich = tmp_path / "rich.jsonl"
    _records(rec, semantic=True)
    _rich(rich)
    report = run(rich_corpus=rich, records_path=rec, cutoff=date(2024, 5, 1), eval_end=date(2024, 12, 31), k_values=(4,), projection_dim=0)
    assert report["semantic_available"] is True
    assert set(report["arms"]) == {"hash", "semantic", "concat"}
    assert report["best_arm"] in {"hash", "semantic", "concat"}
