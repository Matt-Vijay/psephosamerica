"""Offline tests for the House roll-call corpus builder.

Uses a small real-format roll-call XML string (no network) to exercise parsing
(party/state/choice extraction) and the party-alignment signal + cross-pressure
flagging. The network fetch is exercised separately at runtime.
"""

from __future__ import annotations

from datetime import date

from src.runtime.house_clerk_corpus import (
    MemberVote,
    RollCall,
    build_vote_rows_from_rollcalls,
    parse_rollcall_xml,
)

_XML = """<?xml version="1.0"?>
<rollcall-vote>
  <vote-metadata>
    <congress>118</congress>
    <legis-num>H R 2872</legis-num>
    <vote-question>On Passage</vote-question>
    <action-date>18-Jan-2024</action-date>
  </vote-metadata>
  <vote-data>
    <recorded-vote><legislator name-id="A000370" party="D" state="NC">Adams</legislator><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="B001000" party="D" state="CA">Bee</legislator><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="C001000" party="D" state="CA">Cee</legislator><vote>Nay</vote></recorded-vote>
    <recorded-vote><legislator name-id="D001000" party="R" state="TX">Dee</legislator><vote>Nay</vote></recorded-vote>
    <recorded-vote><legislator name-id="E001000" party="R" state="TX">Eee</legislator><vote>Nay</vote></recorded-vote>
    <recorded-vote><legislator name-id="F001000" party="R" state="TX">Eff</legislator><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="G001000" party="R" state="FL">Gee</legislator><vote>Present</vote></recorded-vote>
  </vote-data>
</rollcall-vote>"""


def test_parse_rollcall_extracts_party_state_choice() -> None:
    rollcall = parse_rollcall_xml(_XML)
    assert rollcall is not None
    assert rollcall.congress == 118
    assert rollcall.vote_date == date(2024, 1, 18)
    assert rollcall.bill_id == "us_congress:118:h-r-2872"
    by_id = {vote.bioguide_id: vote for vote in rollcall.votes}
    assert by_id["A000370"].party == "D"
    assert by_id["A000370"].state == "NC"
    assert by_id["A000370"].choice == "yea"
    assert by_id["G001000"].choice == "present"


def test_build_vote_rows_signals_party_alignment_and_cross_pressure() -> None:
    rows = build_vote_rows_from_rollcalls([parse_rollcall_xml(_XML)])  # type: ignore[list-item]
    # 6 binary votes (the Present vote is dropped).
    assert len(rows) == 6
    by_id = {row.member_bioguide_id: row for row in rows}
    # Democrats leaned yea (2 yea, 1 nay) -> party_alignment +1; the Nay D defected.
    assert by_id["A000370"].signals["party_alignment"] == 1.0
    assert by_id["C001000"].is_cross_pressured is True
    assert by_id["A000370"].is_cross_pressured is False
    # Republicans leaned nay (2 nay, 1 yea) -> party_alignment -1; the Yea R defected.
    assert by_id["D001000"].signals["party_alignment"] == -1.0
    assert by_id["F001000"].is_cross_pressured is True


def test_build_drops_rollcalls_with_no_binary_votes() -> None:
    rollcall = RollCall(
        bill_id="b",
        question="Quorum",
        vote_date=date(2024, 1, 1),
        congress=118,
        votes=(MemberVote(bioguide_id="X", party="D", state="CA", choice="present"),),
    )
    assert build_vote_rows_from_rollcalls([rollcall]) == []
