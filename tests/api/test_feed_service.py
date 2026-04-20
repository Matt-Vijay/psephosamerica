from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_movement_feed, get_snapshot_summary
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from tests.support.published_snapshot_fixtures import make_snapshot, make_zip_feed


def _write_homepage_feed(root: Path) -> None:
    payload = HomepageFeedPayload(
        snapshot_date=make_zip_feed().snapshot_date,
        top_changes=[
            MemberMovementSummary(
                bioguide_id="P000197",
                name="Nancy Pelosi",
                slug="nancy-pelosi",
                chamber="house",
                party="Democrat",
                state="CA",
                dimension="conflict_of_interest_risk",
                score_delta=5.0,
                abs_delta=5.0,
                event_count=1,
                top_evidence_card_ids=["ec-0001"],
            ),
            MemberMovementSummary(
                bioguide_id="S000148",
                name="Charles Schumer",
                slug="charles-schumer",
                chamber="senate",
                party="Democrat",
                state="NY",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                abs_delta=3.0,
                event_count=1,
                top_evidence_card_ids=["ec-0002"],
            ),
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-0002",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                member_slug="charles-schumer",
                dimension="conflict_of_interest_risk",
                score_delta=-3.0,
                short_explanation="Second event.",
                evidence_card_id="ec-0002",
                occurred_at=make_zip_feed().snapshot_date,
            ),
            RecentEventSummary(
                feed_event_id="event-0001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=5.0,
                short_explanation="First event.",
                evidence_card_id="ec-0001",
                occurred_at=make_zip_feed().snapshot_date,
            ),
        ],
        recent_evidence_card_ids=["ec-0002", "ec-0001"],
    )
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload.model_dump(mode="json")), encoding="utf-8")


def test_get_movement_feed_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_movement_feed(snapshot_root=tmp_path)

    assert result.ok is True
    assert len(result.data.top_changes) == 2
    assert len(result.data.recent_events) == 2
    assert result.data.recent_evidence_card_ids == ["ec-0002", "ec-0001"]


def test_get_movement_feed_supports_slicing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_movement_feed(
        snapshot_root=tmp_path,
        top_changes_limit=1,
        recent_events_limit=1,
    )

    assert result.ok is True
    assert [change.slug for change in result.data.top_changes] == ["nancy-pelosi"]
    assert [event.feed_event_id for event in result.data.recent_events] == ["event-0002"]
    assert result.data.recent_evidence_card_ids == ["ec-0002"]


def test_get_movement_feed_returns_not_found_without_homepage_feed(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_movement_feed(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "movement_feed"
    assert result.identifier == "current"


def test_get_snapshot_summary_returns_counts_from_latest_manifest(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed()])
    _write_homepage_feed(tmp_path)

    result = get_snapshot_summary(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshot_id == "2026-01-01"
    assert result.data.artifact_counts.members == 1
    assert result.data.artifact_counts.evidence == 1
    assert result.data.artifact_counts.zip_feeds == 1
    assert result.data.artifact_counts.current_member_lookups == 1
    assert result.data.artifact_counts.homepage_feeds == 1


def test_get_snapshot_summary_returns_not_found_without_snapshot(tmp_path: Path) -> None:
    result = get_snapshot_summary(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot"
    assert result.identifier == "latest"


def test_src_api_exports_feed_helpers() -> None:
    assert api.get_movement_feed is get_movement_feed
    assert api.get_snapshot_summary is get_snapshot_summary
