from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.entity_resolution.linker import resolve
from src.graph.ingest.officials import (
    municipal_official_record,
    official_record,
    openstates_official_record,
)
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://example.gov/official/1",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)


# ── generic adapter ────────────────────────────────────────────────


def test_official_record_generic() -> None:
    rec = official_record(
        source_system="custom",
        source_record_id="X1",
        name="Jane Doe",
        jurisdiction_code="us-tx-county-travis",
        external_ids=[("county_clerk", "X1")],
        region="TX",
        provenance=_PROV,
    )
    assert rec.entity_type == "person"
    assert rec.jurisdiction == "us-tx-county-travis"
    assert rec.region == "TX"
    assert rec.external_id_keys == frozenset({"county_clerk:x1"})


def test_official_record_no_external_ids() -> None:
    rec = official_record(
        source_system="custom",
        source_record_id="X1",
        name="Jane Doe",
        jurisdiction_code="us-tx",
        external_ids=[],
        provenance=_PROV,
    )
    assert rec.external_ids == ()


# ── OpenStates state legislators ───────────────────────────────────


def test_openstates_official_record() -> None:
    rec = openstates_official_record(
        openstates_id="ocd-person/abc",
        name="State Sen. John Smith",
        state_usps="ca",
        provenance=_PROV,
    )
    assert rec.source_system == "openstates"
    assert rec.jurisdiction == Jurisdiction.state("ca").code == "us-ca"
    assert rec.region == "CA"
    assert rec.external_id_keys == frozenset({"openstates:ocd-person/abc"})


def test_two_openstates_observations_resolve_to_one() -> None:
    a = openstates_official_record(
        openstates_id="ocd-person/abc", name="John Smith", state_usps="ca", provenance=_PROV
    )
    b = openstates_official_record(
        openstates_id="ocd-person/abc", name="John Q. Smith", state_usps="ca", provenance=_PROV
    )
    # Same OpenStates id -> one canonical person.
    result = resolve([a, b])
    assert len(result.clusters) == 1


def test_openstates_invalid_state_rejected() -> None:
    with pytest.raises(ValueError, match="USPS"):
        openstates_official_record(
            openstates_id="x", name="J", state_usps="california", provenance=_PROV
        )


# ── municipal officials ────────────────────────────────────────────


def test_municipal_city_official() -> None:
    rec = municipal_official_record(
        source_system="legistar",
        source_record_id="c-1",
        name="Council Member Lee",
        state_usps="ca",
        place="Los Angeles",
        provenance=_PROV,
    )
    assert rec.jurisdiction == "us-ca-city-los_angeles"
    assert rec.region == "CA"
    assert rec.source_system == "legistar"


def test_municipal_county_official() -> None:
    rec = municipal_official_record(
        source_system="boarddocs",
        source_record_id="co-1",
        name="Supervisor Ruiz",
        state_usps="il",
        place="Cook County",
        level="county",
        provenance=_PROV,
    )
    assert rec.jurisdiction == "us-il-county-cook_county"


def test_municipal_bad_level_rejected() -> None:
    with pytest.raises(ValueError, match="level"):
        municipal_official_record(
            source_system="x",
            source_record_id="1",
            name="J",
            state_usps="ca",
            place="Somewhere",
            level="state",  # not a municipal level
            provenance=_PROV,
        )


# ── homogeneity: federal/state/local share one model ───────────────


def test_officials_span_jurisdiction_levels_homogeneously() -> None:
    state = openstates_official_record(
        openstates_id="s1", name="A B", state_usps="ny", provenance=_PROV
    )
    city = municipal_official_record(
        source_system="granicus",
        source_record_id="c1",
        name="C D",
        state_usps="ny",
        place="Buffalo",
        provenance=_PROV,
    )
    # Same entity_type, same record shape, different jurisdiction codes.
    assert state.entity_type == city.entity_type == "person"
    assert state.jurisdiction == "us-ny"
    assert city.jurisdiction == "us-ny-city-buffalo"
