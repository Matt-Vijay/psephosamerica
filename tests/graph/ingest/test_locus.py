from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.graph.ingest.locus import (
    LOCUS_ATTRIBUTION,
    LOCUS_LICENSE,
    LocusOrdinance,
    ordinance_bill_ref,
    ordinance_content_sha256,
    ordinance_row,
    parse_locus_row,
    parse_locus_rows,
)

_KNOWN = datetime(2026, 6, 20, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "header": "### 1.05.010 Name of municipality.",
        "content": "A. The City of King Cove shall continue as a municipal corporation.",
        "is_substantive": False,
        "function": "Context",
        "topic": None,
        "source_jurisdiction_type": "cities",
        "state": "ak",
        "city": "kingcove",
        "county": None,
        "opacity": -1.26,
        "paternalism": -1.18,
        "enforcement_discretion": -1.60,
        "problem_salience": -1.46,
    }
    base.update(overrides)
    return base


def test_parse_locus_row_city() -> None:
    parsed = parse_locus_row(_row())
    assert parsed is not None
    assert parsed.level == "city"
    assert parsed.jurisdiction.code == "us-ak-city-kingcove"
    assert parsed.jurisdiction.parent_id == "us-ak"
    assert parsed.dimension_scores == {
        "opacity": -1.26,
        "paternalism": -1.18,
        "enforcement_discretion": -1.60,
        "problem_salience": -1.46,
    }


def test_parse_locus_row_county() -> None:
    parsed = parse_locus_row(
        _row(source_jurisdiction_type="counties", city=None, county="monterey_county", state="ca")
    )
    assert parsed is not None
    assert parsed.level == "county"
    assert parsed.jurisdiction.code == "us-ca-county-monterey_county"


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": ""},  # no state
        {"state": "alaska"},  # not 2-letter
        {"source_jurisdiction_type": "townships"},  # unknown type
        {"city": ""},  # city type with no city
        {"function": "Banana"},  # invalid function
        {"content": "   "},  # empty content
        {"city": "***"},  # unslugifiable place
    ],
)
def test_parse_locus_row_skips_unjoinable(overrides: dict[str, object]) -> None:
    assert parse_locus_row(_row(**overrides)) is None


def test_county_row_uses_county_name_not_city() -> None:
    # A county-type row must read its place from `county`, ignoring a stray city.
    parsed = parse_locus_row(
        _row(source_jurisdiction_type="counties", city="ignored", county="erie", state="ny")
    )
    assert parsed is not None
    assert parsed.jurisdiction.code == "us-ny-county-erie"


def test_ordinance_bill_ref_deterministic_and_distinct() -> None:
    a = parse_locus_row(_row())
    b = parse_locus_row(_row(content="A different provision entirely."))
    assert a is not None and b is not None
    # Same row -> same id across calls.
    assert ordinance_bill_ref(a).canonical_id == ordinance_bill_ref(a).canonical_id
    # Different content in the same jurisdiction -> different id.
    assert ordinance_bill_ref(a).canonical_id != ordinance_bill_ref(b).canonical_id
    assert ordinance_bill_ref(a).canonical_id.startswith("cb-")


def test_same_text_different_jurisdiction_distinct_ids() -> None:
    ak = parse_locus_row(_row())
    ca = parse_locus_row(_row(state="ca", city="kingcove"))
    assert ak is not None and ca is not None
    assert ordinance_bill_ref(ak).canonical_id != ordinance_bill_ref(ca).canonical_id


def test_ordinance_row_carries_scores_and_attribution() -> None:
    parsed = parse_locus_row(_row(function="Rules", topic="Zoning", is_substantive=True))
    assert parsed is not None
    row = ordinance_row(parsed, known_at=_KNOWN)
    assert row.entity_type == "bill"
    assert row.enrichment_status == "pending"
    assert row.known_at == _KNOWN
    assert row.dossier_json is not None
    assert row.dossier_json["function"] == "Rules"
    assert row.dossier_json["topic"] == "Zoning"
    assert row.dossier_json["is_substantive"] is True
    assert row.dossier_json["license"] == LOCUS_LICENSE
    assert row.dossier_json["attribution"] == LOCUS_ATTRIBUTION
    assert row.dossier_json["dimension_scores"]["opacity"] == -1.26
    assert f"license:{LOCUS_LICENSE}" in row.external_ids
    # Anchor content hash matches the text digest, and known_at == valid_from.
    anchor = row.source_anchors[0]
    assert anchor.content_sha256 == ordinance_content_sha256(parsed)
    assert anchor.valid_from == _KNOWN.date()


def test_ordinance_row_requires_aware_known_at() -> None:
    parsed = parse_locus_row(_row())
    assert parsed is not None
    with pytest.raises(ValueError):
        ordinance_row(parsed, known_at=datetime(2026, 6, 20))  # naive


def test_parse_locus_rows_batch() -> None:
    records = [
        _row(),
        _row(state="ca", city="oakland", content="Oakland nuisance code."),
        _row(state="bad"),  # skipped
        _row(source_jurisdiction_type="counties", city=None, county="erie", state="ny"),
    ]
    rows, jurisdictions = parse_locus_rows(records, known_at=_KNOWN)
    assert len(rows) == 3  # one skipped
    assert set(jurisdictions) == {
        "us-ak-city-kingcove",
        "us-ca-city-oakland",
        "us-ny-county-erie",
    }
    assert all(r.entity_type == "bill" for r in rows)


def test_dimension_scores_drops_nulls() -> None:
    parsed = parse_locus_row(_row(opacity=None, paternalism=None))
    assert parsed is not None
    assert set(parsed.dimension_scores) == {"enforcement_discretion", "problem_salience"}


def test_ordinance_known_at_clamps_nothing_but_sets_valid_from() -> None:
    parsed = parse_locus_row(_row())
    assert parsed is not None
    row = ordinance_row(parsed, known_at=_KNOWN)
    assert row.source_anchors[0].valid_to is None


def test_locusordinance_jurisdiction_county_constructor() -> None:
    ord_ = LocusOrdinance(
        state="wa",
        place="King County",
        level="county",
        header="h",
        content="c",
        function="Rules",
        topic="Other",
        is_substantive=True,
        opacity=0.0,
        paternalism=0.0,
        enforcement_discretion=0.0,
        problem_salience=0.0,
    )
    assert ord_.jurisdiction.code == "us-wa-county-king_county"
