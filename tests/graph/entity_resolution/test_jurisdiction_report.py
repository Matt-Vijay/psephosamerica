from __future__ import annotations

from src.graph.entity_resolution.jurisdiction_link import JurisdictionUniverse
from src.graph.entity_resolution.jurisdiction_report import (
    build_canonical_universe,
    jurisdiction_of_code,
    score_locus_jurisdictions,
)
from src.graph.jurisdictions import Jurisdiction


def test_build_canonical_universe_has_legistar() -> None:
    universe = build_canonical_universe()
    assert universe.size >= 130
    assert universe.has_code("us-ca-city-oakland")
    assert universe.has_code("us-wa-county-king_county")


def test_jurisdiction_of_code_roundtrip() -> None:
    j = jurisdiction_of_code("us-ca-city-oakland")
    assert j is not None
    assert j.level == "city"
    assert j.parent_id == "us-ca"
    assert jurisdiction_of_code("us") is None  # no place tail
    assert jurisdiction_of_code("us-ca") is None


def test_score_perfect_recall_and_precision() -> None:
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.city("ca", "Oakland"))  # us-ca-city-oakland
    universe.add(Jurisdiction.city("ak", "King Cove"))  # us-ak-city-king_cove
    # LOCUS codes: one exact, one folded variant, one unknown (minted).
    locus = ["us-ca-city-oakland", "us-ak-city-kingcove", "us-ak-city-nome"]
    report = score_locus_jurisdictions(locus, universe)
    assert report.locus_jurisdictions == 3
    assert report.gold_positives == 2  # oakland (exact) + kingcove (folded)
    assert report.asserted_links == 2
    assert report.true_positive_links == 2
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.minted == 1
    assert report.method_counts["exact_code"] == 1
    assert report.method_counts["folded"] == 1
    assert report.method_counts["minted"] == 1


def test_score_collision_excluded_from_gold() -> None:
    universe = JurisdictionUniverse()
    universe.add_code("us-mn-city-st_paul")  # folds to "stpaul"
    universe.add_code("us-mn-city-s_t_paul")  # also folds to "stpaul"
    # LOCUS "stpaul" folds to the collision key -> ambiguous, not a gold positive.
    report = score_locus_jurisdictions(["us-mn-city-stpaul"], universe)
    assert report.ambiguous == 1
    assert report.gold_positives == 0
    assert report.asserted_links == 0
    assert report.precision == 1.0  # nothing asserted -> no false positives
    assert report.recall == 1.0  # no gold positives to miss


def test_minted_only_yields_unit_pr() -> None:
    universe = JurisdictionUniverse()
    report = score_locus_jurisdictions(["us-ak-city-nome", "us-al-city-alabaster"], universe)
    assert report.minted == 2
    assert report.gold_positives == 0
    assert report.asserted_links == 0
    assert report.precision == 1.0 and report.recall == 1.0
