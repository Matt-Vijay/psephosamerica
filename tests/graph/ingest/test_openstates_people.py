from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.openstates_people import (
    parse_openstates_person_filename,
    parse_openstates_person_yaml,
)
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://github.com/openstates/people/blob/main/data/ak/x.yml",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)

_YAML = """
id: ocd-person/7db414a5-dd7e-48a9-aba2-9c0204f93a5c
name: Andi Story
given_name: Andi
family_name: Story
birth_date: 1959-04-02
gender: Female
email: representative.andi.story@akleg.gov
party:
- name: Democratic
roles:
- start_date: 2023-01-17
  type: lower
  jurisdiction: ocd-jurisdiction/country:us/state:ak/government
  district: '3'
"""


def test_parses_public_conduct_fields() -> None:
    rec = parse_openstates_person_yaml(_YAML, provenance=_PROV)
    assert rec.entity_type == "person"
    assert rec.source_system == "openstates"
    assert rec.display_name == "Andi Story"
    assert rec.party == "Democratic"
    assert rec.jurisdiction == Jurisdiction.state("ak").code == "us-ak"
    assert rec.region == "AK"
    assert rec.external_id_keys == frozenset(
        {"openstates:ocd-person/7db414a5-dd7e-48a9-aba2-9c0204f93a5c"}
    )


def test_drops_private_life_fields() -> None:
    # Hard constraint: public conduct only. birth_date / email / gender are not
    # carried anywhere on the record.
    rec = parse_openstates_person_yaml(_YAML, provenance=_PROV)
    blob = rec.model_dump_json()
    assert "1959" not in blob
    assert "akleg.gov" not in blob
    assert "Female" not in blob


def test_person_name_parses() -> None:
    rec = parse_openstates_person_yaml(_YAML, provenance=_PROV)
    name = rec.person_name()
    assert name is not None
    assert name.given == "andi"
    assert name.family == "story"


def test_missing_id_rejected() -> None:
    with pytest.raises(ValueError, match="id"):
        parse_openstates_person_yaml("name: No Id\nroles: []\n", provenance=_PROV)


def test_missing_state_rejected() -> None:
    bad = "id: ocd-person/x\nname: Floating Person\nroles: []\n"
    with pytest.raises(ValueError, match="state"):
        parse_openstates_person_yaml(bad, provenance=_PROV)


def test_missing_name_rejected() -> None:
    bad = (
        "id: ocd-person/x\nroles:\n- jurisdiction: "
        "ocd-jurisdiction/country:us/state:tx/government\n"
    )
    with pytest.raises(ValueError, match="name"):
        parse_openstates_person_yaml(bad, provenance=_PROV)


def test_party_optional() -> None:
    y = (
        "id: ocd-person/x\nname: No Party\nroles:\n- jurisdiction: "
        "ocd-jurisdiction/country:us/state:tx/government\n"
    )
    rec = parse_openstates_person_yaml(y, provenance=_PROV)
    assert rec.party is None
    assert rec.jurisdiction == "us-tx"


def test_rejects_non_mapping_yaml() -> None:
    with pytest.raises(ValueError):
        parse_openstates_person_yaml("- just\n- a\n- list\n", provenance=_PROV)


def test_state_found_past_non_matching_roles() -> None:
    # First role has a non-string jurisdiction, second lacks a state, third has it.
    y = """
id: ocd-person/x
name: Multi Role
roles:
- jurisdiction: 123
- jurisdiction: ocd-jurisdiction/country:us/government
- jurisdiction: ocd-jurisdiction/country:us/state:ny/government
"""
    rec = parse_openstates_person_yaml(y, provenance=_PROV)
    assert rec.jurisdiction == "us-ny"


def test_parse_filename_roster() -> None:
    ocd, name = parse_openstates_person_filename(
        "Andi-Story-7db414a5-dd7e-48a9-aba2-9c0204f93a5c.yml"
    )
    assert ocd == "ocd-person/7db414a5-dd7e-48a9-aba2-9c0204f93a5c"
    assert name == "Andi Story"


def test_parse_filename_without_uuid_is_none() -> None:
    assert parse_openstates_person_filename("README.md") is None
    assert parse_openstates_person_filename("settings.yml") is None


def test_parse_filename_blank_name_is_none() -> None:
    assert parse_openstates_person_filename("-7db414a5-dd7e-48a9-aba2-9c0204f93a5c.yml") is None


def test_non_dict_party_entry_ignored() -> None:
    y = (
        "id: ocd-person/x\nname: Odd Party\nparty:\n- just-a-string\nroles:\n"
        "- jurisdiction: ocd-jurisdiction/country:us/state:tx/government\n"
    )
    rec = parse_openstates_person_yaml(y, provenance=_PROV)
    assert rec.party is None
