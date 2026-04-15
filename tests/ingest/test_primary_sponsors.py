"""Tests for src/ingest/congress/primary_sponsors.py.

Pure / deterministic — no network calls, no DB.
"""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.models import BillRecord
from src.ingest.congress.primary_sponsors import (
    primary_sponsor_spec_from_bill_detail,
)
from src.load.congress import PrimarySponsorSpec as LoadPrimarySponsorSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bill() -> BillRecord:
    return BillRecord(
        congress=118,
        bill_type="hr",
        bill_number=1,
        title="A bill to do something",
        short_title=None,
        introduced_date=datetime.date(2023, 1, 9),
        latest_action_date=datetime.date(2023, 3, 15),
        current_status="Referred to committee",
        source_url="https://api.congress.gov/v3/bill/118/hr/1",
    )


def _detail(sponsors: list[dict]) -> dict:
    """Minimal bill detail payload with the given sponsors list."""
    return {
        "bill": {
            "congress": 118,
            "type": "HR",
            "number": 1,
            "title": "A bill to do something",
            "introducedDate": "2023-01-09",
            "sponsors": sponsors,
        }
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_spec_with_correct_identity(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197", "url": "https://api.congress.gov/v3/member/P000197"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert isinstance(spec, LoadPrimarySponsorSpec)
        assert spec.bioguide_id == "P000197"
        assert spec.record is bill

    def test_sponsor_date_falls_back_to_bill_introduced_date(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.sponsor_date == datetime.date(2023, 1, 9)

    def test_sponsorship_date_from_entry_takes_precedence(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197", "sponsorshipDate": "2023-01-12"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.sponsor_date == datetime.date(2023, 1, 12)

    def test_source_url_from_member_entry(self, bill: BillRecord) -> None:
        member_url = "https://api.congress.gov/v3/member/P000197"
        detail = _detail([{"bioguideId": "P000197", "url": member_url}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.source_url == member_url

    def test_source_url_falls_back_to_bill_source_url(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.source_url == bill.source_url

    def test_only_first_sponsor_used(self, bill: BillRecord) -> None:
        """Even when multiple sponsors are listed, only the first is used."""
        detail = _detail([
            {"bioguideId": "P000197"},
            {"bioguideId": "S000148"},
        ])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.bioguide_id == "P000197"

    def test_unwrapped_payload_accepted(self, bill: BillRecord) -> None:
        """Payload without top-level 'bill' key is treated as the bill node itself."""
        detail = {
            "congress": 118,
            "type": "HR",
            "number": 1,
            "sponsors": [{"bioguideId": "P000197"}],
        }
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.bioguide_id == "P000197"

    def test_spec_is_frozen(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        with pytest.raises((AttributeError, TypeError)):
            spec.bioguide_id = "X"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# None cases
# ---------------------------------------------------------------------------

class TestReturnNone:
    def test_empty_sponsors_list(self, bill: BillRecord) -> None:
        detail = _detail([])
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_sponsors_key_missing(self, bill: BillRecord) -> None:
        detail = {"bill": {"congress": 118, "type": "HR", "number": 1}}
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_bioguide_id_absent(self, bill: BillRecord) -> None:
        detail = _detail([{"fullName": "Rep. Unknown"}])
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_bioguide_id_none(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": None}])
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_bioguide_id_blank_string(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "   "}])
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_bioguide_id_empty_string(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": ""}])
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None

    def test_sponsors_none_value(self, bill: BillRecord) -> None:
        """Explicit null sponsors field is treated as no sponsors."""
        detail = {"bill": {"sponsors": None}}
        assert primary_sponsor_spec_from_bill_detail(detail, bill) is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_bioguide_id_stripped(self, bill: BillRecord) -> None:
        """Leading/trailing whitespace in bioguide_id is stripped."""
        detail = _detail([{"bioguideId": " P000197 "}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.bioguide_id == "P000197"

    def test_ignores_sponsorship_date_metadata(self, bill: BillRecord) -> None:
        detail = _detail([{"bioguideId": "P000197", "sponsorshipDate": "not-a-date"}])
        spec = primary_sponsor_spec_from_bill_detail(detail, bill)
        assert spec is not None
        assert spec.sponsor_date is None
