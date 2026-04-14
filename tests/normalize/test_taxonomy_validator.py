"""Tests for taxonomy_validator."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.normalize.taxonomy_validator import (
    ALLOWED_MAPPING_TIERS,
    validate_all,
    validate_committee_sector_map,
    validate_crp_crosswalk,
    validate_sectors_yaml,
)


@pytest.fixture()
def data_root() -> Path:
    """Return the real data/ directory."""
    return Path(__file__).resolve().parents[2] / "data"


# --- sectors.yaml ---


class TestSectorsYaml:
    def test_real_file_passes(self, data_root: Path) -> None:
        sector_ids, errors = validate_sectors_yaml(data_root / "taxonomy" / "sectors.yaml")
        assert errors == [], [repr(e) for e in errors]
        assert len(sector_ids) == 15

    def test_unique_sector_ids(self, data_root: Path) -> None:
        data = yaml.safe_load((data_root / "taxonomy" / "sectors.yaml").read_text())
        ids = [s["sector_id"] for s in data["sectors"]]
        assert len(ids) == len(set(ids))

    def test_duplicate_sector_id_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "sectors.yaml"
        p.write_text(yaml.dump({
            "version": 1,
            "taxonomy_name": "test",
            "sectors": [
                {"sector_id": "a", "label": "A", "description": "A"},
                {"sector_id": "a", "label": "B", "description": "B"},
            ],
        }))
        _, errors = validate_sectors_yaml(p)
        assert any("Duplicate" in e.message for e in errors)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        p = tmp_path / "sectors.yaml"
        p.write_text(yaml.dump({
            "version": 1,
            "taxonomy_name": "test",
            "sectors": [{"sector_id": "a", "label": "A"}],
        }))
        _, errors = validate_sectors_yaml(p)
        assert any("description" in e.message for e in errors)

    def test_missing_top_level_field(self, tmp_path: Path) -> None:
        p = tmp_path / "sectors.yaml"
        p.write_text(yaml.dump({"version": 1, "sectors": []}))
        _, errors = validate_sectors_yaml(p)
        assert any("taxonomy_name" in e.message for e in errors)


# --- committee_sector_map.csv ---


class TestCommitteeSectorMap:
    def test_real_file_passes(self, data_root: Path) -> None:
        sector_ids, _ = validate_sectors_yaml(data_root / "taxonomy" / "sectors.yaml")
        errors = validate_committee_sector_map(
            data_root / "taxonomy" / "committee_sector_map.csv", sector_ids
        )
        assert errors == [], [repr(e) for e in errors]

    def test_unknown_sector_id_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "map.csv"
        p.write_text(textwrap.dedent("""\
            congress,chamber,committee_name,subcommittee_name,sector_id,mapping_tier,jurisdiction_basis,basis_source,notes
            119,House,Test,,bogus_sector,deterministic,test basis,src,
        """))
        errors = validate_committee_sector_map(p, {"agriculture_food"})
        assert any("bogus_sector" in e.message for e in errors)

    def test_invalid_tier_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "map.csv"
        p.write_text(textwrap.dedent("""\
            congress,chamber,committee_name,subcommittee_name,sector_id,mapping_tier,jurisdiction_basis,basis_source,notes
            119,House,Test,,agriculture_food,maybe,test basis,src,
        """))
        errors = validate_committee_sector_map(p, {"agriculture_food"})
        assert any("maybe" in e.message for e in errors)

    def test_missing_column_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "map.csv"
        p.write_text("congress,chamber\n119,House\n")
        errors = validate_committee_sector_map(p, set())
        assert any("Missing columns" in e.message for e in errors)

    def test_empty_jurisdiction_basis_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "map.csv"
        p.write_text(textwrap.dedent("""\
            congress,chamber,committee_name,subcommittee_name,sector_id,mapping_tier,jurisdiction_basis,basis_source,notes
            119,House,Test,,agriculture_food,deterministic,,src,
        """))
        errors = validate_committee_sector_map(p, {"agriculture_food"})
        assert any("jurisdiction_basis" in e.message for e in errors)


# --- crp_to_sector.csv ---


class TestCrpCrosswalk:
    def test_real_file_passes(self, data_root: Path) -> None:
        sector_ids, _ = validate_sectors_yaml(data_root / "taxonomy" / "sectors.yaml")
        errors = validate_crp_crosswalk(
            data_root / "crosswalks" / "crp_to_sector.csv", sector_ids
        )
        assert errors == [], [repr(e) for e in errors]

    def test_unknown_sector_id_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "crp.csv"
        p.write_text(textwrap.dedent("""\
            crp_category,crp_label,sector_id,mapping_tier,notes
            test,Test,nonexistent,deterministic,
        """))
        errors = validate_crp_crosswalk(p, {"agriculture_food"})
        assert any("nonexistent" in e.message for e in errors)


# --- validate_all ---


class TestValidateAll:
    def test_real_data_passes(self, data_root: Path) -> None:
        errors = validate_all(data_root)
        assert errors == [], [repr(e) for e in errors]


# --- allowed tiers constant ---


def test_allowed_tiers_match_spec() -> None:
    assert ALLOWED_MAPPING_TIERS == {"deterministic", "review_required", "out_of_scope"}
