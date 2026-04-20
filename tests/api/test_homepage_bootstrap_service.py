from __future__ import annotations

import json
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_homepage_bootstrap
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.export.writer import current_member_lookup_path, homepage_bootstrap_path
from src.api.contracts import HomepageBootstrapPayload
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.pipeline.history_aggregate_run import write_history_aggregate
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


def test_get_homepage_bootstrap_returns_snapshot_movement_and_featured_lookup(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_homepage_bootstrap(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.snapshot.snapshot_id == "2026-01-01"
    assert [change.slug for change in result.data.movement.top_changes] == [
        "nancy-pelosi",
        "charles-schumer",
    ]
    assert [entry.slug for entry in result.data.featured_lookup_entries] == [
        "nancy-pelosi",
    ]


def test_get_homepage_bootstrap_supports_movement_slicing(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    result = get_homepage_bootstrap(
        snapshot_root=tmp_path,
        top_changes_limit=1,
        recent_events_limit=1,
    )

    assert result.ok is True
    assert [change.slug for change in result.data.movement.top_changes] == ["nancy-pelosi"]
    assert [event.feed_event_id for event in result.data.movement.recent_events] == ["event-0002"]
    assert [entry.slug for entry in result.data.featured_lookup_entries] == ["nancy-pelosi"]


def test_get_homepage_bootstrap_prefers_precomputed_artifact_and_falls_back_cleanly(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    fallback = get_homepage_bootstrap(snapshot_root=tmp_path)
    assert fallback.ok is True

    artifact = HomepageBootstrapPayload.model_validate(fallback.data.model_dump(mode="json"))
    artifact_path = tmp_path / homepage_bootstrap_path()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact.model_dump(mode="json")), encoding="utf-8")

    precomputed = get_homepage_bootstrap(snapshot_root=tmp_path)

    assert precomputed.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_homepage_bootstrap_ignores_stale_precomputed_artifact(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    fallback = get_homepage_bootstrap(snapshot_root=tmp_path)
    assert fallback.ok is True

    stale = fallback.data.model_copy(
        update={
            "snapshot": fallback.data.snapshot.model_copy(
                update={"snapshot_id": "stale-snapshot", "root_sha256": "f" * 64}
            ),
            "movement": fallback.data.movement.model_copy(
                update={
                    "top_changes": [],
                    "recent_events": [],
                    "recent_evidence_card_ids": [],
                }
            ),
            "featured_lookup_entries": [],
        }
    )
    artifact_path = tmp_path / homepage_bootstrap_path()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(stale.model_dump(mode="json")), encoding="utf-8")

    result = get_homepage_bootstrap(snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_homepage_bootstrap_repairs_featured_lookup_from_current_lookup(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    fallback = get_homepage_bootstrap(snapshot_root=tmp_path)
    assert fallback.ok is True

    degraded = fallback.data.model_copy(update={"featured_lookup_entries": []})
    artifact_path = tmp_path / homepage_bootstrap_path()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(degraded.model_dump(mode="json")), encoding="utf-8")

    result = get_homepage_bootstrap(snapshot_root=tmp_path)

    assert result.ok is True
    assert [entry.slug for entry in result.data.featured_lookup_entries] == [
        entry.slug for entry in fallback.data.featured_lookup_entries
    ]


def test_get_homepage_bootstrap_returns_not_found_without_homepage_feed(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)

    result = get_homepage_bootstrap(snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "homepage_bootstrap"
    assert result.identifier == "current"


def test_get_homepage_bootstrap_history_aggregate_root_copies_latest_artifact(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(first_root, snapshot_id="2026-01-06")
    make_snapshot(second_root, snapshot_id="2026-01-13")
    _write_homepage_feed(second_root)

    bootstrap = get_homepage_bootstrap(snapshot_root=second_root)
    assert bootstrap.ok is True
    artifact_path = second_root / homepage_bootstrap_path()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(bootstrap.data.model_dump(mode="json")),
        encoding="utf-8",
    )

    write_history_aggregate([first_root, second_root], aggregate_root)

    result = get_homepage_bootstrap(snapshot_root=aggregate_root)

    assert (aggregate_root / homepage_bootstrap_path()).is_file()
    assert result.ok is True
    assert result.data.snapshot.snapshot_id == "2026-01-13"
    assert [change.slug for change in result.data.movement.top_changes] == [
        change.slug for change in bootstrap.data.movement.top_changes
    ]


def test_src_api_exports_homepage_bootstrap_helper() -> None:
    assert api.get_homepage_bootstrap is get_homepage_bootstrap
