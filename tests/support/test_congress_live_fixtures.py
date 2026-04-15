"""Tests for tests/support/congress_live_fixtures.py.

Validates that every public helper returns the correct shape, that
pagination chains are correctly wired, that overrides are applied, and
that vote XML strings parse without error.

No network calls; all data is in-memory.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.support.congress_live_fixtures import (
    bill_detail_response,
    bill_item,
    bills_page,
    committee_item,
    committees_page,
    cosponsor_item,
    cosponsors_page,
    house_vote_xml,
    member_detail_response,
    member_item,
    members_page,
    paginated_bills,
    paginated_committees,
    paginated_cosponsors,
    paginated_members,
    senate_vote_xml,
)


# ---------------------------------------------------------------------------
# Item constructors
# ---------------------------------------------------------------------------


class TestMemberItem:
    def test_has_required_keys(self) -> None:
        m = member_item()
        assert "bioguideId" in m
        assert "firstName" in m
        assert "lastName" in m
        assert "chamber" not in m  # raw API, not normalised

    def test_default_bioguide_id(self) -> None:
        assert member_item()["bioguideId"] == "P000197"

    def test_override_bioguide_id(self) -> None:
        m = member_item(bioguideId="S000148", firstName="Chuck", lastName="Schumer")
        assert m["bioguideId"] == "S000148"
        assert m["firstName"] == "Chuck"

    def test_override_does_not_mutate_default(self) -> None:
        member_item(bioguideId="Z000001")
        assert member_item()["bioguideId"] == "P000197"

    def test_terms_structure(self) -> None:
        m = member_item()
        assert "terms" in m
        assert "item" in m["terms"]
        assert isinstance(m["terms"]["item"], list)


class TestCommitteeItem:
    def test_has_required_keys(self) -> None:
        c = committee_item()
        assert "systemCode" in c
        assert "chamber" in c
        assert "name" in c

    def test_default_system_code(self) -> None:
        assert committee_item()["systemCode"] == "hswm00"

    def test_override(self) -> None:
        c = committee_item(systemCode="ssfi00", chamber="Senate", name="Finance")
        assert c["systemCode"] == "ssfi00"
        assert c["chamber"] == "Senate"

    def test_override_does_not_mutate_default(self) -> None:
        committee_item(systemCode="ssfi00")
        assert committee_item()["systemCode"] == "hswm00"


class TestBillItem:
    def test_has_required_keys(self) -> None:
        b = bill_item()
        for key in ("congress", "type", "number", "title"):
            assert key in b

    def test_default_values(self) -> None:
        b = bill_item()
        assert b["congress"] == 119
        assert b["type"] == "HR"
        assert b["number"] == 1

    def test_override(self) -> None:
        b = bill_item(number=42, type="S")
        assert b["number"] == 42
        assert b["type"] == "S"

    def test_latest_action_present(self) -> None:
        b = bill_item()
        assert "latestAction" in b
        assert "actionDate" in b["latestAction"]


class TestCosponsorItem:
    def test_has_required_keys(self) -> None:
        c = cosponsor_item()
        assert "bioguideId" in c
        assert "isOriginalCosponsor" in c

    def test_default_bioguide_id(self) -> None:
        assert cosponsor_item()["bioguideId"] == "S000148"

    def test_override(self) -> None:
        c = cosponsor_item(bioguideId="B000001", isOriginalCosponsor=True)
        assert c["bioguideId"] == "B000001"
        assert c["isOriginalCosponsor"] is True


# ---------------------------------------------------------------------------
# Single-page response builders
# ---------------------------------------------------------------------------


class TestMembersPage:
    def test_top_level_key(self) -> None:
        page = members_page()
        assert "members" in page

    def test_pagination_key_present(self) -> None:
        page = members_page()
        assert "pagination" in page

    def test_default_has_one_item(self) -> None:
        page = members_page()
        assert len(page["members"]) == 1

    def test_custom_items(self) -> None:
        items = [member_item(bioguideId="A000001"), member_item(bioguideId="A000002")]
        page = members_page(items)
        assert len(page["members"]) == 2

    def test_no_next_url_by_default(self) -> None:
        page = members_page()
        assert "next" not in page["pagination"]

    def test_next_url_propagated(self) -> None:
        page = members_page(next_url="https://example.com/next")
        assert page["pagination"]["next"] == "https://example.com/next"

    def test_empty_items(self) -> None:
        page = members_page([])
        assert page["members"] == []


class TestCommitteesPage:
    def test_top_level_key(self) -> None:
        assert "committees" in committees_page()

    def test_default_has_one_item(self) -> None:
        assert len(committees_page()["committees"]) == 1

    def test_custom_items_length(self) -> None:
        items = [committee_item(), committee_item(systemCode="hjud00")]
        assert len(committees_page(items)["committees"]) == 2

    def test_next_url(self) -> None:
        page = committees_page(next_url="https://example.com/p2")
        assert page["pagination"]["next"] == "https://example.com/p2"


class TestBillsPage:
    def test_top_level_key(self) -> None:
        assert "bills" in bills_page()

    def test_default_has_one_item(self) -> None:
        assert len(bills_page()["bills"]) == 1

    def test_next_url(self) -> None:
        page = bills_page(next_url="https://example.com/p2")
        assert page["pagination"]["next"] == "https://example.com/p2"


class TestCosponsorsPage:
    def test_top_level_key(self) -> None:
        assert "cosponsors" in cosponsors_page()

    def test_default_has_one_item(self) -> None:
        assert len(cosponsors_page()["cosponsors"]) == 1

    def test_next_url(self) -> None:
        page = cosponsors_page(next_url="https://example.com/p2")
        assert page["pagination"]["next"] == "https://example.com/p2"


# ---------------------------------------------------------------------------
# Detail response builders
# ---------------------------------------------------------------------------


class TestMemberDetailResponse:
    def test_wraps_under_member_key(self) -> None:
        resp = member_detail_response()
        assert "member" in resp

    def test_default_bioguide_id(self) -> None:
        resp = member_detail_response()
        assert resp["member"]["bioguideId"] == "P000197"

    def test_custom_payload(self) -> None:
        payload = {"bioguideId": "S000148", "firstName": "Chuck"}
        resp = member_detail_response(payload)
        assert resp["member"]["firstName"] == "Chuck"

    def test_custom_payload_not_mutated(self) -> None:
        payload = {"bioguideId": "X000001"}
        member_detail_response(payload)
        assert "member" not in payload  # original dict unchanged


class TestBillDetailResponse:
    def test_wraps_under_bill_key(self) -> None:
        resp = bill_detail_response()
        assert "bill" in resp

    def test_default_bill_number(self) -> None:
        resp = bill_detail_response()
        assert resp["bill"]["number"] == 1

    def test_custom_payload(self) -> None:
        payload = {"congress": 119, "type": "S", "number": 99, "title": "Custom"}
        resp = bill_detail_response(payload)
        assert resp["bill"]["number"] == 99


# ---------------------------------------------------------------------------
# Multi-page helpers
# ---------------------------------------------------------------------------


class TestPaginatedMembers:
    def test_single_page_no_next(self) -> None:
        pages = paginated_members([[member_item()]])
        assert len(pages) == 1
        assert "next" not in pages[0]["pagination"]

    def test_two_pages_linked(self) -> None:
        page1_items = [member_item(bioguideId="A000001")]
        page2_items = [member_item(bioguideId="A000002")]
        pages = paginated_members([page1_items, page2_items])
        assert len(pages) == 2
        # page 1 must have a next_url
        assert "next" in pages[0]["pagination"]
        # page 2 must not
        assert "next" not in pages[1]["pagination"]

    def test_three_pages_all_linked(self) -> None:
        three = [[member_item()]] * 3
        pages = paginated_members(three)
        assert "next" in pages[0]["pagination"]
        assert "next" in pages[1]["pagination"]
        assert "next" not in pages[2]["pagination"]

    def test_page_items_preserved(self) -> None:
        items = [member_item(bioguideId="Z000001"), member_item(bioguideId="Z000002")]
        pages = paginated_members([items])
        assert pages[0]["members"][0]["bioguideId"] == "Z000001"
        assert pages[0]["members"][1]["bioguideId"] == "Z000002"

    def test_next_url_is_stable_sentinel(self) -> None:
        pages = paginated_members([[member_item()], [member_item()]])
        next_url = pages[0]["pagination"]["next"]
        assert next_url.startswith("https://api.congress.gov/v3/")


class TestPaginatedCommittees:
    def test_single_page_no_next(self) -> None:
        pages = paginated_committees([[committee_item()]])
        assert "next" not in pages[0]["pagination"]

    def test_two_pages_linked(self) -> None:
        pages = paginated_committees([[committee_item()], [committee_item()]])
        assert "next" in pages[0]["pagination"]
        assert "next" not in pages[1]["pagination"]


class TestPaginatedBills:
    def test_single_page_no_next(self) -> None:
        pages = paginated_bills([[bill_item()]])
        assert "next" not in pages[0]["pagination"]

    def test_two_pages_linked(self) -> None:
        pages = paginated_bills([[bill_item()], [bill_item()]])
        assert "next" in pages[0]["pagination"]
        assert "next" not in pages[1]["pagination"]

    def test_bill_items_preserved(self) -> None:
        items = [bill_item(number=10), bill_item(number=11)]
        pages = paginated_bills([items])
        assert pages[0]["bills"][0]["number"] == 10


class TestPaginatedCosponsors:
    def test_single_page_no_next(self) -> None:
        pages = paginated_cosponsors([[cosponsor_item()]])
        assert "next" not in pages[0]["pagination"]

    def test_two_pages_linked(self) -> None:
        pages = paginated_cosponsors([[cosponsor_item()], [cosponsor_item()]])
        assert "next" in pages[0]["pagination"]
        assert "next" not in pages[1]["pagination"]


# ---------------------------------------------------------------------------
# Vote XML generators
# ---------------------------------------------------------------------------


class TestHouseVoteXml:
    def test_is_valid_xml(self) -> None:
        xml = house_vote_xml()
        root = ET.fromstring(xml)
        assert root.tag == "rollcall-vote"

    def test_congress_embedded(self) -> None:
        xml = house_vote_xml(congress=118)
        root = ET.fromstring(xml)
        congress_el = root.find("vote-metadata/congress")
        assert congress_el is not None
        assert congress_el.text == "118"

    def test_session_embedded(self) -> None:
        xml = house_vote_xml(session=2)
        root = ET.fromstring(xml)
        assert root.find("vote-metadata/session").text == "2"

    def test_roll_call_number_embedded(self) -> None:
        xml = house_vote_xml(roll_call_number=42)
        root = ET.fromstring(xml)
        assert root.find("vote-metadata/rollcall-num").text == "42"

    def test_custom_question_and_result(self) -> None:
        xml = house_vote_xml(question="On Amendment", result="Failed")
        root = ET.fromstring(xml)
        assert root.find("vote-metadata/vote-question").text == "On Amendment"
        assert root.find("vote-metadata/vote-result").text == "Failed"

    def test_vote_data_present(self) -> None:
        xml = house_vote_xml()
        root = ET.fromstring(xml)
        vote_data = root.find("vote-data")
        assert vote_data is not None
        assert len(list(vote_data)) >= 1

    def test_year_reflected_in_date(self) -> None:
        xml = house_vote_xml(year=2024)
        assert "2024" in xml


class TestSenateVoteXml:
    def test_is_valid_xml(self) -> None:
        xml = senate_vote_xml()
        root = ET.fromstring(xml)
        assert root.tag == "roll_call_vote"

    def test_congress_embedded(self) -> None:
        xml = senate_vote_xml(congress=118)
        root = ET.fromstring(xml)
        assert root.find("congress").text == "118"

    def test_session_embedded(self) -> None:
        xml = senate_vote_xml(session=2)
        root = ET.fromstring(xml)
        assert root.find("session").text == "2"

    def test_roll_call_number_embedded(self) -> None:
        xml = senate_vote_xml(roll_call_number=7)
        root = ET.fromstring(xml)
        assert root.find("vote_number").text == "7"

    def test_custom_question_and_result(self) -> None:
        xml = senate_vote_xml(question="Cloture", result="Not Invoked")
        root = ET.fromstring(xml)
        assert root.find("vote_question_text").text == "Cloture"
        assert root.find("vote_result_text").text == "Not Invoked"

    def test_members_present(self) -> None:
        xml = senate_vote_xml()
        root = ET.fromstring(xml)
        members_el = root.find("members")
        assert members_el is not None
        assert len(list(members_el)) >= 1

    def test_custom_display_date(self) -> None:
        xml = senate_vote_xml(display_date="March 15, 2025")
        assert "March 15, 2025" in xml

    def test_lis_member_id_present(self) -> None:
        xml = senate_vote_xml()
        root = ET.fromstring(xml)
        lis_el = root.find("members/member/lis_member_id")
        assert lis_el is not None
        assert lis_el.text == "S270"
