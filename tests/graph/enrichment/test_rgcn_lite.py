from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest

from src.graph.contracts import (
    ContractSourceAnchor,
    build_bill_output,
    build_entity_resolution_output,
)
from src.graph.enrichment.rgcn_lite import rgcn_lite_embeddings
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.provenance import ProvenanceEnvelope

_T = datetime(2025, 1, 1, tzinfo=UTC)


def _person(rid: str, *, known: datetime):
    rec = SourceRecord(
        source_system="s",
        source_record_id=rid,
        entity_type="person",
        display_name=rid,
        external_ids=[{"system": "bioguide", "value": rid}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url=f"https://x/{rid}",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )
    return build_entity_resolution_output(
        build_canonical_entity(resolve([rec]).clusters[0], {rec.record_id: rec})  # type: ignore[arg-type]
    )


def _bill(cid: str, *, known: datetime):
    return build_bill_output(
        canonical_bill_id=cid,
        display_name=cid,
        source_anchors=[
            ContractSourceAnchor(
                source_system="congress",
                record_id=cid,
                source_url=f"https://x/{cid}",
                content_sha256="b" * 64,
                content_address="sha256/bb/bb/" + "b" * 64,
                known_at=known,
                valid_from=known.date(),
            )
        ],
    )


def _vote(member_id: str, bill_id: str, *, vote_date: date, known: datetime = _T):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=bill_id,
        choice="yea",
        provenance=vote_provenance(
            source_url=f"https://clerk/{bill_id}",
            content_sha256="c" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_every_node_gets_a_fixed_dim_vector() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    b = _bill("cb-1", known=datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(
        nodes=[p, b], edges=[_vote(p.canonical_id, "cb-1", vote_date=date(2024, 3, 1))]
    )
    emb = rgcn_lite_embeddings(graph, as_of=_T, dim=32, hops=2)
    assert set(emb) == {p.canonical_id, "cb-1"}
    assert all(len(v) == 32 for v in emb.values())


def test_isolated_node_has_nonzero_normalized_embedding() -> None:
    # No edges -> the zero-vector fallback is gone; identity base is non-zero.
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(nodes=[p], edges=[])
    emb = rgcn_lite_embeddings(graph, as_of=_T, dim=16, hops=2)
    vec = emb[p.canonical_id]
    assert any(x != 0.0 for x in vec)
    assert _norm(vec) == pytest.approx(1.0, abs=1e-9)


def test_deterministic() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    b = _bill("cb-1", known=datetime(2023, 1, 1, tzinfo=UTC))
    edge = _vote(p.canonical_id, "cb-1", vote_date=date(2024, 3, 1))
    g1 = KnowledgeGraph(nodes=[p, b], edges=[edge])
    g2 = KnowledgeGraph(nodes=[b, p], edges=[edge])
    assert rgcn_lite_embeddings(g1, as_of=_T, dim=24) == rgcn_lite_embeddings(g2, as_of=_T, dim=24)


def test_message_passing_changes_connected_node_from_base() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    b = _bill("cb-1", known=datetime(2023, 1, 1, tzinfo=UTC))
    edge = _vote(p.canonical_id, "cb-1", vote_date=date(2024, 3, 1))
    base = rgcn_lite_embeddings(KnowledgeGraph(nodes=[p, b], edges=[]), as_of=_T, dim=32, hops=0)
    propagated = rgcn_lite_embeddings(
        KnowledgeGraph(nodes=[p, b], edges=[edge]), as_of=_T, dim=32, hops=2
    )
    assert base[p.canonical_id] != propagated[p.canonical_id]  # neighbourhood shifted it


def test_structurally_sensitive_to_edges() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    b = _bill("cb-1", known=datetime(2023, 1, 1, tzinfo=UTC))
    no_edge = rgcn_lite_embeddings(KnowledgeGraph(nodes=[p, b], edges=[]), as_of=_T, dim=32)
    with_edge = rgcn_lite_embeddings(
        KnowledgeGraph(
            nodes=[p, b], edges=[_vote(p.canonical_id, "cb-1", vote_date=date(2024, 3, 1))]
        ),
        as_of=_T,
        dim=32,
    )
    assert no_edge[p.canonical_id] != with_edge[p.canonical_id]


def test_leakage_safe_to_as_of() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    b = _bill("cb-1", known=datetime(2023, 1, 1, tzinfo=UTC))
    late = _vote(p.canonical_id, "cb-1", vote_date=date(2024, 6, 1))
    # As of April 2024 the June vote is not knowable -> embedding == no-edge case.
    early = rgcn_lite_embeddings(
        KnowledgeGraph(nodes=[p, b], edges=[late]), as_of=datetime(2024, 4, 1, tzinfo=UTC), dim=32
    )
    none = rgcn_lite_embeddings(
        KnowledgeGraph(nodes=[p, b], edges=[]), as_of=datetime(2024, 4, 1, tzinfo=UTC), dim=32
    )
    assert early[p.canonical_id] == none[p.canonical_id]


def test_empty_graph() -> None:
    assert rgcn_lite_embeddings(KnowledgeGraph(nodes=[], edges=[]), as_of=_T) == {}


def test_hops_zero_returns_base_vectors() -> None:
    p = _person("A1", known=datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(nodes=[p], edges=[])
    vec = rgcn_lite_embeddings(graph, as_of=_T, dim=16, hops=0)[p.canonical_id]
    assert _norm(vec) == pytest.approx(1.0, abs=1e-9)
