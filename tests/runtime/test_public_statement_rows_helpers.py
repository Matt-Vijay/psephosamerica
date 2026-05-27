"""Tests for the pure helpers in public_statement_rows_materialize."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime.public_statement_rows_materialize import (
    _explicit_sector_values,
    _first_string,
    _load_taxonomy,
    _statement_date,
    _statement_sectors,
)


def test_statement_date_parses_iso_prefix_and_rejects_blank_or_invalid() -> None:
    assert _statement_date("2024-05-15T09:00:00Z") == "2024-05-15"
    assert _statement_date("") is None
    assert _statement_date("   ") is None
    assert _statement_date("not-a-date") is None


def test_explicit_sector_values_reads_sector_and_sectors() -> None:
    assert _explicit_sector_values({"sector": "Energy"}) == ["Energy"]
    assert _explicit_sector_values({"sectors": ["A", "  ", "B"]}) == ["A", "B"]
    assert _explicit_sector_values({}) == []


def test_first_string_returns_first_non_blank() -> None:
    assert _first_string({"a": "", "b": "x"}, "a", "b") == "x"
    assert _first_string({"a": "  trimmed  "}, "a") == "trimmed"
    assert _first_string({}, "a", "b") is None


def _taxonomy(tmp_path: Path) -> object:
    path = tmp_path / "sectors.yaml"
    path.write_text(
        """
sectors:
  - sector_id: energy_utilities
    label: Energy and Utilities
    aliases:
      - energy
      - oil
  - not_a_mapping
  - {}
""",
        encoding="utf-8",
    )
    return _load_taxonomy(path)


def test_load_taxonomy_builds_aliases_and_skips_malformed_entries(tmp_path: Path) -> None:
    tax = _taxonomy(tmp_path)
    assert tax.normalize("energy") == "energy_utilities"  # alias
    assert tax.normalize("Energy and Utilities") == "energy_utilities"  # label
    assert tax.normalize("unknown") is None


def test_load_taxonomy_rejects_missing_sectors(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("sectors: not-a-list", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain sectors"):
        _load_taxonomy(path)


def test_statement_sectors_explicit_text_and_unknown(tmp_path: Path) -> None:
    tax = _taxonomy(tmp_path)
    # Explicit, known sector.
    assert _statement_sectors({"sector": "energy"}, taxonomy=tax) == ["energy_utilities"]
    # Unknown explicit sector falls through to (empty) text matching.
    assert _statement_sectors({"sector": "zzz"}, taxonomy=tax) == []
    # Text-based keyword match when no explicit sector is given.
    assert _statement_sectors({"title": "new oil pipeline"}, taxonomy=tax) == ["energy_utilities"]
