"""Tests for the cross-congress transfer runner's input guards."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.transfer_experiment import run


def _rich_corpus(path: Path) -> Path:
    rows = [
        {
            "bill_id": f"us_congress:118:hr-{i}",
            "date": f"2024-01-{i:02d}",
            "congress": 118,
            "sectors": ["health"],
            "votes": [
                ["A000001", "D", "CA", "yea"],
                ["B000002", "R", "TX", "nay"],
                ["C000003", "D", "NY", "yea"],
            ],
        }
        for i in range(1, 11)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_missing_target_congress_returns_error_not_keyerror(tmp_path: Path) -> None:
    corpus = _rich_corpus(tmp_path / "rich.jsonl")
    report = run(corpus, target_congress=119)
    assert "error" in report
    assert "119" in report["error"]


def test_empty_corpus_returns_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert run(empty)["error"] == "empty corpus"


def test_present_target_runs_transfer(tmp_path: Path) -> None:
    corpus = _rich_corpus(tmp_path / "rich.jsonl")
    report = run(corpus, target_congress=118)
    assert report["target_congress"] == 118
    assert "transfer" in report
