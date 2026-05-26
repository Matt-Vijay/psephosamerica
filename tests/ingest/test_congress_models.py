from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    VoteCastRecord,
    VoteEventRecord,
)


def test_committee_record_rejects_boolean_congress() -> None:
    with pytest.raises(ValueError, match="congress must be an integer"):
        CommitteeRecord(
            committee_code="HSAS00",
            congress=True,
            chamber="house",
            committee_type="standing",
            name="Committee on Armed Services",
        )


@pytest.mark.parametrize("field", ["congress", "bill_number"])
def test_bill_record_rejects_boolean_identity_fields(field: str) -> None:
    values = {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 1,
        "title": "Test bill",
    }
    values[field] = False

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        BillRecord(**values)


@pytest.mark.parametrize("field", ["congress", "bill_number"])
def test_cosponsor_record_rejects_boolean_identity_fields(field: str) -> None:
    values = {
        "congress": 119,
        "bill_type": "hr",
        "bill_number": 1,
        "bioguide_id": "A000001",
    }
    values[field] = True

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        CosponsorRecord(**values)


@pytest.mark.parametrize("field", ["congress", "session_number", "roll_call_number"])
def test_vote_event_record_rejects_boolean_identity_fields(field: str) -> None:
    values = {
        "chamber": "house",
        "congress": 119,
        "session_number": 1,
        "roll_call_number": 42,
        "vote_date": datetime.date(2025, 1, 3),
        "question": "On Passage",
    }
    values[field] = True

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        VoteEventRecord(**values)


@pytest.mark.parametrize("field", ["congress", "session_number", "roll_call_number"])
def test_vote_cast_record_rejects_boolean_identity_fields(field: str) -> None:
    values = {
        "chamber": "house",
        "congress": 119,
        "session_number": 1,
        "roll_call_number": 42,
        "vote_option": "yea",
        "bioguide_id": "A000001",
    }
    values[field] = False

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        VoteCastRecord(**values)
