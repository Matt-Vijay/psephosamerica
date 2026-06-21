from __future__ import annotations

from src.graph.entity_resolution.jurisdiction_link import (
    JurisdictionUniverse,
    fold_place,
    jurisdiction_from_locus,
    resolve_jurisdiction,
)
from src.graph.jurisdictions import Jurisdiction


def test_fold_place_separator_insensitive() -> None:
    assert fold_place("King Cove") == "kingcove"
    assert fold_place("king_cove") == "kingcove"
    assert fold_place("St. Paul") == "stpaul"
    assert fold_place("San José") == "sanjose"  # accent folded


def test_exact_code_match() -> None:
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.city("ca", "Oakland"))
    locus = jurisdiction_from_locus(state="ca", place="oakland", level="city")
    match = resolve_jurisdiction(locus, universe)
    assert match.method == "exact_code"
    assert match.canonical_code == "us-ca-city-oakland"
    assert match.is_linked


def test_folded_match_bridges_spacing() -> None:
    # Canonical entity registered from a display name ("King Cove" -> king_cove);
    # LOCUS slug is "kingcove". Folded key bridges them.
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.city("ak", "King Cove"))  # -> us-ak-city-king_cove
    locus = jurisdiction_from_locus(
        state="ak", place="kingcove", level="city"
    )  # us-ak-city-kingcove
    assert locus.code == "us-ak-city-kingcove"
    match = resolve_jurisdiction(locus, universe)
    assert match.method == "folded"
    assert match.canonical_code == "us-ak-city-king_cove"
    assert match.is_linked
    assert not match.ambiguous


def test_collision_held_apart_not_merged() -> None:
    # Two DISTINCT canonical places in the same state+level folding to one key.
    universe = JurisdictionUniverse()
    # Two distinct canonical codes that BOTH fold to "stpaul" (variant spellings),
    # neither equal to the LOCUS query code -- the municipal alias collision.
    universe.add_code("us-mn-city-st_paul")  # folds to "stpaul"
    universe.add_code("us-mn-city-s_t_paul")  # also folds to "stpaul"
    assert ("mn", "city", "stpaul") in universe.collision_keys
    # LOCUS slug "St. Paul" -> code us-mn-city-st_paul, equals one exactly.
    exact = resolve_jurisdiction(
        jurisdiction_from_locus(state="mn", place="St. Paul", level="city"), universe
    )
    assert exact.method == "exact_code"  # never force-merges into the other variant
    # LOCUS slug "StPaul" (one token) -> code us-mn-city-stpaul, equals NEITHER:
    locus = jurisdiction_from_locus(state="mn", place="StPaul", level="city")
    assert locus.code == "us-mn-city-stpaul"
    match = resolve_jurisdiction(locus, universe)
    assert match.method == "ambiguous"
    assert match.ambiguous
    assert match.canonical_code is None  # held apart, never force-merged
    assert set(match.candidates) == {"us-mn-city-st_paul", "us-mn-city-s_t_paul"}


def test_minted_when_unknown() -> None:
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.city("ca", "Oakland"))
    locus = jurisdiction_from_locus(state="ak", place="nome", level="city")
    match = resolve_jurisdiction(locus, universe)
    assert match.method == "minted"
    assert match.canonical_code == "us-ak-city-nome"  # LOCUS-attested
    assert not match.is_linked


def test_county_level_resolves_folded() -> None:
    # Canonical "King County" -> us-wa-county-king_county; LOCUS slug "kingcounty"
    # (no separator) folds to the same key but is not the same code.
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.county("wa", "King County"))
    locus = jurisdiction_from_locus(state="wa", place="kingcounty", level="county")
    assert locus.code == "us-wa-county-kingcounty"
    match = resolve_jurisdiction(locus, universe)
    assert match.method == "folded"
    assert match.canonical_code == "us-wa-county-king_county"


def test_city_and_county_same_name_dont_collide() -> None:
    # Level discriminator keeps city/county apart even with the same place name.
    universe = JurisdictionUniverse()
    universe.add(Jurisdiction.county("ca", "Sonoma"))
    locus_city = jurisdiction_from_locus(state="ca", place="sonoma", level="city")
    match = resolve_jurisdiction(locus_city, universe)
    assert match.method == "minted"  # the county entity does not satisfy a city query
