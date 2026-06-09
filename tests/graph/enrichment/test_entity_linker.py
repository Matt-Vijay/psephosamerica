from __future__ import annotations

from src.graph.enrichment.entity_linker import (
    EntityLinker,
    extract_person_names,
    link_precision_recall,
    roster_from_names,
)


def _linker() -> EntityLinker:
    return EntityLinker(
        roster_from_names(
            {
                "ce-pelosi": "Nancy Pelosi",
                "ce-schumer": "Charles Schumer",
                "ce-aoc": "Alexandria Ocasio-Cortez",
                "ce-smith-bob": "Robert Smith",
                "ce-smith-jane": "Jane Smith",
            }
        )
    )


# ── extraction ─────────────────────────────────────────────────────


def test_extract_title_prefixed_names() -> None:
    names = extract_person_names("Rep. Nancy Pelosi and Senator Charles Schumer spoke.")
    assert "Nancy Pelosi" in names
    assert "Charles Schumer" in names


def test_extract_bare_names() -> None:
    assert "Jane Smith" in extract_person_names("The motion by Jane Smith carried.")


def test_extract_dedupes_order_preserving() -> None:
    names = extract_person_names("Nancy Pelosi met Charles Schumer; Nancy Pelosi left.")
    assert names == ["Nancy Pelosi", "Charles Schumer"]


# ── linking ────────────────────────────────────────────────────────


def test_links_unambiguous_full_name() -> None:
    assert _linker().link("Nancy Pelosi") == "ce-pelosi"


def test_links_via_given_compatibility() -> None:
    # "Bob Smith" -> Robert Smith (nickname), and unambiguous (Jane Smith differs).
    assert _linker().link("Bob Smith") == "ce-smith-bob"


def test_ambiguous_family_only_is_dropped() -> None:
    # "Smith" alone matches both Smiths -> no link (precision over recall).
    assert _linker().link("Smith") is None


def test_unknown_name_is_none() -> None:
    assert _linker().link("Zebediah Nobody") is None


def test_mention_without_family_is_none() -> None:
    # A degenerate mention with no surname can't link.
    assert _linker().link("") is None
    assert _linker().link(".") is None


def test_hyphenated_surname() -> None:
    assert _linker().link("Alexandria Ocasio-Cortez") == "ce-aoc"


def test_link_text_finds_multiple() -> None:
    text = "Rep. Nancy Pelosi and Sen. Charles Schumer; also Jane Smith voted."
    links = dict(_linker().link_text(text))
    assert links == {
        "Nancy Pelosi": "ce-pelosi",
        "Charles Schumer": "ce-schumer",
        "Jane Smith": "ce-smith-jane",
    }


# ── precision / recall ─────────────────────────────────────────────


def test_precision_recall_perfect() -> None:
    labeled = [
        ("Rep. Nancy Pelosi spoke.", {"ce-pelosi"}),
        ("Jane Smith and Bob Smith attended.", {"ce-smith-jane", "ce-smith-bob"}),
    ]
    pr = link_precision_recall(_linker(), labeled)
    assert pr["precision"] == 1.0
    assert pr["recall"] == 1.0
    assert pr["f1"] == 1.0


def test_precision_recall_reports_misses() -> None:
    # "Smith" is ambiguous (dropped) -> a recall miss; no false positives.
    labeled = [("The Smith amendment failed.", {"ce-smith-jane"})]
    pr = link_precision_recall(_linker(), labeled)
    assert pr["fn"] == 1.0
    assert pr["fp"] == 0.0
    assert pr["recall"] == 0.0
    assert pr["precision"] == 1.0  # no wrong links


def test_empty_labeled_is_unit_precision_recall() -> None:
    pr = link_precision_recall(_linker(), [])
    assert pr["precision"] == 1.0
    assert pr["recall"] == 1.0
