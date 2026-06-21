from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.usaspending import (
    FederalAward,
    award_appropriation_edge,
    award_bill_ref,
    award_family,
    award_provenance,
    federal_award_edge,
    parse_awarding_agency,
    parse_federal_award,
    parse_recipient_org,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://www.usaspending.gov/award/CONT_AWD_X",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 6, 1, tzinfo=UTC),
    valid_from=date(2022, 3, 15),
    known_at=datetime(2022, 3, 15, tzinfo=UTC),
)

# API-shape contract row.
_API_ROW = {
    "generated_internal_id": "CONT_AWD_HHSN123_7529",
    "recipient_name": "ACME RESEARCH INSTITUTE",
    "recipient_uei": "ABC123DEF456",
    "recipient_state_code": "ca",
    "awarding_toptier_agency_name": "Department of Health and Human Services",
    "awarding_toptier_agency_code": "075",
    "awarding_subtier_agency_name": "National Institutes of Health",
    "type": "C",
    "total_obligation": 1250000.5,
    "action_date": "2022-03-15",
    "public_law": "Public Law 117-103",
}

# Bulk-download-shape contract row (Title Case keys, $-formatted amount).
_BULK_ROW = {
    "Award ID": "BULK_AWD_42",
    "Recipient Name": "Beta Logistics LLC",
    "Recipient UEI": "ZZZ999YYY888",
    "Awarding Agency": "Department of Defense",
    "Awarding Sub Agency": "Department of the Army",
    "Award Type": "Definitive Contract",
    "Award Amount": "$3,400,000.00",
    "Action Date": "2023-07-01",
}


# ── award_family ───────────────────────────────────────────────────


def test_award_family_contract_code() -> None:
    assert award_family("C") == "contract"


def test_award_family_assistance_code() -> None:
    assert award_family("04") == "assistance"


def test_award_family_label_fallback() -> None:
    assert award_family("Definitive Contract") == "contract"
    assert award_family("Project Grant") == "assistance"


def test_award_family_unknown() -> None:
    assert award_family("ZZZ") is None


# ── parse_federal_award ────────────────────────────────────────────


def test_parse_api_row() -> None:
    award = parse_federal_award(_API_ROW)
    assert isinstance(award, FederalAward)
    assert award.award_id == "CONT_AWD_HHSN123_7529"
    assert award.recipient_name == "ACME RESEARCH INSTITUTE"
    assert award.recipient_uei == "ABC123DEF456"
    assert award.recipient_state == "CA"
    assert award.agency_name == "Department of Health and Human Services"
    assert award.agency_code == "075"
    assert award.sub_agency_name == "National Institutes of Health"
    assert award.family == "contract"
    assert award.amount == "1250000.50"
    assert award.action_date == date(2022, 3, 15)
    assert award.public_law == "117-103"
    assert award.congress == 117


def test_parse_bulk_row() -> None:
    award = parse_federal_award(_BULK_ROW)
    assert award is not None
    assert award.award_id == "BULK_AWD_42"
    assert award.recipient_uei == "ZZZ999YYY888"
    assert award.agency_name == "Department of Defense"
    assert award.sub_agency_name == "Department of the Army"
    assert award.family == "contract"
    assert award.amount == "3400000.00"
    assert award.public_law is None


def test_parse_missing_award_id_skipped() -> None:
    assert parse_federal_award({**_API_ROW, "generated_internal_id": ""}) is None


def test_parse_missing_recipient_skipped() -> None:
    row = {k: v for k, v in _API_ROW.items() if k != "recipient_name"}
    assert parse_federal_award(row) is None


def test_parse_missing_agency_skipped() -> None:
    row = {k: v for k, v in _API_ROW.items() if k != "awarding_toptier_agency_name"}
    assert parse_federal_award(row) is None


def test_parse_bad_date_skipped() -> None:
    assert parse_federal_award({**_API_ROW, "action_date": "not-a-date"}) is None


def test_parse_invalid_uei_dropped() -> None:
    award = parse_federal_award({**_API_ROW, "recipient_uei": "short"})
    assert award is not None
    assert award.recipient_uei is None


# ── recipient / agency orgs ────────────────────────────────────────


def test_recipient_uei_keyed() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    rec = parse_recipient_org(award, provenance=_PROV)
    assert rec.entity_type == "org"
    assert rec.source_system == "usaspending"
    assert "uei:abc123def456" in rec.external_id_keys
    assert rec.region == "CA"


def test_recipient_name_fallback_when_no_ids() -> None:
    award = FederalAward(
        award_id="X",
        recipient_name="No Id Co",
        recipient_uei=None,
        recipient_duns=None,
        recipient_hash=None,
        recipient_state=None,
        agency_name="Agency",
        agency_code=None,
        sub_agency_name=None,
        award_type="C",
        family="contract",
        amount=None,
        action_date=date(2022, 1, 1),
        public_law=None,
        congress=None,
    )
    rec = parse_recipient_org(award, provenance=_PROV)
    keys = list(rec.external_id_keys)
    assert len(keys) == 1
    assert keys[0].startswith("usaspending_recipient:name-")


def test_agency_keyed_on_code() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    agency = parse_awarding_agency(award, provenance=_PROV)
    assert agency.entity_type == "org"
    assert "usa_agency:075" in agency.external_id_keys


def test_agency_name_fallback_when_no_code() -> None:
    award = parse_federal_award(_BULK_ROW)
    assert award is not None
    agency = parse_awarding_agency(award, provenance=_PROV)
    keys = list(agency.external_id_keys)
    assert keys[0].startswith("usa_agency:name-")


# ── provenance ─────────────────────────────────────────────────────


def test_award_provenance_known_at_is_action_date() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    prov = award_provenance(
        award,
        source_url="https://www.usaspending.gov/award/CONT_AWD_HHSN123_7529",
        content_sha256="b" * 64,
        first_observed_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    assert prov.valid_from == date(2022, 3, 15)
    assert prov.known_at == datetime(2022, 3, 15, tzinfo=UTC)


# ── edges ──────────────────────────────────────────────────────────


def test_federal_award_edge() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    edge = federal_award_edge(
        recipient_canonical_id="ce-recipient",
        agency_canonical_id="ce-agency",
        award=award,
        provenance=_PROV,
    )
    assert edge.edge_type == "federal_award"
    assert edge.src_id == "ce-recipient"
    assert edge.dst_id == "ce-agency"
    assert edge.attributes["family"] == "contract"
    assert edge.attributes["amount"] == "1250000.50"
    assert edge.attributes["sub_agency"] == "National Institutes of Health"
    assert edge.attributes["public_law"] == "117-103"
    assert edge.external_key == "CONT_AWD_HHSN123_7529"


def test_federal_award_edge_self_loop_rejected() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    with pytest.raises(ValueError, match="self-loop"):
        federal_award_edge(
            recipient_canonical_id="ce-x",
            agency_canonical_id="ce-x",
            award=award,
            provenance=_PROV,
        )


def test_award_bill_ref_from_public_law() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    ref = award_bill_ref(award)
    assert ref is not None
    assert ref.session_id == "117"
    assert ref.identifier == "pl-117-103"
    assert ref.canonical_id.startswith("cb-")


def test_award_bill_ref_none_without_law() -> None:
    award = parse_federal_award(_BULK_ROW)
    assert award is not None
    assert award_bill_ref(award) is None


def test_appropriation_edge() -> None:
    award = parse_federal_award(_API_ROW)
    assert award is not None
    edge = award_appropriation_edge(
        agency_canonical_id="ce-agency",
        award=award,
        provenance=_PROV,
    )
    assert edge is not None
    assert edge.edge_type == "funded_by"
    assert edge.src_id == "ce-agency"
    assert edge.attributes["public_law"] == "117-103"


def test_appropriation_edge_none_without_law() -> None:
    award = parse_federal_award(_BULK_ROW)
    assert award is not None
    assert (
        award_appropriation_edge(agency_canonical_id="ce-a", award=award, provenance=_PROV) is None
    )
