"""Tests for the lazy on-disk state vote index.

Pins the scalable wiring: build a byte-offset index over a small edge file, then
resolve a person's / bill's votes by seeking, verifying provenance round-trips and
that no edge for an unrelated entity leaks in.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.query.state_vote_index import StateVoteIndex, build_index


def _edge(person: str, bill: str, choice: str, vote_id: str, url: str) -> dict[str, object]:
    return {
        "edge_type": "vote",
        "src_id": person,
        "dst_id": bill,
        "attributes": {"choice": choice, "motion": "passage", "result": "pass"},
        "external_key": vote_id,
        "provenance": {
            "source_url": url,
            "content_sha256": "abc123",
            "known_at": "2025-01-01T00:00:00Z",
            "valid_from": "2025-01-01",
        },
    }


def _write_edges(path: Path) -> None:
    rows = [
        _edge("ce-alice", "cb-1", "yea", "ocd-vote/v1", "https://leg/v1"),
        _edge("ce-bob", "cb-1", "nay", "ocd-vote/v1", "https://leg/v1"),
        _edge("ce-alice", "cb-2", "nay", "ocd-vote/v2", "https://leg/v2"),
        _edge("ce-carol", "cb-2", "yea", "ocd-vote/v2", "https://leg/v2"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_build_and_query_person(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    index_path = tmp_path / "index.jsonl"
    _write_edges(edges)
    stats = build_index(edges, index_path)
    assert stats == {"edges": 4, "persons": 3, "bills": 2}

    idx = StateVoteIndex.load(index_path, edges)
    alice = idx.votes_for_person("ce-alice")
    assert len(alice) == 2
    assert {v.bill_id for v in alice} == {"cb-1", "cb-2"}
    assert {v.choice for v in alice} == {"yea", "nay"}
    # Provenance round-trips for citation.
    v1 = next(v for v in alice if v.bill_id == "cb-1")
    assert v1.source_url == "https://leg/v1"
    assert v1.content_sha256 == "abc123"
    assert v1.known_at == "2025-01-01T00:00:00Z"
    assert v1.vote_id == "ocd-vote/v1"
    assert idx.vote_count_for_person("ce-alice") == 2


def test_query_bill_and_isolation(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    index_path = tmp_path / "index.jsonl"
    _write_edges(edges)
    build_index(edges, index_path)
    idx = StateVoteIndex.load(index_path, edges)

    bill1 = idx.votes_for_bill("cb-1")
    assert {v.person_id for v in bill1} == {"ce-alice", "ce-bob"}
    # No leakage: carol only voted on cb-2.
    assert "ce-carol" not in {v.person_id for v in bill1}
    assert not idx.has_person("ce-nobody")
    assert idx.votes_for_person("ce-nobody") == []


def test_limit(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    index_path = tmp_path / "index.jsonl"
    _write_edges(edges)
    build_index(edges, index_path)
    idx = StateVoteIndex.load(index_path, edges)
    assert len(idx.votes_for_person("ce-alice", limit=1)) == 1
