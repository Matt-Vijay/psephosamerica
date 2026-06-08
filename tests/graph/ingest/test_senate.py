from __future__ import annotations

from datetime import date

import pytest

from src.graph.ingest.senate import (
    SenateRollCall,
    SenateMemberVote,
    bill_canonical_id_for,
    parse_senate_rollcall_xml,
)

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<roll_call_vote>
  <congress>118</congress>
  <session>1</session>
  <vote_number>10</vote_number>
  <vote_date>February 9, 2023,  11:00 AM</vote_date>
  <vote_result>Passed</vote_result>
  <document><document_name>S. 316</document_name></document>
  <members>
    <member>
      <member_full>Baldwin (D-WI)</member_full>
      <last_name>Baldwin</last_name>
      <first_name>Tammy</first_name>
      <party>D</party>
      <state>WI</state>
      <vote_cast>Yea</vote_cast>
      <lis_member_id>S354</lis_member_id>
    </member>
    <member>
      <member_full>Cruz (R-TX)</member_full>
      <last_name>Cruz</last_name>
      <first_name>Ted</first_name>
      <party>R</party>
      <state>TX</state>
      <vote_cast>Nay</vote_cast>
      <lis_member_id>S355</lis_member_id>
    </member>
  </members>
</roll_call_vote>
"""

_NOMINATION = """<roll_call_vote>
<congress>118</congress><session>1</session><vote_number>1</vote_number>
<vote_date>January 23, 2023,  05:26 PM</vote_date><vote_result>Confirmed</vote_result>
<document><document_name>PN1</document_name></document><members></members></roll_call_vote>"""


def test_parse_metadata() -> None:
    rc = parse_senate_rollcall_xml(_XML)
    assert isinstance(rc, SenateRollCall)
    assert rc.congress == 118
    assert rc.session == 1
    assert rc.vote_number == 10
    assert rc.document_name == "S. 316"
    assert rc.vote_result == "Passed"
    assert rc.vote_date == date(2023, 2, 9)


def test_parse_member_votes() -> None:
    rc = parse_senate_rollcall_xml(_XML)
    assert rc.recorded_votes == (
        SenateMemberVote("S354", "Tammy Baldwin", "WI", "D", "Yea"),
        SenateMemberVote("S355", "Ted Cruz", "TX", "R", "Nay"),
    )


def test_bill_canonical_id_for_bill_vote() -> None:
    rc = parse_senate_rollcall_xml(_XML)
    bill_id = bill_canonical_id_for(rc)
    assert bill_id is not None
    from src.graph.bills import BillRef

    assert bill_id == BillRef.for_congress(118, "S. 316").canonical_id


def test_nomination_has_no_bill() -> None:
    rc = parse_senate_rollcall_xml(_NOMINATION)
    assert rc.document_name == "PN1"
    assert bill_canonical_id_for(rc) is None
    assert rc.recorded_votes == ()


def test_blank_document_has_no_bill() -> None:
    xml = """<roll_call_vote><congress>118</congress><session>1</session>
    <vote_number>9</vote_number><vote_date>February 9, 2023,  11:00 AM</vote_date>
    <vote_result>Passed</vote_result><document><document_name></document_name></document>
    <members></members></roll_call_vote>"""
    rc = parse_senate_rollcall_xml(xml)
    assert rc.document_name is None
    assert bill_canonical_id_for(rc) is None


def test_missing_document_element_has_no_bill() -> None:
    xml = """<roll_call_vote><congress>118</congress><session>1</session>
    <vote_number>3</vote_number><vote_date>February 9, 2023,  11:00 AM</vote_date>
    <vote_result>Passed</vote_result><members></members></roll_call_vote>"""
    rc = parse_senate_rollcall_xml(xml)
    assert rc.document_name is None
    assert bill_canonical_id_for(rc) is None


def test_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="roll-call"):
        parse_senate_rollcall_xml("<other/>")


def test_rejects_non_xml() -> None:
    with pytest.raises(ValueError):
        parse_senate_rollcall_xml("<<<garbage")


def test_member_missing_fields_skipped() -> None:
    xml = """<roll_call_vote><congress>118</congress><session>1</session>
    <vote_number>5</vote_number><vote_date>February 9, 2023,  11:00 AM</vote_date>
    <vote_result>Passed</vote_result><document><document_name>S. 1</document_name></document><members>
    <member><first_name>No</first_name><last_name>Id</last_name><vote_cast>Yea</vote_cast></member>
    <member><lis_member_id>S1</lis_member_id><first_name>A</first_name><last_name>B</last_name><vote_cast></vote_cast></member>
    <member><lis_member_id>S2</lis_member_id><first_name>Real</first_name><last_name>One</last_name><state>NY</state><party>D</party><vote_cast>Yea</vote_cast></member>
    </members></roll_call_vote>"""
    rc = parse_senate_rollcall_xml(xml)
    assert rc.recorded_votes == (SenateMemberVote("S2", "Real One", "NY", "D", "Yea"),)


def test_unparseable_date_rejected() -> None:
    xml = _XML.replace("February 9, 2023,  11:00 AM", "sometime")
    with pytest.raises(ValueError, match="date"):
        parse_senate_rollcall_xml(xml)
