from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from src.graph.ingest.lda import (
    LdaFiling,
    lda_provenance,
    lda_retention_edge,
    parse_lda_client,
    parse_lda_filing,
    parse_lda_registrant,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://lda.senate.gov/api/v1/filings/abc/",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 6, 1, tzinfo=UTC),
    valid_from=date(2024, 1, 2),
    known_at=datetime(2024, 1, 2, tzinfo=UTC),
)

_FILING = {
    "filing_uuid": "7866327b-c892-4430-b9f0-1f0f679c58c6",
    "filing_year": 2024,
    "filing_type_display": "Registration",
    "dt_posted": "2024-01-02T10:13:41-05:00",
    "income": "50000.00",
    "expenses": None,
    "client": {"name": "E-Com 911 Dispatch", "id": 58116, "state": "IL"},
    "registrant": {"name": "Smith Garson", "id": 35707, "state": "DC"},
}


# ── registrant / client orgs ───────────────────────────────────────


def test_parse_registrant() -> None:
    rec = parse_lda_registrant(
        {"id": 35707, "name": "Smith Garson", "state": "DC"}, provenance=_PROV
    )
    assert rec.entity_type == "org"
    assert rec.source_system == "senate_lda"
    assert rec.display_name == "Smith Garson"
    assert rec.external_id_keys == frozenset({"senate_lda_registrant:35707"})
    assert rec.region == "DC"


def test_parse_client() -> None:
    rec = parse_lda_client({"id": 58116, "name": "E-Com 911", "state": "IL"}, provenance=_PROV)
    assert rec.entity_type == "org"
    assert rec.external_id_keys == frozenset({"senate_lda_client:58116"})


def test_missing_id_rejected() -> None:
    with pytest.raises(ValueError, match="id"):
        parse_lda_registrant({"name": "No Id"}, provenance=_PROV)


def test_missing_name_rejected() -> None:
    with pytest.raises(ValueError, match="name"):
        parse_lda_client({"id": 1, "name": "  "}, provenance=_PROV)


# ── filing ─────────────────────────────────────────────────────────


def test_parse_filing() -> None:
    filing = parse_lda_filing(_FILING)
    assert isinstance(filing, LdaFiling)
    assert filing.filing_uuid == "7866327b-c892-4430-b9f0-1f0f679c58c6"
    assert filing.year == 2024
    assert filing.client_id == 58116
    assert filing.registrant_id == 35707
    assert filing.income == "50000.00"
    assert filing.expenses is None
    assert filing.dt_posted == datetime(
        2024, 1, 2, 10, 13, 41, tzinfo=timezone(timedelta(hours=-5))
    )


def test_parse_filing_missing_uuid_rejected() -> None:
    with pytest.raises(ValueError, match="filing_uuid"):
        parse_lda_filing({**_FILING, "filing_uuid": ""})


def test_parse_filing_bad_dt_rejected() -> None:
    with pytest.raises(ValueError, match="dt_posted"):
        parse_lda_filing({**_FILING, "dt_posted": "nope"})


# ── provenance + edge ──────────────────────────────────────────────


def test_lda_provenance_known_at_is_post_date() -> None:
    filing = parse_lda_filing(_FILING)
    prov = lda_provenance(
        filing,
        source_url="https://lda.senate.gov/api/v1/filings/abc/",
        content_sha256="b" * 64,
        first_observed_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    assert prov.valid_from == date(2024, 1, 2)
    assert prov.known_at == datetime(2024, 1, 2, 15, 13, 41, tzinfo=UTC)  # -05:00 -> UTC


def test_retention_edge() -> None:
    filing = parse_lda_filing(_FILING)
    edge = lda_retention_edge(
        client_canonical_id="ce-client",
        registrant_canonical_id="ce-registrant",
        filing=filing,
        provenance=_PROV,
    )
    assert edge.edge_type == "lobbying_retention"
    assert edge.src_id == "ce-client"
    assert edge.dst_id == "ce-registrant"
    assert edge.attributes["year"] == "2024"
    assert edge.attributes["income"] == "50000.00"
    assert "expenses" not in edge.attributes  # null dropped
    assert edge.external_key == "7866327b-c892-4430-b9f0-1f0f679c58c6"


def test_retention_edge_expenses_no_income_no_type() -> None:
    filing = parse_lda_filing(
        {**_FILING, "filing_type_display": "", "income": None, "expenses": "12000"}
    )
    edge = lda_retention_edge(
        client_canonical_id="ce-c",
        registrant_canonical_id="ce-r",
        filing=filing,
        provenance=_PROV,
    )
    assert edge.attributes["expenses"] == "12000"
    assert "income" not in edge.attributes
    assert "filing_type" not in edge.attributes


def test_retention_edge_self_loop_rejected() -> None:
    filing = parse_lda_filing(_FILING)
    with pytest.raises(ValueError, match="self-loop"):
        lda_retention_edge(
            client_canonical_id="ce-x",
            registrant_canonical_id="ce-x",
            filing=filing,
            provenance=_PROV,
        )
