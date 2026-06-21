from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.openstates import (
    StateRollCall,
    StateVote,
    bill_external_keys,
    bill_provenance,
    legislator_external_id,
    normalize_openstates_choice,
    parse_legislator_csv_row,
    parse_state_bill,
    rollcall_provenance,
    state_bill_ref,
    state_from_ocd,
    state_vote_edge,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://openstates.org/x",
    content_sha256="a" * 64,
    first_observed_at=datetime(2026, 6, 1, tzinfo=UTC),
    valid_from=date(2026, 5, 1),
    known_at=datetime(2026, 5, 1, tzinfo=UTC),
)

_BILL_RECORD = {
    "id": "ocd-bill/38d45106-2bf9-4bdc-8ad1-2a86e1f4065f",
    "identifier": "HB 22",
    "title": "An act relating to taxes",
    "session": "89",
    "jurisdiction": {"id": "ocd-jurisdiction/country:us/state:tx/government", "name": "Texas"},
    "from_organization": {"classification": "lower", "name": "House"},
    "latest_action_date": "2026-05-20",
    "openstates_url": "https://openstates.org/tx/bills/89/HB22/",
    "votes": [
        {
            "id": "ocd-vote/aaaa",
            "motion_text": "Final passage",
            "result": "pass",
            "start_date": "2026-05-18",
            "sources": [{"url": "https://capitol.texas.gov/vote1"}],
            "votes": [
                {
                    "option": "yes",
                    "voter_name": "Jane Smith",
                    "voter": {
                        "id": "ocd-person/11111111-1111-1111-1111-111111111111",
                        "name": "Jane Smith",
                        "current_role": {"division_id": "ocd-division/country:us/state:tx/sldl:5"},
                    },
                },
                {
                    "option": "no",
                    "voter_name": "Bob Jones",
                    "voter": {"id": "ocd-person/22222222-2222-2222-2222-222222222222"},
                },
                {
                    "option": "absent",
                    "voter_name": "No OCD Member",
                },  # no voter id -> kept but no edge
            ],
        },
        {
            "id": "ocd-vote/tally-only",
            "motion_text": "Procedural",
            "start_date": "2026-05-17",
            "votes": [],  # tally-only roll call -> dropped (no per-legislator votes)
        },
    ],
}


# ── state_from_ocd ──────────────────────────────────────────────────


def test_state_from_ocd_jurisdiction() -> None:
    assert state_from_ocd("ocd-jurisdiction/country:us/state:tx/government") == "tx"


def test_state_from_ocd_division() -> None:
    assert state_from_ocd("ocd-division/country:us/state:ca/sldl:66") == "ca"


def test_state_from_ocd_none() -> None:
    assert state_from_ocd("garbage") is None


# ── choice normalization ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("yes", "yea"),
        ("no", "nay"),
        ("excused", "not_voting"),
        ("absent", "not_voting"),
        ("abstain", "abstain"),
        ("other", "abstain"),
        ("Yea", "yea"),
    ],
)
def test_normalize_choice(raw: str, expected: str) -> None:
    assert normalize_openstates_choice(raw) == expected


def test_normalize_choice_blank_or_unknown() -> None:
    assert normalize_openstates_choice("") is None
    assert normalize_openstates_choice("???") is None


# ── legislator CSV ──────────────────────────────────────────────────


def test_parse_legislator_csv_row() -> None:
    row = {
        "id": "ocd-person/11111111-1111-1111-1111-111111111111",
        "name": "Jane Smith",
        "current_party": "Republican",
        "_state": "tx",
        "birth_date": "1970-01-01",  # private field, must be ignored
    }
    rec = parse_legislator_csv_row(row, provenance=_PROV)
    assert rec is not None
    assert rec.entity_type == "person"
    assert rec.display_name == "Jane Smith"
    assert rec.party == "Republican"
    assert rec.jurisdiction == "us-tx"
    assert ("openstates", "ocd-person/11111111-1111-1111-1111-111111111111") in {
        (e.system, e.value) for e in rec.external_ids
    }


def test_parse_legislator_csv_row_bad_id() -> None:
    assert (
        parse_legislator_csv_row({"id": "x", "name": "Y", "_state": "tx"}, provenance=_PROV) is None
    )


def test_parse_legislator_csv_row_no_state() -> None:
    row = {"id": "ocd-person/11111111-1111-1111-1111-111111111111", "name": "Y"}
    assert parse_legislator_csv_row(row, provenance=_PROV) is None


# ── bill parsing ────────────────────────────────────────────────────


def test_parse_state_bill() -> None:
    bill = parse_state_bill(_BILL_RECORD)
    assert bill is not None
    assert bill.state == "tx"
    assert bill.session == "89"
    assert bill.identifier == "HB 22"
    assert bill.chamber == "lower"
    assert bill.action_date == date(2026, 5, 20)
    # Only the roll call with named voters survives; the tally-only one is dropped.
    assert len(bill.rollcalls) == 1
    rc = bill.rollcalls[0]
    assert rc.start_date == date(2026, 5, 18)
    # Three voter rows in, but the one without a usable identity is still kept
    # (it has a voter_name); only the OCD-less one yields no edge later.
    options = [v.choice for v in rc.votes]
    assert "yea" in options and "nay" in options


def test_parse_state_bill_skips_malformed() -> None:
    assert parse_state_bill({"id": "not-a-bill"}) is None
    assert parse_state_bill({"id": "ocd-bill/x", "identifier": ""}) is None
    assert (
        parse_state_bill(
            {
                "id": "ocd-bill/x",
                "identifier": "HB 1",
                "session": "1",
                "jurisdiction": {"id": "no-state-here"},
                "latest_action_date": "2026-01-01",
            }
        )
        is None
    )


def test_state_bill_ref_deterministic() -> None:
    bill = parse_state_bill(_BILL_RECORD)
    assert bill is not None
    ref = state_bill_ref(bill)
    assert ref.jurisdiction_id == "us-tx"
    assert ref.session_id == "89"
    assert ref.identifier == "hb-22"
    # Same triple -> same canonical id.
    assert state_bill_ref(bill).canonical_id == ref.canonical_id


def test_bill_external_keys_sorted_unique() -> None:
    bill = parse_state_bill(_BILL_RECORD)
    assert bill is not None
    keys = bill_external_keys(bill)
    assert keys == sorted(set(keys))
    assert any(k.startswith("openstates_bill:") for k in keys)
    assert any(k.startswith("canonical_bill:") for k in keys)


# ── vote edge ───────────────────────────────────────────────────────


def test_state_vote_edge_leakage_and_attrs() -> None:
    rollcall = StateRollCall(
        ocd_vote_id="ocd-vote/aaaa",
        motion_text="Final passage",
        result="pass",
        start_date=date(2026, 5, 18),
        source_url="https://capitol.texas.gov/vote1",
        votes=(),
    )
    vote = StateVote(
        voter_ocd_id="ocd-person/11111111-1111-1111-1111-111111111111",
        voter_name="Jane Smith",
        choice="yea",
        voter_state="tx",
    )
    prov = rollcall_provenance(
        rollcall, content_sha256="b" * 64, first_observed_at=datetime(2026, 6, 1, tzinfo=UTC)
    )
    edge = state_vote_edge(
        voter_canonical_id="ce-voter",
        bill_canonical_id="cb-bill",
        vote=vote,
        rollcall=rollcall,
        provenance=prov,
    )
    assert edge.edge_type == "vote"
    assert edge.src_id == "ce-voter"
    assert edge.dst_id == "cb-bill"
    assert edge.attributes["choice"] == "yea"
    assert edge.attributes["result"] == "pass"
    assert edge.external_key == "ocd-vote/aaaa"
    # Leakage gate: known at the vote day, not before.
    assert edge.known_at == datetime(2026, 5, 18, tzinfo=UTC)
    assert not edge.known_as_of(datetime(2026, 5, 17, tzinfo=UTC))
    assert edge.known_as_of(datetime(2026, 5, 18, tzinfo=UTC))


def test_bill_provenance_known_at_action_date() -> None:
    bill = parse_state_bill(_BILL_RECORD)
    assert bill is not None
    prov = bill_provenance(
        bill, content_sha256="c" * 64, first_observed_at=datetime(2026, 6, 1, tzinfo=UTC)
    )
    assert prov.valid_from == date(2026, 5, 20)
    assert prov.known_at == datetime(2026, 5, 20, tzinfo=UTC)


def test_legislator_external_id() -> None:
    ext = legislator_external_id("ocd-person/abc")
    assert ext.system == "openstates"
    assert ext.value == "ocd-person/abc"
