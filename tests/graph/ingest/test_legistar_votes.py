from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.legistar_votes import (
    LegistarVote,
    legistar_vote_edge,
    normalize_municipal_vote_choice,
    parse_legistar_vote,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://webapi.legistar.com/v1/oakland/eventitems/236539/votes",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 6, 5, tzinfo=UTC),
    valid_from=date(2024, 6, 4),
    known_at=datetime(2024, 6, 4, tzinfo=UTC),
)

_MEMBER = "ce-councilmember"
_BILL = "cb-municipal-bill"


def test_parse_vote() -> None:
    vote = parse_legistar_vote(
        {"VotePersonId": 1010, "VotePersonName": "Rowena Brown", "VoteValueName": "Aye"}
    )
    assert isinstance(vote, LegistarVote)
    assert vote.person_id == 1010
    assert vote.person_name == "Rowena Brown"
    assert vote.vote_value == "Aye"


def test_parse_rejects_missing_person() -> None:
    with pytest.raises(ValueError, match="VotePersonId"):
        parse_legistar_vote({"VoteValueName": "Aye"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Aye", "yea"),
        ("Yes", "yea"),
        ("Yea", "yea"),
        ("No", "nay"),
        ("Nay", "nay"),
        ("Absent", "not_voting"),
        ("Excused", "not_voting"),
        ("Recused", "abstain"),
        ("Abstain", "abstain"),
        ("Present", "present"),
    ],
)
def test_normalize_choice(raw: str, expected: str) -> None:
    assert normalize_municipal_vote_choice(raw) == expected


@pytest.mark.parametrize("raw", ["Guest", "", "   ", "Chair"])
def test_non_member_vote_values_are_none(raw: str) -> None:
    assert normalize_municipal_vote_choice(raw) is None


def test_vote_edge_built_for_real_choice() -> None:
    edge = legistar_vote_edge(
        member_canonical_id=_MEMBER,
        bill_canonical_id=_BILL,
        vote_value="Aye",
        vote_external_key="236539:1010",
        provenance=_PROV,
    )
    assert edge is not None
    assert edge.edge_type == "vote"
    assert edge.src_id == _MEMBER
    assert edge.dst_id == _BILL
    assert edge.attributes["choice"] == "yea"
    assert edge.external_key == "236539:1010"


def test_vote_edge_none_for_non_member_value() -> None:
    edge = legistar_vote_edge(
        member_canonical_id=_MEMBER,
        bill_canonical_id=_BILL,
        vote_value="Guest",
        vote_external_key="236539:702",
        provenance=_PROV,
    )
    assert edge is None


def test_vote_edge_leakage_gated() -> None:
    edge = legistar_vote_edge(
        member_canonical_id=_MEMBER,
        bill_canonical_id=_BILL,
        vote_value="No",
        vote_external_key="k",
        provenance=_PROV,
    )
    assert edge is not None
    assert edge.known_as_of(datetime(2024, 6, 4, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 6, 3, tzinfo=UTC)) is False
