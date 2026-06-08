from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.ingest.votes import (
    VOTE_CHOICES,
    normalize_vote_choice,
    vote_edge,
    vote_provenance,
)

_MEMBER = "ce-person1"
_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://clerk.house.gov/Votes/2024100",
        "content_sha256": "a" * 64,
        "vote_date": date(2024, 3, 1),
        "first_observed_at": datetime(2024, 3, 2, tzinfo=UTC),
    }
    base.update(overrides)
    return vote_provenance(**base)  # type: ignore[arg-type]


# ── choice normalization ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yea", "yea"),
        ("YES", "yea"),
        ("aye", "yea"),
        ("Y", "yea"),
        ("Nay", "nay"),
        ("no", "nay"),
        ("N", "nay"),
        ("Present", "present"),
        ("Not Voting", "not_voting"),
        ("absent", "not_voting"),
        ("NV", "not_voting"),
        ("Paired", "paired"),
        ("Abstain", "abstain"),
    ],
)
def test_normalize_vote_choice(raw: str, expected: str) -> None:
    assert normalize_vote_choice(raw) == expected
    assert expected in VOTE_CHOICES


def test_normalize_unknown_choice_raises() -> None:
    with pytest.raises(ValueError, match="vote choice"):
        normalize_vote_choice("maybe")


# ── vote_provenance ────────────────────────────────────────────────


def test_vote_provenance_defaults_known_at_to_vote_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2024, 3, 1)
    # Public at the time of the vote: known at the start of the vote day (UTC).
    assert prov.known_at == datetime(2024, 3, 1, tzinfo=UTC)


def test_vote_provenance_explicit_known_at() -> None:
    prov = _prov(known_at=datetime(2024, 3, 1, 18, 30, tzinfo=UTC))
    assert prov.known_at == datetime(2024, 3, 1, 18, 30, tzinfo=UTC)


# ── vote_edge ──────────────────────────────────────────────────────


def test_vote_edge_connects_member_to_bill() -> None:
    edge = vote_edge(
        member_canonical_id=_MEMBER,
        bill_canonical_id=_BILL,
        choice="Yea",
        provenance=_prov(),
    )
    assert edge.edge_type == "vote"
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _BILL
    assert edge.attributes == {"choice": "yea"}


def test_vote_edge_normalizes_choice() -> None:
    edge = vote_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, choice="NO", provenance=_prov()
    )
    assert edge.attributes["choice"] == "nay"


def test_vote_edge_is_leakage_gated_to_vote_day() -> None:
    edge = vote_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, choice="yea", provenance=_prov()
    )
    assert edge.known_as_of(datetime(2024, 3, 1, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 2, 28, tzinfo=UTC)) is False


def test_vote_edge_id_is_stable_per_member_bill_day() -> None:
    a = vote_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, choice="yea", provenance=_prov()
    )
    b = vote_edge(
        member_canonical_id=_MEMBER, bill_canonical_id=_BILL, choice="nay", provenance=_prov()
    )
    # Same member/bill/day -> same edge identity regardless of recorded choice.
    assert a.edge_id == b.edge_id


def test_vote_edge_rejects_unknown_choice() -> None:
    with pytest.raises(ValueError, match="vote choice"):
        vote_edge(
            member_canonical_id=_MEMBER, bill_canonical_id=_BILL, choice="huh", provenance=_prov()
        )


def test_vote_edge_rejects_self_loop() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        vote_edge(
            member_canonical_id="ce-x", bill_canonical_id="ce-x", choice="yea", provenance=_prov()
        )
