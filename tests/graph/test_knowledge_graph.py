from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.bills import BillRef
from src.graph.contracts import EntityResolutionOutput, build_entity_resolution_output
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.provenance import ProvenanceEnvelope


def _person(name: str, rid: str, *, known: datetime) -> EntityResolutionOutput:
    record = SourceRecord(
        source_system="congress_bioguide",
        source_record_id=rid,
        entity_type="person",
        display_name=name,
        external_ids=[{"system": "bioguide", "value": rid}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url=f"https://bioguide.congress.gov/search/bio/{rid}",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )
    entity = build_canonical_entity(resolve([record]).clusters[0], {record.record_id: record})
    assert entity is not None
    return build_entity_resolution_output(entity)


_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _vote(member_id: str, *, choice: str, vote_date: date) -> GraphEdge:
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=_BILL,
        choice=choice,
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="b" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _graph() -> KnowledgeGraph:
    alice = _person("Alice Adams", "A000001", known=datetime(2023, 1, 1, tzinfo=UTC))
    bob = _person("Bob Brown", "B000001", known=datetime(2024, 5, 1, tzinfo=UTC))
    return KnowledgeGraph(
        nodes=[alice, bob],
        edges=[
            _vote(alice.canonical_id, choice="yea", vote_date=date(2024, 3, 1)),
            _vote(bob.canonical_id, choice="nay", vote_date=date(2024, 6, 1)),
        ],
    )


# ── lookups + adjacency ────────────────────────────────────────────


def test_node_lookup() -> None:
    graph = _graph()
    alice_id = graph.nodes[0].canonical_id
    assert graph.node(alice_id) is graph.nodes[0]
    assert graph.node("ce-missing") is None


def test_edges_from_and_to() -> None:
    graph = _graph()
    alice_id = graph.nodes[0].canonical_id
    out = graph.edges_from(alice_id)
    assert len(out) == 1
    assert out[0].dst_id == _BILL
    assert graph.edges_to(_BILL)  # both votes point at the bill
    assert len(graph.edges_to(_BILL)) == 2
    assert graph.edges_from(_BILL) == ()


def test_neighbors() -> None:
    graph = _graph()
    alice_id = graph.nodes[0].canonical_id
    assert graph.neighbors(alice_id) == frozenset({_BILL})
    assert graph.neighbors(_BILL) == frozenset(
        {graph.nodes[0].canonical_id, graph.nodes[1].canonical_id}
    )


def test_edges_of_type() -> None:
    graph = _graph()
    assert len(graph.edges_of_type("vote")) == 2
    assert graph.edges_of_type("donation") == ()


# ── as_of leakage-safe snapshot ────────────────────────────────────


def test_as_of_drops_future_nodes_and_edges() -> None:
    graph = _graph()
    # As of April 2024: Alice known (Jan 2023), Bob not yet (May 2024);
    # Alice's March vote known, Bob's June vote not.
    snapshot = graph.as_of(datetime(2024, 4, 1, tzinfo=UTC))
    assert len(snapshot.nodes) == 1
    assert snapshot.nodes[0].display_name == "Alice Adams"
    assert len(snapshot.edges) == 1
    assert snapshot.edges[0].attributes["choice"] == "yea"


def test_as_of_full_history() -> None:
    snapshot = _graph().as_of(datetime(2025, 1, 1, tzinfo=UTC))
    assert len(snapshot.nodes) == 2
    assert len(snapshot.edges) == 2


def test_as_of_before_everything_is_empty() -> None:
    snapshot = _graph().as_of(datetime(2020, 1, 1, tzinfo=UTC))
    assert snapshot.nodes == ()
    assert snapshot.edges == ()


def test_as_of_returns_new_graph() -> None:
    graph = _graph()
    snapshot = graph.as_of(datetime(2024, 4, 1, tzinfo=UTC))
    assert snapshot is not graph
    assert len(graph.nodes) == 2  # original unchanged


def test_as_of_rejects_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _graph().as_of(datetime(2024, 4, 1))


# ── duplicate node ids rejected ────────────────────────────────────


def test_duplicate_node_ids_rejected() -> None:
    alice = _person("Alice Adams", "A000001", known=datetime(2023, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="duplicate"):
        KnowledgeGraph(nodes=[alice, alice], edges=[])


def test_empty_graph() -> None:
    graph = KnowledgeGraph(nodes=[], edges=[])
    assert graph.nodes == ()
    assert graph.edges == ()
    assert graph.neighbors("ce-x") == frozenset()
