"""Tests for src/ingest/congress/live_bill_details.py."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from src.ingest.congress.live_bill_details import fetch_primary_sponsor_specs
from src.ingest.congress.models import BillRecord
from src.load.congress import PrimarySponsorSpec as LoadPrimarySponsorSpec


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BILL_HR1 = BillRecord(
    congress=119,
    bill_type="hr",
    bill_number=1,
    title="A House Bill",
    introduced_date=datetime.date(2025, 1, 15),
    source_url="https://api.congress.gov/v3/bill/119/hr/1",
)

_BILL_S50 = BillRecord(
    congress=119,
    bill_type="s",
    bill_number=50,
    title="A Senate Bill",
    introduced_date=datetime.date(2025, 2, 1),
    source_url="https://api.congress.gov/v3/bill/119/s/50",
)

_DETAIL_WITH_SPONSOR = {
    "bill": {
        "sponsors": [
            {
                "bioguideId": "A000001",
                "fullName": "Rep. Ada Lovelace (D-CA)",
                "party": "D",
                "state": "CA",
                "sponsorshipDate": "2025-01-15",
            }
        ]
    }
}

_DETAIL_NO_SPONSORS: dict = {"bill": {"sponsors": []}}

_DETAIL_MISSING_BIOGUIDE = {
    "bill": {
        "sponsors": [
            {
                "fullName": "Rep. Unknown Member",
                "party": "R",
                "state": "TX",
            }
        ]
    }
}


def _make_client() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# fetch_primary_sponsor_specs
# ---------------------------------------------------------------------------


class TestFetchPrimarySponsorSpecs:
    def test_returns_spec_for_bill_with_sponsor(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_WITH_SPONSOR
        result = fetch_primary_sponsor_specs(client, [_BILL_HR1])
        assert len(result) == 1
        assert result[0].bioguide_id == "A000001"
        assert result[0].record == _BILL_HR1

    def test_calls_get_bill_detail_payload(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_WITH_SPONSOR
        fetch_primary_sponsor_specs(client, [_BILL_HR1])
        client.get_bill_detail_payload.assert_called_once_with(119, "hr", 1)

    def test_omits_bill_with_no_sponsors(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_NO_SPONSORS
        result = fetch_primary_sponsor_specs(client, [_BILL_HR1])
        assert result == []

    def test_omits_bill_with_missing_bioguide_id(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_MISSING_BIOGUIDE
        result = fetch_primary_sponsor_specs(client, [_BILL_HR1])
        assert result == []

    def test_empty_bills_returns_empty_list(self):
        client = _make_client()
        result = fetch_primary_sponsor_specs(client, [])
        assert result == []
        client.get_bill_detail_payload.assert_not_called()

    def test_preserves_relative_order_across_bills(self):
        bills = [
            BillRecord(congress=119, bill_type="hr", bill_number=i, title=f"Bill {i}")
            for i in (1, 2, 3)
        ]
        details_by_number = {
            1: {"bill": {"sponsors": [{"bioguideId": "X000001", "sponsorshipDate": "2025-01-01"}]}},
            2: _DETAIL_NO_SPONSORS,
            3: {"bill": {"sponsors": [{"bioguideId": "X000003", "sponsorshipDate": "2025-01-03"}]}},
        }

        def _payload_side_effect(congress: int, bill_type: str, bill_number: int) -> dict:
            assert congress == 119
            assert bill_type == "hr"
            return details_by_number[bill_number]

        client = _make_client()
        client.get_bill_detail_payload.side_effect = _payload_side_effect
        result = fetch_primary_sponsor_specs(client, bills)
        assert [s.record.bill_number for s in result] == [1, 3]
        assert result[0].bioguide_id == "X000001"
        assert result[1].bioguide_id == "X000003"

    def test_calls_get_once_per_bill(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_WITH_SPONSOR
        fetch_primary_sponsor_specs(client, [_BILL_HR1, _BILL_S50])
        assert client.get_bill_detail_payload.call_count == 2

    def test_url_encodes_senate_bill_type(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_NO_SPONSORS
        fetch_primary_sponsor_specs(client, [_BILL_S50])
        client.get_bill_detail_payload.assert_called_once_with(119, "s", 50)

    def test_returned_specs_are_primary_sponsor_spec_instances(self):
        client = _make_client()
        client.get_bill_detail_payload.return_value = _DETAIL_WITH_SPONSOR
        result = fetch_primary_sponsor_specs(client, [_BILL_HR1])
        assert all(isinstance(s, LoadPrimarySponsorSpec) for s in result)
