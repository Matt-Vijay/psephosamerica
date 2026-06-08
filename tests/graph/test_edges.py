from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.graph.edges import (
    RECOMMENDED_EDGE_TYPES,
    GraphEdge,
    edges_known_as_of,
    is_recommended_edge_type,
)
from src.graph.provenance import ProvenanceEnvelope


def _prov(**overrides: object) -> ProvenanceEnvelope:
    base: dict[str, object] = {
        "source_url": "https://clerk.house.gov/Votes/2024100",
        "content_sha256": "a" * 64,
        "first_observed_at": datetime(2024, 3, 2, tzinfo=UTC),
        "valid_from": date(2024, 3, 1),
        "known_at": datetime(2024, 3, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return ProvenanceEnvelope(**base)  # type: ignore[arg-type]


def _edge(**overrides: object) -> GraphEdge:
    base: dict[str, object] = {
        "edge_type": "vote",
        "src_id": "ce-person1",
        "dst_id": "ce-bill1",
        "attributes": {"choice": "yea"},
        "provenance": _prov(),
    }
    base.update(overrides)
    return GraphEdge(**base)  # type: ignore[arg-type]


# ── construction + normalization ───────────────────────────────────


def test_edge_type_is_normalized() -> None:
    assert _edge(edge_type="  Committee Membership ").edge_type == "committee_membership"
    assert _edge(edge_type="co-sponsorship").edge_type == "co_sponsorship"


def test_blank_edge_type_rejected() -> None:
    with pytest.raises(ValidationError, match="edge_type"):
        _edge(edge_type="   ")


@pytest.mark.parametrize("field", ["src_id", "dst_id"])
def test_blank_endpoint_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _edge(**{field: "  "})


def test_self_loop_rejected() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        _edge(src_id="ce-x", dst_id="ce-x")


def test_non_string_identity_fields_rejected() -> None:
    # Non-strings fall through the normalizers to pydantic's type validation.
    with pytest.raises(ValidationError):
        _edge(edge_type=123)
    with pytest.raises(ValidationError):
        _edge(src_id=123)


def test_edge_is_frozen() -> None:
    edge = _edge()
    with pytest.raises(ValidationError):
        edge.edge_type = "donation"  # type: ignore[misc]


def test_attributes_carried() -> None:
    assert _edge(attributes={"choice": "nay"}).attributes == {"choice": "nay"}


# ── edge_id determinism ────────────────────────────────────────────


def test_edge_id_is_deterministic_and_prefixed() -> None:
    assert _edge().edge_id.startswith("ge-")
    assert _edge().edge_id == _edge().edge_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"edge_type": "endorsement"},
        {"src_id": "ce-other"},
        {"dst_id": "ce-other"},
        {"provenance": _prov(valid_from=date(2024, 1, 1))},
    ],
)
def test_edge_id_changes_with_identity_fields(overrides: dict[str, object]) -> None:
    assert _edge().edge_id != _edge(**overrides).edge_id


def test_edge_id_ignores_attributes() -> None:
    # Identity is (type, src, dst, valid_from, external_key); payload is excluded.
    assert (
        _edge(attributes={"choice": "yea"}).edge_id == _edge(attributes={"choice": "nay"}).edge_id
    )


def test_external_key_distinguishes_same_day_relations() -> None:
    a = _edge(edge_type="donation", external_key="txn-1")
    b = _edge(edge_type="donation", external_key="txn-2")
    assert a.edge_id != b.edge_id
    assert a.external_key == "txn-1"


def test_blank_external_key_is_none() -> None:
    assert _edge(external_key="   ").external_key is None
    # None and absent behave identically for identity.
    assert _edge(external_key="   ").edge_id == _edge().edge_id


def test_non_string_external_key_rejected() -> None:
    with pytest.raises(ValidationError):
        _edge(external_key=123)


# ── bitemporal + leakage passthrough ───────────────────────────────


def test_known_at_and_known_as_of() -> None:
    edge = _edge(provenance=_prov(known_at=datetime(2024, 3, 1, tzinfo=UTC)))
    assert edge.known_at == datetime(2024, 3, 1, tzinfo=UTC)
    assert edge.known_as_of(datetime(2024, 4, 1, tzinfo=UTC)) is True
    assert edge.known_as_of(datetime(2024, 1, 1, tzinfo=UTC)) is False


def test_covers_delegates_to_provenance() -> None:
    edge = _edge(provenance=_prov(valid_from=date(2024, 3, 1), valid_to=date(2024, 6, 1)))
    assert edge.covers(date(2024, 4, 1)) is True
    assert edge.covers(date(2024, 7, 1)) is False


# ── recommended edge-type taxonomy ─────────────────────────────────


def test_recommended_edge_types_include_core_relations() -> None:
    assert {"vote", "sponsorship", "donation", "endorsement"} <= RECOMMENDED_EDGE_TYPES


def test_is_recommended_edge_type_normalizes() -> None:
    assert is_recommended_edge_type("Vote") is True
    assert is_recommended_edge_type("committee membership") is True
    assert is_recommended_edge_type("frobnicate") is False


def test_unrecommended_edge_type_still_constructs() -> None:
    # The taxonomy is open: novel edge types are allowed, just not "recommended".
    edge = _edge(edge_type="frobnicate")
    assert edge.edge_type == "frobnicate"
    assert is_recommended_edge_type(edge.edge_type) is False


# ── edges_known_as_of filter (leakage-safe view) ───────────────────


def test_edges_known_as_of_filters_future_facts() -> None:
    early = _edge(
        src_id="ce-a",
        provenance=_prov(
            first_observed_at=datetime(2024, 1, 2, tzinfo=UTC),
            known_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
    )
    late = _edge(
        src_id="ce-b",
        provenance=_prov(
            first_observed_at=datetime(2024, 6, 2, tzinfo=UTC),
            known_at=datetime(2024, 6, 1, tzinfo=UTC),
        ),
    )
    visible = edges_known_as_of([early, late], datetime(2024, 3, 1, tzinfo=UTC))
    assert visible == [early]


def test_edges_known_as_of_rejects_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        edges_known_as_of([_edge()], datetime(2024, 3, 1))


def test_edges_known_as_of_empty() -> None:
    assert edges_known_as_of([], datetime(2024, 3, 1, tzinfo=UTC)) == []
