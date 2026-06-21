from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.ingest.openstates import (
    StateBill,
    StateRollCall,
    StateVote,
    parse_legislator_csv_row,
    state_bill_ref,
)
from src.graph.ingest.openstates_export import (
    BILLS_PER_PAGE,
    build_graph,
    fetch_state_bills,
)
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


def test_build_graph_skips_future_dated_bill_and_rollcall() -> None:
    """Leakage guard: a fact dated after the observation instant is skipped, not raised."""
    future_rc = StateRollCall(
        ocd_vote_id="ocd-vote/future",
        motion_text="Prefiled vote",
        result=None,
        start_date=date(2030, 1, 1),
        source_url="https://x/y",
        votes=(StateVote(voter_ocd_id=_JANE, voter_name="Jane", choice="yea", voter_state="tx"),),
    )
    future_bill = StateBill(
        ocd_bill_id="ocd-bill/future",
        state="tx",
        session="91",
        identifier="HB 999",
        title="Prefiled",
        chamber="lower",
        action_date=date(2030, 1, 1),  # after _OBS (2026)
        source_url="https://x/y",
        rollcalls=(future_rc,),
    )
    built = build_graph(
        legislator_records=_legislators(), bills=[future_bill, _bill()], first_observed_at=_OBS
    )
    # Only the present-dated bill survives; the future one is dropped without error.
    assert built.bills == 1
    assert all(r.entity_type != "bill" or "HB 999" not in r.display_name for r in built.rows)
    # All edges come from the present bill's roll call.
    assert all(e.known_at.year == 2026 for e in built.vote_edges)


class _FakeSession:
    """A RateLimitedSession stand-in returning canned /bills pages, counting calls."""

    def __init__(self, pages: list[dict], cap: int = 100) -> None:
        self._pages = pages
        self.call_cap = cap
        self.calls_used = 0
        self.requested: list[int] = []

    @property
    def exhausted(self) -> bool:
        return self.calls_used >= self.call_cap

    def get(self, path: str, params: dict) -> dict | None:
        if self.exhausted:
            return None
        page = int(params["page"])
        self.requested.append(page)
        self.calls_used += 1
        idx = page - 1
        if idx >= len(self._pages):
            return {"results": [], "pagination": {"max_page": len(self._pages)}}
        return self._pages[idx]


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, s: str) -> None:
        self.lines.append(s)


def _page_with_named_vote(page_no: int, total: int) -> dict:
    bill = {
        "id": f"ocd-bill/p{page_no}",
        "identifier": f"HB {page_no}",
        "session": "89",
        "jurisdiction": {"id": "ocd-jurisdiction/country:us/state:tx/government"},
        "latest_action_date": "2026-05-01",
        "votes": [
            {
                "id": f"ocd-vote/p{page_no}",
                "motion_text": "passage",
                "start_date": "2026-05-01",
                "votes": [
                    {
                        "option": "yes",
                        "voter": {"id": "ocd-person/" + "1" * 8 + "-1111-1111-1111-111111111111"},
                    }
                ],
            }
        ],
    }
    return {"results": [bill], "pagination": {"max_page": total}}


def test_fetch_state_bills_caps_pages_in_breadth() -> None:
    # 10 pages available, but breadth budget of 3 should fetch exactly 3.
    pages = [_page_with_named_vote(i, 10) for i in range(1, 11)]
    session = _FakeSession(pages)
    sink = _Sink()
    bills, n = fetch_state_bills(state="tx", session=session, max_pages=3, sink=sink)  # type: ignore[arg-type]
    assert n == 3
    assert session.requested == [1, 2, 3]
    assert len(bills) == 3
    assert BILLS_PER_PAGE == 20


def test_fetch_state_bills_stops_on_empty_page() -> None:
    pages = [_page_with_named_vote(1, 5)]  # page 2 onward empty
    session = _FakeSession(pages)
    sink = _Sink()
    bills, n = fetch_state_bills(state="tx", session=session, max_pages=5, sink=sink)  # type: ignore[arg-type]
    # Stops after the empty page-2 response.
    assert len(bills) == 1
    assert n == 2
