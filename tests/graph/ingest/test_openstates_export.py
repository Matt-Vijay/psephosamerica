from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.ingest.openstates import (
    StateBill,
    StateRollCall,
    StateVote,
    parse_legislator_csv_row,
    state_bill_ref,
)
from src.graph.ingest.openstates_export import build_graph
from src.graph.provenance import ProvenanceEnvelope

_OBS = datetime(2026, 6, 10, tzinfo=UTC)
_PROV = ProvenanceEnvelope(
    source_url="https://data.openstates.org/people/current/tx.csv",
    content_sha256="a" * 64,
    first_observed_at=_OBS,
    valid_from=_OBS.date(),
    known_at=_OBS,
)

_JANE = "ocd-person/11111111-1111-1111-1111-111111111111"
_BOB = "ocd-person/22222222-2222-2222-2222-222222222222"


def _legislators() -> list:
    return [
        parse_legislator_csv_row(
            {"id": _JANE, "name": "Jane Smith", "current_party": "Republican", "_state": "tx"},
            provenance=_PROV,
        ),
        parse_legislator_csv_row(
            {"id": _BOB, "name": "Bob Jones", "current_party": "Democratic", "_state": "tx"},
            provenance=_PROV,
        ),
    ]


def _bill() -> StateBill:
    rc = StateRollCall(
        ocd_vote_id="ocd-vote/aaaa",
        motion_text="Final passage",
        result="pass",
        start_date=date(2026, 5, 18),
        source_url="https://capitol.texas.gov/vote1",
        votes=(
            StateVote(voter_ocd_id=_JANE, voter_name="Jane Smith", choice="yea", voter_state="tx"),
            StateVote(voter_ocd_id=_BOB, voter_name="Bob Jones", choice="nay", voter_state="tx"),
            # A voter with no OCD id -> cannot resolve -> no edge.
            StateVote(voter_ocd_id=None, voter_name="Ghost", choice="yea", voter_state="tx"),
            # An OCD voter not in the roster -> no edge (skipped honestly).
            StateVote(
                voter_ocd_id="ocd-person/99999999-9999-9999-9999-999999999999",
                voter_name="Former Member",
                choice="yea",
                voter_state="tx",
            ),
        ),
    )
    return StateBill(
        ocd_bill_id="ocd-bill/38d45106-2bf9-4bdc-8ad1-2a86e1f4065f",
        state="tx",
        session="89",
        identifier="HB 22",
        title="An act",
        chamber="lower",
        action_date=date(2026, 5, 20),
        source_url="https://openstates.org/tx/bills/89/HB22/",
        rollcalls=(rc,),
    )


def test_build_graph_emits_persons_bill_and_two_vote_edges() -> None:
    built = build_graph(legislator_records=_legislators(), bills=[_bill()], first_observed_at=_OBS)
    persons = [r for r in built.rows if r.entity_type == "person"]
    bills = [r for r in built.rows if r.entity_type == "bill"]
    assert len(persons) == 2
    assert len(bills) == 1
    assert built.legislators == 2
    assert built.bills == 1
    # Two resolvable voters -> two vote edges (Ghost + Former Member skipped).
    assert len(built.vote_edges) == 2
    bill_cid = state_bill_ref(_bill()).canonical_id
    assert bills[0].canonical_id == bill_cid
    for edge in built.vote_edges:
        assert edge.edge_type == "vote"
        assert edge.dst_id == bill_cid
        assert edge.src_id in {p.canonical_id for p in persons}
        assert edge.attributes["choice"] in {"yea", "nay"}
        # Leakage stamp at the vote day.
        assert edge.known_at == datetime(2026, 5, 18, tzinfo=UTC)


def test_build_graph_bill_carries_canonical_bill_external_id() -> None:
    built = build_graph(legislator_records=_legislators(), bills=[_bill()], first_observed_at=_OBS)
    (bill_row,) = [r for r in built.rows if r.entity_type == "bill"]
    assert any(k.startswith("openstates_bill:") for k in bill_row.external_ids)
    assert any(k.startswith("canonical_bill:") for k in bill_row.external_ids)


def test_build_graph_no_legislators_no_edges() -> None:
    built = build_graph(legislator_records=[], bills=[_bill()], first_observed_at=_OBS)
    assert built.legislators == 0
    assert built.bills == 1
    assert built.vote_edges == []


def test_build_graph_dedupes_vote_edges_on_rerun() -> None:
    bills = [_bill(), _bill()]  # same bill twice
    built = build_graph(legislator_records=_legislators(), bills=bills, first_observed_at=_OBS)
    # Bill de-duped to one row; edges de-duped by identity to two.
    assert len([r for r in built.rows if r.entity_type == "bill"]) == 1
    assert len(built.vote_edges) == 2
