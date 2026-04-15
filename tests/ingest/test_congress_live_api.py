"""Tests for src/ingest/congress/live_api.py.

No live network; CongressAPIClient iter_* methods are patched at the boundary.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from src.ingest.congress.live_api import (
    fetch_bills,
    fetch_committees,
    fetch_cosponsors_for_bills,
    fetch_members,
)
from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MEMBER = MemberRecord(
    bioguide_id="A000001",
    first_name="Ada",
    last_name="Lovelace",
    full_name="Ada Lovelace",
    chamber="house",
    party="D",
    state="CA",
    is_current=True,
)

_COMMITTEE = CommitteeRecord(
    committee_code="HJUD00",
    congress=119,
    chamber="house",
    committee_type="standing",
    name="Committee on the Judiciary",
)

_BILL = BillRecord(
    congress=119,
    bill_type="hr",
    bill_number=1,
    title="A bill",
    introduced_date=datetime.date(2025, 1, 15),
)

_COSPONSOR = CosponsorRecord(
    congress=119,
    bill_type="hr",
    bill_number=1,
    bioguide_id="B000002",
    is_original=False,
    sponsor_date=datetime.date(2025, 1, 20),
)


def _make_client() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# fetch_members
# ---------------------------------------------------------------------------


class TestFetchMembers:
    def test_returns_list_of_member_records(self):
        client = _make_client()
        client.iter_members.return_value = iter([_MEMBER])
        result = fetch_members(client)
        assert result == [_MEMBER]

    def test_passes_congress_to_iter_members(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        fetch_members(client, congress=119)
        client.iter_members.assert_called_once_with(119)

    def test_passes_none_congress_when_omitted(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        fetch_members(client)
        client.iter_members.assert_called_once_with(None)

    def test_returns_empty_list_when_no_members(self):
        client = _make_client()
        client.iter_members.return_value = iter([])
        assert fetch_members(client) == []

    def test_returns_multiple_members(self):
        m2 = MemberRecord(
            bioguide_id="B000002",
            first_name="Bob",
            last_name="Smith",
            full_name="Bob Smith",
            chamber="senate",
            is_current=True,
        )
        client = _make_client()
        client.iter_members.return_value = iter([_MEMBER, m2])
        result = fetch_members(client, congress=118)
        assert len(result) == 2
        assert result[0].bioguide_id == "A000001"
        assert result[1].bioguide_id == "B000002"


# ---------------------------------------------------------------------------
# fetch_committees
# ---------------------------------------------------------------------------


class TestFetchCommittees:
    def test_returns_list_of_committee_records(self):
        client = _make_client()
        client.iter_committees.return_value = iter([_COMMITTEE])
        result = fetch_committees(client, congress=119)
        assert result == [_COMMITTEE]

    def test_passes_congress_and_chamber(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        fetch_committees(client, congress=119, chamber="house")
        client.iter_committees.assert_called_once_with(119, "house")

    def test_omits_chamber_when_not_provided(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        fetch_committees(client, congress=119)
        client.iter_committees.assert_called_once_with(119)

    def test_returns_empty_list_when_none(self):
        client = _make_client()
        client.iter_committees.return_value = iter([])
        assert fetch_committees(client, congress=119) == []


# ---------------------------------------------------------------------------
# fetch_bills
# ---------------------------------------------------------------------------


class TestFetchBills:
    def test_returns_list_of_bill_records(self):
        client = _make_client()
        client.iter_bills.return_value = iter([_BILL])
        result = fetch_bills(client, congress=119)
        assert result == [_BILL]

    def test_passes_congress_and_bill_type(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        fetch_bills(client, congress=119, bill_type="hr")
        client.iter_bills.assert_called_once_with(119, "hr")

    def test_omits_bill_type_when_not_provided(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        fetch_bills(client, congress=119)
        client.iter_bills.assert_called_once_with(119)

    def test_returns_empty_list_when_none(self):
        client = _make_client()
        client.iter_bills.return_value = iter([])
        assert fetch_bills(client, congress=119) == []


# ---------------------------------------------------------------------------
# fetch_cosponsors_for_bills
# ---------------------------------------------------------------------------


class TestFetchCosponsorsForBills:
    def test_returns_cosponsors_for_single_bill(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([_COSPONSOR])
        result = fetch_cosponsors_for_bills(client, [_BILL])
        assert result == [_COSPONSOR]

    def test_calls_iter_cosponsors_with_bill_fields(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([])
        fetch_cosponsors_for_bills(client, [_BILL])
        client.iter_cosponsors.assert_called_once_with(119, "hr", 1)

    def test_concatenates_cosponsors_across_bills(self):
        bill2 = BillRecord(
            congress=119,
            bill_type="s",
            bill_number=50,
            title="Another bill",
        )
        cosponsor2 = CosponsorRecord(
            congress=119,
            bill_type="s",
            bill_number=50,
            bioguide_id="C000003",
        )

        def _side_effect(congress, bill_type, bill_number):
            if bill_type == "hr":
                return iter([_COSPONSOR])
            return iter([cosponsor2])

        client = _make_client()
        client.iter_cosponsors.side_effect = _side_effect
        result = fetch_cosponsors_for_bills(client, [_BILL, bill2])
        assert result == [_COSPONSOR, cosponsor2]

    def test_empty_bills_returns_empty_list(self):
        client = _make_client()
        assert fetch_cosponsors_for_bills(client, []) == []
        client.iter_cosponsors.assert_not_called()

    def test_bill_with_no_cosponsors_contributes_nothing(self):
        client = _make_client()
        client.iter_cosponsors.return_value = iter([])
        result = fetch_cosponsors_for_bills(client, [_BILL])
        assert result == []

    def test_preserves_input_order(self):
        bills = [
            BillRecord(congress=119, bill_type="hr", bill_number=i, title=f"Bill {i}")
            for i in range(1, 4)
        ]
        cosponsors_by_number = {
            1: [CosponsorRecord(congress=119, bill_type="hr", bill_number=1, bioguide_id="X000001")],
            2: [],
            3: [CosponsorRecord(congress=119, bill_type="hr", bill_number=3, bioguide_id="X000003")],
        }

        def _side_effect(congress, bill_type, bill_number):
            return iter(cosponsors_by_number[bill_number])

        client = _make_client()
        client.iter_cosponsors.side_effect = _side_effect
        result = fetch_cosponsors_for_bills(client, bills)
        assert [r.bill_number for r in result] == [1, 3]
