from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_member_history_chart
from src.api.read_service import get_member_history_page
from src.api.read_service import get_member_timeline_dimension
from src.api.read_service import get_member_timeline_page
from src.api.read_service import get_member_timeline_year
from src.export.writer import member_history_chart_path, member_history_page_path
from src.export.contracts import MemberHistoryPayload
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_history,
    make_snapshot,
)


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


def _history_root(tmp_path: Path) -> Path:
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


def test_get_member_history_page_returns_member_current_and_history(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_history_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.member_page.profile.slug == "nancy-pelosi"
    assert result.data.history.slug == "nancy-pelosi"
    assert result.data.recent_change.slug == "nancy-pelosi"
    assert result.data.recent_change.top_evidence_card_ids == ["ec-0001"]
    assert result.data.history_evidence_cards[0].evidence_card_id == "ec-0001"
    history_card = result.data.model_dump(mode="json")["history_evidence_cards"][0]
    assert history_card["source_count"] == 1
    assert history_card["official_source_count"] == 1
    assert history_card["primary_source_url"].startswith("https://disclosures.house.gov/")
    assert result.data.snapshot_index.latest_snapshot_id == "2026-01-01"
    assert result.data.timeline_index is not None
    assert result.data.timeline_page is not None
    assert result.data.timeline_index.total_events == 1
    assert result.data.timeline_page.events[0].event_id.startswith("he-")


def test_get_member_timeline_page_returns_resolved_evidence_cards(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_page("nancy-pelosi", 1, snapshot_root=tmp_path)

    assert result.ok is True
    assert [event.evidence_card_id for event in result.data.events] == ["ec-0001"]
    assert [card.evidence_card_id for card in result.data.evidence_cards] == ["ec-0001"]
    assert result.data.missing_evidence_card_ids == []
    timeline_card = result.data.model_dump(mode="json")["evidence_cards"][0]
    assert timeline_card["source_count"] == 1
    assert timeline_card["official_source_count"] == 1
    assert timeline_card["primary_source_url"].startswith("https://disclosures.house.gov/")


def test_get_member_timeline_page_surfaces_missing_evidence_cards(tmp_path: Path) -> None:
    make_snapshot(
        tmp_path,
        member_histories=[make_member_history()],
        evidence_cards=[],
    )

    result = get_member_timeline_page("nancy-pelosi", 1, snapshot_root=tmp_path)

    assert result.ok is True
    assert [event.evidence_card_id for event in result.data.events] == ["ec-0001"]
    assert result.data.evidence_cards == []
    assert result.data.missing_evidence_card_ids == ["ec-0001"]


def test_get_member_timeline_page_returns_not_found_for_out_of_range_page(
    tmp_path: Path,
) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    result = get_member_timeline_page("nancy-pelosi", 2, snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member_timeline_page"
    assert result.identifier == "nancy-pelosi:2"


def test_get_member_timeline_wrappers_hydrate_nested_page_cards(tmp_path: Path) -> None:
    make_snapshot(tmp_path, member_histories=[make_member_history()])

    year_result = get_member_timeline_year("nancy-pelosi", 2026, snapshot_root=tmp_path)
    dimension_result = get_member_timeline_dimension(
        "nancy-pelosi",
        "conflict_of_interest_risk",
        snapshot_root=tmp_path,
    )

    assert year_result.ok is True
    assert [card.evidence_card_id for card in year_result.data.timeline_page.evidence_cards] == [
        "ec-0001"
    ]
    assert year_result.data.timeline_page.missing_evidence_card_ids == []
    assert dimension_result.ok is True
    assert [
        card.evidence_card_id for card in dimension_result.data.timeline_page.evidence_cards
    ] == ["ec-0001"]
    assert dimension_result.data.timeline_page.missing_evidence_card_ids == []


def test_get_member_history_page_returns_not_found_for_missing_member(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_member_history_page("ghost-member", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member"
    assert result.identifier == "ghost-member"


def test_get_member_history_page_includes_member_trend_summary_windows(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.member_page.profile.slug == "nancy-pelosi"
    assert result.data.history.slug == "nancy-pelosi"
    assert result.data.trend_summary.slug == "nancy-pelosi"
    assert result.data.coverage is not None
    assert result.data.coverage.snapshot_count == 5
    assert [window.window_key for window in result.data.trend_summary.windows] == [
        "4w",
        "12w",
        "cycle",
    ]
    windows = {window.window_key: window for window in result.data.trend_summary.windows}
    assert windows["4w"].start_snapshot_date == dt.date(2026, 3, 18)
    assert windows["4w"].score_total_delta == 15.0
    assert windows["4w"].top_evidence_card_ids == ["ec-2026-04-15"]
    assert windows["12w"].start_snapshot_date == dt.date(2026, 1, 15)
    assert windows["12w"].score_total_delta == 35.0
    assert windows["12w"].recent_event_count == 3
    assert windows["cycle"].start_snapshot_date == dt.date(2026, 1, 1)
    assert windows["cycle"].score_total_delta == 40.0
    assert windows["cycle"].recent_event_count == 4
    assert result.data.chart.latest_snapshot_id == "2026-04-15"
    assert [preset.preset_key for preset in result.data.chart.compare_presets] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]
    assert result.data.chart.points[-1].event_count == 1
    assert result.data.default_window_compare.start_snapshot_id == "2026-03-18"
    assert result.data.default_window_compare.end_snapshot_id == "2026-04-15"
    assert result.data.default_window_compare.summary.score_total_delta == 15.0
    assert [
        card.evidence_card_id for card in result.data.default_window_compare.evidence_cards
    ] == ["ec-2026-04-15"]
    assert result.data.timeline_index is not None
    assert result.data.timeline_page is not None
    assert result.data.timeline_index.total_events == 5
    assert result.data.timeline_index.year_buckets[0].year == 2026
    assert result.data.timeline_index.year_buckets[0].start_page == 1
    assert result.data.timeline_page.page == 1
    assert result.data.timeline_page.events[0].evidence_card_id == "ec-2026-04-15"


def test_get_member_history_chart_returns_precomputed_chart_payload(tmp_path: Path) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_member_history_chart("nancy-pelosi", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.latest_snapshot_id == "2026-04-15"
    assert result.data.default_preset_key == "4w"
    assert [point.snapshot_id for point in result.data.points] == [
        "2026-01-01",
        "2026-01-15",
        "2026-02-19",
        "2026-03-18",
        "2026-04-15",
    ]
    assert [preset.preset_key for preset in result.data.compare_presets] == [
        "latest",
        "4w",
        "12w",
        "cycle",
    ]


def test_get_member_history_page_returns_not_found_when_default_preset_is_missing(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)
    (aggregate_root / member_history_page_path("nancy-pelosi")).unlink()
    chart_path = aggregate_root / member_history_chart_path("nancy-pelosi")
    chart_data = json.loads(chart_path.read_text())
    chart_data["default_preset_key"] = "latest"
    chart_data["compare_presets"] = [
        preset for preset in chart_data["compare_presets"] if preset["preset_key"] != "latest"
    ]
    chart_path.write_text(json.dumps(chart_data, sort_keys=True))

    result = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member_window_compare"
    assert result.identifier == "nancy-pelosi"


def test_get_member_history_page_prefers_precomputed_artifact_and_falls_back_cleanly(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    precomputed = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)
    (aggregate_root / member_history_page_path("nancy-pelosi")).unlink()
    fallback = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_member_history_page_ignores_stale_precomputed_artifact(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)
    artifact_path = aggregate_root / member_history_page_path("nancy-pelosi")
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["snapshot_index"]["latest_snapshot_id"] = "stale-snapshot"
    artifact_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.snapshot_index.latest_snapshot_id == "2026-04-15"


def test_get_member_history_page_backfills_timeline_fields_for_older_artifact(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)
    artifact_path = aggregate_root / member_history_page_path("nancy-pelosi")
    payload = json.loads(artifact_path.read_text())
    payload.pop("timeline_index", None)
    payload.pop("timeline_page", None)
    artifact_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = get_member_history_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.timeline_index is not None
    assert result.data.timeline_page is not None
    assert result.data.timeline_index.total_events == 5
    assert result.data.timeline_page.events[0].evidence_card_id == "ec-2026-04-15"


def test_src_api_exports_member_history_page_helper() -> None:
    assert api.get_member_history_page is get_member_history_page


def test_src_api_exports_member_history_chart_helper() -> None:
    assert api.get_member_history_chart is get_member_history_chart
