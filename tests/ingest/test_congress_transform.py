"""Tests for src/ingest/congress/transform.py.

All tests are pure / deterministic — no network calls, no DB.
"""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
    VoteCastRecord,
    VoteEventRecord,
)
from src.ingest.congress.transform import (
    bill_row,
    bill_sponsor_row,
    committee_membership_row,
    committee_row,
    cosponsor_row,
    member_row,
    member_term_row,
    vote_cast_row,
    vote_event_row,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def house_member() -> MemberRecord:
    return MemberRecord(
        bioguide_id="P000197",
        first_name="Nancy",
        last_name="Pelosi",
        full_name="Nancy Pelosi",
        chamber="house",
        party="Democrat",
        state="CA",
        middle_name=None,
        lis_member_id=None,
        current_term_start=datetime.date(2023, 1, 3),
        current_term_end=datetime.date(2025, 1, 3),
        is_current=True,
        source_url="https://api.congress.gov/v3/member/P000197",
    )


@pytest.fixture()
def senate_member() -> MemberRecord:
    return MemberRecord(
        bioguide_id="S000148",
        first_name="Chuck",
        last_name="Schumer",
        full_name="Charles E. Schumer",
        chamber="senate",
        party="Democrat",
        state="NY",
        middle_name="E.",
        lis_member_id="S270",
        current_term_start=datetime.date(2023, 1, 3),
        current_term_end=datetime.date(2029, 1, 3),
        is_current=True,
        source_url="https://api.congress.gov/v3/member/S000148",
    )


@pytest.fixture()
def committee_record() -> CommitteeRecord:
    return CommitteeRecord(
        committee_code="HSJU00",
        congress=118,
        chamber="house",
        committee_type="standing",
        name="Committee on the Judiciary",
        parent_committee_code=None,
        source_url="https://api.congress.gov/v3/committee/house/HSJU00",
    )


@pytest.fixture()
def subcommittee_record() -> CommitteeRecord:
    return CommitteeRecord(
        committee_code="HSJU01",
        congress=118,
        chamber="house",
        committee_type="subcommittee",
        name="Subcommittee on Courts",
        parent_committee_code="HSJU00",
        source_url="https://api.congress.gov/v3/committee/house/HSJU01",
    )


@pytest.fixture()
def bill_record() -> BillRecord:
    return BillRecord(
        congress=118,
        bill_type="hr",
        bill_number=1,
        title="A bill to do something important",
        short_title="Important Act",
        introduced_date=datetime.date(2023, 1, 9),
        latest_action_date=datetime.date(2023, 3, 15),
        current_status="Referred to committee",
        source_url="https://api.congress.gov/v3/bill/118/hr/1",
    )


@pytest.fixture()
def cosponsor_record() -> CosponsorRecord:
    return CosponsorRecord(
        congress=118,
        bill_type="hr",
        bill_number=1,
        bioguide_id="S000148",
        is_original=True,
        sponsor_date=datetime.date(2023, 1, 9),
        source_url="https://api.congress.gov/v3/bill/118/hr/1/cosponsors",
    )


@pytest.fixture()
def vote_event_record() -> VoteEventRecord:
    return VoteEventRecord(
        chamber="house",
        congress=118,
        session_number=1,
        roll_call_number=42,
        vote_date=datetime.date(2023, 3, 8),
        question="On Passage",
        result="Passed",
        source_url="https://clerk.house.gov/evs/2023/roll042.xml",
    )


@pytest.fixture()
def house_vote_cast() -> VoteCastRecord:
    return VoteCastRecord(
        chamber="house",
        congress=118,
        session_number=1,
        roll_call_number=42,
        vote_option="yea",
        bioguide_id="P000197",
        lis_member_id=None,
    )


@pytest.fixture()
def senate_vote_cast() -> VoteCastRecord:
    return VoteCastRecord(
        chamber="senate",
        congress=118,
        session_number=1,
        roll_call_number=100,
        vote_option="nay",
        bioguide_id=None,
        lis_member_id="S270",
    )


# ---------------------------------------------------------------------------
# member_row
# ---------------------------------------------------------------------------


class TestMemberRow:
    def test_basic_fields(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["bioguide_id"] == "P000197"
        assert row["last_name"] == "Pelosi"
        assert row["first_name"] == "Nancy"
        assert row["full_name"] == "Nancy Pelosi"
        assert row["chamber"] == "house"
        assert row["party"] == "Democrat"
        assert row["state"] == "CA"
        assert row["is_current"] is True

    def test_slug_is_deterministic(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["slug"] == "p000197"
        # Same input → same slug
        assert member_row(house_member)["slug"] == row["slug"]

    def test_lis_member_id_propagated(self, senate_member: MemberRecord) -> None:
        row = member_row(senate_member)
        assert row["lis_member_id"] == "S270"
        assert row["bioguide_id"] == "S000148"

    def test_fec_candidate_id_is_none(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["fec_candidate_id"] is None

    def test_source_record_id_is_url(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["source_record_id"] == house_member.source_url

    def test_source_artifact_id_is_none(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["source_artifact_id"] is None

    def test_current_term_dates(self, house_member: MemberRecord) -> None:
        row = member_row(house_member)
        assert row["current_term_start"] == datetime.date(2023, 1, 3)
        assert row["current_term_end"] == datetime.date(2025, 1, 3)


# ---------------------------------------------------------------------------
# member_term_row
# ---------------------------------------------------------------------------


class TestMemberTermRow:
    def test_house_member_district(self, house_member: MemberRecord) -> None:
        row = member_term_row(
            house_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
            end_date=datetime.date(2025, 1, 3),
            district=12,
            is_current=True,
        )
        assert row["district"] == 12
        assert row["chamber"] == "house"
        assert row["congress"] == 118
        assert row["_bioguide_id"] == "P000197"
        assert row["member_id"] is None

    def test_senator_district_forced_none(self, senate_member: MemberRecord) -> None:
        """Senators must never carry a district value even if caller passes one."""
        row = member_term_row(
            senate_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
            district=5,  # caller mistake — should be ignored for senators
        )
        assert row["district"] is None
        assert row["chamber"] == "senate"

    def test_defaults(self, house_member: MemberRecord) -> None:
        row = member_term_row(
            house_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
        )
        assert row["end_date"] is None
        assert row["is_current"] is False

    def test_bioguide_hint_present(self, senate_member: MemberRecord) -> None:
        row = member_term_row(
            senate_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
        )
        assert "_bioguide_id" in row
        assert row["_bioguide_id"] == senate_member.bioguide_id


# ---------------------------------------------------------------------------
# committee_row
# ---------------------------------------------------------------------------


class TestCommitteeRow:
    def test_basic_fields(self, committee_record: CommitteeRecord) -> None:
        row = committee_row(committee_record)
        assert row["committee_code"] == "HSJU00"
        assert row["congress"] == 118
        assert row["chamber"] == "house"
        assert row["committee_type"] == "standing"
        assert row["name"] == "Committee on the Judiciary"
        assert row["is_active"] is True
        assert row["parent_committee_id"] is None

    def test_no_parent(self, committee_record: CommitteeRecord) -> None:
        row = committee_row(committee_record)
        assert row["_parent_committee_code"] is None

    def test_subcommittee_parent_hint(self, subcommittee_record: CommitteeRecord) -> None:
        row = committee_row(subcommittee_record)
        assert row["_parent_committee_code"] == "HSJU00"
        assert row["parent_committee_id"] is None  # not resolved here

    def test_default_review_tier(self, committee_record: CommitteeRecord) -> None:
        row = committee_row(committee_record)
        assert row["review_tier"] == "review_required"


# ---------------------------------------------------------------------------
# committee_membership_row
# ---------------------------------------------------------------------------


class TestCommitteeMembershipRow:
    def test_basic_fields(self) -> None:
        row = committee_membership_row(
            "P000197",
            "HSJU00",
            118,
            role="member",
            start_date=datetime.date(2023, 1, 3),
            is_current=True,
        )
        assert row["_bioguide_id"] == "P000197"
        assert row["_committee_code"] == "HSJU00"
        assert row["_congress"] == 118
        assert row["role"] == "member"
        assert row["is_current"] is True
        assert row["committee_id"] is None
        assert row["member_id"] is None

    def test_end_date_defaults_none(self) -> None:
        row = committee_membership_row(
            "X000001",
            "SSFI00",
            118,
            role="chair",
            start_date=datetime.date(2023, 1, 3),
        )
        assert row["end_date"] is None

    def test_source_url(self) -> None:
        url = "https://example.com/committee"
        row = committee_membership_row(
            "X000001",
            "SSFI00",
            118,
            role="member",
            start_date=datetime.date(2023, 1, 3),
            source_url=url,
        )
        assert row["source_record_id"] == url


# ---------------------------------------------------------------------------
# bill_row
# ---------------------------------------------------------------------------


class TestBillRow:
    def test_basic_fields(self, bill_record: BillRecord) -> None:
        row = bill_row(bill_record)
        assert row["congress"] == 118
        assert row["bill_type"] == "hr"
        assert row["bill_number"] == 1
        assert row["title"] == "A bill to do something important"
        assert row["short_title"] == "Important Act"
        assert row["introduced_date"] == datetime.date(2023, 1, 9)
        assert row["latest_action_date"] == datetime.date(2023, 3, 15)
        assert row["current_status"] == "Referred to committee"

    def test_no_fk_columns(self, bill_record: BillRecord) -> None:
        row = bill_row(bill_record)
        assert row["source_artifact_id"] is None


# ---------------------------------------------------------------------------
# bill_sponsor_row
# ---------------------------------------------------------------------------


class TestBillSponsorRow:
    def test_primary_sponsor(self, bill_record: BillRecord) -> None:
        row = bill_sponsor_row(bill_record, "P000197")
        assert row["sponsor_role"] == "primary"
        assert row["is_primary"] is True
        assert row["_bioguide_id"] == "P000197"
        assert row["_bill_key"] == (118, "hr", 1)
        assert row["bill_id"] is None
        assert row["member_id"] is None

    def test_sponsor_date_from_introduced(self, bill_record: BillRecord) -> None:
        row = bill_sponsor_row(bill_record, "P000197")
        assert row["sponsor_date"] == datetime.date(2023, 1, 9)


# ---------------------------------------------------------------------------
# cosponsor_row
# ---------------------------------------------------------------------------


class TestCosponsorRow:
    def test_original_cosponsor(self, cosponsor_record: CosponsorRecord) -> None:
        row = cosponsor_row(cosponsor_record)
        assert row["sponsor_role"] == "original_cosponsor"
        assert row["is_primary"] is False
        assert row["_bioguide_id"] == "S000148"
        assert row["_bill_key"] == (118, "hr", 1)

    def test_regular_cosponsor(self) -> None:
        rec = CosponsorRecord(
            congress=118,
            bill_type="hr",
            bill_number=1,
            bioguide_id="X000002",
            is_original=False,
            sponsor_date=datetime.date(2023, 2, 1),
        )
        row = cosponsor_row(rec)
        assert row["sponsor_role"] == "cosponsor"

    def test_sponsor_date(self, cosponsor_record: CosponsorRecord) -> None:
        row = cosponsor_row(cosponsor_record)
        assert row["sponsor_date"] == datetime.date(2023, 1, 9)


# ---------------------------------------------------------------------------
# vote_event_row
# ---------------------------------------------------------------------------


class TestVoteEventRow:
    def test_basic_fields(self, vote_event_record: VoteEventRecord) -> None:
        row = vote_event_row(vote_event_record)
        assert row["chamber"] == "house"
        assert row["congress"] == 118
        assert row["session_number"] == 1
        assert row["roll_call_number"] == 42
        assert row["vote_date"] == datetime.date(2023, 3, 8)
        assert row["question"] == "On Passage"
        assert row["result"] == "Passed"

    def test_uniqueness_key_fields_present(self, vote_event_record: VoteEventRecord) -> None:
        """All four columns of the UNIQUE constraint must be present."""
        row = vote_event_row(vote_event_record)
        for col in ("chamber", "congress", "session_number", "roll_call_number"):
            assert col in row

    def test_source_record_id(self, vote_event_record: VoteEventRecord) -> None:
        row = vote_event_row(vote_event_record)
        assert row["source_record_id"] == vote_event_record.source_url


# ---------------------------------------------------------------------------
# vote_cast_row
# ---------------------------------------------------------------------------


class TestVoteCastRow:
    def test_house_cast(self, house_vote_cast: VoteCastRecord) -> None:
        row = vote_cast_row(house_vote_cast)
        assert row["vote_option"] == "yea"
        assert row["_bioguide_id"] == "P000197"
        assert row["_lis_member_id"] is None
        assert row["vote_event_id"] is None
        assert row["member_id"] is None

    def test_senate_cast_uses_lis_id(self, senate_vote_cast: VoteCastRecord) -> None:
        """Senate records arrive with lis_member_id only; bioguide is None."""
        row = vote_cast_row(senate_vote_cast)
        assert row["_lis_member_id"] == "S270"
        assert row["_bioguide_id"] is None
        assert row["vote_option"] == "nay"

    def test_vote_event_key_tuple(self, house_vote_cast: VoteCastRecord) -> None:
        row = vote_cast_row(house_vote_cast)
        assert row["_vote_event_key"] == ("house", 118, 1, 42)

    def test_vote_event_key_senate(self, senate_vote_cast: VoteCastRecord) -> None:
        row = vote_cast_row(senate_vote_cast)
        assert row["_vote_event_key"] == ("senate", 118, 1, 100)

    def test_fk_columns_none(self, house_vote_cast: VoteCastRecord) -> None:
        row = vote_cast_row(house_vote_cast)
        assert row["vote_event_id"] is None
        assert row["member_id"] is None

    def test_at_least_one_id_available(self) -> None:
        """VoteCastRecord itself enforces that at least one ID is present."""
        with pytest.raises(ValueError):
            VoteCastRecord(
                chamber="house",
                congress=118,
                session_number=1,
                roll_call_number=1,
                vote_option="yea",
                bioguide_id=None,
                lis_member_id=None,
            )
