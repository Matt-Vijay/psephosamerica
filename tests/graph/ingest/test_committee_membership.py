from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.committees import CommitteeRef
from src.graph.ingest.committee_membership import (
    committee_membership_edge,
    committee_membership_provenance,
)

_MEMBER = "ce-person1"
_COMMITTEE = CommitteeRef.for_congress("house", "HSAG").canonical_id


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://www.congress.gov/committee/house-agriculture",
        "content_sha256": "a" * 64,
        "start_date": date(2023, 1, 3),
        "first_observed_at": datetime(2023, 1, 5, tzinfo=UTC),
    }
    base.update(overrides)
    return committee_membership_provenance(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("member", "member"),
        ("Member", "member"),
        ("Chair", "chair"),
        ("chairman", "chair"),
        ("Ranking Member", "ranking_member"),
        ("Vice Chair", "vice_chair"),
    ],
)
def test_role_normalization(role: str, expected: str) -> None:
    edge = committee_membership_edge(
        member_canonical_id=_MEMBER,
        committee_canonical_id=_COMMITTEE,
        role=role,
        provenance=_prov(),
    )
    assert edge.edge_type == "committee_membership"
    assert edge.attributes["role"] == expected
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _COMMITTEE


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError, match="role"):
        committee_membership_edge(
            member_canonical_id=_MEMBER,
            committee_canonical_id=_COMMITTEE,
            role="overlord",
            provenance=_prov(),
        )


def test_known_at_defaults_to_start_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2023, 1, 3)
    assert prov.known_at == datetime(2023, 1, 3, tzinfo=UTC)


def test_term_window_with_end_date() -> None:
    edge = committee_membership_edge(
        member_canonical_id=_MEMBER,
        committee_canonical_id=_COMMITTEE,
        role="member",
        provenance=_prov(end_date=date(2025, 1, 3)),
    )
    assert edge.covers(date(2024, 6, 1)) is True
    assert edge.covers(date(2025, 1, 3)) is False  # term ended


def test_active_membership_is_open_ended() -> None:
    edge = committee_membership_edge(
        member_canonical_id=_MEMBER,
        committee_canonical_id=_COMMITTEE,
        role="chair",
        provenance=_prov(),
    )
    assert edge.covers(date(2030, 1, 1)) is True


def test_leakage_gate() -> None:
    edge = committee_membership_edge(
        member_canonical_id=_MEMBER,
        committee_canonical_id=_COMMITTEE,
        role="member",
        provenance=_prov(),
    )
    assert edge.known_as_of(datetime(2023, 1, 3, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2023, 1, 2, tzinfo=UTC)) is False
