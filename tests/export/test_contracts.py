from __future__ import annotations

import datetime as dt

import pytest

from src.export.contracts import (
    MemberHistoryCoverageWindowPayload,
    MemberTimelinePagePayload,
    MemberTrendWindowPayload,
    ScoreSummary,
)


def test_export_contracts_reject_boolean_required_int_fields() -> None:
    with pytest.raises(ValueError, match="rule_fire_count must be an integer"):
        ScoreSummary(
            dimension="conflict_of_interest_risk",
            current_score=72.0,
            rule_fire_count=True,
        )


def test_export_contracts_reject_boolean_page_counts() -> None:
    with pytest.raises(ValueError, match="page must be an integer"):
        MemberTimelinePagePayload(
            bioguide_id="P000197",
            name="Nancy Pelosi",
            slug="nancy-pelosi",
            state="CA",
            chamber="house",
            party="Democrat",
            page=True,
            page_size=25,
            total_pages=1,
            total_events=0,
            events=[],
        )


def test_export_contracts_reject_boolean_optional_int_fields() -> None:
    with pytest.raises(ValueError, match="requested_days must be an integer"):
        MemberHistoryCoverageWindowPayload(
            window_key="4w",
            requested_days=True,
            has_full_window=True,
            start_snapshot_date=None,
            end_snapshot_date=dt.date(2026, 4, 13),
        )


def test_export_contracts_accept_boolean_flags() -> None:
    result = MemberTrendWindowPayload(
        window_key="4w",
        label="4 weeks",
        requested_days=28,
        has_full_window=True,
        start_snapshot_date=dt.date(2026, 3, 16),
        end_snapshot_date=dt.date(2026, 4, 13),
        current_score_total=72.0,
        previous_score_total=70.0,
        score_total_delta=2.0,
        recent_event_count=1,
    )

    assert result.has_full_window is True
