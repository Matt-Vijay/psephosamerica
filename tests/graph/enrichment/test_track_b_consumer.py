"""Track B-shaped consumer test: the full output contract, fully populated.

This proves the integration blocker is closed — that every field of the spec
contract (canonical_person_id / canonical_bill_id, dossier_json,
dossier_embedding, structural_embedding, known_at, source_anchors[]) is
populated for a Person *and* a Bill using the deterministic local embedder, and
that a downstream consumer can round-trip the emitted JSON.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.contracts import (
    ContractSourceAnchor,
    EntityResolutionOutput,
    build_bill_output,
    build_entity_resolution_output,
)
from src.graph.enrichment.dossier import Dossier, DossierClaim
from src.graph.enrichment.enrich import dossier_text, enrich_output
from src.graph.enrichment.local_embedder import default_text_embedder
from src.graph.enrichment.structural_features import (
    STRUCTURAL_EDGE_VOCAB,
    structural_feature_vector,
)
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.provenance import ProvenanceEnvelope

_T = datetime(2025, 1, 1, tzinfo=UTC)
_EMBED_DIM = default_text_embedder().dim
_STRUCT_DIM = 2 * len(STRUCTURAL_EDGE_VOCAB) + 4


def _person_output() -> EntityResolutionOutput:
    record = SourceRecord(
        source_system="house_clerk",
        source_record_id="A000055",
        entity_type="person",
        display_name="Robert Aderholt",
        external_ids=[{"system": "bioguide", "value": "A000055"}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url="https://bioguide.congress.gov/search/bio/A000055",
            content_sha256="a" * 64,
            first_observed_at=datetime(2023, 1, 1, tzinfo=UTC),
            valid_from=date(2023, 1, 1),
            known_at=datetime(2023, 1, 1, tzinfo=UTC),
        ),
    )
    entity = build_canonical_entity(resolve([record]).clusters[0], {record.record_id: record})
    assert entity is not None
    return build_entity_resolution_output(entity)


def _bill_anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="congress",
        record_id="118-hr-1",
        source_url="https://www.congress.gov/bill/118th-congress/house-bill/1",
        content_sha256="b" * 64,
        content_address="sha256/bb/bb/" + "b" * 64,
        known_at=datetime(2023, 1, 9, tzinfo=UTC),
        valid_from=date(2023, 1, 9),
    )


def _assert_contract_complete(row: EntityResolutionOutput) -> None:
    """Validate the spec's 7 fields are all populated, and JSON round-trips."""
    assert row.enrichment_status == "ready"
    assert row.dossier_json is not None
    assert isinstance(row.dossier_embedding, list) and len(row.dossier_embedding) == _EMBED_DIM
    assert (
        isinstance(row.structural_embedding, list) and len(row.structural_embedding) == _STRUCT_DIM
    )
    assert row.known_at is not None
    assert len(row.source_anchors) >= 1
    # A non-degenerate (non-zero) embedding -- the field is genuinely populated.
    assert any(value != 0.0 for value in row.dossier_embedding)
    # Round-trip through JSON exactly, as a downstream consumer would.
    restored = EntityResolutionOutput.model_validate_json(row.model_dump_json())
    assert restored == row


def test_person_and_bill_emit_a_fully_populated_contract() -> None:
    person = _person_output()
    bill = build_bill_output(
        canonical_bill_id="cb-118-hr-1",
        display_name="H.R. 1 - Lower Energy Costs Act",
        source_anchors=[_bill_anchor()],
        external_ids=["congress:118-hr-1"],
    )

    # A small real graph: the member voted on the bill (gives both structure).
    edge = vote_edge(
        member_canonical_id=person.canonical_id,
        bill_canonical_id=bill.canonical_id,
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="c" * 64,
            vote_date=date(2024, 3, 1),
            first_observed_at=datetime(2024, 3, 2, tzinfo=UTC),
        ),
    )
    graph = KnowledgeGraph(nodes=[person, bill], edges=[edge])
    embedder = default_text_embedder()

    # --- enrich the PERSON ---
    person_dossier = Dossier(
        canonical_id=person.canonical_id,
        as_of=_T,
        summary="Republican U.S. Representative from Alabama; voted yea on H.R. 1.",
        claims=[
            DossierClaim(
                text="Voted yea on H.R. 1.",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256="c" * 64,
            )
        ],
    )
    person_struct = structural_feature_vector(graph, person.canonical_id, as_of=_T)
    assert person_struct is not None
    enriched_person = enrich_output(
        person, dossier=person_dossier, structural_embedding=person_struct, embedder=embedder
    )

    # --- enrich the BILL ---
    bill_dossier = Dossier(
        canonical_id=bill.canonical_id,
        as_of=_T,
        summary="H.R. 1 (118th): an energy and permitting bill; passed the House.",
        claims=[
            DossierClaim(
                text="Received a yea vote from Rep. Aderholt.",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256="c" * 64,
            )
        ],
    )
    bill_struct = structural_feature_vector(graph, bill.canonical_id, as_of=_T)
    assert bill_struct is not None
    enriched_bill = enrich_output(
        bill, dossier=bill_dossier, structural_embedding=bill_struct, embedder=embedder
    )

    # --- consume both as Track B would ---
    _assert_contract_complete(enriched_person)
    _assert_contract_complete(enriched_bill)
    assert enriched_person.canonical_person_id == person.canonical_id
    assert enriched_person.canonical_bill_id is None
    assert enriched_bill.canonical_bill_id == bill.canonical_id
    assert enriched_bill.canonical_person_id is None

    # The bill's structural embedding reflects its incoming vote (real signal).
    from src.graph.enrichment.structural_features import structural_feature_index

    idx = structural_feature_index()
    assert enriched_bill.structural_embedding[idx["vote_in"]] == 1.0
    assert enriched_person.structural_embedding[idx["vote_out"]] == 1.0


def test_embeddings_are_deterministic_for_the_consumer() -> None:
    embedder = default_text_embedder()
    dossier = Dossier(canonical_id="ce-x", as_of=_T, summary="Stable text.", claims=[])
    # Same dossier text -> identical embedding every emission (stable joins/dedup).
    assert embedder(dossier_text(dossier)) == embedder(dossier_text(dossier))
