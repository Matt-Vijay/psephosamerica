"""Tests for House roll-call URL builders and XML parsing."""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.house_votes import (
    _vote_option,
    extract_bioguide_ids,
    parse_house_vote_xml,
    roll_call_index_url,
    roll_call_url,
)


def _xml(
    *,
    metadata: bool = True,
    date_attr: str | None = "2024-03-15",
    vote_data: str | None,
) -> str:
    meta = (
        """
        <vote-metadata>
          <congress>118</congress>
          <session>2</session>
          <rollcall-num>123</rollcall-num>
          <vote-question>On Passage</vote-question>
          <vote-result>Passed</vote-result>
          {action_date}
        </vote-metadata>
        """.format(
            action_date=f'<action-date date="{date_attr}"/>' if date_attr is not None else ""
        )
        if metadata
        else ""
    )
    body = vote_data or ""
    return f"<rollcall-vote>{meta}{body}</rollcall-vote>"


def test_roll_call_url_zero_pads_number() -> None:
    assert roll_call_url(2024, 7) == "https://clerk.house.gov/evs/2024/roll007.xml"


def test_roll_call_index_url() -> None:
    assert roll_call_index_url(2024) == "https://clerk.house.gov/evs/2024/index.asp"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yea", "yea"),
        ("Aye", "yea"),
        ("Nay", "nay"),
        ("No", "nay"),
        ("Present", "present"),
        ("Not Voting", "not_voting"),
        ("anything-else", "not_voting"),
    ],
)
def test_vote_option_mapping(raw: str, expected: str) -> None:
    assert _vote_option(raw) == expected


def test_parse_house_vote_xml_requires_metadata() -> None:
    with pytest.raises(ValueError, match="Missing <vote-metadata>"):
        parse_house_vote_xml(_xml(metadata=False, vote_data="<vote-data/>"))


def test_parse_house_vote_xml_parses_event_and_casts_and_skips_bad_voters() -> None:
    vote_data = """
    <vote-data>
      <recorded-vote><legislator name-id="A000001">A</legislator><vote>Yea</vote></recorded-vote>
      <recorded-vote><legislator>NoId</legislator><vote>Nay</vote></recorded-vote>
      <recorded-vote><vote>Aye</vote></recorded-vote>
    </vote-data>
    """
    event, casts = parse_house_vote_xml(_xml(vote_data=vote_data))
    assert event.chamber == "house"
    assert event.congress == 118
    assert event.roll_call_number == 123
    assert event.vote_date == datetime.date(2024, 3, 15)
    assert event.source_url == "https://clerk.house.gov/evs/2024/roll123.xml"
    # Only the voter with a usable bioguide id is kept (no-id and no-legislator skipped).
    assert [(c.bioguide_id, c.vote_option) for c in casts] == [("A000001", "yea")]


def test_parse_house_vote_xml_falls_back_to_dmy_date_format() -> None:
    event, _ = parse_house_vote_xml(_xml(date_attr="15-Mar-2024", vote_data="<vote-data/>"))
    assert event.vote_date == datetime.date(2024, 3, 15)


def test_parse_house_vote_xml_defaults_missing_date_to_today() -> None:
    event, _ = parse_house_vote_xml(_xml(date_attr=None, vote_data="<vote-data/>"))
    assert event.vote_date == datetime.date.today()


def test_parse_house_vote_xml_handles_absent_vote_data() -> None:
    event, casts = parse_house_vote_xml(_xml(vote_data=None))
    assert casts == []
    assert event.roll_call_number == 123


def test_extract_bioguide_ids_collects_unique_sorted_and_skips_idless() -> None:
    xml = """
    <rollcall-vote><vote-data>
      <recorded-vote><legislator name-id="B000002"/></recorded-vote>
      <recorded-vote><legislator bioguideid="A000001"/></recorded-vote>
      <recorded-vote><legislator name-id="A000001"/></recorded-vote>
      <recorded-vote><legislator/></recorded-vote>
    </vote-data></rollcall-vote>
    """
    assert extract_bioguide_ids(xml) == ["A000001", "B000002"]
