from __future__ import annotations

from datetime import date
from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_member_page
from src.export.writer import member_page_payload_path
from src.export.contracts import RecentRuleFire
from src.pipeline.history_aggregate_run import write_history_aggregate
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_profile,
    make_snapshot,
)


def _member_profile_with_cards() -> object:
    base = make_member_profile()
    return base.model_copy(
        update={
            "top_evidence_card_ids": ["ec-0002", "ec-0001"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0002",
                    short_explanation="More recent event.",
                    score_delta=-3.0,
                    snapshot_date=base.snapshot_date,
                ),
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0001",
                    short_explanation="Older event.",
                    score_delta=5.0,
                    snapshot_date=base.snapshot_date,
                ),
            ],
            "total_evidence_cards": 2,
        }
    )


def test_get_member_page_returns_profile_and_resolved_evidence(tmp_path: Path) -> None:
    profile = _member_profile_with_cards()
    cards = [
        make_evidence_card(evidence_card_id="ec-0001", score_delta=5.0),
        make_evidence_card(evidence_card_id="ec-0002", score_delta=-3.0),
    ]
    make_snapshot(tmp_path, member_profiles=[profile], evidence_cards=cards)

    result = get_member_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert result.data.profile.slug == "nancy-pelosi"
    assert [card.evidence_card_id for card in result.data.top_evidence_cards] == ["ec-0002", "ec-0001"]
    assert [card.evidence_card_id for card in result.data.recent_evidence_cards] == ["ec-0002", "ec-0001"]


def test_get_member_page_supports_independent_limits(tmp_path: Path) -> None:
    profile = _member_profile_with_cards()
    cards = [
        make_evidence_card(evidence_card_id="ec-0001", score_delta=5.0),
        make_evidence_card(evidence_card_id="ec-0002", score_delta=-3.0),
    ]
    make_snapshot(tmp_path, member_profiles=[profile], evidence_cards=cards)

    result = get_member_page(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        top_cards_limit=1,
        recent_cards_limit=1,
    )

    assert result.ok is True
    assert [card.evidence_card_id for card in result.data.top_evidence_cards] == ["ec-0002"]
    assert [card.evidence_card_id for card in result.data.recent_evidence_cards] == ["ec-0002"]


def test_get_member_page_prefers_precomputed_artifact_and_falls_back_cleanly(tmp_path: Path) -> None:
    profile = _member_profile_with_cards()
    cards = [
        make_evidence_card(evidence_card_id="ec-0001", score_delta=5.0),
        make_evidence_card(evidence_card_id="ec-0002", score_delta=-3.0),
    ]
    make_snapshot(tmp_path, member_profiles=[profile], evidence_cards=cards)

    precomputed = get_member_page("nancy-pelosi", snapshot_root=tmp_path)
    (tmp_path / member_page_payload_path("nancy-pelosi")).unlink()
    fallback = get_member_page("nancy-pelosi", snapshot_root=tmp_path)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_member_page_dedupes_missing_and_matches_artifact_and_fallback(
    tmp_path: Path,
) -> None:
    base = make_member_profile().snapshot_date
    profile = _member_profile_with_cards().model_copy(
        update={
            "top_evidence_card_ids": ["ec-0002", "ec-0002", "ec-missing", "ec-0001"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0002",
                    short_explanation="Duplicate recent event.",
                    score_delta=-3.0,
                    snapshot_date=base,
                ),
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0002",
                    short_explanation="Duplicate recent event again.",
                    score_delta=-3.0,
                    snapshot_date=base,
                ),
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-missing",
                    short_explanation="Missing event.",
                    score_delta=-1.0,
                    snapshot_date=base,
                ),
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0001",
                    short_explanation="Older event.",
                    score_delta=5.0,
                    snapshot_date=base,
                ),
            ],
        }
    )
    cards = [
        make_evidence_card(evidence_card_id="ec-0001", score_delta=5.0),
        make_evidence_card(evidence_card_id="ec-0002", score_delta=-3.0),
    ]
    make_snapshot(tmp_path, member_profiles=[profile], evidence_cards=cards)

    precomputed = get_member_page(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        top_cards_limit=2,
        recent_cards_limit=2,
    )
    (tmp_path / member_page_payload_path("nancy-pelosi")).unlink()
    fallback = get_member_page(
        "nancy-pelosi",
        snapshot_root=tmp_path,
        top_cards_limit=2,
        recent_cards_limit=2,
    )

    assert precomputed.ok is True
    assert fallback.ok is True
    assert [card.evidence_card_id for card in precomputed.data.top_evidence_cards] == [
        "ec-0002",
        "ec-0001",
    ]
    assert [card.evidence_card_id for card in precomputed.data.recent_evidence_cards] == [
        "ec-0002",
        "ec-0001",
    ]
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_get_member_page_skips_missing_evidence_sidecars(tmp_path: Path) -> None:
    profile = _member_profile_with_cards().model_copy(
        update={
            "top_evidence_card_ids": ["ec-0002", "ec-missing", "ec-0001"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-missing",
                    short_explanation="Missing event.",
                    score_delta=-1.0,
                    snapshot_date=make_member_profile().snapshot_date,
                ),
                *list(_member_profile_with_cards().recent_rule_fires),
            ],
        }
    )
    cards = [
        make_evidence_card(evidence_card_id="ec-0001", score_delta=5.0),
        make_evidence_card(evidence_card_id="ec-0002", score_delta=-3.0),
    ]
    make_snapshot(tmp_path, member_profiles=[profile], evidence_cards=cards)

    result = get_member_page("nancy-pelosi", snapshot_root=tmp_path)

    assert result.ok is True
    assert [card.evidence_card_id for card in result.data.top_evidence_cards] == ["ec-0002", "ec-0001"]
    assert [card.evidence_card_id for card in result.data.recent_evidence_cards] == ["ec-0002", "ec-0001"]


def test_get_member_page_returns_not_found_for_missing_member(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_member_page("ghost-member", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member"
    assert result.identifier == "ghost-member"


def test_get_member_page_history_aggregate_root_serves_copied_artifact_without_sidecars(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "2026-01-06"
    second_root = tmp_path / "2026-01-13"
    aggregate_root = tmp_path / "aggregate"
    first_date = date(2026, 1, 6)
    second_date = date(2026, 1, 13)
    profile = _member_profile_with_cards()
    cards = [
        make_evidence_card(
            evidence_card_id="ec-0001",
            score_delta=5.0,
            snapshot_date=profile.snapshot_date,
        ),
        make_evidence_card(
            evidence_card_id="ec-0002",
            score_delta=-3.0,
            snapshot_date=profile.snapshot_date,
        ),
    ]
    make_snapshot(
        first_root,
        snapshot_id="2026-01-06",
        member_profiles=[
            profile.model_copy(update={"name": "Early Nancy", "snapshot_date": first_date})
        ],
        evidence_cards=[
            card.model_copy(update={"snapshot_date": first_date})
            for card in cards
        ],
    )
    make_snapshot(
        second_root,
        snapshot_id="2026-01-13",
        member_profiles=[
            profile.model_copy(update={"name": "Latest Nancy", "snapshot_date": second_date})
        ],
        evidence_cards=[
            card.model_copy(update={"snapshot_date": second_date})
            for card in cards
        ],
    )

    write_history_aggregate([first_root, second_root], aggregate_root)
    (aggregate_root / "members" / "nancy-pelosi.json").unlink()
    for evidence_file in (aggregate_root / "evidence").glob("*.json"):
        evidence_file.unlink()

    result = get_member_page("nancy-pelosi", snapshot_root=aggregate_root)

    assert result.ok is True
    assert result.data.profile.name == "Latest Nancy"
    assert [card.evidence_card_id for card in result.data.top_evidence_cards] == ["ec-0002", "ec-0001"]


def test_src_api_exports_member_page_helper() -> None:
    assert api.get_member_page is get_member_page
