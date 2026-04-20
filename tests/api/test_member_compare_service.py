from __future__ import annotations

from pathlib import Path

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_member_compare
from src.export.writer import member_page_payload_path
from src.export.contracts import CommitteeMembership, RecentRuleFire, ScoreSummary
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_profile,
    make_snapshot,
)


def _build_compare_profiles() -> tuple[object, object]:
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
                ),
                ScoreSummary(
                    dimension="ethics_enforcement_risk",
                    current_score=20.0,
                    rule_fire_count=1,
                ),
            ],
            "committees": [
                CommitteeMembership(committee_name="Appropriations", role="Member"),
                CommitteeMembership(
                    committee_name="Energy and Commerce",
                    role="Member",
                ),
            ],
            "top_evidence_card_ids": ["ec-left-1"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-left-1",
                    short_explanation="Left recent event.",
                    score_delta=-5.0,
                    snapshot_date=make_member_profile().snapshot_date,
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
                ),
                ScoreSummary(
                    dimension="financial_entanglement_risk",
                    current_score=10.0,
                    rule_fire_count=4,
                ),
            ],
            "committees": [
                CommitteeMembership(committee_name="Appropriations", role="Member"),
                CommitteeMembership(committee_name="Finance", role="Member"),
            ],
            "top_evidence_card_ids": ["ec-right-1"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-right-1",
                    short_explanation="Right recent event.",
                    score_delta=-2.0,
                    snapshot_date=make_member_profile().snapshot_date,
                )
            ],
            "total_evidence_cards": 1,
        }
    )
    return left, right


def test_get_member_compare_returns_side_by_side_payload(tmp_path: Path) -> None:
    left, right = _build_compare_profiles()
    cards = [
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
    ]
    make_snapshot(tmp_path, member_profiles=[left, right], evidence_cards=cards)

    result = get_member_compare(
        "nancy-pelosi",
        "charles-schumer",
        snapshot_root=tmp_path,
    )

    assert result.ok is True
    assert result.data.left.profile.slug == "nancy-pelosi"
    assert result.data.right.profile.slug == "charles-schumer"
    assert [row.dimension for row in result.data.score_comparisons] == [
        "conflict_of_interest_risk",
        "ethics_enforcement_risk",
        "financial_entanglement_risk",
    ]
    assert result.data.score_comparisons[0].score_gap == -15.0
    assert result.data.shared_committees == ["Appropriations"]
    assert result.data.left_only_committees == ["Energy and Commerce"]
    assert result.data.right_only_committees == ["Finance"]
    assert result.data.same_chamber is False
    assert result.data.same_state is False


def test_get_member_compare_returns_not_found_for_missing_member(tmp_path: Path) -> None:
    make_snapshot(tmp_path)

    result = get_member_compare(
        "nancy-pelosi",
        "ghost-member",
        snapshot_root=tmp_path,
    )

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "member"
    assert result.identifier == "ghost-member"


def test_get_member_compare_prefers_precomputed_member_pages_and_falls_back_cleanly(
    tmp_path: Path,
) -> None:
    left, right = _build_compare_profiles()
    cards = [
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
    ]
    make_snapshot(tmp_path, member_profiles=[left, right], evidence_cards=cards)

    precomputed = get_member_compare("nancy-pelosi", "charles-schumer", snapshot_root=tmp_path)
    (tmp_path / member_page_payload_path("nancy-pelosi")).unlink()
    (tmp_path / member_page_payload_path("charles-schumer")).unlink()
    fallback = get_member_compare("nancy-pelosi", "charles-schumer", snapshot_root=tmp_path)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_src_api_exports_member_compare_helper() -> None:
    assert api.get_member_compare is get_member_compare
