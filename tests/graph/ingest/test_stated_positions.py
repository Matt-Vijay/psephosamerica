from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.ingest.stated_positions import (
    stated_position_edge,
    stated_position_provenance,
)

_OFFICIAL = "ce-candidate1"
_TOPIC = "issue-climate"


def _prov(**overrides: object):
    base: dict[str, object] = {
        "source_url": "https://votesmart.org/candidate/1",
        "content_sha256": "a" * 64,
        "statement_date": date(2024, 2, 1),
        "first_observed_at": datetime(2024, 2, 3, tzinfo=UTC),
    }
    base.update(overrides)
    return stated_position_provenance(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("support", "support"),
        ("Favor", "support"),
        ("yes", "support"),
        ("oppose", "oppose"),
        ("against", "oppose"),
        ("no", "oppose"),
        ("neutral", "neutral"),
        ("undecided", "neutral"),
    ],
)
def test_stance_normalization(raw: str, expected: str) -> None:
    edge = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance=raw,
        statement_type="questionnaire",
        provenance=_prov(),
    )
    assert edge.edge_type == "stated_position"
    assert edge.attributes["stance"] == expected
    assert edge.attributes["statement_type"] == "questionnaire"
    assert edge.src_id == _OFFICIAL
    assert edge.dst_id == _TOPIC


def test_statement_type_normalized() -> None:
    edge = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance="support",
        statement_type="Campaign Site",
        provenance=_prov(),
    )
    assert edge.attributes["statement_type"] == "campaign_site"


def test_unknown_stance_rejected() -> None:
    with pytest.raises(ValueError, match="stance"):
        stated_position_edge(
            official_canonical_id=_OFFICIAL,
            topic_id=_TOPIC,
            stance="maybe-ish",
            statement_type="questionnaire",
            provenance=_prov(),
        )


def test_blank_statement_type_rejected() -> None:
    with pytest.raises(ValueError, match="statement_type"):
        stated_position_edge(
            official_canonical_id=_OFFICIAL,
            topic_id=_TOPIC,
            stance="support",
            statement_type="  ",
            provenance=_prov(),
        )


def test_known_at_is_statement_day() -> None:
    prov = _prov()
    assert prov.valid_from == date(2024, 2, 1)
    assert prov.known_at == datetime(2024, 2, 1, tzinfo=UTC)


def test_leakage_gate() -> None:
    edge = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance="support",
        statement_type="questionnaire",
        provenance=_prov(),
    )
    assert edge.known_as_of(datetime(2024, 2, 1, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 1, 31, tzinfo=UTC)) is False


def test_distinct_statements_stay_separate() -> None:
    a = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance="support",
        statement_type="questionnaire",
        statement_id="S1",
        provenance=_prov(),
    )
    b = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance="oppose",
        statement_type="op_ed",
        statement_id="S2",
        provenance=_prov(),
    )
    assert a.edge_id != b.edge_id


def test_wayback_captured_position() -> None:
    # A campaign-site position captured via the Wayback Machine: the source URL
    # is the archived snapshot, the statement date the capture date.
    prov = stated_position_provenance(
        source_url="https://web.archive.org/web/20240201/https://campaign.example/issues",
        content_sha256="d" * 64,
        statement_date=date(2024, 2, 1),
        first_observed_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    edge = stated_position_edge(
        official_canonical_id=_OFFICIAL,
        topic_id=_TOPIC,
        stance="support",
        statement_type="campaign_site",
        provenance=prov,
    )
    assert "web.archive.org" in edge.provenance.source_url
