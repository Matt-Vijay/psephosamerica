"""Tests for the bill-question/description sector tagger."""

from __future__ import annotations

from src.prediction.bill_sector import sector_keywords, tag_bill_sectors


def test_tags_energy_and_environment() -> None:
    sectors = tag_bill_sectors("To expand oil and gas pipeline leasing on public lands")
    assert "energy_utilities" in sectors
    assert "natural_resources_environment" in sectors  # "public lands"


def test_tags_defense() -> None:
    assert "defense_national_security" in tag_bill_sectors(
        "National Defense Authorization Act for the armed forces"
    )


def test_untagged_returns_empty() -> None:
    assert tag_bill_sectors("On Motion to Suspend the Rules") == []


def test_results_are_sorted_and_unique() -> None:
    sectors = tag_bill_sectors("health and medicare and medicaid hospital drug")
    assert sectors == sorted(set(sectors))
    assert sectors == ["health"]


def test_keyword_map_covers_15_sectors() -> None:
    assert len(sector_keywords()) == 15
