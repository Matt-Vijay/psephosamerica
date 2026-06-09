from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.graph.contracts import (
    ContractSourceAnchor,
    EntityResolutionOutput,
    build_entity_resolution_output,
)
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.provenance import ProvenanceEnvelope


def _rec(
    name: str, rid: str, *, ext: list[dict[str, str]] | None = None, sha: str = "a"
) -> SourceRecord:
    return SourceRecord(
        source_system="congress",
        source_record_id=rid,
        entity_type="person",
        display_name=name,
        external_ids=ext or [],
        jurisdiction="us",
        provenance=ProvenanceEnvelope(
            source_url=f"https://www.congress.gov/member/{rid}",
            content_sha256=sha * 64,
            first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
            valid_from=date(2024, 1, 1),
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
        ),
    )


def _entity(*records: SourceRecord):
    by_id = {r.record_id: r for r in records}
    cluster = resolve(list(records)).clusters[0]
    entity = build_canonical_entity(cluster, by_id)
    assert entity is not None
    return entity


def _anchor(**overrides: object) -> ContractSourceAnchor:
    base: dict[str, object] = {
        "source_system": "congress",
        "record_id": "sr-abc",
        "source_url": "https://www.congress.gov/member/1",
        "content_sha256": "a" * 64,
        "content_address": "sha256/aa/aa/" + "a" * 64,
        "known_at": datetime(2024, 1, 5, tzinfo=UTC),
        "valid_from": date(2024, 1, 1),
        "valid_to": None,
    }
    base.update(overrides)
    return ContractSourceAnchor(**base)  # type: ignore[arg-type]


# ── building from a canonical entity ───────────────────────────────


def test_build_output_from_entity() -> None:
    entity = _entity(
        _rec("Jane M. Doe", "1", ext=[{"system": "bioguide", "value": "D1"}]),
        _rec("Doe, Jane M.", "2", ext=[{"system": "bioguide", "value": "D1"}]),
    )
    out = build_entity_resolution_output(entity)
    assert out.canonical_id == entity.canonical_id
    assert out.entity_type == "person"
    assert out.display_name == "Jane M. Doe"
    assert out.external_ids == ["bioguide:d1"]
    assert out.known_at == datetime(2024, 1, 5, tzinfo=UTC)
    assert len(out.source_anchors) == 2
    assert out.enrichment_status == "pending"
    assert out.dossier_json is None
    assert out.dossier_embedding is None
    assert out.structural_embedding is None


def test_output_anchor_carries_full_provenance() -> None:
    entity = _entity(_rec("Jane Doe", "1", sha="c"))
    out = build_entity_resolution_output(entity)
    anchor = out.source_anchors[0]
    assert anchor.source_system == "congress"
    assert anchor.source_url == "https://www.congress.gov/member/1"
    assert anchor.content_sha256 == "c" * 64
    assert anchor.content_address == "sha256/cc/cc/" + "c" * 64
    assert anchor.known_at == datetime(2024, 1, 5, tzinfo=UTC)


def test_output_round_trips_through_json() -> None:
    entity = _entity(_rec("Jane Doe", "1", ext=[{"system": "fec", "value": "H1"}]))
    out = build_entity_resolution_output(entity)
    restored = EntityResolutionOutput.model_validate_json(out.model_dump_json())
    assert restored == out


def test_canonical_person_id_property() -> None:
    person = build_entity_resolution_output(_entity(_rec("Jane Doe", "1")))
    assert person.canonical_person_id == person.canonical_id


def test_build_output_with_persistent_id_override() -> None:
    entity = _entity(_rec("Jane Doe", "1"))
    out = build_entity_resolution_output(entity, canonical_id="ce-persistent-stable")
    assert out.canonical_id == "ce-persistent-stable"
    assert out.canonical_id != entity.canonical_id


def test_build_bill_output() -> None:
    from src.graph.contracts import build_bill_output

    out = build_bill_output(
        canonical_bill_id="cb-hr1-118",
        display_name="H.R. 1",
        source_anchors=[_anchor(known_at=datetime(2024, 1, 5, tzinfo=UTC))],
        external_ids=["congress:118-hr-1"],
    )
    assert out.entity_type == "bill"
    assert out.canonical_bill_id == "cb-hr1-118"
    assert out.canonical_person_id is None
    assert out.known_at == datetime(2024, 1, 5, tzinfo=UTC)
    assert out.enrichment_status == "pending"


def test_build_bill_output_requires_anchor() -> None:
    from src.graph.contracts import build_bill_output

    with pytest.raises(ValueError, match="source anchor"):
        build_bill_output(canonical_bill_id="cb-x", display_name="X", source_anchors=[])


def test_canonical_person_id_none_for_org() -> None:
    out = EntityResolutionOutput(
        canonical_id="ce-x",
        entity_type="org",
        display_name="Acme PAC",
        external_ids=[],
        known_at=datetime(2024, 1, 5, tzinfo=UTC),
        source_anchors=[_anchor()],
    )
    assert out.canonical_person_id is None


# ── contract invariants ────────────────────────────────────────────


def test_source_anchors_must_be_non_empty() -> None:
    with pytest.raises(ValidationError, match="source_anchors"):
        EntityResolutionOutput(
            canonical_id="ce-x",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=[],
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
            source_anchors=[],
        )


def test_known_at_must_equal_earliest_anchor() -> None:
    with pytest.raises(ValidationError, match="known_at"):
        EntityResolutionOutput(
            canonical_id="ce-x",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=[],
            known_at=datetime(2024, 1, 1, tzinfo=UTC),  # earlier than any anchor
            source_anchors=[_anchor(known_at=datetime(2024, 1, 5, tzinfo=UTC))],
        )


def test_external_ids_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="external_ids"):
        EntityResolutionOutput(
            canonical_id="ce-x",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=["fec:h1", "bioguide:d1"],  # not sorted
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
            source_anchors=[_anchor()],
        )
    with pytest.raises(ValidationError, match="external_ids"):
        EntityResolutionOutput(
            canonical_id="ce-x",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=["bioguide:d1", "bioguide:d1"],  # duplicate
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
            source_anchors=[_anchor()],
        )


def test_ready_enrichment_requires_dossier_and_embeddings() -> None:
    with pytest.raises(ValidationError, match="enrichment"):
        EntityResolutionOutput(
            canonical_id="ce-x",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=[],
            known_at=datetime(2024, 1, 5, tzinfo=UTC),
            source_anchors=[_anchor()],
            enrichment_status="ready",  # but no dossier/embeddings
        )


def test_ready_enrichment_with_payload_is_valid() -> None:
    out = EntityResolutionOutput(
        canonical_id="ce-x",
        entity_type="person",
        display_name="Jane Doe",
        external_ids=[],
        known_at=datetime(2024, 1, 5, tzinfo=UTC),
        source_anchors=[_anchor()],
        enrichment_status="ready",
        dossier_json={"summary": "..."},
        dossier_embedding=[0.1, 0.2],
        structural_embedding=[0.3, 0.4],
    )
    assert out.enrichment_status == "ready"


# ── ContractSourceAnchor invariants ────────────────────────────────


def test_anchor_content_address_must_match_sha() -> None:
    with pytest.raises(ValidationError, match="content_address"):
        _anchor(content_address="sha256/zz/zz/" + "a" * 64)


def test_anchor_rejects_naive_known_at() -> None:
    with pytest.raises(ValidationError):
        _anchor(known_at=datetime(2024, 1, 5))


def test_anchor_rejects_bad_sha() -> None:
    with pytest.raises(ValidationError, match="content_sha256"):
        _anchor(content_sha256="not-a-sha", content_address="sha256/no/pe/not-a-sha")


def test_anchor_valid_to_after_valid_from() -> None:
    with pytest.raises(ValidationError, match="valid_to"):
        _anchor(valid_from=date(2024, 6, 1), valid_to=date(2024, 1, 1))
