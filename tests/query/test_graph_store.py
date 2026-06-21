"""Tests for the connected-graph store (entities + cited edges)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.query.graph_store import (
    Edge,
    GraphStore,
    Node,
    Provenance,
    build_store,
    jurisdiction_of,
    load_edges_from,
    load_nodes_from,
)


def _prov(url: str = "https://example.test/a") -> Provenance:
    return Provenance(source_url=url, content_sha256="abc", known_at="2026-06-01T00:00:00Z")


def _store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            canonical_id="ce-person1",
            entity_type="person",
            display_name="Jane Doe",
            external_ids=("legistar:oakland:7",),
            known_at="2026-06-01T00:00:00Z",
            citations=(_prov(),),
            jurisdiction="oakland",
        )
    )
    store.add_node(
        Node(
            canonical_id="cb-bill1",
            entity_type="bill",
            display_name="Rent Control Ordinance",
            external_ids=("legistar:oakland:1001",),
            known_at="2026-06-01T00:00:00Z",
            citations=(_prov(),),
            jurisdiction="oakland",
        )
    )
    store.add_edge(
        Edge(
            edge_type="vote",
            src_id="ce-person1",
            dst_id="cb-bill1",
            attributes={"choice": "yea"},
            provenance=_prov("https://example.test/vote"),
        )
    )
    store.add_edge(
        Edge(
            edge_type="policy_area",
            src_id="cb-bill1",
            dst_id="csub-housing",
            attributes={"name": "Housing"},
            provenance=_prov("https://example.test/topic"),
        )
    )
    return store


def test_jurisdiction_inference() -> None:
    assert jurisdiction_of(["legistar:chicago:328"]) == "chicago"
    assert jurisdiction_of(["bioguide:M001199"]) == "us-congress"
    assert jurisdiction_of(["ca_leginfo:201720180ab1566"]) == "ca-legislature"
    assert jurisdiction_of(["openstates:ocd-person/x"]) is None


def test_node_and_external_lookup() -> None:
    store = _store()
    assert store.node("ce-person1") is not None
    assert store.resolve_external("legistar:oakland:7") is not None
    assert store.resolve_external("legistar:oakland:7").canonical_id == "ce-person1"
    assert store.resolve_external("missing") is None


def test_adjacency_directions() -> None:
    store = _store()
    out = store.neighbors("ce-person1", edge_type="vote", direction="out")
    assert len(out) == 1
    edge, node = out[0]
    assert edge.attributes["choice"] == "yea"
    assert node is not None and node.canonical_id == "cb-bill1"
    incoming = store.neighbors("cb-bill1", edge_type="vote", direction="in")
    assert len(incoming) == 1
    assert incoming[0][1].canonical_id == "ce-person1"


def test_jurisdictions_counts() -> None:
    store = _store()
    assert store.jurisdictions() == {"oakland": 1}


def test_build_store_from_files(tmp_path: Path) -> None:
    nodes_path = tmp_path / "nodes.jsonl"
    edges_path = tmp_path / "edges.jsonl"
    nodes_path.write_text(
        json.dumps(
            {
                "canonical_id": "cb-x",
                "entity_type": "bill",
                "display_name": "AB1 Test",
                "external_ids": ["ca_leginfo:1"],
                "known_at": "2026-06-01T00:00:00Z",
                "source_anchors": [
                    {
                        "source_url": "https://example.test/x",
                        "content_sha256": "deadbeef",
                        "known_at": "2026-06-01T00:00:00Z",
                        "valid_from": "2026-01-01",
                        "valid_to": None,
                    }
                ],
                "dossier_embedding": [0.1, 0.2, 0.3],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    edges_path.write_text(
        json.dumps(
            {
                "edge_type": "policy_area",
                "src_id": "cb-x",
                "dst_id": "csub-1",
                "attributes": {"name": "Taxation"},
                "external_key": None,
                "provenance": {
                    "source_url": "https://example.test/e",
                    "content_sha256": "feed",
                    "known_at": "2026-06-01T00:00:00Z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = build_store(
        node_paths=[nodes_path],
        edge_paths=[edges_path],
        load_embeddings=True,
    )
    node = store.node("cb-x")
    assert node is not None
    assert node.jurisdiction == "ca-legislature"
    assert node.dossier_embedding is not None
    assert np.allclose(node.dossier_embedding, [0.1, 0.2, 0.3])
    assert node.citations[0].source_url == "https://example.test/x"
    assert len(store.edges) == 1
    assert store.edges[0].attributes["name"] == "Taxation"


def test_build_store_skips_missing_files(tmp_path: Path) -> None:
    store = build_store(
        node_paths=[tmp_path / "nope.jsonl"],
        edge_paths=[tmp_path / "nope2.jsonl"],
    )
    assert len(store) == 0


def test_loaders_yield_typed_objects(tmp_path: Path) -> None:
    p = tmp_path / "n.jsonl"
    p.write_text(
        json.dumps(
            {
                "canonical_id": "ce-1",
                "entity_type": "person",
                "display_name": "X",
                "external_ids": [],
                "known_at": "2026-06-01T00:00:00Z",
                "source_anchors": [],
            }
        )
        + "\n\n",  # trailing blank line tolerated
        encoding="utf-8",
    )
    nodes = list(load_nodes_from(p))
    assert len(nodes) == 1 and isinstance(nodes[0], Node)
    e = tmp_path / "e.jsonl"
    e.write_text(
        json.dumps(
            {
                "edge_type": "vote",
                "src_id": "ce-1",
                "dst_id": "cb-1",
                "attributes": {"choice": "nay"},
                "provenance": {
                    "source_url": "u",
                    "content_sha256": "h",
                    "known_at": "k",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    edges = list(load_edges_from(e))
    assert len(edges) == 1 and isinstance(edges[0], Edge)
    assert edges[0].attributes["choice"] == "nay"
