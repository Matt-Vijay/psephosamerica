from __future__ import annotations

from datetime import UTC, date, datetime


from src.graph.bills import BillRef
from src.graph.contracts import build_entity_resolution_output
from src.graph.edges import RECOMMENDED_EDGE_TYPES
from src.graph.enrichment.structural_features import (
    STRUCTURAL_EDGE_VOCAB,
    structural_feature_index,
    structural_feature_vector,
)
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.provenance import ProvenanceEnvelope


def _person(known: datetime):
    record = SourceRecord(
        source_system="congress_bioguide",
        source_record_id="P1",
        entity_type="person",
        display_name="Jane Doe",
        external_ids=[{"system": "bioguide", "value": "P000001"}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url="https://bioguide.congress.gov/search/bio/P000001",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )
    entity = build_canonical_entity(resolve([record]).clusters[0], {record.record_id: record})
    assert entity is not None
    return build_entity_resolution_output(entity)


def _vote(member_id: str, bill_id: str, *, vote_date: date):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=bill_id,
        choice="yea",
        provenance=vote_provenance(
            source_url=f"https://clerk.house.gov/Votes/{bill_id}",
            content_sha256="b" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _graph():
    jane = _person(datetime(2023, 1, 1, tzinfo=UTC))
    b1 = BillRef.for_congress(118, "H.R. 1").canonical_id
    b2 = BillRef.for_congress(118, "H.R. 2").canonical_id
    graph = KnowledgeGraph(
        nodes=[jane],
        edges=[
            _vote(jane.canonical_id, b1, vote_date=date(2024, 3, 1)),
            _vote(jane.canonical_id, b2, vote_date=date(2024, 6, 1)),
        ],
    )
    return graph, jane.canonical_id


# ── vocabulary + layout ────────────────────────────────────────────


def test_vocab_is_sorted_recommended_types() -> None:
    assert STRUCTURAL_EDGE_VOCAB == tuple(sorted(RECOMMENDED_EDGE_TYPES))


def test_vector_has_fixed_length() -> None:
    graph, jane_id = _graph()
    vec = structural_feature_vector(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert vec is not None
    assert len(vec) == 2 * len(STRUCTURAL_EDGE_VOCAB) + 4


def test_feature_index_maps_dimensions() -> None:
    idx = structural_feature_index()
    assert idx["vote_out"] < idx["vote_in"]
    assert "total_out" in idx and "distinct_out_neighbors" in idx


# ── counts ─────────────────────────────────────────────────────────


def test_out_degree_counts_votes() -> None:
    graph, jane_id = _graph()
    vec = structural_feature_vector(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert vec is not None
    idx = structural_feature_index()
    assert vec[idx["vote_out"]] == 2.0
    assert vec[idx["vote_in"]] == 0.0
    assert vec[idx["total_out"]] == 2.0
    assert vec[idx["distinct_out_neighbors"]] == 2.0


def test_in_degree_counts_incoming_edges() -> None:
    from src.graph.ingest.endorsements import endorsement_edge, endorsement_provenance

    jane = _person(datetime(2023, 1, 1, tzinfo=UTC))
    endorsement = endorsement_edge(
        endorser_canonical_id="ce-sierra-club",
        endorsee_canonical_id=jane.canonical_id,
        provenance=endorsement_provenance(
            source_url="https://sierraclub.org/e/1",
            content_sha256="c" * 64,
            announced_date=date(2024, 2, 1),
            first_observed_at=datetime(2024, 2, 2, tzinfo=UTC),
        ),
    )
    graph = KnowledgeGraph(nodes=[jane], edges=[endorsement])
    vec = structural_feature_vector(
        graph, jane.canonical_id, as_of=datetime(2025, 1, 1, tzinfo=UTC)
    )
    assert vec is not None
    idx = structural_feature_index()
    assert vec[idx["endorsement_in"]] == 1.0
    assert vec[idx["total_in"]] == 1.0
    assert vec[idx["distinct_in_neighbors"]] == 1.0


def test_empty_node_has_zero_structure() -> None:
    jane = _person(datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(nodes=[jane], edges=[])
    vec = structural_feature_vector(
        graph, jane.canonical_id, as_of=datetime(2025, 1, 1, tzinfo=UTC)
    )
    assert vec is not None
    assert vec == [0.0] * (2 * len(STRUCTURAL_EDGE_VOCAB) + 4)


# ── leakage safety ─────────────────────────────────────────────────


def test_features_are_leakage_safe() -> None:
    graph, jane_id = _graph()
    # As of April 2024, only the March vote is known.
    vec = structural_feature_vector(graph, jane_id, as_of=datetime(2024, 4, 1, tzinfo=UTC))
    assert vec is not None
    idx = structural_feature_index()
    assert vec[idx["vote_out"]] == 1.0


def test_unknown_node_returns_none() -> None:
    graph, _ = _graph()
    assert (
        structural_feature_vector(graph, "ce-missing", as_of=datetime(2025, 1, 1, tzinfo=UTC))
        is None
    )


def test_node_not_yet_known_returns_none() -> None:
    graph, jane_id = _graph()
    assert structural_feature_vector(graph, jane_id, as_of=datetime(2020, 1, 1, tzinfo=UTC)) is None


# ── determinism ────────────────────────────────────────────────────


def test_deterministic() -> None:
    graph, jane_id = _graph()
    t = datetime(2025, 1, 1, tzinfo=UTC)
    assert structural_feature_vector(graph, jane_id, as_of=t) == structural_feature_vector(
        graph, jane_id, as_of=t
    )
