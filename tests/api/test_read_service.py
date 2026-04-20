from __future__ import annotations

import json
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import (
    get_current_member_lookup,
    get_evidence,
    get_homepage,
    get_last_updated,
    get_member,
    get_zip,
    search_current_member_lookup,
)
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.runtime.inspect import load_latest_local_snapshot_metadata
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
            )
        ],
        recent_events=[
            RecentEventSummary(
                feed_event_id="event-0001",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                member_slug="nancy-pelosi",
                dimension="conflict_of_interest_risk",
                score_delta=5.0,
                short_explanation="Test explanation.",
                evidence_card_id="ec-0001",
                occurred_at=make_zip_feed().snapshot_date,
            )
        ],
        recent_evidence_card_ids=["ec-0001"],
    )
    dest = root / HOMEPAGE_FEED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload.model_dump(mode="json")), encoding="utf-8")


def test_get_member_returns_wrapped_payload_with_batch_meta(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed()])

    result = get_member("nancy-pelosi", snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_member_returns_not_found_for_missing_slug(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_member("ghost-member", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member"
    assert result.identifier == "ghost-member"


def test_get_evidence_returns_not_found_for_missing_card(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_evidence("ec-missing", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "evidence"
    assert result.identifier == "ec-missing"


def test_get_zip_returns_not_found_for_missing_zip(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_zip("99999", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "zip"
    assert result.identifier == "99999"


def test_get_homepage_returns_wrapped_payload_with_batch_meta(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_homepage(snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.recent_evidence_card_ids == ["ec-0001"]
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_homepage_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_homepage(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "homepage"
    assert result.identifier == "current"


def test_get_current_member_lookup_returns_wrapped_payload(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_current_member_lookup(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.members[0].slug == "nancy-pelosi"
    assert result.data.members[0].search_name == "nancy pelosi"


def test_get_current_member_lookup_returns_not_found_when_artifact_missing(tmp_path: Path) -> None:
    result = get_current_member_lookup(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "current_member_lookup"
    assert result.identifier == "current"


def test_search_current_member_lookup_returns_filtered_results(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = search_current_member_lookup("nancy", snapshot_root=tmp_path)

    assert result.ok is True
    assert [member.slug for member in result.data.members] == ["nancy-pelosi"]


def test_search_current_member_lookup_keeps_ok_true_with_no_matches(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = search_current_member_lookup("ghost", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.members == []


def test_get_last_updated_returns_snapshot_metadata(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_last_updated(snapshot_root=tmp_path)
    snapshot_date, published_at = load_latest_local_snapshot_metadata(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshot_date == snapshot_date
    assert result.data.published_at == published_at
    assert result.meta.snapshot_date == snapshot_date
    assert result.meta.published_at == published_at


def test_get_last_updated_returns_not_found_without_snapshot(tmp_path: Path) -> None:
    result = get_last_updated(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot"
    assert result.identifier == "latest"


def test_src_api_exports_read_service_helpers() -> None:
    assert api.get_homepage is get_homepage
    assert api.get_zip is get_zip
    assert api.get_member is get_member
    assert api.get_evidence is get_evidence
    assert api.get_current_member_lookup is get_current_member_lookup
    assert api.search_current_member_lookup is search_current_member_lookup
    assert api.get_last_updated is get_last_updated
