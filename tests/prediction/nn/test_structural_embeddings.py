"""Tests for RGCN-lite structural embeddings over the ontology graph.

OVERALL_GOAL.md wants a graph-derived structural embedding capturing "who is
this person similar to in network position." This computes one by relational
message-passing over the ontology graph. With identical initial features and
shared per-relation weights, the embedding becomes a function of a node's
rooted relational neighborhood, so structurally-isomorphic nodes get equal
embeddings -- the property the tests pin.
"""

from __future__ import annotations

import numpy as np

from src.prediction.nn.structural_embeddings import (
    StructuralEdge,
    compute_structural_embeddings,
    structural_edges_from_ontology,
)


def test_embeddings_have_requested_dimension_and_are_unit_norm() -> None:
    edges = [
        StructuralEdge(relation="endorses", source="org:a", target="person:1"),
        StructuralEdge(relation="endorses", source="org:a", target="person:2"),
    ]
    embeddings = compute_structural_embeddings(edges, dim=16, num_layers=2, seed=0)
    vector = embeddings.get("person:1")
    assert vector is not None
    assert vector.shape == (16,)
    assert np.isclose(np.linalg.norm(vector), 1.0)


def test_structurally_isomorphic_nodes_get_equal_embeddings() -> None:
    # person:1 and person:2 are twins: each is endorsed by the same org and
    # nothing else distinguishes them.
    edges = [
        StructuralEdge(relation="endorses", source="org:a", target="person:1"),
        StructuralEdge(relation="endorses", source="org:a", target="person:2"),
    ]
    embeddings = compute_structural_embeddings(edges, dim=16, num_layers=3, seed=1)
    assert np.allclose(embeddings.get("person:1"), embeddings.get("person:2"))
    assert embeddings.similarity("person:1", "person:2") > 0.999


def test_distinct_structural_roles_differ() -> None:
    edges = [
        StructuralEdge(relation="endorses", source="org:a", target="person:1"),
        StructuralEdge(relation="endorses", source="org:a", target="person:2"),
    ]
    embeddings = compute_structural_embeddings(edges, dim=16, num_layers=3, seed=2)
    # The endorsing org sits in a different network position than the endorsed.
    assert not np.allclose(embeddings.get("org:a"), embeddings.get("person:1"))


def test_embeddings_are_deterministic_for_a_seed() -> None:
    edges = [StructuralEdge(relation="votes_with", source="person:1", target="person:2")]
    first = compute_structural_embeddings(edges, dim=8, num_layers=2, seed=3)
    second = compute_structural_embeddings(edges, dim=8, num_layers=2, seed=3)
    assert np.allclose(first.get("person:1"), second.get("person:1"))


def test_isolated_node_differs_from_connected_node() -> None:
    edges = [
        StructuralEdge(relation="endorses", source="org:a", target="person:1"),
        # person:2 appears only as an isolated declaration with a self-less edge set
        StructuralEdge(relation="endorses", source="org:b", target="org:b"),
    ]
    embeddings = compute_structural_embeddings(edges, dim=8, num_layers=2, seed=4)
    assert embeddings.get("person:1") is not None
    # A node that is only endorsed differs from a node sitting in a self-loop.
    assert not np.allclose(embeddings.get("person:1"), embeddings.get("org:b"))


def test_missing_node_returns_none() -> None:
    edges = [StructuralEdge(relation="endorses", source="org:a", target="person:1")]
    embeddings = compute_structural_embeddings(edges, dim=8, num_layers=1, seed=5)
    assert embeddings.get("person:does-not-exist") is None


def test_structural_edges_from_ontology_maps_typed_nodes() -> None:
    from src.export.contracts import SourceAnchor
    from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef

    edge = OntologyEdgePayload(
        edge_id="e1",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="A000001", label="Rep A"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-1",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2025/fd.pdf",
                label="Disclosure",
            )
        ],
    )
    structural = structural_edges_from_ontology([edge])
    assert structural == [
        StructuralEdge(
            relation="member_sector_transaction_exposure",
            source="member:A000001",
            target="sector:energy",
        )
    ]
