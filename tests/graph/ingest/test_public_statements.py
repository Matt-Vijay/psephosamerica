from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.public_statements import (
    statement_alignment_edge,
    statement_provenance,
)

_MEMBER = "ce-person1"
_SECTOR = "sector:defense_national_security"


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://cuellar.house.gov/news/1",
        "content_sha256": "a" * 64,
        "statement_date": date(2008, 2, 29),
        "first_observed_at": datetime(2024, 1, 10, tzinfo=UTC),
    }
    base.update(overrides)
    return statement_provenance(**base)  # type: ignore[arg-type]


def test_statement_alignment_edge_fields() -> None:
    edge = statement_alignment_edge(
        member_canonical_id=_MEMBER,
        sector_id=_SECTOR,
        match_method="keyword",
        statement_id="statement-1c7b",
        provenance=_prov(),
    )
    assert edge.edge_type == "public_statement"
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _SECTOR
    assert edge.attributes["match_method"] == "keyword"
    assert edge.external_key == "statement-1c7b"


def test_blank_match_method_rejected() -> None:
    with pytest.raises(ValueError, match="match_method"):
        statement_alignment_edge(
            member_canonical_id=_MEMBER,
            sector_id=_SECTOR,
            match_method="  ",
            statement_id="s1",
            provenance=_prov(),
        )


def test_known_at_is_statement_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2008, 2, 29)
    assert prov.known_at == datetime(2008, 2, 29, tzinfo=UTC)


def test_leakage_gate() -> None:
    edge = statement_alignment_edge(
        member_canonical_id=_MEMBER,
        sector_id=_SECTOR,
        match_method="keyword",
        statement_id="s1",
        provenance=_prov(),
    )
    assert edge.known_as_of(datetime(2008, 2, 29, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2008, 2, 28, tzinfo=UTC)) is False


def test_distinct_statements_separate() -> None:
    a = statement_alignment_edge(
        member_canonical_id=_MEMBER,
        sector_id=_SECTOR,
        match_method="keyword",
        statement_id="s1",
        provenance=_prov(),
    )
    b = statement_alignment_edge(
        member_canonical_id=_MEMBER,
        sector_id=_SECTOR,
        match_method="keyword",
        statement_id="s2",
        provenance=_prov(),
    )
    assert a.edge_id != b.edge_id
