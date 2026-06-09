"""Tests for the real declared-position loader."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.runtime.stated_position_real import load_declared_positions

_REAL = Path("data/prepared/public-statement-sector-rows.jsonl")


def _write(path: Path) -> None:
    rows = [
        {"member_bioguide_id": "A000001", "sector": "health", "statement_date": "2020-01-01"},
        {"member_bioguide_id": "A000001", "sector": "health", "statement_date": "2020-02-01"},
        {"member_bioguide_id": "A000001", "sector": "energy", "statement_date": "2020-03-01"},
        {"member_bioguide_id": "B000002", "sector": "defense", "statement_date": "2024-01-01"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_declared_positions_are_normalized(tmp_path: Path) -> None:
    path = tmp_path / "sectors.jsonl"
    _write(path)
    positions = load_declared_positions(path)
    assert positions["A000001"]["health"] == 2 / 3
    assert positions["A000001"]["energy"] == 1 / 3
    assert abs(sum(positions["A000001"].values()) - 1.0) < 1e-9


def test_cutoff_excludes_later_statements(tmp_path: Path) -> None:
    path = tmp_path / "sectors.jsonl"
    _write(path)
    positions = load_declared_positions(path, before=date(2020, 2, 15))
    # Only the two pre-cutoff health statements survive for A000001.
    assert positions["A000001"] == {"health": 1.0}
    assert "B000002" not in positions  # its 2024 statement is after the cutoff


def test_loads_real_corpus_if_present() -> None:
    if not _REAL.exists():
        return
    positions = load_declared_positions(_REAL)
    assert len(positions) >= 10
    for profile in positions.values():
        assert abs(sum(profile.values()) - 1.0) < 1e-9
