from __future__ import annotations

import datetime as dt

from src.export.contracts import MemberHistoryPayload
from src.query.member_history import assemble_member_history

MEMBER_ROW: dict = {
    "bioguide_id": "A000001",
    "full_name": "Jane Smith",
    "slug": "jane-smith",
    "state": "CA",
    "district": 12,
    "chamber": "house",
    "party": "D",
}

SNAPSHOT_ROWS: list[dict] = [
    {
        "snapshot_at": dt.date(2024, 1, 1),
        "score_total": 10.0,
        "dimension_scores": {"conflict_of_interest_risk": 10.0},
        "published_at": dt.datetime(2024, 1, 2, tzinfo=dt.UTC),
    },
    {
        "snapshot_at": dt.date(2024, 6, 1),
        "score_total": 25.0,
        "dimension_scores": {
            "conflict_of_interest_risk": 20.0,
            "ethics_enforcement_risk": 5.0,
        },
        "published_at": dt.datetime(2024, 6, 2, tzinfo=dt.UTC),
    },
]

FIRE_ROWS: list[dict] = [
    {
        "rule_id": "late_or_amended_disclosure.v1",
        "dimension": "ethics_enforcement_risk",
        "severity": "medium",
        "fired_at": dt.datetime(2024, 5, 20, 9, 0, 0, tzinfo=dt.UTC),
        "evidence_card_id": "ec-002",
        "short_explanation": "Filed disclosure 36 days late.",
        "score_delta": 5.0,
        "snapshot_date": dt.date(2024, 6, 1),
    },
    {
        "rule_id": "committee_sector_trade.v1",
        "dimension": "conflict_of_interest_risk",
        "severity": "high",
        "fired_at": dt.datetime(2024, 3, 15, 11, 0, 0, tzinfo=dt.UTC),
        "evidence_card_id": "ec-001",
        "short_explanation": "Traded in committee sector.",
        "score_delta": 10.0,
        "snapshot_date": dt.date(2024, 3, 15),
    },
]

COMMITTEE_ROWS: list[dict] = [
    {
        "committee_name": "Committee on Energy",
        "role": "member",
        "start_date": dt.date(2023, 1, 3),
        "end_date": None,
        "is_current": True,
        "chamber": "house",
        "committee_type": "standing",
    },
    {
        "committee_name": "Committee on Finance",
        "role": "chair",
        "start_date": dt.date(2021, 1, 3),
        "end_date": dt.date(2023, 1, 2),
        "is_current": False,
        "chamber": "house",
        "committee_type": "standing",
    },
]


def test_returns_member_history_payload() -> None:
    result = assemble_member_history(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)
    assert isinstance(result, MemberHistoryPayload)


def test_snapshot_history_is_chronological_with_total_deltas() -> None:
    result = assemble_member_history(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)

    assert [point.snapshot_date for point in result.snapshots] == [
        dt.date(2024, 1, 1),
        dt.date(2024, 6, 1),
    ]
    assert result.snapshots[0].score_total == 10.0
    assert result.snapshots[0].score_total_delta is None
    assert result.snapshots[1].score_total == 25.0
    assert result.snapshots[1].score_total_delta == 15.0


def test_events_are_most_recent_first() -> None:
    result = assemble_member_history(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)

    assert [event.evidence_card_id for event in result.events] == ["ec-002", "ec-001"]
    assert result.events[0].severity == "medium"
    assert result.events[1].severity == "high"


def test_committee_history_preserves_current_and_historical_memberships() -> None:
    result = assemble_member_history(MEMBER_ROW, SNAPSHOT_ROWS, FIRE_ROWS, COMMITTEE_ROWS)

    assert [row.committee_name for row in result.committee_history] == [
        "Committee on Energy",
        "Committee on Finance",
    ]
    assert result.committee_history[0].is_current is True
    assert result.committee_history[1].end_date == dt.date(2023, 1, 2)
