"""Tests for the senate.gov roll-call parser (offline fixture)."""

from __future__ import annotations

from src.runtime.senate_corpus import parse_senate_vote_xml

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<roll_call_vote>
  <congress>119</congress>
  <session>1</session>
  <vote_number>1</vote_number>
  <vote_date>January 9, 2025, 05:30 PM</vote_date>
  <vote_question_text>On Cloture on the Motion to Proceed S. 5</vote_question_text>
  <vote_document_text>A bill relating to homeland security and border enforcement</vote_document_text>
  <document><document_name>S. 5</document_name></document>
  <members>
    <member><last_name>Alsobrooks</last_name><party>D</party><state>MD</state><vote_cast>Yea</vote_cast><lis_member_id>S428</lis_member_id></member>
    <member><last_name>Barrasso</last_name><party>R</party><state>WY</state><vote_cast>Nay</vote_cast><lis_member_id>S317</lis_member_id></member>
  </members>
</roll_call_vote>"""


def test_parse_senate_vote() -> None:
    row = parse_senate_vote_xml(_XML)
    assert row is not None
    assert row["bill_id"] == "us_congress:119:s-5"
    assert row["congress"] == 119
    assert row["date"] == "2025-01-09"
    assert row["votes"] == [["S428", "D", "MD", "yea"], ["S317", "R", "WY", "nay"]]
    assert "defense_national_security" in row["sectors"] or row["sectors"] == []


def test_parse_returns_none_on_no_members() -> None:
    assert (
        parse_senate_vote_xml(
            "<roll_call_vote><congress>119</congress><vote_date>January 9, 2025, 1:00 PM</vote_date><members></members></roll_call_vote>"
        )
        is None
    )
