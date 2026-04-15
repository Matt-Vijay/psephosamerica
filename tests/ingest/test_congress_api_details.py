"""Tests for detail URL builders and CongressAPIClient detail fetchers.

No network calls; _get is patched at the boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.ingest.congress.congress_api import (
    CongressAPIClient,
    bill_detail_url,
    member_detail_url,
)
from src.ingest.congress.models import BillRecord, MemberRecord


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


class TestMemberDetailUrl:
    def test_contains_bioguide_id(self):
        url = member_detail_url("A000001")
        assert "member/A000001" in url

    def test_includes_format_json(self):
        url = member_detail_url("A000001")
        assert "format=json" in url

    def test_different_bioguide_ids_produce_different_urls(self):
        assert member_detail_url("A000001") != member_detail_url("B000002")


class TestBillDetailUrl:
    def test_contains_congress_type_number(self):
        url = bill_detail_url(119, "hr", 42)
        assert "bill/119/hr/42" in url

    def test_includes_format_json(self):
        url = bill_detail_url(119, "hr", 42)
        assert "format=json" in url

    def test_different_bills_produce_different_urls(self):
        assert bill_detail_url(119, "hr", 1) != bill_detail_url(119, "s", 1)
        assert bill_detail_url(119, "hr", 1) != bill_detail_url(119, "hr", 2)
        assert bill_detail_url(118, "hr", 1) != bill_detail_url(119, "hr", 1)


# ---------------------------------------------------------------------------
# CongressAPIClient.get_member_detail
# ---------------------------------------------------------------------------

_RAW_MEMBER = {
    "bioguideId": "A000001",
    "firstName": "Ada",
    "lastName": "Lovelace",
    "directOrderName": "Ada Lovelace",
    "partyName": "D",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives"}]},
}

_RAW_BILL = {
    "congress": 119,
    "type": "HR",
    "number": 42,
    "title": "A test bill",
    "introducedDate": "2025-01-15",
    "latestAction": {"actionDate": "2025-03-01", "text": "Referred to committee"},
}


def _make_client(get_return: dict | None = None) -> CongressAPIClient:
    client = CongressAPIClient.__new__(CongressAPIClient)
    client._api_key = "test-key"
    client._client = MagicMock()
    if get_return is not None:
        client._get = MagicMock(return_value=get_return)
    return client


class TestGetMemberDetail:
    def setup_method(self):
        self.client = _make_client({"member": _RAW_MEMBER})
        self.result = self.client.get_member_detail("A000001")

    def test_returns_member_record(self):
        assert isinstance(self.result, MemberRecord)
        assert self.result.bioguide_id == "A000001"

    def test_calls_member_detail_url(self):
        called_url = self.client._get.call_args[0][0]
        assert "member/A000001" in called_url

    def test_source_url_set_on_record(self):
        assert self.result.source_url is not None
        assert "member/A000001" in self.result.source_url

    def test_normalizes_party_and_state(self):
        assert self.result.party == "D"
        assert self.result.state == "CA"


# ---------------------------------------------------------------------------
# CongressAPIClient.get_bill_detail
# ---------------------------------------------------------------------------


class TestGetBillDetail:
    def setup_method(self):
        self.client = _make_client({"bill": _RAW_BILL})
        self.result = self.client.get_bill_detail(119, "hr", 42)

    def test_returns_bill_record(self):
        assert isinstance(self.result, BillRecord)
        assert self.result.bill_number == 42

    def test_calls_bill_detail_url(self):
        called_url = self.client._get.call_args[0][0]
        assert "bill/119/hr/42" in called_url

    def test_source_url_set_on_record(self):
        assert self.result.source_url is not None
        assert "bill/119/hr/42" in self.result.source_url

    def test_normalizes_bill_type_to_lowercase(self):
        client = _make_client({"bill": {**_RAW_BILL, "type": "HR"}})
        result = client.get_bill_detail(119, "hr", 42)
        assert result.bill_type == "hr"

    def test_title_and_status_populated(self):
        assert self.result.title == "A test bill"
        assert self.result.current_status == "Referred to committee"
