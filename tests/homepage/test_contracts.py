from __future__ import annotations

import datetime as dt

import pytest

from src.homepage.contracts import MemberMovementSummary, MovementWindowPayload


def _movement(event_count: object = 1) -> MemberMovementSummary:
    return MemberMovementSummary(
        bioguide_id="P000197",
        name="Nancy Pelosi",
        slug="nancy-pelosi",
        chamber="house",
        party="Democrat",
        state="CA",
        dimension="conflict_of_interest_risk",
        score_delta=-2.0,
        abs_delta=2.0,
        event_count=event_count,
    )


def test_homepage_contracts_reject_boolean_event_count() -> None:
    with pytest.raises(ValueError, match="event_count must be an integer"):
        _movement(event_count=True)


def test_homepage_contracts_accept_boolean_window_flags() -> None:
    payload = MovementWindowPayload(
        latest_snapshot_id="2026-04-13",
        latest_snapshot_date=dt.date(2026, 4, 13),
        previous_snapshot_id=None,
        previous_snapshot_date=None,
        has_full_window=True,
        top_changes=[_movement()],
        recent_events=[],
    )

    assert payload.has_full_window is True
