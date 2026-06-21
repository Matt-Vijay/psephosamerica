"""GraphRAG reasoning: retrieval, grounding, key-gated answerer + stub."""

from __future__ import annotations

import numpy as np

from src.query.graph_rag import (
    ANTHROPIC_MODEL,
    GraphRagAnswerer,
    build_grounding_context,
    retrieve_subgraph,
    stub_generate,
)
from src.query.graph_store import Edge, GraphStore, Node, Provenance
from src.query.query_embedder import QueryEmbedder


def _prov(url: str) -> Provenance:
    return Provenance(
        source_url=url, content_sha256="h" + url[-3:], known_at="2026-06-01T00:00:00Z"
    )


def _embed(text: str) -> np.ndarray:
    return np.asarray(QueryEmbedder().embed(text), dtype=np.float64)


def _store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-housing",
            "person",
            "Housing Advocate",
            ("legistar:oakland:1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p1"),),
            "oakland",
            dossier_embedding=_embed("affordable housing rent control tenant protection"),
        )
    )
    store.add_node(
        Node(
            "ce-tax",
            "person",
            "Tax Hawk",
            ("legistar:oakland:2",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p2"),),
            "oakland",
            dossier_embedding=_embed("taxation budget appropriations fiscal"),
        )
    )
    store.add_node(
        Node(
            "cb-rent",
            "bill",
            "Rent Control Ordinance",
            ("legistar:oakland:9",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/b1"),),
            "oakland",
            dossier_embedding=_embed("affordable housing rent control ordinance"),
        )
    )
    store.add_edge(Edge("vote", "ce-housing", "cb-rent", {"choice": "yea"}, _prov("https://x/v1")))
    store.add_edge(
        Edge("policy_area", "cb-rent", "csub-h", {"name": "Housing"}, _prov("https://x/t1"))
    )
    return store


def test_retrieval_ranks_relevant_entity_first() -> None:
    store = _store()
    retrieved = retrieve_subgraph(store, "affordable housing rent control", top_k=3)
    assert retrieved
    ids = [r.node.canonical_id for r in retrieved]
    # housing-related nodes must rank above the tax hawk
    assert ids.index("ce-housing") < ids.index("ce-tax")
    assert retrieved[0].score > 0


def test_retrieval_expands_cited_facts() -> None:
    store = _store()
    retrieved = retrieve_subgraph(store, "rent control", top_k=3)
    person = next(r for r in retrieved if r.node.canonical_id == "ce-housing")
    assert any("voted yea on Rent Control Ordinance" in f for f in person.facts)
    assert person.fact_citations
    assert person.fact_citations[0]["source_url"] == "https://x/v1"


def test_retrieval_empty_when_no_embeddings() -> None:
    store = GraphStore()
    store.add_node(Node("ce-x", "person", "No Vec", (), "2026-06-01T00:00:00Z", (), None))
    assert retrieve_subgraph(store, "anything") == []


def test_grounding_context_carries_citations() -> None:
    store = _store()
    retrieved = retrieve_subgraph(store, "rent control", top_k=2)
    context = build_grounding_context(retrieved)
    assert "source=" in context
    assert "known_at=" in context
    assert "Rent Control Ordinance" in context


def test_grounding_context_handles_no_matches() -> None:
    assert build_grounding_context([]).startswith("(no matching")


def test_stub_answerer_grounds_and_cites() -> None:
    store = _store()
    answerer = GraphRagAnswerer(store=store, top_k=3)
    result = answerer.answer("Who supports affordable housing?")
    assert not result.used_llm
    assert "no external knowledge" in result.answer
    citations = result.citations()
    assert citations
    # every citation has a real source url
    assert all(c["source_url"] for c in citations)
    payload = result.as_dict()
    assert payload["question"] == "Who supports affordable housing?"
    assert payload["citations"]


def test_stub_generate_refuses_without_evidence() -> None:
    answer = stub_generate("anything", "(no matching entities found)")
    assert "Nothing can be answered" in answer


def test_injected_generate_is_used() -> None:
    store = _store()
    captured: dict[str, str] = {}

    def fake_generate(question: str, context: str) -> str:
        captured["q"] = question
        captured["ctx"] = context
        return "GROUNDED: " + question

    answerer = GraphRagAnswerer(store=store, generate=fake_generate, used_llm=True, top_k=2)
    result = answerer.answer("rent control supporters")
    assert result.answer == "GROUNDED: rent control supporters"
    assert result.used_llm
    assert "Rent Control Ordinance" in captured["ctx"]


def test_from_environment_picks_stub_without_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    answerer = GraphRagAnswerer.from_environment(_store())
    assert not answerer.used_llm
    assert answerer.generate is stub_generate


def test_from_environment_picks_llm_with_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    answerer = GraphRagAnswerer.from_environment(_store())
    assert answerer.used_llm
    assert answerer.generate is not stub_generate


def test_model_id_is_pinned() -> None:
    assert ANTHROPIC_MODEL == "claude-opus-4-8"
