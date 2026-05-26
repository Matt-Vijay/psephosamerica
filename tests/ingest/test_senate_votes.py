"""Tests for Senate roll-call URL builders and XML parsing."""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.senate_votes import (
    _vote_option,
    extract_lis_member_ids,
    parse_senate_vote_date,
    parse_senate_vote_xml,
    roll_call_list_url,
    roll_call_url,
)


def test_roll_call_url_zero_pads_vote_number() -> None:
    assert (
        roll_call_url(118, 2, 7)
        == "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1182/vote_118_2_00007.xml"
    )


def test_roll_call_list_url() -> None:
    assert (
        roll_call_list_url(118, 2)
        == "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1182/vote_summary.xml"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yea", "yea"),
        ("Guilty", "yea"),
        ("Nay", "nay"),
        ("Not Guilty", "nay"),
        ("Present", "present"),
        ("Not Voting", "not_voting"),
        ("???", "not_voting"),
    ],
)
def test_vote_option_mapping(raw: str, expected: str) -> None:
    assert _vote_option(raw) == expected


def test_parse_senate_vote_date_iso() -> None:
    assert parse_senate_vote_date("2024-01-15T12:00:00") == datetime.date(2024, 1, 15)


def test_parse_senate_vote_date_long_form_with_trailing_time() -> None:
    assert parse_senate_vote_date("January 15, 2024, 12:00 PM") == datetime.date(2024, 1, 15)


def test_parse_senate_vote_date_requires_value() -> None:
    with pytest.raises(ValueError, match="vote_date is required"):
        parse_senate_vote_date("")


def test_parse_senate_vote_date_rejects_unparseable() -> None:
    # Reaches the long-form branch but strptime fails (not a real month).
    with pytest.raises(ValueError, match="unparseable vote_date"):
        parse_senate_vote_date("Foo 1, 2024")
    # No comma parts at all -> skips the long-form branch entirely.
    with pytest.raises(ValueError, match="unparseable vote_date"):
        parse_senate_vote_date("garbage")


def _xml(*, members: str | None) -> str:
    body = members if members is not None else ""
    return f"""
    <roll_call_vote>
      <congress>118</congress>
      <session>2</session>
      <vote_number>123</vote_number>
      <vote_question_text>On the Nomination</vote_question_text>
      <vote_result_text>Confirmed</vote_result_text>
      <vote_date>January 15, 2024, 12:00 PM</vote_date>
      {body}
    </roll_call_vote>
    """


def test_parse_senate_vote_xml_parses_event_and_skips_idless_members() -> None:
    members = """
    <members>
      <member><lis_member_id>S001</lis_member_id><vote_cast>Yea</vote_cast></member>
      <member><vote_cast>Nay</vote_cast></member>
    </members>
    """
    event, casts = parse_senate_vote_xml(_xml(members=members))
    assert event.chamber == "senate"
    assert event.roll_call_number == 123
    assert event.vote_date == datetime.date(2024, 1, 15)
    assert event.source_url.endswith("/vote_118_2_00123.xml")
    # Only the member carrying a lis_member_id is kept; bioguide_id stays None.
    assert [(c.lis_member_id, c.vote_option, c.bioguide_id) for c in casts] == [
        ("S001", "yea", None)
    ]


def test_parse_senate_vote_xml_handles_absent_members() -> None:
    event, casts = parse_senate_vote_xml(_xml(members=None))
    assert casts == []
    assert event.roll_call_number == 123


def test_extract_lis_member_ids_unique_sorted_skips_idless() -> None:
    members = """
    <members>
      <member><lis_member_id>S002</lis_member_id></member>
      <member><lis_member_id>S001</lis_member_id></member>
      <member><lis_member_id>S001</lis_member_id></member>
      <member><vote_cast>Yea</vote_cast></member>
    </members>
    """
    assert extract_lis_member_ids(_xml(members=members)) == ["S001", "S002"]
