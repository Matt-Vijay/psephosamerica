from __future__ import annotations

import datetime as dt
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_history_preset_range
from src.api.read_service import get_member_preset_compare
from src.api.read_service import get_member_window_compare
from src.api.read_service import get_snapshot_preset_compare
from src.api.read_service import get_snapshot_compare
from src.export.writer import (
    history_preset_range_path,
    member_preset_compare_path,
    snapshot_preset_compare_path,
)
from src.export.contracts import MemberHistoryPayload
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_history,
    make_snapshot,
)


def _history(
    *,
    bioguide_id: str,
    slug: str,
    name: str,
    chamber: str,
    state: str,
    snapshot_date: dt.date,
    score_total: float,
    score_total_delta: float | None,
    evidence_card_id: str,
    score_delta: float,
    fired_at: dt.datetime | None = None,
) -> MemberHistoryPayload:
    published_at = dt.datetime.combine(snapshot_date, dt.time.min, tzinfo=dt.UTC)
    resolved_fired_at = fired_at or published_at
    payload = make_member_history(
        bioguide_id=bioguide_id,
        slug=slug,
        name=name,
        chamber=chamber,  # type: ignore[arg-type]
        state=state,
        snapshot_date=snapshot_date,
        published_at=published_at,
        fired_at=resolved_fired_at,
    )
    snapshot = payload.snapshots[0].model_copy(
        update={
            "score_total": score_total,
            "score_total_delta": score_total_delta,
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
    start_root = tmp_path / "2026-01-01"
    middle_root = tmp_path / "2026-01-08"
    end_root = tmp_path / "2026-01-15"
    aggregate_root = tmp_path / "aggregate"
    make_snapshot(
        start_root,
        snapshot_id="2026-01-01",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-0001",
                snapshot_date=dt.date(2026, 1, 1),
                created_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            make_evidence_card(
                evidence_card_id="ec-1000",
                snapshot_date=dt.date(2026, 1, 1),
                created_at=dt.datetime(2026, 1, 2, 8, 0, tzinfo=dt.UTC),
            ),
        ],
        member_histories=[
            _history(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                chamber="house",
                state="CA",
                snapshot_date=dt.date(2026, 1, 1),
                score_total=40.0,
                score_total_delta=None,
                evidence_card_id="ec-0001",
                score_delta=5.0,
                fired_at=dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC),
            ),
            _history(
                bioguide_id="S000033",
                slug="chuck-schumer",
                name="Chuck Schumer",
                chamber="senate",
                state="NY",
                snapshot_date=dt.date(2026, 1, 1),
                score_total=30.0,
                score_total_delta=None,
                evidence_card_id="ec-1000",
                score_delta=4.0,
                fired_at=dt.datetime(2026, 1, 2, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )
    make_snapshot(
        middle_root,
        snapshot_id="2026-01-08",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-0002",
                snapshot_date=dt.date(2026, 1, 8),
                created_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            make_evidence_card(
                evidence_card_id="ec-1001",
                snapshot_date=dt.date(2026, 1, 8),
                created_at=dt.datetime(2026, 1, 7, 8, 0, tzinfo=dt.UTC),
            ),
        ],
        member_histories=[
            _history(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                chamber="house",
                state="CA",
                snapshot_date=dt.date(2026, 1, 8),
                score_total=47.0,
                score_total_delta=7.0,
                evidence_card_id="ec-0002",
                score_delta=7.0,
                fired_at=dt.datetime(2026, 1, 8, 8, 0, tzinfo=dt.UTC),
            ),
            _history(
                bioguide_id="S000033",
                slug="chuck-schumer",
                name="Chuck Schumer",
                chamber="senate",
                state="NY",
                snapshot_date=dt.date(2026, 1, 8),
                score_total=26.0,
                score_total_delta=-4.0,
                evidence_card_id="ec-1001",
                score_delta=-4.0,
                fired_at=dt.datetime(2026, 1, 7, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )
    make_snapshot(
        end_root,
        snapshot_id="2026-01-15",
        evidence_cards=[
            make_evidence_card(
                evidence_card_id="ec-0003",
                snapshot_date=dt.date(2026, 1, 15),
                created_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
            make_evidence_card(
                evidence_card_id="ec-1002",
                snapshot_date=dt.date(2026, 1, 15),
                created_at=dt.datetime(2026, 1, 14, 8, 0, tzinfo=dt.UTC),
            ),
        ],
        member_histories=[
            _history(
                bioguide_id="P000197",
                slug="nancy-pelosi",
                name="Nancy Pelosi",
                chamber="house",
                state="CA",
                snapshot_date=dt.date(2026, 1, 15),
                score_total=55.0,
                score_total_delta=8.0,
                evidence_card_id="ec-0003",
                score_delta=8.0,
                fired_at=dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC),
            ),
            _history(
                bioguide_id="S000033",
                slug="chuck-schumer",
                name="Chuck Schumer",
                chamber="senate",
                state="NY",
                snapshot_date=dt.date(2026, 1, 15),
                score_total=18.0,
                score_total_delta=-8.0,
                evidence_card_id="ec-1002",
                score_delta=-8.0,
                fired_at=dt.datetime(2026, 1, 14, 8, 0, tzinfo=dt.UTC),
            ),
        ],
    )
    write_history_aggregate([start_root, middle_root, end_root], aggregate_root)
    return aggregate_root


def test_get_snapshot_compare_returns_window_movement_and_featured_member_changes(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_snapshot_compare(
        "2026-01-01",
        "2026-01-15",
        snapshot_root=aggregate_root,
    )

    assert result.ok is True
    assert result.data.start_snapshot_id == "2026-01-01"
    assert result.data.end_snapshot_id == "2026-01-15"
    assert result.data.start_snapshot_date == dt.date(2026, 1, 1)
    assert result.data.end_snapshot_date == dt.date(2026, 1, 15)
    assert [change.slug for change in result.data.top_changes] == [
        "nancy-pelosi",
        "chuck-schumer",
    ]
    assert [change.score_delta for change in result.data.top_changes] == [15.0, -12.0]
    assert [change.top_evidence_card_ids for change in result.data.top_changes] == [
        ["ec-0003", "ec-0002"],
        ["ec-1002", "ec-1001"],
    ]
    assert [event.evidence_card_id for event in result.data.recent_events] == [
        "ec-0003",
        "ec-1002",
        "ec-0002",
        "ec-1001",
    ]
    assert result.data.recent_evidence_card_ids == [
        "ec-0003",
        "ec-1002",
        "ec-0002",
        "ec-1001",
    ]
    assert [summary.slug for summary in result.data.featured_member_changes] == [
        "nancy-pelosi",
        "chuck-schumer",
    ]
    assert [summary.score_total_delta for summary in result.data.featured_member_changes] == [
        15.0,
        -12.0,
    ]
    assert [summary.top_evidence_card_ids for summary in result.data.featured_member_changes] == [
        ["ec-0003", "ec-0002"],
        ["ec-1002", "ec-1001"],
    ]


def test_get_snapshot_compare_returns_not_found_for_unknown_start_snapshot(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_snapshot_compare(
        "2026-01-02",
        "2026-01-15",
        snapshot_root=aggregate_root,
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot"
    assert result.identifier == "2026-01-02"


def test_get_snapshot_compare_returns_not_found_for_invalid_window(tmp_path: Path) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_snapshot_compare(
        "2026-01-15",
        "2026-01-01",
        snapshot_root=aggregate_root,
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot_compare"
    assert result.identifier == "2026-01-15..2026-01-01"


def test_get_snapshot_preset_compare_returns_precomputed_or_fallback_payload(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    precomputed = get_snapshot_preset_compare("latest", snapshot_root=aggregate_root)
    (aggregate_root / snapshot_preset_compare_path("latest")).unlink()
    fallback = get_snapshot_preset_compare("latest", snapshot_root=aggregate_root)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.start_snapshot_id == "2026-01-08"
    assert precomputed.data.end_snapshot_id == "2026-01-15"
    assert [change.slug for change in precomputed.data.top_changes] == [
        "nancy-pelosi",
        "chuck-schumer",
    ]
    assert fallback.data.model_dump(mode="json") == precomputed.data.model_dump(mode="json")


def test_get_snapshot_preset_compare_returns_not_found_for_unknown_preset(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_snapshot_preset_compare("bogus", snapshot_root=aggregate_root)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "snapshot_preset_compare"
    assert result.identifier == "bogus"


def test_get_history_preset_range_returns_precomputed_or_fallback_payload(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    precomputed = get_history_preset_range("latest", snapshot_root=aggregate_root)
    (aggregate_root / history_preset_range_path("latest")).unlink()
    fallback = get_history_preset_range("latest", snapshot_root=aggregate_root)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.preset.preset_key == "latest"
    assert precomputed.data.movement_window.window_key == "latest"
    assert precomputed.data.snapshot_compare.start_snapshot_id == "2026-01-08"
    assert precomputed.data.snapshot_compare.end_snapshot_id == "2026-01-15"
    assert fallback.data.model_dump(mode="json") == precomputed.data.model_dump(mode="json")


def test_get_history_preset_range_returns_not_found_for_unknown_preset(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_history_preset_range("bogus", snapshot_root=aggregate_root)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "history_preset_range"
    assert result.identifier == "bogus"


def test_get_member_window_compare_returns_member_delta_and_evidence_cards(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_member_window_compare(
        "nancy-pelosi",
        "2026-01-01",
        "2026-01-15",
        snapshot_root=aggregate_root,
    )

    assert result.ok is True
    assert result.data.slug == "nancy-pelosi"
    assert result.data.start_snapshot_id == "2026-01-01"
    assert result.data.end_snapshot_id == "2026-01-15"
    assert result.data.summary.score_total_delta == 15.0
    assert [change.dimension for change in result.data.summary.top_dimension_changes] == [
        "conflict_of_interest_risk"
    ]
    assert [card.evidence_card_id for card in result.data.evidence_cards] == [
        "ec-0003",
        "ec-0002",
    ]


def test_get_member_window_compare_returns_not_found_for_invalid_window(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_member_window_compare(
        "nancy-pelosi",
        "2026-01-15",
        "2026-01-01",
        snapshot_root=aggregate_root,
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member_window_compare"
    assert result.identifier == "nancy-pelosi:2026-01-15..2026-01-01"


def test_get_member_preset_compare_returns_precomputed_or_fallback_payload(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    precomputed = get_member_preset_compare(
        "nancy-pelosi",
        "4w",
        snapshot_root=aggregate_root,
    )
    (aggregate_root / member_preset_compare_path("nancy-pelosi", "4w")).unlink()
    fallback = get_member_preset_compare(
        "nancy-pelosi",
        "4w",
        snapshot_root=aggregate_root,
    )

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.slug == "nancy-pelosi"
    assert precomputed.data.start_snapshot_id == "2026-01-01"
    assert precomputed.data.end_snapshot_id == "2026-01-15"
    assert precomputed.data.summary.score_total_delta == 15.0
    assert [card.evidence_card_id for card in precomputed.data.evidence_cards] == [
        "ec-0003",
        "ec-0002",
    ]
    assert fallback.data.model_dump(mode="json") == precomputed.data.model_dump(mode="json")


def test_get_member_preset_compare_returns_not_found_for_unknown_preset(
    tmp_path: Path,
) -> None:
    aggregate_root = _history_root(tmp_path)

    result = get_member_preset_compare(
        "nancy-pelosi",
        "bogus",
        snapshot_root=aggregate_root,
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member_preset_compare"
    assert result.identifier == "nancy-pelosi:bogus"


def test_src_api_exports_snapshot_compare_helper() -> None:
    assert api.get_snapshot_compare is get_snapshot_compare


def test_src_api_exports_member_window_compare_helper() -> None:
    assert api.get_member_window_compare is get_member_window_compare


def test_src_api_exports_member_preset_compare_helper() -> None:
    assert api.get_member_preset_compare is get_member_preset_compare


def test_src_api_exports_snapshot_preset_compare_helper() -> None:
    assert api.get_snapshot_preset_compare is get_snapshot_preset_compare


def test_src_api_exports_history_preset_range_helper() -> None:
    assert api.get_history_preset_range is get_history_preset_range
