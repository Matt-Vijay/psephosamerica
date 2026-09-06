from __future__ import annotations

import src.api as api
from src.api.contracts import NotFoundBody
from src.api.read_service import get_search_session
from src.export.contracts import RecentRuleFire
from src.export.writer import member_page_payload_path
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_profile,
    make_snapshot,
)


def _nancy_profile() -> object:
    base = make_member_profile()
    return base.model_copy(
        update={
            "top_evidence_card_ids": ["ec-0001"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0001",
                    short_explanation="Nancy event.",
                    score_delta=5.0,
                    snapshot_date=base.snapshot_date,
                )
            ],
            "total_evidence_cards": 1,
        }
    )


def _charles_profile() -> object:
    base = make_member_profile(
        bioguide_id="S000148",
        slug="charles-schumer",
        name="Charles Schumer",
        chamber="senate",
        state="NY",
    )
    return base.model_copy(
        update={
            "top_evidence_card_ids": ["ec-0002"],
            "recent_rule_fires": [
                RecentRuleFire(
                    rule_id="committee_sector_trade",
                    evidence_card_id="ec-0002",
                    short_explanation="Charles event.",
                    score_delta=-3.0,
                    snapshot_date=base.snapshot_date,
                )
            ],
            "total_evidence_cards": 1,
        }
    )


def test_get_search_session_returns_ranked_results_with_preview_for_top_hits(
    tmp_path,
) -> None:
    profiles = [_nancy_profile(), _charles_profile()]
    cards = [
        make_evidence_card(evidence_card_id="ec-0001"),
        make_evidence_card(
            evidence_card_id="ec-0002",
            member_slug="charles-schumer",
            member_bioguide_id="S000148",
            member_name="Charles Schumer",
            score_delta=-3.0,
        ),
    ]
    make_snapshot(tmp_path, member_profiles=profiles, evidence_cards=cards)

    result = get_search_session("a", snapshot_root=tmp_path, limit=5, preview_limit=1)

    assert result.ok is True
    assert result.data.query == "a"
    assert result.data.total_matches == 2
    assert [row.lookup_entry.slug for row in result.data.results] == [
        "nancy-pelosi",
        "charles-schumer",
    ]
    assert result.data.results[0].member_page_preview is not None
    assert result.data.results[0].member_page_preview.profile.slug == "nancy-pelosi"
    assert result.data.results[1].member_page_preview is None


def test_get_search_session_supports_limit_and_preview_limit(tmp_path) -> None:
    profiles = [_nancy_profile(), _charles_profile()]
    cards = [
        make_evidence_card(evidence_card_id="ec-0001"),
        make_evidence_card(
            evidence_card_id="ec-0002",
            member_slug="charles-schumer",
            member_bioguide_id="S000148",
            member_name="Charles Schumer",
            score_delta=-3.0,
        ),
    ]
    make_snapshot(tmp_path, member_profiles=profiles, evidence_cards=cards)

    result = get_search_session("a", snapshot_root=tmp_path, limit=1, preview_limit=1)

    assert result.ok is True
    assert result.data.total_matches == 1
    assert [row.lookup_entry.slug for row in result.data.results] == ["nancy-pelosi"]


def test_get_search_session_returns_not_found_without_lookup(tmp_path) -> None:
    result = get_search_session("nancy", snapshot_root=tmp_path)

    assert isinstance(result, NotFoundBody)
    assert result.resource_type == "search_session"
    assert result.identifier == "current"


def test_get_search_session_preview_prefers_precomputed_member_pages_and_falls_back_cleanly(
    tmp_path,
) -> None:
    profiles = [_nancy_profile(), _charles_profile()]
    cards = [
        make_evidence_card(evidence_card_id="ec-0001"),
        make_evidence_card(
            evidence_card_id="ec-0002",
            member_slug="charles-schumer",
            member_bioguide_id="S000148",
            member_name="Charles Schumer",
            score_delta=-3.0,
        ),
    ]
    make_snapshot(tmp_path, member_profiles=profiles, evidence_cards=cards)

    precomputed = get_search_session("a", snapshot_root=tmp_path, limit=5, preview_limit=2)
    (tmp_path / member_page_payload_path("nancy-pelosi")).unlink()
    (tmp_path / member_page_payload_path("charles-schumer")).unlink()
    fallback = get_search_session("a", snapshot_root=tmp_path, limit=5, preview_limit=2)

    assert precomputed.ok is True
    assert fallback.ok is True
    assert precomputed.data.model_dump(mode="json") == fallback.data.model_dump(mode="json")


def test_src_api_exports_search_session_helper() -> None:
    assert api.get_search_session is get_search_session
