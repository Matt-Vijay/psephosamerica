"""GraphRAG reasoning: retrieval, grounding, key-gated answerer + stub."""

from __future__ import annotations

import numpy as np

from src.query.graph_rag import (
    ANTHROPIC_MODEL,
    GraphRagAnswerer,
    build_grounding_context,
    load_dotenv as _real_load_dotenv,  # real ref, pre-patch
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


def test_unembedded_money_and_speech_paths_keep_edge_provenance() -> None:
    from src.query.graph_rag import _expand_facts

    store = _store()
    for identity, name in (
        ("org-acme", "Acme"),
        ("org-big", "Large Recipient"),
        ("org-empty", "No Evidence"),
    ):
        store.add_node(Node(identity, "org", name, (), "2026-06-01", ()))
    edges = [
        Edge("federal_award", "org-acme", "agency", {"amount": "42"}, _prov("https://x/award")),
        Edge("federal_award", "org-big", "agency", {"amount": "900"}, _prov("https://x/large")),
        Edge("lobbying_retention", "org-acme", "firm", {}, _prov("https://x/retention")),
        Edge("lobbying_contact", "org-acme", "cb-rent", {}, _prov("https://x/contact")),
        Edge("floor_speech", "ce-housing", "cb-rent", {}, _prov("https://x/speech")),
    ]
    for edge in edges:
        store.add_edge(edge)
    results = retrieve_subgraph(store, "Acme lobbying federal awards", top_k=2)
    assert [row.node.canonical_id for row in results[:2]] == ["org-acme", "org-big"]
    assert all(row.node.canonical_id != "org-empty" for row in results)
    assert len(results[0].facts) == len(results[0].fact_citations) == 3
    for identity in ("ce-housing", "cb-rent"):
        facts, citations = _expand_facts(store, store.nodes[identity])
        assert any("spoke on the floor" in fact for fact in facts)
        assert any(citation["source_url"] == "https://x/speech" for citation in citations)
        assert len(facts) == len(citations)
    answer = GraphRagAnswerer(store).answer("Acme lobbying federal awards")
    assert answer.used_llm is False
    assert {citation["source_url"] for citation in answer.citations()} >= {
        "https://x/award",
        "https://x/contact",
    }


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
    # Neutralise .env loading so the test reflects only the in-process env.
    monkeypatch.setattr("src.query.graph_rag.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    answerer = GraphRagAnswerer.from_environment(_store())
    assert not answerer.used_llm
    assert answerer.generate is stub_generate


def test_from_environment_prefers_openrouter(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_generate

    monkeypatch.setattr("src.query.graph_rag.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # OpenRouter still wins
    answerer = GraphRagAnswerer.from_environment(_store())
    assert answerer.used_llm
    assert answerer.generate is openrouter_generate


def test_from_environment_anthropic_alternate(monkeypatch) -> None:
    from src.query.graph_rag import anthropic_generate

    monkeypatch.setattr("src.query.graph_rag.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    answerer = GraphRagAnswerer.from_environment(_store())
    assert answerer.used_llm
    assert answerer.generate is anthropic_generate


def test_answer_degrades_to_stub_when_live_backend_fails(monkeypatch) -> None:
    def boom(_q: str, _c: str) -> str:
        raise RuntimeError("all models 429")

    answerer = GraphRagAnswerer(store=_store(), generate=boom, used_llm=True, top_k=2)
    result = answerer.answer("rent control")
    assert not result.used_llm  # degraded
    assert "source-anchored" in result.answer or "No cited evidence" in result.answer


def test_openrouter_models_primary_then_fallbacks(monkeypatch) -> None:
    from src.query.graph_rag import _openrouter_models

    monkeypatch.setenv("PSEPHOS_LLM_MODEL", "primary/model")
    monkeypatch.setenv("PSEPHOS_LLM_FALLBACKS", "fb/one, fb/two ,fb/one")
    assert _openrouter_models() == ["primary/model", "fb/one", "fb/two"]


def test_load_dotenv_does_not_override_existing(monkeypatch, tmp_path) -> None:
    # _real_load_dotenv is bound at import time, before the autouse fixture stubs
    # the module attribute, so it points at the genuine implementation.
    import os

    env_file = tmp_path / ".env"
    env_file.write_text('FOO_KEY="from_file"\nBAR_KEY=barval\n# comment\n', encoding="utf-8")
    monkeypatch.setenv("FOO_KEY", "from_env")
    monkeypatch.delenv("BAR_KEY", raising=False)
    _real_load_dotenv(env_file)
    assert os.environ["FOO_KEY"] == "from_env"  # existing wins
    assert os.environ["BAR_KEY"] == "barval"  # quotes stripped, loaded


def test_extract_message_text_handles_string_and_parts() -> None:
    from src.query.graph_rag import _extract_message_text

    assert _extract_message_text({"content": "hello"}) == "hello"
    assert (
        _extract_message_text(
            {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        )
        == "ab"
    )
    # A separate reasoning field is ignored; empty content yields "".
    assert _extract_message_text({"content": "", "reasoning": "thoughts"}) == ""


def test_strip_reasoning_preamble_drops_leaked_cot() -> None:
    from src.query.graph_rag import _strip_reasoning_preamble

    leaked = (
        "The user wants me to answer using the evidence.\n\n"
        "Senators X and Y voted yea on the budget act (see [1])."
    )
    assert _strip_reasoning_preamble(leaked).startswith("Senators X and Y")
    # A clean answer is left untouched.
    clean = "Senator X voted yea on the budget act (see [1])."
    assert _strip_reasoning_preamble(clean) == clean


class _FakeResponse:
    """Minimal httpx.Response stand-in for fallback tests."""

    def __init__(self, status_code: int, body: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


def _ok_body(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _fallback_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("PSEPHOS_LLM_MODEL", "primary/model")
    monkeypatch.setenv("PSEPHOS_LLM_FALLBACKS", "fb/one,fb/two")
    # No real sleeping during backoff tests.
    monkeypatch.setattr("src.query.graph_rag.time.sleep", lambda *_a, **_k: None)


def test_fallback_429_then_next_model(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)
    calls: list[str] = []

    def poster(url, *, headers, json):  # noqa: A002
        model = json["model"]
        calls.append(model)
        if model == "primary/model":
            return _FakeResponse(429, {}, {"retry-after": "0"})
        return _FakeResponse(200, _ok_body("clean answer from fallback"))

    answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=2)
    assert answer == "clean answer from fallback"
    assert served_by == "fb/one"
    assert calls[0] == "primary/model"  # primary was attempted (and retried) first


def test_fallback_body_embedded_error(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        if json["model"] == "primary/model":
            return _FakeResponse(200, {"error": {"message": "rate limited", "code": 429}})
        return _FakeResponse(200, _ok_body("served by fallback"))

    answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
    assert served_by == "fb/one"
    assert "fallback" in answer


def test_fallback_empty_content(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        if json["model"] == "primary/model":
            return _FakeResponse(200, _ok_body("   "))  # whitespace-only
        return _FakeResponse(200, _ok_body("real answer"))

    _answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
    assert served_by == "fb/one"


def test_fallback_reasoning_only_content(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        if json["model"] == "primary/model":
            # Reasoning preamble with no answer paragraph after it.
            return _FakeResponse(200, _ok_body("The user wants me to answer the question."))
        return _FakeResponse(200, _ok_body("Senator X voted yea (see [1])."))

    answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
    # primary returned reasoning-only content (no answer paragraph) -> fall through.
    assert served_by == "fb/one"
    assert "Senator X" in answer


def test_fallback_refusal_falls_through(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        if json["model"] == "primary/model":
            return _FakeResponse(200, _ok_body("I cannot help with that request."))
        return _FakeResponse(200, _ok_body("Here is the grounded answer (see [1])."))

    answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
    assert served_by == "fb/one"
    assert "grounded answer" in answer


def test_all_models_fail_raises(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        return _FakeResponse(500, {})

    try:
        openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "all OpenRouter models failed" in str(exc)


def test_answerer_degrades_to_stub_when_all_models_fail(monkeypatch) -> None:
    _fallback_env(monkeypatch)
    monkeypatch.setattr("src.query.graph_rag.load_dotenv", lambda *a, **k: None)

    def poster(url, *, headers, json):  # noqa: A002
        return _FakeResponse(503, {})

    # Patch the default poster so the answerer's openrouter path uses our fake.
    monkeypatch.setattr("src.query.graph_rag._default_poster", lambda: poster)
    answerer = GraphRagAnswerer.from_environment(_store())
    assert answerer.backend == "openrouter"
    result = answerer.answer("rent control")
    assert not result.used_llm  # degraded
    assert result.served_by == "stub"
    assert "no external knowledge" in result.answer


def test_transport_error_falls_through(monkeypatch) -> None:
    from src.query.graph_rag import openrouter_chat_with_fallback

    _fallback_env(monkeypatch)

    def poster(url, *, headers, json):  # noqa: A002
        if json["model"] in ("primary/model", "fb/one"):
            raise ConnectionError("boom")
        return _FakeResponse(200, _ok_body("answer from last model"))

    answer, served_by = openrouter_chat_with_fallback("q", "ctx", poster=poster, max_retries=1)
    assert served_by == "fb/two"
    assert answer == "answer from last model"


def test_model_id_is_pinned() -> None:
    assert ANTHROPIC_MODEL == "claude-opus-4-8"
