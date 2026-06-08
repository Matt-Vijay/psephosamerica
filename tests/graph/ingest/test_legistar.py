from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.legistar import is_active_person, parse_legistar_person
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://webapi.legistar.com/v1/seattle/persons",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)

_SEATTLE = Jurisdiction.city("wa", "Seattle")

_RECORD = {
    "PersonId": 240,
    "PersonFirstName": "Jane",
    "PersonLastName": "Councilmember",
    "PersonFullName": "Jane Councilmember",
    "PersonActiveFlag": 1,
    "PersonEmail": "jane@seattle.gov",
    "PersonAddress1": "600 4th Ave",
    "PersonPhone": "206-555-1234",
}


def test_parses_council_member() -> None:
    rec = parse_legistar_person(
        _RECORD, client="seattle", jurisdiction=_SEATTLE, region="WA", provenance=_PROV
    )
    assert rec.entity_type == "person"
    assert rec.source_system == "legistar"
    assert rec.display_name == "Jane Councilmember"
    assert rec.jurisdiction == "us-wa-city-seattle"
    assert rec.region == "WA"
    assert rec.external_id_keys == frozenset({"legistar:seattle:240"})


def test_drops_private_contact_fields() -> None:
    # Hard constraint: public conduct only; no personal contact/address.
    rec = parse_legistar_person(
        _RECORD, client="seattle", jurisdiction=_SEATTLE, region="WA", provenance=_PROV
    )
    blob = rec.model_dump_json()
    assert "seattle.gov" not in blob
    assert "4th Ave" not in blob
    assert "206-555" not in blob


def test_falls_back_to_first_last_when_no_full_name() -> None:
    rec = parse_legistar_person(
        {"PersonId": 1, "PersonFirstName": "Bob", "PersonLastName": "Smith"},
        client="sf",
        jurisdiction=Jurisdiction.city("ca", "San Francisco"),
        region="CA",
        provenance=_PROV,
    )
    assert rec.display_name == "Bob Smith"


def test_missing_person_id_rejected() -> None:
    with pytest.raises(ValueError, match="PersonId"):
        parse_legistar_person(
            {"PersonFullName": "No Id"},
            client="seattle",
            jurisdiction=_SEATTLE,
            region="WA",
            provenance=_PROV,
        )


def test_blank_name_rejected() -> None:
    with pytest.raises(ValueError, match="name"):
        parse_legistar_person(
            {"PersonId": 5, "PersonFullName": "  "},
            client="seattle",
            jurisdiction=_SEATTLE,
            region="WA",
            provenance=_PROV,
        )


def test_is_active_person() -> None:
    assert is_active_person({"PersonActiveFlag": 1}) is True
    assert is_active_person({"PersonActiveFlag": 0}) is False
    assert is_active_person({}) is False
