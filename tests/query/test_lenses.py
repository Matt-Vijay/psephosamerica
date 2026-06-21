"""Tests for the analytical lenses (V8 deliverable #3)."""

from __future__ import annotations

import json
from pathlib import Path

from src.query import lenses
from src.query.graph_store import Edge, GraphStore, Node, Provenance


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _person(cid: str, name: str, juris: str | None, *, cited: bool = True) -> Node:
    return Node(
        cid,
        "person",
        name,
        (),
        "2026-06-01T00:00:00Z",
        (_prov(f"https://src/{cid}"),) if cited else (),
        juris,
    )


def _store_with_votes() -> GraphStore:
    store = GraphStore()
    store.add_node(_person("ce-a", "Alice Adams", "us-congress"))
    store.add_node(_person("ce-b", "Bob Brown", "us-congress"))
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Budget Act",
            (),
            "2026-06-01T00:00:00Z",
            (_prov("https://b/1"),),
            "us-congress",
        )
    )
    # Alice: 3 cited votes; Bob: 1 vote with no source url.
    for i in range(3):
        store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov(f"https://v/a{i}")))
    store.add_edge(
        Edge("vote", "ce-b", "cb-1", {"choice": "nay"}, Provenance("", "", "2026-06-01T00:00:00Z"))
    )
    return store


# -- accountability -----------------------------------------------------


def test_official_accountability_ranks_and_explains() -> None:
    store = _store_with_votes()
    rows = lenses.official_accountability(store, limit=10)
    assert [r.display_name for r in rows][0] == "Alice Adams"  # more cited votes
    alice = rows[0]
    assert alice.observations["votes"] == 3
    assert alice.observations["cited_votes"] == 3
    assert alice.components["source_coverage"] == 1.0
    assert alice.citation["source_url"] == "https://src/ce-a"


def test_official_accountability_source_coverage_penalises_missing_url() -> None:
    store = _store_with_votes()
    rows = {r.entity_id: r for r in lenses.official_accountability(store, limit=10)}
    assert rows["ce-b"].components["source_coverage"] == 0.0


def test_jurisdiction_accountability_aggregates() -> None:
    store = _store_with_votes()
    rows = lenses.jurisdiction_accountability(store, limit=10)
    assert rows[0].jurisdiction == "us-congress"
    assert rows[0].officials == 2


# -- said vs voted ------------------------------------------------------


def test_said_vs_voted_returns_votes_with_honest_note_when_no_speeches() -> None:
    store = _store_with_votes()
    result = lenses.said_vs_voted(store, "ce-a")
    assert result is not None
    assert result.speeches == ()
    assert "lights up" in result.note
    assert len(result.votes) == 3
    assert all(v["citation"]["source_url"] for v in result.votes)


def test_said_vs_voted_lights_up_with_speech_edges() -> None:
    store = _store_with_votes()
    store.add_edge(
        Edge(
            "floor_speech",
            "ce-a",
            "cr:2013-01-04",
            {"chamber": "H", "role": "speaking"},
            _prov("https://crec/1"),
        )
    )
    result = lenses.said_vs_voted(store, "ce-a")
    assert result is not None
    assert len(result.speeches) == 1
    assert result.note == ""
    assert result.speeches[0]["citation"]["source_url"] == "https://crec/1"


def test_said_vs_voted_unknown_official_is_none() -> None:
    assert lenses.said_vs_voted(_store_with_votes(), "ce-missing") is None


# -- copied bills (MinHash over a synthetic content sidecar) ------------


def test_copied_bill_clusters_finds_near_identical_text(tmp_path: Path) -> None:
    shared = " ".join(
        f"section {i} the provision shall apply to all entities herein" for i in range(20)
    )
    rows = [
        {
            "canonical_id": "cb-1",
            "title": "Model Act A",
            "congress": 118,
            "text": shared,
            "provenance": {"source_url": "https://a", "content_sha256": "x"},
        },
        {
            "canonical_id": "cb-2",
            "title": "Model Act B",
            "congress": 118,
            "text": shared,
            "provenance": {"source_url": "https://b", "content_sha256": "y"},
        },
        {
            "canonical_id": "cb-3",
            "title": "Unrelated",
            "congress": 118,
            "text": "completely different words about unrelated matters " * 10,
        },
    ]
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    pairs = lenses.copied_bill_clusters((sidecar,), threshold=0.8, max_pairs=10)
    assert len(pairs) == 1
    ids = {pairs[0].a["bill_id"], pairs[0].b["bill_id"]}
    assert ids == {"cb-1", "cb-2"}
    assert pairs[0].jaccard >= 0.8
    assert pairs[0].a["citation"]["source_url"] in ("https://a", "https://b")


def test_copied_bill_clusters_enriches_citation_from_store(tmp_path: Path) -> None:
    shared = " ".join(
        f"clause {i} the rule binds every party named below always" for i in range(20)
    )
    rows = [
        {"canonical_id": "cb-1", "title": "A", "congress": 118, "text": shared},
        {"canonical_id": "cb-2", "title": "B", "congress": 118, "text": shared},
    ]
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    store = GraphStore()
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "A",
            (),
            "2026-06-01T00:00:00Z",
            (_prov("https://canon/1"),),
            "us-congress",
        )
    )
    pairs = lenses.copied_bill_clusters((sidecar,), store=store, threshold=0.8, max_pairs=10)
    assert len(pairs) == 1
    urls = {pairs[0].a["citation"]["source_url"], pairs[0].b["citation"]["source_url"]}
    assert "https://canon/1" in urls  # enriched from the canonical node


def test_copied_bill_clusters_missing_sidecar_is_empty() -> None:
    assert lenses.copied_bill_clusters((Path("/no/such/file.jsonl"),)) == []
