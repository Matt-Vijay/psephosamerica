from __future__ import annotations

from datetime import date

import pytest

from src.export.contracts import MemberProfilePayload
from src.identity.current_member_lookup import (
    build_current_member_lookup,
    normalize_lookup_name,
    search_current_member_lookup,
    validate_current_member_lookup,
)

_SNAPSHOT_DATE = date(2026, 4, 14)


def _member_profile(
    *,
    bioguide_id: str,
    name: str,
    slug: str,
    state: str,
    chamber: str,
    district: str | None,
) -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        district=district,
        chamber=chamber,
        party="D",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=_SNAPSHOT_DATE,
    )


def test_normalize_lookup_name_strips_punctuation_and_accents() -> None:
    assert normalize_lookup_name("  José O'Rourke-Smith, Jr.  ") == "jose o rourke smith jr"


def test_build_current_member_lookup_emits_compact_alias_contract() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="S000001",
                name="Jose Alvarez",
                slug="jose-alvarez",
                state="nm",
                chamber="senate",
                district=None,
            )
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    assert payload.model_dump(mode="json", by_alias=True) == {
        "v": 1,
        "sd": "2026-04-14",
        "m": [
            {
                "b": "S000001",
                "s": "jose-alvarez",
                "n": "Jose Alvarez",
                "q": "jose alvarez",
                "st": "NM",
                "d": None,
                "c": "senate",
            }
        ],
    }


def test_build_current_member_lookup_is_sorted_deterministically() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="S000002",
                name="Zara Zee",
                slug="zara-zee",
                state="CA",
                chamber="senate",
                district=None,
            ),
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            ),
            _member_profile(
                bioguide_id="H000002",
                name="Bob Baker",
                slug="bob-baker",
                state="CA",
                chamber="house",
                district="2",
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    assert [member.slug for member in payload.members] == [
        "bob-baker",
        "alice-able",
        "zara-zee",
    ]


def test_build_current_member_lookup_rejects_mixed_snapshot_dates() -> None:
    first = _member_profile(
        bioguide_id="H000001",
        name="Alice Able",
        slug="alice-able",
        state="CA",
        chamber="house",
        district="12",
    )
    second = first.model_copy(update={"bioguide_id": "H000002", "snapshot_date": date(2026, 4, 15)})

    with pytest.raises(ValueError, match="same snapshot_date"):
        build_current_member_lookup([first, second], snapshot_date=_SNAPSHOT_DATE)


def test_build_current_member_lookup_rejects_duplicate_bioguide_ids() -> None:
    first = _member_profile(
        bioguide_id="H000001",
        name="Alice Able",
        slug="alice-able",
        state="CA",
        chamber="house",
        district="12",
    )
    second = first.model_copy(update={"slug": "alice-able-2"})

    with pytest.raises(ValueError, match="duplicate bioguide_id"):
        build_current_member_lookup([first, second], snapshot_date=_SNAPSHOT_DATE)


def test_build_current_member_lookup_rejects_duplicate_slugs() -> None:
    first = _member_profile(
        bioguide_id="H000001",
        name="Alice Able",
        slug="alice-able",
        state="CA",
        chamber="house",
        district="12",
    )
    second = first.model_copy(update={"bioguide_id": "H000002"})

    with pytest.raises(ValueError, match="duplicate slug"):
        build_current_member_lookup([first, second], snapshot_date=_SNAPSHOT_DATE)


def test_validate_current_member_lookup_rejects_stale_search_name() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="S000001",
                name="Jose Alvarez",
                slug="jose-alvarez",
                state="NM",
                chamber="senate",
                district=None,
            )
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )
    stale = payload.model_copy(
        update={
            "members": [
                payload.members[0].model_copy(update={"search_name": "stale lookup name"})
            ]
        }
    )

    with pytest.raises(ValueError, match="stale search_name"):
        validate_current_member_lookup(stale)


def test_search_current_member_lookup_exact_name_first() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="S000001",
                name="Charles Schumer",
                slug="charles-schumer",
                state="NY",
                chamber="senate",
                district=None,
            ),
            _member_profile(
                bioguide_id="H000001",
                name="Chuck Smith",
                slug="chuck-smith",
                state="CA",
                chamber="house",
                district="12",
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "Charles Schumer")
    assert [member.slug for member in result.members] == ["charles-schumer"]


def test_search_current_member_lookup_matches_state_and_district() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            ),
            _member_profile(
                bioguide_id="S000001",
                name="Zara Zee",
                slug="zara-zee",
                state="CA",
                chamber="senate",
                district=None,
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "CA 12")
    assert [member.slug for member in result.members] == ["alice-able"]


def test_search_current_member_lookup_matches_mixed_name_geo_and_chamber_query() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            ),
            _member_profile(
                bioguide_id="S000001",
                name="Alice Adams",
                slug="alice-adams",
                state="CA",
                chamber="senate",
                district=None,
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "alice ca 12 house")
    assert [member.slug for member in result.members] == ["alice-able"]


def test_search_current_member_lookup_matches_chamber_and_state_queries() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            ),
            _member_profile(
                bioguide_id="S000001",
                name="Zara Zee",
                slug="zara-zee",
                state="CA",
                chamber="senate",
                district=None,
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "senate ca")
    assert [member.slug for member in result.members] == ["zara-zee"]


def test_search_current_member_lookup_normalizes_state_prefixed_districts() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="CA-12",
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "ca 12")
    assert [member.slug for member in result.members] == ["alice-able"]


def test_search_current_member_lookup_applies_limit() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            ),
            _member_profile(
                bioguide_id="H000002",
                name="Alice Adams",
                slug="alice-adams",
                state="CA",
                chamber="house",
                district="13",
            ),
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "alice", limit=1)
    assert len(result.members) == 1


def test_search_current_member_lookup_empty_query_returns_empty_payload() -> None:
    payload = build_current_member_lookup(
        [
            _member_profile(
                bioguide_id="H000001",
                name="Alice Able",
                slug="alice-able",
                state="CA",
                chamber="house",
                district="12",
            )
        ],
        snapshot_date=_SNAPSHOT_DATE,
    )

    result = search_current_member_lookup(payload, "   ")
    assert result.members == []
