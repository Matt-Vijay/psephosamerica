"""Tests for taxonomy_runtime loaders and access helpers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from normalize.taxonomy_runtime import (
    CommitteeMapping,
    TaxonomyRuntime,
    _load_committee_mappings,
    _load_crp_crosswalk,
    _load_sectors,
    load_taxonomy_runtime,
)


@pytest.fixture()
def data_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


@pytest.fixture()
def runtime(data_root: Path) -> TaxonomyRuntime:
    return load_taxonomy_runtime(data_root)


# ---------------------------------------------------------------------------
# Loader: sectors
# ---------------------------------------------------------------------------


class TestLoadSectors:
    def test_returns_all_15_sectors(self, data_root: Path) -> None:
        sectors = _load_sectors(data_root / "taxonomy" / "sectors.yaml")
        assert len(sectors) == 15

    def test_sector_fields_populated(self, data_root: Path) -> None:
        sectors = _load_sectors(data_root / "taxonomy" / "sectors.yaml")
        for s in sectors:
            assert s.sector_id
            assert s.label
            assert s.description

    def test_aliases_are_tuple(self, data_root: Path) -> None:
        sectors = _load_sectors(data_root / "taxonomy" / "sectors.yaml")
        for s in sectors:
            assert isinstance(s.aliases, tuple)

    def test_known_sector_present(self, data_root: Path) -> None:
        sectors = _load_sectors(data_root / "taxonomy" / "sectors.yaml")
        ids = {s.sector_id for s in sectors}
        assert "agriculture_food" in ids
        assert "financial_services" in ids
        assert "health" in ids

    def test_sector_without_aliases_uses_empty_tuple(self, tmp_path: Path) -> None:
        p = tmp_path / "sectors.yaml"
        p.write_text(
            yaml.dump(
                {
                    "version": 1,
                    "taxonomy_name": "test",
                    "sectors": [{"sector_id": "x", "label": "X", "description": "X desc"}],
                }
            )
        )
        sectors = _load_sectors(p)
        assert sectors[0].aliases == ()


# ---------------------------------------------------------------------------
# Loader: committee mappings
# ---------------------------------------------------------------------------


class TestLoadCommitteeMappings:
    def test_returns_rows(self, data_root: Path) -> None:
        rows = _load_committee_mappings(data_root / "taxonomy" / "committee_sector_map.csv")
        assert len(rows) > 0

    def test_congress_is_int(self, data_root: Path) -> None:
        rows = _load_committee_mappings(data_root / "taxonomy" / "committee_sector_map.csv")
        for r in rows:
            assert isinstance(r.congress, int)

    def test_known_committee_present(self, data_root: Path) -> None:
        rows = _load_committee_mappings(data_root / "taxonomy" / "committee_sector_map.csv")
        found = [
            r
            for r in rows
            if r.committee_name == "Committee on Agriculture" and r.subcommittee_name == ""
        ]
        assert len(found) == 1
        assert found[0].sector_id == "agriculture_food"
        assert found[0].mapping_tier == "deterministic"

    def test_subcommittee_name_empty_string_when_absent(self, tmp_path: Path) -> None:
        p = tmp_path / "map.csv"
        p.write_text(
            textwrap.dedent("""\
            congress,chamber,committee_name,subcommittee_name,sector_id,mapping_tier,jurisdiction_basis,basis_source,notes
            119,House,Test Committee,,health,deterministic,Test basis,src,
        """)
        )
        rows = _load_committee_mappings(p)
        assert rows[0].subcommittee_name == ""


# ---------------------------------------------------------------------------
# Loader: CRP crosswalk
# ---------------------------------------------------------------------------


class TestLoadCrpCrosswalk:
    def test_returns_rows(self, data_root: Path) -> None:
        rows = _load_crp_crosswalk(data_root / "crosswalks" / "crp_to_sector.csv")
        assert len(rows) > 0

    def test_known_category_present(self, data_root: Path) -> None:
        rows = _load_crp_crosswalk(data_root / "crosswalks" / "crp_to_sector.csv")
        found = [r for r in rows if r.crp_category == "agribusiness"]
        assert len(found) == 1
        assert found[0].sector_id == "agriculture_food"
        assert found[0].mapping_tier == "deterministic"

    def test_fields_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "crp.csv"
        p.write_text(
            textwrap.dedent("""\
            crp_category,crp_label,sector_id,mapping_tier,notes
            " banks "," Banks "," financial_services "," deterministic ",
        """)
        )
        rows = _load_crp_crosswalk(p)
        assert rows[0].crp_category == "banks"
        assert rows[0].sector_id == "financial_services"


# ---------------------------------------------------------------------------
# TaxonomyRuntime: sector_by_id
# ---------------------------------------------------------------------------


class TestSectorById:
    def test_known_sector(self, runtime: TaxonomyRuntime) -> None:
        s = runtime.sector_by_id("agriculture_food")
        assert s is not None
        assert s.label == "Agriculture and Food"

    def test_unknown_returns_none(self, runtime: TaxonomyRuntime) -> None:
        assert runtime.sector_by_id("does_not_exist") is None

    def test_all_loaded_ids_are_accessible(self, runtime: TaxonomyRuntime) -> None:
        for sector in runtime.sectors:
            assert runtime.sector_by_id(sector.sector_id) is sector


# ---------------------------------------------------------------------------
# TaxonomyRuntime: committee_sector
# ---------------------------------------------------------------------------


class TestCommitteeSector:
    def test_chamber_disambiguates_duplicate_committee_names(self) -> None:
        runtime = TaxonomyRuntime(
            sectors=[],
            committee_mappings=[
                CommitteeMapping(
                    congress=119,
                    chamber="House",
                    committee_name="Committee on Finance",
                    subcommittee_name="",
                    sector_id="health",
                    mapping_tier="review_required",
                    jurisdiction_basis="House basis",
                    basis_source="src",
                    notes="",
                ),
                CommitteeMapping(
                    congress=119,
                    chamber="Senate",
                    committee_name="Committee on Finance",
                    subcommittee_name="",
                    sector_id="financial_services",
                    mapping_tier="review_required",
                    jurisdiction_basis="Senate basis",
                    basis_source="src",
                    notes="",
                ),
            ],
            crp_mappings=[],
        )

        house = runtime.committee_sector("Committee on Finance", congress=119, chamber="House")
        senate = runtime.committee_sector("Committee on Finance", congress=119, chamber="Senate")

        assert house is not None
        assert house.sector_id == "health"
        assert senate is not None
        assert senate.sector_id == "financial_services"

    def test_full_committee_match(self, runtime: TaxonomyRuntime) -> None:
        m = runtime.committee_sector("Committee on Agriculture", congress=119)
        assert m is not None
        assert m.sector_id == "agriculture_food"
        assert m.mapping_tier == "deterministic"

    def test_subcommittee_exact_match(self, runtime: TaxonomyRuntime) -> None:
        m = runtime.committee_sector(
            "Committee on Energy and Commerce",
            "Subcommittee on Health",
            congress=119,
        )
        assert m is not None
        assert m.sector_id == "health"

    def test_subcommittee_fallback_to_parent(self, runtime: TaxonomyRuntime) -> None:
        # "Unknown Subcommittee" is not mapped; should fall back to parent committee row
        m = runtime.committee_sector(
            "Committee on Agriculture",
            "Unknown Subcommittee",
            congress=119,
        )
        assert m is not None
        assert m.sector_id == "agriculture_food"
        assert m.subcommittee_name == ""  # returned the parent row

    def test_unknown_committee_returns_none(self, runtime: TaxonomyRuntime) -> None:
        assert runtime.committee_sector("Committee on Nonsense", congress=119) is None

    def test_wrong_congress_returns_none(self, runtime: TaxonomyRuntime) -> None:
        assert runtime.committee_sector("Committee on Agriculture", congress=100) is None

    def test_senate_committee(self, runtime: TaxonomyRuntime) -> None:
        m = runtime.committee_sector(
            "Committee on Agriculture, Nutrition, and Forestry", congress=119
        )
        assert m is not None
        assert m.sector_id == "agriculture_food"
        assert m.chamber == "Senate"

    def test_default_congress_is_119(self, runtime: TaxonomyRuntime) -> None:
        with_default = runtime.committee_sector("Committee on Agriculture")
        explicit = runtime.committee_sector("Committee on Agriculture", congress=119)
        assert with_default == explicit


# ---------------------------------------------------------------------------
# TaxonomyRuntime: committees_for_sector
# ---------------------------------------------------------------------------


class TestCommitteesForSector:
    def test_returns_list(self, runtime: TaxonomyRuntime) -> None:
        results = runtime.committees_for_sector("health", congress=119)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_all_results_match_sector(self, runtime: TaxonomyRuntime) -> None:
        for m in runtime.committees_for_sector("health", congress=119):
            assert m.sector_id == "health"

    def test_no_congress_filter_returns_more(self, runtime: TaxonomyRuntime) -> None:
        filtered = runtime.committees_for_sector("health", congress=119)
        unfiltered = runtime.committees_for_sector("health")
        assert len(unfiltered) >= len(filtered)

    def test_unknown_sector_returns_empty(self, runtime: TaxonomyRuntime) -> None:
        assert runtime.committees_for_sector("nonexistent") == []


# ---------------------------------------------------------------------------
# TaxonomyRuntime: crp_sector
# ---------------------------------------------------------------------------


class TestCrpSector:
    def test_known_category(self, runtime: TaxonomyRuntime) -> None:
        m = runtime.crp_sector("agribusiness")
        assert m is not None
        assert m.sector_id == "agriculture_food"
        assert m.mapping_tier == "deterministic"

    def test_unknown_returns_none(self, runtime: TaxonomyRuntime) -> None:
        assert runtime.crp_sector("completely_unknown_category") is None

    def test_all_loaded_categories_accessible(self, runtime: TaxonomyRuntime) -> None:
        for crp in runtime.crp_mappings:
            assert runtime.crp_sector(crp.crp_category) is crp


# ---------------------------------------------------------------------------
# load_taxonomy_runtime: integration and error path
# ---------------------------------------------------------------------------


class TestLoadTaxonomyRuntime:
    def test_loads_real_data_without_error(self, data_root: Path) -> None:
        rt = load_taxonomy_runtime(data_root)
        assert len(rt.sectors) == 15
        assert len(rt.committee_mappings) > 0
        assert len(rt.crp_mappings) > 0

    def test_raises_on_invalid_data(self, tmp_path: Path) -> None:
        taxonomy_dir = tmp_path / "taxonomy"
        taxonomy_dir.mkdir()
        crosswalks_dir = tmp_path / "crosswalks"
        crosswalks_dir.mkdir()

        # sectors.yaml with duplicate sector_id triggers a validation error
        (taxonomy_dir / "sectors.yaml").write_text(
            yaml.dump(
                {
                    "version": 1,
                    "taxonomy_name": "test",
                    "sectors": [
                        {"sector_id": "dup", "label": "A", "description": "A"},
                        {"sector_id": "dup", "label": "B", "description": "B"},
                    ],
                }
            )
        )
        (taxonomy_dir / "committee_sector_map.csv").write_text(
            "congress,chamber,committee_name,subcommittee_name,sector_id,mapping_tier,jurisdiction_basis,basis_source,notes\n"
        )
        (crosswalks_dir / "crp_to_sector.csv").write_text(
            "crp_category,crp_label,sector_id,mapping_tier,notes\n"
        )

        with pytest.raises(ValueError, match="Taxonomy validation failed"):
            load_taxonomy_runtime(tmp_path)


def test_committee_sector_chamber_scoped_subcommittee_falls_back_to_parent() -> None:
    from normalize.taxonomy_runtime import CommitteeMapping, TaxonomyRuntime

    parent = CommitteeMapping(
        congress=119,
        chamber="house",
        committee_name="Energy Committee",
        subcommittee_name="",
        sector_id="energy_utilities",
        mapping_tier="deterministic",
        jurisdiction_basis="House Rules X",
        basis_source="rules",
        notes="",
    )
    rt = TaxonomyRuntime(sectors=[], committee_mappings=[parent], crp_mappings=[])

    # Unmapped subcommittee under a known chamber+committee -> parent fallback.
    hit = rt.committee_sector(
        "Energy Committee",
        subcommittee_name="Subcommittee on Nothing",
        congress=119,
        chamber="house",
    )
    assert hit is parent

    # Unknown committee under a chamber, no subcommittee -> None.
    assert rt.committee_sector("Unknown Committee", congress=119, chamber="house") is None
