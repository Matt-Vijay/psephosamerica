from __future__ import annotations

import pytest

from src.graph.entity_resolution.names import (
    PersonName,
    canonical_given_root,
    given_names_compatible,
    normalize_name_token,
)

# ── normalize_name_token ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Doe", "doe"),
        ("  Doe  ", "doe"),
        ("O'Brien", "obrien"),
        ("García", "garcia"),
        ("Núñez", "nunez"),
        ("Smith-Jones", "smith jones"),
        ("J.", "j"),
        ("MÜLLER", "muller"),
    ],
)
def test_normalize_name_token_folds_accents_punct_and_case(raw: str, expected: str) -> None:
    assert normalize_name_token(raw) == expected


def test_normalize_name_token_empty() -> None:
    assert normalize_name_token("   ") == ""
    assert normalize_name_token("...") == ""


# ── PersonName.parse: space order ──────────────────────────────────


def test_parse_simple_space_order() -> None:
    name = PersonName.parse("Jane Doe")
    assert name.given == "jane"
    assert name.family == "doe"
    assert name.middle == ()
    assert name.suffix is None
    assert name.nickname is None


def test_parse_with_middle_names() -> None:
    name = PersonName.parse("Jane Marie Anne Doe")
    assert name.given == "jane"
    assert name.middle == ("marie", "anne")
    assert name.family == "doe"


def test_parse_with_middle_initial() -> None:
    name = PersonName.parse("Jane M. Doe")
    assert name.given == "jane"
    assert name.middle == ("m",)
    assert name.family == "doe"


def test_parse_single_token_is_family() -> None:
    name = PersonName.parse("Madonna")
    assert name.given == ""
    assert name.family == "madonna"


@pytest.mark.parametrize("raw", ["", "   ", "Jr.", "."])
def test_parse_degenerate_input_yields_empty_parts(raw: str) -> None:
    name = PersonName.parse(raw)
    assert name.given == ""
    assert name.family == ""
    assert name.middle == ()


# ── PersonName.parse: comma order ──────────────────────────────────


def test_parse_comma_order() -> None:
    name = PersonName.parse("Doe, Jane Marie")
    assert name.given == "jane"
    assert name.middle == ("marie",)
    assert name.family == "doe"


def test_parse_comma_order_with_suffix_after_given() -> None:
    name = PersonName.parse("Doe, Jane Jr.")
    assert name.family == "doe"
    assert name.given == "jane"
    assert name.suffix == "jr"


# ── Suffix handling ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "suffix", "family"),
    [
        ("John Smith Jr.", "jr", "smith"),
        ("John Smith Sr", "sr", "smith"),
        ("John Smith III", "iii", "smith"),
        ("John Smith II", "ii", "smith"),
        ("John Smith IV", "iv", "smith"),
    ],
)
def test_parse_extracts_suffix(raw: str, suffix: str, family: str) -> None:
    name = PersonName.parse(raw)
    assert name.suffix == suffix
    assert name.family == family
    assert name.given == "john"


def test_suffix_is_not_confused_with_family() -> None:
    # "Sr" alone after a single name must not eat the only surname token.
    name = PersonName.parse("Cesar Chavez")
    assert name.family == "chavez"
    assert name.suffix is None


# ── Nickname extraction ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ['Robert "Bob" Smith', "Robert (Bob) Smith", "Robert 'Bob' Smith"],
)
def test_parse_extracts_quoted_and_parenthetical_nickname(raw: str) -> None:
    name = PersonName.parse(raw)
    assert name.given == "robert"
    assert name.family == "smith"
    assert name.nickname == "bob"


# ── Particles attach to the family name ────────────────────────────


def test_particle_attaches_to_family_space_order() -> None:
    name = PersonName.parse("Martin van Buren")
    assert name.given == "martin"
    assert name.family == "van buren"


def test_multi_particle_family() -> None:
    name = PersonName.parse("Charles de la Cruz")
    assert name.given == "charles"
    assert name.family == "de la cruz"


# ── blocking_key ───────────────────────────────────────────────────


def test_blocking_key_is_family_plus_given_initial() -> None:
    assert PersonName.parse("Jane Doe").blocking_key() == "doe|j"
    assert PersonName.parse("Doe, Jane").blocking_key() == "doe|j"


def test_blocking_key_groups_nickname_and_formal_given() -> None:
    # Bob and Robert share a canonical root, so their initial does too.
    assert PersonName.parse("Bob Smith").blocking_key() == "smith|r"
    assert PersonName.parse("Robert Smith").blocking_key() == "smith|r"


def test_blocking_key_single_name_has_empty_initial() -> None:
    assert PersonName.parse("Madonna").blocking_key() == "madonna|"


# ── canonical_given_root / nickname equivalence ────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Bob", "Robert"),
        ("Bill", "William"),
        ("Bill", "Will"),
        ("Jim", "James"),
        ("Liz", "Elizabeth"),
        ("Beth", "Elizabeth"),
        ("Tony", "Anthony"),
        ("Dick", "Richard"),
    ],
)
def test_canonical_given_root_unifies_nicknames(a: str, b: str) -> None:
    assert canonical_given_root(a) == canonical_given_root(b)


def test_canonical_given_root_distinct_names_differ() -> None:
    assert canonical_given_root("Robert") != canonical_given_root("William")


def test_canonical_given_root_unknown_name_is_identity() -> None:
    assert canonical_given_root("Zebadiah") == "zebadiah"


# ── given_names_compatible ─────────────────────────────────────────


def test_given_compatible_exact() -> None:
    assert given_names_compatible("Jane", "Jane") is True


def test_given_compatible_via_initial() -> None:
    assert given_names_compatible("Jane", "J") is True
    assert given_names_compatible("J", "Jane") is True


def test_given_compatible_via_nickname() -> None:
    assert given_names_compatible("Bob", "Robert") is True


def test_given_incompatible() -> None:
    assert given_names_compatible("Jane", "John") is False
    assert given_names_compatible("Jane", "Robert") is False


def test_given_compatible_initial_mismatch() -> None:
    assert given_names_compatible("Jane", "R") is False


def test_given_compatible_empty_is_false() -> None:
    assert given_names_compatible("", "Jane") is False
    assert given_names_compatible("Jane", "") is False


# ── comparison_vector between two PersonNames ──────────────────────


def test_comparison_vector_strong_match() -> None:
    a = PersonName.parse("Robert J. Smith Jr.")
    b = PersonName.parse("Smith, Bob James")
    vec = a.comparison_vector(b)
    assert vec.family_exact is True
    assert vec.given_compatible is True
    assert vec.middle_compatible is True  # "j" initial vs "james"
    assert vec.suffix_conflict is False  # one has jr, other unspecified


def test_comparison_vector_family_mismatch() -> None:
    a = PersonName.parse("Jane Doe")
    b = PersonName.parse("Jane Smith")
    vec = a.comparison_vector(b)
    assert vec.family_exact is False
    assert vec.given_compatible is True


def test_comparison_vector_suffix_conflict() -> None:
    a = PersonName.parse("John Smith Jr.")
    b = PersonName.parse("John Smith Sr.")
    vec = a.comparison_vector(b)
    assert vec.suffix_conflict is True


def test_comparison_vector_middle_initial_vs_full() -> None:
    a = PersonName.parse("Jane Marie Doe")
    b = PersonName.parse("Jane M. Doe")
    vec = a.comparison_vector(b)
    assert vec.middle_compatible is True


def test_comparison_vector_middle_conflict() -> None:
    a = PersonName.parse("Jane Marie Doe")
    b = PersonName.parse("Jane Anne Doe")
    vec = a.comparison_vector(b)
    assert vec.middle_compatible is False


def test_comparison_vector_missing_middle_is_compatible() -> None:
    a = PersonName.parse("Jane Doe")
    b = PersonName.parse("Jane Marie Doe")
    vec = a.comparison_vector(b)
    assert vec.middle_compatible is True  # absence never conflicts
