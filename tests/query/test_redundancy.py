"""Tests for the redundancy / waste reasoning applications."""

from __future__ import annotations

import numpy as np

from src.query.graph_store import Edge, GraphStore, Node, Provenance
from src.query.query_embedder import QueryEmbedder
from src.query.redundancy import (
    donor_to_vote_paths,
    near_duplicate_ordinances,
    reauthorization_clusters,
    redundant_bills_by_policy_area,
)


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _embed(text: str) -> np.ndarray:
    return np.asarray(QueryEmbedder().embed(text), dtype=np.float64)


def _bill(cid: str, name: str, juris: str | None = "us-congress", emb: str | None = None) -> Node:
    return Node(
        cid,
        "bill",
        name,
        (f"congress:{cid}",),
        "2026-06-01T00:00:00Z",
        (_prov(f"https://x/{cid}"),),
        juris,
        dossier_embedding=_embed(emb) if emb else None,
    )


def test_redundant_bills_by_policy_area() -> None:
    store = GraphStore()
    for i in range(6):
        store.add_node(_bill(f"cb-{i}", f"Immigration Bill {i}"))
        store.add_edge(
            Edge(
                "policy_area", f"cb-{i}", "csub-imm", {"name": "Immigration"}, _prov("https://x/e")
            )
        )
    # an area below threshold
    store.add_node(_bill("cb-tax", "Tax Bill"))
    store.add_edge(
        Edge("policy_area", "cb-tax", "csub-tax", {"name": "Taxation"}, _prov("https://x/e"))
    )
    findings = redundant_bills_by_policy_area(store, min_bills=5)
    assert len(findings) == 1
    assert findings[0].policy_area == "Immigration"
    assert findings[0].bill_count == 6
    assert findings[0].bills[0]["citation"]["source_url"]


def test_reauthorization_clusters() -> None:
    store = GraphStore()
    store.add_node(_bill("cb-a", "Older Americans Act Reauthorization of 2019"))
    store.add_node(_bill("cb-b", "Older Americans Act Reauthorization of 2023"))
    store.add_node(_bill("cb-c", "Unrelated Single Measure About Bridges"))
    clusters = reauthorization_clusters(store, min_count=2)
    assert len(clusters) == 1
    assert clusters[0].count == 2
    assert "older americans act reauthorization" in clusters[0].normalized_title
    assert all(m["citation"]["source_url"] for m in clusters[0].measures)


def test_near_duplicate_ordinances_cross_jurisdiction() -> None:
    store = GraphStore()
    text = "plastic bag ban single use retail prohibition"
    store.add_node(_bill("cb-oak", "Plastic Bag Ban", "oakland", text))
    store.add_node(_bill("cb-sf", "Single-Use Bag Prohibition", "sanfrancisco", text))
    store.add_node(
        _bill("cb-other", "Annual Budget Appropriation", "oakland", "budget money fiscal")
    )
    pairs = near_duplicate_ordinances(store, threshold=0.9)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.cross_jurisdiction is True
    assert {pair.a["jurisdiction"], pair.b["jurisdiction"]} == {"oakland", "sanfrancisco"}
    assert pair.similarity >= 0.9
    assert pair.a["citation"]["source_url"]


def test_near_duplicate_respects_cross_jurisdiction_flag() -> None:
    store = GraphStore()
    text = "identical text body"
    store.add_node(_bill("cb-1", "A", "oakland", text))
    store.add_node(_bill("cb-2", "B", "oakland", text))
    assert near_duplicate_ordinances(store, threshold=0.9, cross_jurisdiction_only=True) == []
    same = near_duplicate_ordinances(store, threshold=0.9, cross_jurisdiction_only=False)
    assert len(same) == 1
    assert same[0].cross_jurisdiction is False


def test_near_duplicate_empty_without_embeddings() -> None:
    store = GraphStore()
    store.add_node(_bill("cb-1", "A", "oakland", None))
    assert near_duplicate_ordinances(store) == []


def test_donor_to_vote_paths() -> None:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-energy",
            "person",
            "Energy Friend",
            ("bioguide:E000001",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p1"),),
            "us-congress",
            dossier_embedding=_embed("oil gas energy pipeline drilling fossil fuel"),
        )
    )
    store.add_node(
        Node(
            "ce-green",
            "person",
            "Climate Hawk",
            ("bioguide:G000001",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p2"),),
            "us-congress",
            dossier_embedding=_embed("renewable solar wind climate conservation"),
        )
    )
    store.add_node(_bill("cb-drill", "Offshore Drilling Expansion Act"))
    store.add_edge(Edge("vote", "ce-energy", "cb-drill", {"choice": "yea"}, _prov("https://x/v1")))
    paths = donor_to_vote_paths(store, "oil and gas energy company")
    assert paths
    # the energy-aligned official ranks first
    assert paths[0].official["canonical_id"] == "ce-energy"
    assert paths[0].votes
    assert paths[0].votes[0]["citation"]["source_url"] == "https://x/v1"


def test_donor_to_vote_paths_empty_without_dossiers() -> None:
    store = GraphStore()
    store.add_node(Node("ce-x", "person", "X", (), "2026-06-01T00:00:00Z", (), None))
    assert donor_to_vote_paths(store, "anything") == []
