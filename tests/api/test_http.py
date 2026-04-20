from __future__ import annotations

import json
import datetime as dt
from datetime import date
from pathlib import Path

from src.api.http import serve_snapshot_compare
from src.api.http import (
    JsonHttpResponse,
    serve_current_member_lookup,
    serve_current_member_lookup_search,
    serve_history_bootstrap,
    serve_history_preset_range,
    serve_homepage_bootstrap,
    serve_member_change_summary,
    serve_member_compare,
    serve_member_history_chart,
    serve_member_preset_compare,
    serve_member_window_compare,
    serve_member_history,
    serve_homepage,
    serve_last_updated,
    serve_member_page,
    serve_member_history_page,
    serve_member,
    serve_movement_feed,
    serve_movement_window,
    serve_search_session,
    serve_snapshot_compare,
    serve_snapshot_preset_compare,
    serve_snapshot_index,
    serve_zip_entry,
    serve_snapshot_summary,
)
from src.export.builders import sha256_hex
from src.export.contracts import CommitteeMembership, MemberHistoryPayload, RecentRuleFire, ScoreSummary
from src.export.local_store import HOMEPAGE_FEED_PATH
from src.homepage.contracts import HomepageFeedPayload, MemberMovementSummary, RecentEventSummary
from src.pipeline.history_aggregate_run import write_history_aggregate
from src.runtime.inspect import load_latest_local_manifest
from src.export.writer import current_member_lookup_path, member_history_path, member_path
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_history,
    make_member_profile,
    make_snapshot,
    make_zip_feed,
)


def _decode(response: JsonHttpResponse) -> dict[str, object]:
    return json.loads(response.body.decode("utf-8"))


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


def _history(
    snapshot_date: dt.date,
    *,
    score_total: float,
    score_total_delta: float | None,
    evidence_card_id: str,
    score_delta: float,
) -> MemberHistoryPayload:
    published_at = dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC)
    payload = make_member_history(
        snapshot_date=snapshot_date,
        published_at=published_at,
        fired_at=published_at,
    )
    snapshot = payload.snapshots[0].model_copy(
        update={
            "score_total": score_total,
            "score_total_delta": score_total_delta,
            "dimension_scores": {"conflict_of_interest_risk": score_total},
        }
    )
    event = payload.events[0].model_copy(
        update={
            "evidence_card_id": evidence_card_id,
            "score_delta": score_delta,
        }
    )
    return payload.model_copy(update={"snapshots": [snapshot], "events": [event]})


def _member_history_trend_root(tmp_path: Path) -> Path:
    snapshot_dates = [
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 15),
        dt.date(2026, 2, 19),
        dt.date(2026, 3, 18),
        dt.date(2026, 4, 15),
    ]
    scores = [10.0, 15.0, 25.0, 35.0, 50.0]
    deltas = [10.0, 5.0, 10.0, 10.0, 15.0]
    root_paths = [tmp_path / snapshot_date.isoformat() for snapshot_date in snapshot_dates]
    aggregate_root = tmp_path / "aggregate"

    for root, snapshot_date, score_total, score_delta in zip(
        root_paths,
        snapshot_dates,
        scores,
        deltas,
        strict=True,
    ):
        evidence_card_id = f"ec-{snapshot_date.isoformat()}"
        make_snapshot(
            root,
            snapshot_id=snapshot_date.isoformat(),
            evidence_cards=[
                make_evidence_card(
                    evidence_card_id=evidence_card_id,
                    snapshot_date=snapshot_date,
                    created_at=dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC),
                )
            ],
            member_histories=[
                _history(
                    snapshot_date,
                    score_total=score_total,
                    score_total_delta=None if snapshot_date == snapshot_dates[0] else score_delta,
                    evidence_card_id=evidence_card_id,
                    score_delta=score_delta,
                )
            ],
        )

    write_history_aggregate(root_paths, aggregate_root)
    return aggregate_root


def test_serve_member_returns_200_json_and_manifest_backed_etag(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed()])

    response = serve_member("nancy-pelosi", snapshot_root=tmp_path)
    manifest = load_latest_local_manifest(snapshot_root=tmp_path)
    member_entry = next(entry for entry in manifest.entries if entry.path == member_path("nancy-pelosi"))

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    expected_etag = sha256_hex(
        f"{manifest.root_sha256}:{member_entry.sha256}".encode("utf-8")
    )
    assert response.headers["ETag"] == f'"{expected_etag}"'
    assert _decode(response)["data"]["slug"] == "nancy-pelosi"


def test_serve_member_returns_404_not_found_json(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    response = serve_member("ghost-member", snapshot_root=tmp_path)

    assert response.status_code == 404
    body = _decode(response)
    assert body["ok"] is False
    assert body["error"] == "not_found"
    assert body["resource_type"] == "member"


def test_serve_member_returns_304_when_if_none_match_hits(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    first = serve_member("nancy-pelosi", snapshot_root=tmp_path)

    second = serve_member(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert second.status_code == 304
    assert second.body == b""
    assert second.headers["ETag"] == first.headers["ETag"]


def test_serve_current_member_lookup_uses_manifest_backed_etag(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    response = serve_current_member_lookup(snapshot_root=tmp_path)
    manifest = load_latest_local_manifest(snapshot_root=tmp_path)
    lookup_entry = next(
        entry for entry in manifest.entries if entry.path == current_member_lookup_path()
    )

    assert response.status_code == 200
    expected_etag = sha256_hex(
        f"{manifest.root_sha256}:{lookup_entry.sha256}".encode("utf-8")
    )
    assert response.headers["ETag"] == f'"{expected_etag}"'
    assert _decode(response)["data"]["members"][0]["slug"] == "nancy-pelosi"


def test_serve_current_member_lookup_search_uses_body_hash_etag_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    first = serve_current_member_lookup_search("nancy", snapshot_root=tmp_path)
    second = serve_current_member_lookup_search(
        "nancy",
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    assert _decode(first)["data"]["members"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_homepage_falls_back_to_body_hash_etag_when_unmanifested(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    first = serve_homepage(snapshot_root=tmp_path)
    second = serve_homepage(
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    assert _decode(first)["data"]["recent_evidence_card_ids"] == ["ec-0001"]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_last_updated_uses_manifest_root_sha_as_etag(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    manifest = load_latest_local_manifest(snapshot_root=tmp_path)

    response = serve_last_updated(snapshot_root=tmp_path)

    assert response.status_code == 200
    assert response.headers["ETag"] == f'"{manifest.root_sha256}"'
    body = _decode(response)
    assert body["data"]["snapshot_date"] == manifest.snapshot_id


def test_serve_last_updated_returns_404_without_snapshot(tmp_path: Path) -> None:
    response = serve_last_updated(snapshot_root=tmp_path)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert _decode(response)["identifier"] == "latest"


def test_serve_movement_feed_supports_slicing_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    first = serve_movement_feed(
        snapshot_root=tmp_path,
        top_changes_limit=1,
        recent_events_limit=1,
    )
    second = serve_movement_feed(
        snapshot_root=tmp_path,
        top_changes_limit=1,
        recent_events_limit=1,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    assert _decode(first)["data"]["top_changes"][0]["slug"] == "nancy-pelosi"
    assert _decode(first)["data"]["recent_evidence_card_ids"] == ["ec-0001"]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_summary_returns_root_hash_etag(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed()])
    _write_homepage_feed(tmp_path)
    manifest = load_latest_local_manifest(snapshot_root=tmp_path)

    response = serve_snapshot_summary(snapshot_root=tmp_path)

    assert response.status_code == 200
    assert response.headers["ETag"] == f'"{manifest.root_sha256}"'
    body = _decode(response)
    assert body["data"]["artifact_counts"]["members"] == 1
    assert body["data"]["artifact_counts"]["homepage_feeds"] == 1


def test_serve_snapshot_summary_returns_503_without_snapshot(tmp_path: Path) -> None:
    response = serve_snapshot_summary(snapshot_root=tmp_path)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


def test_serve_member_history_returns_manifest_backed_etag_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])
    manifest = load_latest_local_manifest(snapshot_root=tmp_path)
    history_entry = next(
        entry
        for entry in manifest.entries
        if entry.path == member_history_path("nancy-pelosi")
    )

    first = serve_member_history("nancy-pelosi", snapshot_root=tmp_path)
    second = serve_member_history(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    expected_etag = sha256_hex(
        f"{manifest.root_sha256}:{history_entry.sha256}".encode("utf-8")
    )
    assert first.status_code == 200
    assert first.headers["ETag"] == f'"{expected_etag}"'
    assert _decode(first)["data"]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_change_summary_falls_back_to_history_and_supports_304(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    first = serve_member_change_summary("nancy-pelosi", snapshot_root=tmp_path)
    second = serve_member_change_summary(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["slug"] == "nancy-pelosi"
    assert body["data"]["top_evidence_card_ids"] == ["ec-0001"]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_index_returns_dynamic_etag_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path, snapshot_id="2026-01-01")
    make_snapshot(tmp_path, snapshot_id="2026-02-01")

    first = serve_snapshot_index(snapshot_root=tmp_path)
    second = serve_snapshot_index(
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    assert _decode(first)["data"]["latest_snapshot_id"] == "2026-02-01"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_compare_returns_window_payload_and_304(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(first_root, snapshot_id="2026-01-01", member_histories=[make_member_history()])
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[make_member_history(snapshot_date=date(2026, 1, 8))],
    )
    write_history_aggregate([first_root, second_root], aggregate_root)

    first = serve_snapshot_compare(
        "2026-01-01",
        "2026-01-08",
        snapshot_root=aggregate_root,
    )
    second = serve_snapshot_compare(
        "2026-01-01",
        "2026-01-08",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["start_snapshot_id"] == "2026-01-01"
    assert body["data"]["end_snapshot_id"] == "2026-01-08"
    assert body["data"]["featured_member_changes"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_preset_compare_returns_payload_and_304(tmp_path: Path) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_snapshot_preset_compare("latest", snapshot_root=aggregate_root)
    second = serve_snapshot_preset_compare(
        "latest",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["start_snapshot_id"] == "2026-03-18"
    assert body["data"]["end_snapshot_id"] == "2026-04-15"
    assert body["data"]["featured_member_changes"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_preset_compare_returns_404_for_unknown_preset(
    tmp_path: Path,
) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    response = serve_snapshot_preset_compare("bogus", snapshot_root=aggregate_root)

    assert response.status_code == 404
    body = _decode(response)
    assert body["resource_type"] == "snapshot_preset_compare"
    assert body["identifier"] == "bogus"


def test_serve_history_preset_range_returns_payload_and_304(tmp_path: Path) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_history_preset_range("latest", snapshot_root=aggregate_root)
    second = serve_history_preset_range(
        "latest",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["preset"]["preset_key"] == "latest"
    assert body["data"]["movement_window"]["window_key"] == "latest"
    assert body["data"]["snapshot_compare"]["end_snapshot_id"] == "2026-04-15"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_history_preset_range_returns_404_for_unknown_preset(
    tmp_path: Path,
) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    response = serve_history_preset_range("bogus", snapshot_root=aggregate_root)

    assert response.status_code == 404
    body = _decode(response)
    assert body["resource_type"] == "history_preset_range"
    assert body["identifier"] == "bogus"


def test_serve_member_page_returns_aggregate_payload_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path)
    response = serve_member_page("nancy-pelosi", snapshot_root=tmp_path)
    second = serve_member_page(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["profile"]["slug"] == "nancy-pelosi"
    assert body["data"]["recent_evidence_cards"][0]["evidence_card_id"] == "ec-0001"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_history_page_returns_aggregate_payload_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    first = serve_member_history_page("nancy-pelosi", snapshot_root=tmp_path)
    second = serve_member_history_page(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["member_page"]["profile"]["slug"] == "nancy-pelosi"
    assert body["data"]["history"]["slug"] == "nancy-pelosi"
    assert body["data"]["recent_change"]["slug"] == "nancy-pelosi"
    assert body["data"]["history_evidence_cards"][0]["evidence_card_id"] == "ec-0001"
    assert body["data"]["snapshot_index"]["latest_snapshot_id"] == "2026-01-01"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_history_page_includes_trend_summary_windows_and_304(
    tmp_path: Path,
) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)
    second = serve_member_history_page(
        "nancy-pelosi",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["member_page"]["profile"]["slug"] == "nancy-pelosi"
    assert body["data"]["history"]["slug"] == "nancy-pelosi"
    assert body["data"]["trend_summary"]["slug"] == "nancy-pelosi"
    windows = {
        window["window_key"]: window for window in body["data"]["trend_summary"]["windows"]
    }
    assert list(windows) == ["4w", "12w", "cycle"]
    assert windows["4w"]["start_snapshot_date"] == "2026-03-18"
    assert windows["4w"]["score_total_delta"] == 15.0
    assert windows["4w"]["top_evidence_card_ids"] == ["ec-2026-04-15"]
    assert windows["12w"]["start_snapshot_date"] == "2026-01-15"
    assert windows["12w"]["score_total_delta"] == 35.0
    assert windows["12w"]["recent_event_count"] == 3
    assert windows["cycle"]["start_snapshot_date"] == "2026-01-01"
    assert windows["cycle"]["score_total_delta"] == 40.0
    assert windows["cycle"]["recent_event_count"] == 4
    assert body["data"]["chart"]["latest_snapshot_id"] == "2026-04-15"
    assert [preset["preset_key"] for preset in body["data"]["chart"]["compare_presets"]] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    assert body["data"]["default_window_compare"]["start_snapshot_id"] == "2026-03-18"
    assert body["data"]["default_window_compare"]["end_snapshot_id"] == "2026-04-15"
    assert body["data"]["default_window_compare"]["summary"]["score_total_delta"] == 15.0
    assert [
        card["evidence_card_id"]
        for card in body["data"]["default_window_compare"]["evidence_cards"]
    ] == ["ec-2026-04-15"]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_history_chart_returns_chart_payload_and_304(tmp_path: Path) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_member_history_chart("nancy-pelosi", snapshot_root=aggregate_root)
    second = serve_member_history_chart(
        "nancy-pelosi",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["slug"] == "nancy-pelosi"
    assert body["data"]["latest_snapshot_id"] == "2026-04-15"
    assert body["data"]["default_preset_key"] == "4w"
    assert [point["snapshot_id"] for point in body["data"]["points"]] == [
        "2026-01-01",
        "2026-01-15",
        "2026-02-19",
        "2026-03-18",
        "2026-04-15",
    ]
    assert [preset["preset_key"] for preset in body["data"]["compare_presets"]] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_window_compare_returns_payload_and_304(tmp_path: Path) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_member_window_compare(
        "nancy-pelosi",
        "2026-01-15",
        "2026-04-15",
        snapshot_root=aggregate_root,
    )
    second = serve_member_window_compare(
        "nancy-pelosi",
        "2026-01-15",
        "2026-04-15",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["slug"] == "nancy-pelosi"
    assert body["data"]["start_snapshot_id"] == "2026-01-15"
    assert body["data"]["end_snapshot_id"] == "2026-04-15"
    assert body["data"]["summary"]["score_total_delta"] == 35.0
    assert [card["evidence_card_id"] for card in body["data"]["evidence_cards"]] == [
        "ec-2026-04-15",
        "ec-2026-03-18",
        "ec-2026-02-19",
    ]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_preset_compare_returns_payload_and_304(tmp_path: Path) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    first = serve_member_preset_compare(
        "nancy-pelosi",
        "latest",
        snapshot_root=aggregate_root,
    )
    second = serve_member_preset_compare(
        "nancy-pelosi",
        "latest",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["slug"] == "nancy-pelosi"
    assert body["data"]["start_snapshot_id"] == "2026-03-18"
    assert body["data"]["end_snapshot_id"] == "2026-04-15"
    assert body["data"]["summary"]["score_total_delta"] == 15.0
    assert [card["evidence_card_id"] for card in body["data"]["evidence_cards"]] == [
        "ec-2026-04-15"
    ]
    assert second.status_code == 304
    assert second.body == b""


def test_serve_movement_window_falls_back_to_histories_and_supports_304(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    first = serve_movement_window(snapshot_root=tmp_path)
    second = serve_movement_window(
        snapshot_root=tmp_path,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["latest_snapshot_id"] == "2026-01-01"
    assert body["data"]["top_changes"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_movement_window_supports_preset_windows_and_304(tmp_path: Path) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(first_root, snapshot_id="2026-01-01", member_histories=[make_member_history(snapshot_date=date(2026, 1, 1))])
    make_snapshot(second_root, snapshot_id="2026-01-08", member_histories=[make_member_history(snapshot_date=date(2026, 1, 8))])
    write_history_aggregate([first_root, second_root], aggregate_root)

    response = serve_movement_window(name="4w", snapshot_root=aggregate_root)
    second = serve_movement_window(
        name="4w",
        snapshot_root=aggregate_root,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["window_key"] == "4w"
    assert body["data"]["has_full_window"] is False
    assert body["data"]["latest_snapshot_id"] == "2026-01-08"
    assert body["data"]["previous_snapshot_id"] == "2026-01-01"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_zip_entry_returns_aggregate_payload_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path, zip_feeds=[make_zip_feed(zip_code="94102")])

    response = serve_zip_entry("94102", snapshot_root=tmp_path)
    second = serve_zip_entry(
        "94102",
        snapshot_root=tmp_path,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["zip_feed"]["zip_code"] == "94102"
    assert body["data"]["member_lookup_entries"][0]["slug"] == "nancy-pelosi"
    assert body["data"]["snapshot"]["artifact_counts"]["zip_feeds"] == 1
    assert second.status_code == 304
    assert second.body == b""


def test_serve_homepage_bootstrap_returns_aggregate_payload_and_304(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path)
    _write_homepage_feed(tmp_path)

    response = serve_homepage_bootstrap(snapshot_root=tmp_path)
    second = serve_homepage_bootstrap(
        snapshot_root=tmp_path,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["snapshot"]["snapshot_id"] == "2026-01-01"
    assert body["data"]["movement"]["top_changes"][0]["slug"] == "nancy-pelosi"
    assert body["data"]["featured_lookup_entries"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_history_bootstrap_returns_aggregate_payload_and_304(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-01"
    second_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(first_root, snapshot_id="2026-01-01", member_histories=[make_member_history(snapshot_date=make_zip_feed().snapshot_date)])
    make_snapshot(
        second_root,
        snapshot_id="2026-01-08",
        member_histories=[make_member_history(snapshot_date=date(2026, 1, 8))],
    )

    write_history_aggregate([first_root, second_root], aggregate_root)

    response = serve_history_bootstrap(snapshot_root=aggregate_root)
    second = serve_history_bootstrap(
        snapshot_root=aggregate_root,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["snapshot_index"]["latest_snapshot_id"] == "2026-01-08"
    assert body["data"]["movement_window"]["latest_snapshot_id"] == "2026-01-08"
    assert body["data"]["default_compare_preset_key"] == "latest"
    assert [preset["preset_key"] for preset in body["data"]["compare_presets"]] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    assert body["data"]["featured_member_changes"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_history_bootstrap_uses_distinct_etag_for_sliced_payloads(
    tmp_path: Path,
) -> None:
    aggregate_root = _member_history_trend_root(tmp_path)

    unsliced = serve_history_bootstrap(snapshot_root=aggregate_root)
    sliced = serve_history_bootstrap(
        snapshot_root=aggregate_root,
        featured_limit=0,
        if_none_match=unsliced.headers["ETag"],
    )
    sliced_second = serve_history_bootstrap(
        snapshot_root=aggregate_root,
        featured_limit=0,
        if_none_match=sliced.headers["ETag"],
    )

    assert unsliced.status_code == 200
    assert sliced.status_code == 200
    assert sliced.headers["ETag"] != unsliced.headers["ETag"]
    assert sliced_second.status_code == 304
    body = _decode(sliced)
    assert body["data"]["featured_member_changes"] == []


def test_serve_search_session_returns_results_and_304(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    response = serve_search_session("nancy", snapshot_root=tmp_path)
    second = serve_search_session(
        "nancy",
        snapshot_root=tmp_path,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["query"] == "nancy"
    assert body["data"]["results"][0]["lookup_entry"]["slug"] == "nancy-pelosi"
    assert body["data"]["results"][0]["member_page_preview"]["profile"]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_member_compare_returns_aggregate_payload_and_304(tmp_path: Path) -> None:
    left = make_member_profile(
        bioguide_id="P000197",
        slug="nancy-pelosi",
        name="Nancy Pelosi",
        chamber="house",
        state="CA",
    ).model_copy(
        update={
            "scores": [
                ScoreSummary(
                    dimension="conflict_of_interest_risk",
                    current_score=55.0,
                    rule_fire_count=3,
                )
            ],
            "committees": [
                CommitteeMembership(committee_name="Appropriations", role="Member")
            ],
            "top_evidence_card_ids": ["ec-left-1"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-left-1",
                    short_explanation="Left recent event.",
                    score_delta=-5.0,
                    snapshot_date=make_zip_feed().snapshot_date,
                )
            ],
            "total_evidence_cards": 1,
        }
    )
    right = make_member_profile(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        state="NY",
    ).model_copy(
        update={
            "scores": [
                ScoreSummary(
                    dimension="conflict_of_interest_risk",
                    current_score=70.0,
                    rule_fire_count=2,
                )
            ],
            "committees": [
                CommitteeMembership(committee_name="Finance", role="Member")
            ],
            "top_evidence_card_ids": ["ec-right-1"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-right-1",
                    short_explanation="Right recent event.",
                    score_delta=-2.0,
                    snapshot_date=make_zip_feed().snapshot_date,
                )
            ],
            "total_evidence_cards": 1,
        }
    )
    make_snapshot(
        tmp_path,
        member_profiles=[left, right],
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-left-1",
                member_slug="nancy-pelosi",
                member_bioguide_id="P000197",
                member_name="Nancy Pelosi",
                score_delta=-5.0,
            ),
            make_evidence_card(
                evidence_card_id="ec-right-1",
                member_slug="charles-schumer",
                member_bioguide_id="S000148",
                member_name="Charles Schumer",
                score_delta=-2.0,
            ),
        ],
    )

    response = serve_member_compare(
        "nancy-pelosi",
        "charles-schumer",
        snapshot_root=tmp_path,
    )
    second = serve_member_compare(
        "nancy-pelosi",
        "charles-schumer",
        snapshot_root=tmp_path,
        if_none_match=response.headers["ETag"],
    )

    assert response.status_code == 200
    body = _decode(response)
    assert body["data"]["left"]["profile"]["slug"] == "nancy-pelosi"
    assert body["data"]["right"]["profile"]["slug"] == "charles-schumer"
    assert body["data"]["score_comparisons"][0]["dimension"] == "conflict_of_interest_risk"
    assert second.status_code == 304
    assert second.body == b""


def test_serve_snapshot_compare_returns_window_payload_and_304(tmp_path: Path) -> None:
    start_root = tmp_path / "2026-01-01"
    end_root = tmp_path / "2026-01-08"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        start_root,
        snapshot_id="2026-01-01",
        member_histories=[make_member_history(snapshot_date=date(2026, 1, 1))],
    )
    make_snapshot(
        end_root,
        snapshot_id="2026-01-08",
        member_histories=[make_member_history(snapshot_date=date(2026, 1, 8))],
    )
    write_history_aggregate([start_root, end_root], aggregate_root)

    first = serve_snapshot_compare(
        "2026-01-01",
        "2026-01-08",
        snapshot_root=aggregate_root,
    )
    second = serve_snapshot_compare(
        "2026-01-01",
        "2026-01-08",
        snapshot_root=aggregate_root,
        if_none_match=first.headers["ETag"],
    )

    assert first.status_code == 200
    body = _decode(first)
    assert body["data"]["start_snapshot_id"] == "2026-01-01"
    assert body["data"]["end_snapshot_id"] == "2026-01-08"
    assert body["data"]["featured_member_changes"][0]["slug"] == "nancy-pelosi"
    assert second.status_code == 304
    assert second.body == b""
