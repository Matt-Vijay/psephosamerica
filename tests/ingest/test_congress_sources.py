"""Tests for Congress ingest clients — URL builders, normalization, and XML parsing."""

from __future__ import annotations

import datetime
import textwrap

import pytest

from src.ingest.congress.congress_api import (
    bills_url,
    committees_url,
    cosponsors_url,
    member_detail_url,
    members_url,
    normalize_bill,
    normalize_committee,
    normalize_cosponsor,
    normalize_member,
)
from src.ingest.congress.house_votes import (
    extract_bioguide_ids,
    parse_house_vote_xml,
    roll_call_url as house_roll_call_url,
)
from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
    VoteCastRecord,
    VoteEventRecord,
)
from src.ingest.congress.senate_votes import (
    extract_lis_member_ids,
    parse_senate_vote_xml,
    roll_call_url as senate_roll_call_url,
)


# ===================================================================
# URL builder tests
# ===================================================================


class TestCongressAPIUrls:
    def test_members_url_default(self) -> None:
        url = members_url()
        assert "api.congress.gov/v3/member" in url
        assert "format=json" in url

    def test_members_url_with_congress(self) -> None:
        url = members_url(118)
        assert "member/congress/118" in url

    def test_member_detail_url(self) -> None:
        url = member_detail_url("P000197")
        assert "member/P000197" in url

    def test_committees_url(self) -> None:
        url = committees_url(118, "senate")
        assert "committee/congress/118/senate" in url

    def test_bills_url(self) -> None:
        url = bills_url(118, "hr")
        assert "bill/118/hr" in url

    def test_cosponsors_url(self) -> None:
        url = cosponsors_url(118, "hr", 1)
        assert "bill/118/hr/1/cosponsors" in url


class TestHouseVoteUrls:
    def test_roll_call_url(self) -> None:
        url = house_roll_call_url(2025, 42)
        assert url == "https://clerk.house.gov/evs/2025/roll042.xml"

    def test_roll_call_url_large_number(self) -> None:
        url = house_roll_call_url(2024, 1234)
        assert "roll1234.xml" in url


class TestSenateVoteUrls:
    def test_roll_call_url(self) -> None:
        url = senate_roll_call_url(118, 1, 5)
        assert "vote1181" in url
        assert "vote_118_1_00005.xml" in url


# ===================================================================
# Normalization tests
# ===================================================================


class TestNormalizeMember:
    def test_basic_member(self) -> None:
        raw = {
            "bioguideId": "P000197",
            "firstName": "Nancy",
            "lastName": "Pelosi",
            "directOrderName": "Nancy Pelosi",
            "partyName": "Democratic",
            "state": "CA",
            "currentMember": True,
            "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023-01-03"}]},
        }
        rec = normalize_member(raw)
        assert isinstance(rec, MemberRecord)
        assert rec.bioguide_id == "P000197"
        assert rec.chamber == "house"
        assert rec.party == "Democratic"
        assert rec.is_current is True

    def test_senate_member(self) -> None:
        raw = {
            "bioguideId": "S000148",
            "firstName": "Charles",
            "lastName": "Schumer",
            "directOrderName": "Charles Schumer",
            "lisId": "S270",
            "currentMember": True,
            "terms": {"item": [{"chamber": "Senate"}]},
        }
        rec = normalize_member(raw)
        assert rec.chamber == "senate"
        assert rec.lis_member_id == "S270"

    def test_bare_year_terms_parse_to_congress_term_boundaries(self) -> None:
        raw = {
            "bioguideId": "A000001",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "directOrderName": "Ada Lovelace",
            "currentMember": False,
            "terms": {
                "item": [
                    {
                        "chamber": "House of Representatives",
                        "startYear": "2023",
                        "endYear": "2025",
                    }
                ]
            },
        }
        rec = normalize_member(raw)
        assert rec.current_term_start == datetime.date(2023, 1, 3)
        assert rec.current_term_end == datetime.date(2025, 1, 3)


class TestNormalizeBill:
    def test_basic_bill(self) -> None:
        raw = {
            "congress": 118,
            "type": "HR",
            "number": 1,
            "title": "Test Bill",
            "introducedDate": "2023-01-09",
            "latestAction": {"actionDate": "2023-06-01", "text": "Passed"},
        }
        rec = normalize_bill(raw)
        assert isinstance(rec, BillRecord)
        assert rec.bill_type == "hr"
        assert rec.bill_number == 1
        assert rec.introduced_date == datetime.date(2023, 1, 9)


class TestNormalizeCommittee:
    def test_basic_committee(self) -> None:
        raw = {
            "systemCode": "ssfi00",
            "chamber": "Senate",
            "committeeTypeCode": "Standing",
            "name": "Committee on Finance",
        }
        rec = normalize_committee(raw, congress=118)
        assert isinstance(rec, CommitteeRecord)
        assert rec.committee_code == "ssfi00"
        assert rec.chamber == "senate"
        assert rec.committee_type == "standing"


class TestNormalizeCosponsor:
    def test_basic_cosponsor(self) -> None:
        raw = {
            "bioguideId": "A000360",
            "isOriginalCosponsor": True,
            "sponsorshipDate": "2023-01-09",
        }
        rec = normalize_cosponsor(raw, congress=118, bill_type="hr", bill_number=1)
        assert isinstance(rec, CosponsorRecord)
        assert rec.bioguide_id == "A000360"
        assert rec.is_original is True


# ===================================================================
# Vote XML parsing tests
# ===================================================================


HOUSE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>118</congress>
        <session>1</session>
        <rollcall-num>42</rollcall-num>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date date="2023-02-01">1-Feb-2023</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="P000197" party="D" state="CA">Pelosi</legislator>
          <vote>Yea</vote>
        </recorded-vote>
        <recorded-vote>
          <legislator name-id="J000289" party="R" state="OH">Jordan</legislator>
          <vote>Nay</vote>
        </recorded-vote>
        <recorded-vote>
          <legislator name-id="X000001" party="I" state="VT">Example</legislator>
          <vote>Not Voting</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")


class TestHouseVoteParsing:
    def test_parse_event(self) -> None:
        event, casts = parse_house_vote_xml(HOUSE_VOTE_XML)
        assert isinstance(event, VoteEventRecord)
        assert event.chamber == "house"
        assert event.congress == 118
        assert event.session_number == 1
        assert event.roll_call_number == 42
        assert event.vote_date == datetime.date(2023, 2, 1)
        assert event.result == "Passed"

    def test_parse_casts(self) -> None:
        _, casts = parse_house_vote_xml(HOUSE_VOTE_XML)
        assert len(casts) == 3
        assert all(c.chamber == "house" for c in casts)
        by_id = {c.bioguide_id: c for c in casts}
        assert by_id["P000197"].vote_option == "yea"
        assert by_id["J000289"].vote_option == "nay"
        assert by_id["X000001"].vote_option == "not_voting"

    def test_all_casts_have_bioguide(self) -> None:
        _, casts = parse_house_vote_xml(HOUSE_VOTE_XML)
        for c in casts:
            assert c.bioguide_id is not None

    def test_extract_bioguide_ids(self) -> None:
        ids = extract_bioguide_ids(HOUSE_VOTE_XML)
        assert ids == ["J000289", "P000197", "X000001"]


SENATE_VOTE_XML = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>118</congress>
      <session>1</session>
      <vote_number>10</vote_number>
      <vote_question_text>On the Nomination</vote_question_text>
      <vote_result_text>Confirmed</vote_result_text>
      <vote_date>February 15, 2023</vote_date>
      <members>
        <member>
          <lis_member_id>S270</lis_member_id>
          <member_full>Schumer (D-NY)</member_full>
          <vote_cast>Yea</vote_cast>
        </member>
        <member>
          <lis_member_id>S174</lis_member_id>
          <member_full>McConnell (R-KY)</member_full>
          <vote_cast>Nay</vote_cast>
        </member>
        <member>
          <lis_member_id>S999</lis_member_id>
          <member_full>Test (I-VT)</member_full>
          <vote_cast>Not Voting</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


class TestSenateVoteParsing:
    def test_parse_event(self) -> None:
        event, casts = parse_senate_vote_xml(SENATE_VOTE_XML)
        assert isinstance(event, VoteEventRecord)
        assert event.chamber == "senate"
        assert event.congress == 118
        assert event.roll_call_number == 10
        assert event.vote_date == datetime.date(2023, 2, 15)
        assert event.result == "Confirmed"

    def test_parse_event_strips_time_suffix_from_vote_date(self) -> None:
        xml = SENATE_VOTE_XML.replace(
            "<vote_date>February 15, 2023</vote_date>",
            "<vote_date>February 15, 2023, 12:15 PM</vote_date>",
        )
        event, _ = parse_senate_vote_xml(xml)
        assert event.vote_date == datetime.date(2023, 2, 15)

    def test_missing_vote_date_raises_value_error(self) -> None:
        xml = SENATE_VOTE_XML.replace(
            "<vote_date>February 15, 2023</vote_date>",
            "<vote_date></vote_date>",
        )
        with pytest.raises(ValueError, match="vote_date"):
            parse_senate_vote_xml(xml)

    def test_parse_casts_use_lis_id(self) -> None:
        _, casts = parse_senate_vote_xml(SENATE_VOTE_XML)
        assert len(casts) == 3
        for c in casts:
            assert c.lis_member_id is not None
            assert c.bioguide_id is None  # downstream crosswalk fills this

    def test_vote_options(self) -> None:
        _, casts = parse_senate_vote_xml(SENATE_VOTE_XML)
        by_lis = {c.lis_member_id: c for c in casts}
        assert by_lis["S270"].vote_option == "yea"
        assert by_lis["S174"].vote_option == "nay"
        assert by_lis["S999"].vote_option == "not_voting"

    def test_extract_lis_member_ids(self) -> None:
        ids = extract_lis_member_ids(SENATE_VOTE_XML)
        assert ids == ["S174", "S270", "S999"]


# ===================================================================
# Model validation tests
# ===================================================================


class TestVoteCastValidation:
    def test_requires_at_least_one_id(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            VoteCastRecord(
                chamber="house",
                congress=118,
                session_number=1,
                roll_call_number=1,
                vote_option="yea",
                bioguide_id=None,
                lis_member_id=None,
            )

    def test_bioguide_only(self) -> None:
        rec = VoteCastRecord(
            chamber="house", congress=118, session_number=1,
            roll_call_number=1, vote_option="yea", bioguide_id="P000197",
        )
        assert rec.bioguide_id == "P000197"

    def test_lis_only(self) -> None:
        rec = VoteCastRecord(
            chamber="senate", congress=118, session_number=1,
            roll_call_number=1, vote_option="nay", lis_member_id="S270",
        )
        assert rec.lis_member_id == "S270"
