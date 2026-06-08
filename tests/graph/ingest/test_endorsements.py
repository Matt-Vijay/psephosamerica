from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.endorsements import endorsement_edge, endorsement_provenance

_ENDORSER = "ce-sierra-club"
_ENDORSEE = "ce-candidate1"


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://www.sierraclub.org/endorsements/2024",
        "content_sha256": "a" * 64,
        "announced_date": date(2024, 5, 1),
        "first_observed_at": datetime(2024, 5, 3, tzinfo=UTC),
    }
    base.update(overrides)
    return endorsement_provenance(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("stance", "expected"),
    [
        ("endorse", "endorse"),
        ("Endorse", "endorse"),
        ("support", "endorse"),
        ("oppose", "oppose"),
        ("Anti", "oppose"),
        ("against", "oppose"),
    ],
)
def test_stance_normalization(stance: str, expected: str) -> None:
    edge = endorsement_edge(
        endorser_canonical_id=_ENDORSER,
        endorsee_canonical_id=_ENDORSEE,
        stance=stance,
        provenance=_prov(),
    )
    assert edge.edge_type == "endorsement"
    assert edge.src_id == _ENDORSER
    assert edge.dst_id == _ENDORSEE
    assert edge.attributes["stance"] == expected


def test_default_stance_is_endorse() -> None:
    edge = endorsement_edge(
        endorser_canonical_id=_ENDORSER, endorsee_canonical_id=_ENDORSEE, provenance=_prov()
    )
    assert edge.attributes["stance"] == "endorse"


def test_unknown_stance_rejected() -> None:
    with pytest.raises(ValueError, match="stance"):
        endorsement_edge(
            endorser_canonical_id=_ENDORSER,
            endorsee_canonical_id=_ENDORSEE,
            stance="lukewarm",
            provenance=_prov(),
        )


def test_self_endorsement_rejected() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        endorsement_edge(
            endorser_canonical_id="ce-x", endorsee_canonical_id="ce-x", provenance=_prov()
        )


def test_known_at_defaults_to_announcement_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2024, 5, 1)
    assert prov.known_at == datetime(2024, 5, 1, tzinfo=UTC)


def test_leakage_gate() -> None:
    edge = endorsement_edge(
        endorser_canonical_id=_ENDORSER, endorsee_canonical_id=_ENDORSEE, provenance=_prov()
    )
    assert edge.known_as_of(datetime(2024, 5, 1, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 4, 30, tzinfo=UTC)) is False


def test_rescinded_endorsement_has_closed_window() -> None:
    edge = endorsement_edge(
        endorser_canonical_id=_ENDORSER,
        endorsee_canonical_id=_ENDORSEE,
        provenance=_prov(rescinded_date=date(2024, 9, 1)),
    )
    assert edge.covers(date(2024, 6, 1)) is True
    assert edge.covers(date(2024, 9, 1)) is False  # rescinded
