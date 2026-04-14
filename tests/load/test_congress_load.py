"""Tests for src/load/congress.py — Congress load-plan helpers.

All tests are pure and deterministic: no network calls, no DB.
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
from src.load.congress import (
    CommitteeMembershipSpec,
    MemberTermSpec,
    PrimarySponsorSpec,
    congress_load_plan,
    plan_bill_sponsors,
    plan_bills,
    plan_committee_memberships,
    plan_committees,
    plan_member_terms,
    plan_members,
    plan_vote_casts,
    plan_vote_events,
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
        lis_member_id="S270",
    )


@pytest.fixture()
def committee_record() -> CommitteeRecord:
    return CommitteeRecord(
        committee_code="HSJU00",
        congress=118,
        chamber="house",
        committee_type="standing",
        name="Committee on the Judiciary",
    )


@pytest.fixture()
def bill_record() -> BillRecord:
    return BillRecord(
        congress=118,
        bill_type="hr",
        bill_number=1,
        title="A bill to do something important",
        introduced_date=datetime.date(2023, 1, 9),
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
    )


# ---------------------------------------------------------------------------
# plan_members
# ---------------------------------------------------------------------------


class TestPlanMembers:
    def test_table_name(self, house_member: MemberRecord) -> None:
        op = plan_members([house_member])
        assert op["table"] == "member"

    def test_mode_is_upsert(self, house_member: MemberRecord) -> None:
        op = plan_members([house_member])
        assert op["mode"] == "upsert"

    def test_conflict_columns(self, house_member: MemberRecord) -> None:
        op = plan_members([house_member])
        assert op["conflict_columns"] == ["bioguide_id"]

    def test_row_count(self, house_member: MemberRecord, senate_member: MemberRecord) -> None:
        op = plan_members([house_member, senate_member])
        assert len(op["rows"]) == 2

    def test_row_contains_bioguide_id(self, house_member: MemberRecord) -> None:
        op = plan_members([house_member])
        assert op["rows"][0]["bioguide_id"] == "P000197"

    def test_empty_input(self) -> None:
        op = plan_members([])
        assert op["rows"] == []

    def test_required_keys_present(self, house_member: MemberRecord) -> None:
        op = plan_members([house_member])
        for key in ("table", "rows", "conflict_columns", "mode"):
            assert key in op


# ---------------------------------------------------------------------------
# plan_member_terms
# ---------------------------------------------------------------------------


class TestPlanMemberTerms:
    def test_table_name(self, house_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=house_member, congress=118, start_date=datetime.date(2023, 1, 3)
        )
        op = plan_member_terms([spec])
        assert op["table"] == "member_term"

    def test_conflict_columns_cover_unique_constraint(self, house_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=house_member, congress=118, start_date=datetime.date(2023, 1, 3)
        )
        op = plan_member_terms([spec])
        assert set(op["conflict_columns"]) == {
            "member_id", "congress", "chamber", "start_date"
        }

    def test_senator_district_forced_none(self, senate_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=senate_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
            district=5,  # caller mistake; transform should ignore for senators
        )
        op = plan_member_terms([spec])
        assert op["rows"][0]["district"] is None

    def test_house_district_preserved(self, house_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=house_member,
            congress=118,
            start_date=datetime.date(2023, 1, 3),
            district=12,
        )
        op = plan_member_terms([spec])
        assert op["rows"][0]["district"] == 12

    def test_bioguide_hint_present(self, house_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=house_member, congress=118, start_date=datetime.date(2023, 1, 3)
        )
        op = plan_member_terms([spec])
        assert op["rows"][0]["_bioguide_id"] == "P000197"

    def test_mode_is_upsert(self, house_member: MemberRecord) -> None:
        spec = MemberTermSpec(
            record=house_member, congress=118, start_date=datetime.date(2023, 1, 3)
        )
        op = plan_member_terms([spec])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_member_terms([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_committees
# ---------------------------------------------------------------------------


class TestPlanCommittees:
    def test_table_name(self, committee_record: CommitteeRecord) -> None:
        op = plan_committees([committee_record])
        assert op["table"] == "committee"

    def test_conflict_columns(self, committee_record: CommitteeRecord) -> None:
        op = plan_committees([committee_record])
        assert set(op["conflict_columns"]) == {"congress", "committee_code"}

    def test_row_fields(self, committee_record: CommitteeRecord) -> None:
        op = plan_committees([committee_record])
        row = op["rows"][0]
        assert row["committee_code"] == "HSJU00"
        assert row["congress"] == 118
        assert row["chamber"] == "house"

    def test_mode_is_upsert(self, committee_record: CommitteeRecord) -> None:
        op = plan_committees([committee_record])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_committees([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_committee_memberships
# ---------------------------------------------------------------------------


class TestPlanCommitteeMemberships:
    def _spec(self, **kwargs) -> CommitteeMembershipSpec:
        defaults = dict(
            bioguide_id="P000197",
            committee_code="HSJU00",
            congress=118,
            role="member",
            start_date=datetime.date(2023, 1, 3),
        )
        defaults.update(kwargs)
        return CommitteeMembershipSpec(**defaults)

    def test_table_name(self) -> None:
        op = plan_committee_memberships([self._spec()])
        assert op["table"] == "committee_membership"

    def test_conflict_columns(self) -> None:
        op = plan_committee_memberships([self._spec()])
        assert set(op["conflict_columns"]) == {"committee_id", "member_id", "start_date"}

    def test_hint_keys_bioguide(self) -> None:
        op = plan_committee_memberships([self._spec(bioguide_id="S000148")])
        assert op["rows"][0]["_bioguide_id"] == "S000148"

    def test_hint_keys_committee_code(self) -> None:
        op = plan_committee_memberships([self._spec(committee_code="SSFI00")])
        assert op["rows"][0]["_committee_code"] == "SSFI00"

    def test_hint_keys_congress(self) -> None:
        op = plan_committee_memberships([self._spec(congress=119)])
        assert op["rows"][0]["_congress"] == 119

    def test_fk_columns_none(self) -> None:
        op = plan_committee_memberships([self._spec()])
        row = op["rows"][0]
        assert row["committee_id"] is None
        assert row["member_id"] is None

    def test_mode_is_upsert(self) -> None:
        op = plan_committee_memberships([self._spec()])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_committee_memberships([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_bills
# ---------------------------------------------------------------------------


class TestPlanBills:
    def test_table_name(self, bill_record: BillRecord) -> None:
        op = plan_bills([bill_record])
        assert op["table"] == "bill"

    def test_conflict_columns(self, bill_record: BillRecord) -> None:
        op = plan_bills([bill_record])
        assert set(op["conflict_columns"]) == {"congress", "bill_type", "bill_number"}

    def test_row_fields(self, bill_record: BillRecord) -> None:
        op = plan_bills([bill_record])
        row = op["rows"][0]
        assert row["congress"] == 118
        assert row["bill_type"] == "hr"
        assert row["bill_number"] == 1

    def test_mode_is_upsert(self, bill_record: BillRecord) -> None:
        op = plan_bills([bill_record])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_bills([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_bill_sponsors
# ---------------------------------------------------------------------------


class TestPlanBillSponsors:
    def test_table_name(self, bill_record: BillRecord) -> None:
        spec = PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")
        op = plan_bill_sponsors([spec], [])
        assert op["table"] == "bill_sponsor"

    def test_conflict_columns(self, bill_record: BillRecord) -> None:
        spec = PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")
        op = plan_bill_sponsors([spec], [])
        assert set(op["conflict_columns"]) == {"bill_id", "member_id"}

    def test_primary_and_cosponsor_combined(self, bill_record: BillRecord) -> None:
        primary = PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")
        cosponsor = CosponsorRecord(
            congress=118,
            bill_type="hr",
            bill_number=1,
            bioguide_id="S000148",
            is_original=True,
            sponsor_date=datetime.date(2023, 1, 9),
        )
        op = plan_bill_sponsors([primary], [cosponsor])
        assert len(op["rows"]) == 2
        assert op["rows"][0]["sponsor_role"] == "primary"
        assert op["rows"][1]["sponsor_role"] == "original_cosponsor"

    def test_cosponsor_only(self, bill_record: BillRecord) -> None:
        cosponsor = CosponsorRecord(
            congress=118, bill_type="hr", bill_number=1,
            bioguide_id="X000001", is_original=False,
        )
        op = plan_bill_sponsors([], [cosponsor])
        assert len(op["rows"]) == 1
        assert op["rows"][0]["sponsor_role"] == "cosponsor"

    def test_bill_hint_key_present(self, bill_record: BillRecord) -> None:
        spec = PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")
        op = plan_bill_sponsors([spec], [])
        assert op["rows"][0]["_bill_key"] == (118, "hr", 1)

    def test_mode_is_upsert(self, bill_record: BillRecord) -> None:
        spec = PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")
        op = plan_bill_sponsors([spec], [])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_bill_sponsors([], [])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_vote_events
# ---------------------------------------------------------------------------


class TestPlanVoteEvents:
    def test_table_name(self, vote_event_record: VoteEventRecord) -> None:
        op = plan_vote_events([vote_event_record])
        assert op["table"] == "vote_event"

    def test_conflict_columns(self, vote_event_record: VoteEventRecord) -> None:
        op = plan_vote_events([vote_event_record])
        assert set(op["conflict_columns"]) == {
            "chamber", "congress", "session_number", "roll_call_number"
        }

    def test_row_fields(self, vote_event_record: VoteEventRecord) -> None:
        op = plan_vote_events([vote_event_record])
        row = op["rows"][0]
        assert row["chamber"] == "house"
        assert row["congress"] == 118
        assert row["roll_call_number"] == 42

    def test_mode_is_upsert(self, vote_event_record: VoteEventRecord) -> None:
        op = plan_vote_events([vote_event_record])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_vote_events([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_vote_casts
# ---------------------------------------------------------------------------


class TestPlanVoteCasts:
    def _house_cast(self) -> VoteCastRecord:
        return VoteCastRecord(
            chamber="house",
            congress=118,
            session_number=1,
            roll_call_number=42,
            vote_option="yea",
            bioguide_id="P000197",
        )

    def _senate_cast(self) -> VoteCastRecord:
        return VoteCastRecord(
            chamber="senate",
            congress=118,
            session_number=1,
            roll_call_number=100,
            vote_option="nay",
            lis_member_id="S270",
        )

    def test_table_name(self) -> None:
        op = plan_vote_casts([self._house_cast()])
        assert op["table"] == "vote_cast"

    def test_conflict_columns(self) -> None:
        op = plan_vote_casts([self._house_cast()])
        assert set(op["conflict_columns"]) == {"vote_event_id", "member_id"}

    def test_fk_columns_none(self) -> None:
        op = plan_vote_casts([self._house_cast()])
        row = op["rows"][0]
        assert row["vote_event_id"] is None
        assert row["member_id"] is None

    def test_vote_event_key_hint(self) -> None:
        op = plan_vote_casts([self._house_cast()])
        assert op["rows"][0]["_vote_event_key"] == ("house", 118, 1, 42)

    def test_senate_lis_hint(self) -> None:
        op = plan_vote_casts([self._senate_cast()])
        assert op["rows"][0]["_lis_member_id"] == "S270"
        assert op["rows"][0]["_bioguide_id"] is None

    def test_mode_is_upsert(self) -> None:
        op = plan_vote_casts([self._house_cast()])
        assert op["mode"] == "upsert"

    def test_empty_input(self) -> None:
        op = plan_vote_casts([])
        assert op["rows"] == []


# ---------------------------------------------------------------------------
# congress_load_plan — orchestration and FK ordering
# ---------------------------------------------------------------------------


class TestCongressLoadPlan:
    def _full_plan(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> list[dict]:
        return congress_load_plan(
            members=[house_member],
            member_terms=[
                MemberTermSpec(
                    record=house_member,
                    congress=118,
                    start_date=datetime.date(2023, 1, 3),
                    is_current=True,
                )
            ],
            committees=[committee_record],
            memberships=[
                CommitteeMembershipSpec(
                    bioguide_id="P000197",
                    committee_code="HSJU00",
                    congress=118,
                    role="member",
                    start_date=datetime.date(2023, 1, 3),
                )
            ],
            bills=[bill_record],
            primary_sponsors=[PrimarySponsorSpec(record=bill_record, bioguide_id="P000197")],
            cosponsors=[],
            vote_events=[vote_event_record],
            vote_casts=[
                VoteCastRecord(
                    chamber="house",
                    congress=118,
                    session_number=1,
                    roll_call_number=42,
                    vote_option="yea",
                    bioguide_id="P000197",
                )
            ],
        )

    def test_returns_eight_batches(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        assert len(plan) == 8

    def test_table_names_in_fk_order(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        names = [op["table"] for op in plan]
        assert names == [
            "member",
            "committee",
            "member_term",
            "committee_membership",
            "bill",
            "bill_sponsor",
            "vote_event",
            "vote_cast",
        ]

    def test_member_before_member_term(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        names = [op["table"] for op in plan]
        assert names.index("member") < names.index("member_term")

    def test_committee_before_membership(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        names = [op["table"] for op in plan]
        assert names.index("committee") < names.index("committee_membership")

    def test_bill_before_bill_sponsor(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        names = [op["table"] for op in plan]
        assert names.index("bill") < names.index("bill_sponsor")

    def test_vote_event_before_vote_cast(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        names = [op["table"] for op in plan]
        assert names.index("vote_event") < names.index("vote_cast")

    def test_all_ops_have_required_keys(
        self,
        house_member: MemberRecord,
        committee_record: CommitteeRecord,
        bill_record: BillRecord,
        vote_event_record: VoteEventRecord,
    ) -> None:
        plan = self._full_plan(house_member, committee_record, bill_record, vote_event_record)
        for op in plan:
            assert "table" in op
            assert "rows" in op
            assert "conflict_columns" in op
            assert "mode" in op

    def test_empty_inputs_returns_eight_empty_batches(self) -> None:
        plan = congress_load_plan(
            members=[],
            member_terms=[],
            committees=[],
            memberships=[],
            bills=[],
            primary_sponsors=[],
            cosponsors=[],
            vote_events=[],
            vote_casts=[],
        )
        assert len(plan) == 8
        for op in plan:
            assert op["rows"] == []
