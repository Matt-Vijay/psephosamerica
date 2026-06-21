"""GraphRAG reasoning layer over the connected graph.

Deliverable #2 of the V8 platform pivot: a natural-language question becomes
a **cited subgraph** (numpy cosine retrieval over the contract's
``dossier_embedding`` vectors) which is then **grounded** into an answer.

Two halves, cleanly separated so the retrieval + grounding ship now and the
actual model call activates only when a key is present:

* **Retrieval + grounding (always on, numpy-only).**
  :func:`retrieve_subgraph` embeds the question with the same in-repo
  deterministic embedder Track A uses for its dossiers, ranks contract
  entities by cosine similarity, and expands each hit one hop over the graph
  (votes / policy areas) to form a small, fully-cited subgraph.
  :func:`build_grounding_context` renders that subgraph into a prompt block
  where every fact carries its ``[source_url | known_at]`` citation.

* **Answer generation (key-gated, injected).** :class:`GraphRagAnswerer`
  takes an injected ``generate`` callable. The default is
  :func:`stub_generate` — a deterministic, offline, fully-tested grounded
  summary that cites the retrieved nodes. When ``ANTHROPIC_API_KEY`` is
  present, :func:`anthropic_generate` wires the real Claude call
  (``claude-opus-4-8``, adaptive thinking) behind the same interface. The
  answerer never invents facts: it answers *only* from the cited context, and
  always returns the citation list alongside the prose.

Staying numpy keeps this independent of Track A's embedding backend — the
vectors arrive in the contract and we only do cosine here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from src.query.graph_store import GraphStore, Node
from src.query.query_embedder import QueryEmbedder

# The model the grounded answerer uses when a key is present. Per the
# claude-api skill: default to claude-opus-4-8 with adaptive thinking.
ANTHROPIC_MODEL = "claude-opus-4-8"

GenerateFn = Callable[[str, str], str]
"""(question, grounding_context) -> answer text. Injected into the answerer."""


@dataclass(frozen=True)
class RetrievedNode:
    """A graph node retrieved for a question, with its similarity + citation."""

    node: Node
    score: float
    # One-hop cited facts expanded from this node (e.g. "voted yea on X").
    facts: tuple[str, ...] = ()
    fact_citations: tuple[dict[str, str | None], ...] = ()


@dataclass(frozen=True)
class GraphRagResult:
    """The full reasoning result: the answer plus its cited subgraph."""

    question: str
    answer: str
    retrieved: tuple[RetrievedNode, ...]
    used_llm: bool

    def citations(self) -> list[dict[str, str | None]]:
        """Flat, de-duplicated citation list for everything the answer rests on."""
        seen: set[str] = set()
        out: list[dict[str, str | None]] = []
        for retrieved in self.retrieved:
            for citation in (_node_citation(retrieved.node), *retrieved.fact_citations):
                url = citation.get("source_url")
                key = f"{url}|{citation.get('content_sha256')}"
                if url and key not in seen:
                    seen.add(key)
                    out.append(citation)
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "answer": self.answer,
            "used_llm": self.used_llm,
            "retrieved": [
                {
                    "canonical_id": r.node.canonical_id,
                    "display_name": r.node.display_name,
                    "entity_type": r.node.entity_type,
                    "jurisdiction": r.node.jurisdiction,
                    "score": round(r.score, 4),
                    "facts": list(r.facts),
                }
                for r in self.retrieved
            ],
            "citations": self.citations(),
        }


def _node_citation(node: Node) -> dict[str, str | None]:
    if node.citations:
        return node.citations[0].as_citation()
    return {"source_url": None, "content_sha256": None, "known_at": node.known_at}


def _cosine(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity between ``matrix`` rows and ``query``."""
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return np.zeros(matrix.shape[0], dtype=np.float64)
    row_norms = np.linalg.norm(matrix, axis=1)
    safe = np.where(row_norms == 0, 1.0, row_norms)
    scores: np.ndarray = (matrix @ query) / (safe * query_norm)
    return scores


def _expand_facts(
    store: GraphStore, node: Node
) -> tuple[tuple[str, ...], tuple[dict[str, str | None], ...]]:
    """One-hop cited facts for a node (votes for people, topics for bills)."""
    facts: list[str] = []
    citations: list[dict[str, str | None]] = []
    if node.entity_type == "person":
        for edge, bill in store.neighbors(node.canonical_id, edge_type="vote"):
            name = bill.display_name if bill else edge.dst_id
            choice = edge.attributes.get("choice", "voted")
            facts.append(f"{node.display_name} voted {choice} on {name}")
            citations.append(edge.provenance.as_citation())
            if len(facts) >= 5:
                break
    else:
        for edge in store.out_edges(node.canonical_id):
            if edge.edge_type in ("policy_area", "legislative_subject"):
                facts.append(f"{node.display_name} is tagged {edge.attributes.get('name')}")
                citations.append(edge.provenance.as_citation())
            elif edge.edge_type == "vote":
                continue
            if len(facts) >= 5:
                break
        for edge, person in store.neighbors(node.canonical_id, edge_type="vote", direction="in"):
            who = person.display_name if person else edge.src_id
            facts.append(
                f"{who} voted {edge.attributes.get('choice', 'voted')} on {node.display_name}"
            )
            citations.append(edge.provenance.as_citation())
            if len(facts) >= 8:
                break
    return tuple(facts), tuple(citations)


def retrieve_subgraph(
    store: GraphStore,
    question: str,
    *,
    embedder: QueryEmbedder | None = None,
    top_k: int = 5,
) -> list[RetrievedNode]:
    """Rank embedded entities by cosine to the question; expand one hop.

    Only nodes carrying a ``dossier_embedding`` participate (the store must be
    built with ``load_embeddings=True``). Each retrieved node is expanded into
    cited one-hop facts so the grounding step has real, attributable evidence.
    """
    embedder = embedder or QueryEmbedder()
    candidates = [n for n in store.nodes.values() if n.dossier_embedding is not None]
    if not candidates:
        return []
    matrix = np.vstack([n.dossier_embedding for n in candidates])  # type: ignore[misc]
    query_vec = np.asarray(embedder.embed(question), dtype=np.float64)
    if query_vec.shape[0] != matrix.shape[1]:
        # Dimension mismatch (embedder vs contract): cannot score honestly.
        return []
    scores = _cosine(matrix, query_vec)
    order = np.argsort(-scores)[:top_k]
    retrieved: list[RetrievedNode] = []
    for idx in order:
        node = candidates[int(idx)]
        facts, citations = _expand_facts(store, node)
        retrieved.append(
            RetrievedNode(
                node=node,
                score=float(scores[int(idx)]),
                facts=facts,
                fact_citations=citations,
            )
        )
    return retrieved


def build_grounding_context(retrieved: list[RetrievedNode]) -> str:
    """Render the retrieved subgraph into a cited prompt block.

    Every line carries its provenance so the model (or the stub) can only
    ground claims in attributable facts.
    """
    lines: list[str] = []
    for index, item in enumerate(retrieved, start=1):
        node = item.node
        citation = _node_citation(node)
        lines.append(
            f"[{index}] {node.entity_type.upper()} {node.display_name}"
            f" (jurisdiction={node.jurisdiction or 'unknown'};"
            f" known_at={node.known_at};"
            f" source={citation.get('source_url')})"
        )
        for fact, fact_citation in zip(item.facts, item.fact_citations, strict=False):
            lines.append(
                f"    - {fact} [source={fact_citation.get('source_url')};"
                f" known_at={fact_citation.get('known_at')}]"
            )
    return "\n".join(lines) if lines else "(no matching entities found)"


# -- answer generation -------------------------------------------------

_SYSTEM_INSTRUCTION = (
    "You are OpenPact's grounded analyst over a connected legislative graph. "
    "Answer the question USING ONLY the cited evidence provided. Every claim "
    "must trace to a numbered evidence item. If the evidence does not support "
    "an answer, say so. Never invent votes, donors, or sources."
)


def stub_generate(question: str, grounding_context: str) -> str:
    """Deterministic offline grounded answer (used when no key is present).

    This is intentionally a faithful, non-hallucinating summary: it restates
    what the cited subgraph contains rather than reasoning beyond it, so the
    platform serves an honest, tested answer with zero external dependency.
    """
    if grounding_context.startswith("(no matching"):
        return (
            f"No cited evidence in the graph matches: {question!r}. "
            "Nothing can be answered without supporting sources."
        )
    return (
        f"Question: {question}\n"
        "Grounded answer (from the cited subgraph below — no external knowledge "
        "used):\n"
        f"{grounding_context}\n"
        "Each line above is a source-anchored fact; see citations for "
        "provenance and known_at."
    )


def anthropic_generate(question: str, grounding_context: str) -> str:
    """Real Claude grounded answer. Requires ``ANTHROPIC_API_KEY``.

    Wired behind the same ``GenerateFn`` interface as the stub. POSTs to the
    Anthropic Messages API with httpx (already a core dependency — no SDK added,
    matching ``src.prediction.anthropic_forecaster``). Uses ``claude-opus-4-8``
    with adaptive thinking per the claude-api skill. Import is local so the
    module loads even where httpx isn't installed.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; inject the stub instead")

    import httpx

    user_content = (
        f"Question:\n{question}\n\n"
        "Cited evidence (each numbered item is source-anchored):\n"
        f"{grounding_context}\n\n"
        "Answer using only this evidence and reference the item numbers you relied on."
    )
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 2000,
            "thinking": {"type": "adaptive"},
            "system": _SYSTEM_INSTRUCTION,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    blocks = response.json().get("content", [])
    return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")


@dataclass
class GraphRagAnswerer:
    """Answers NL questions over the graph: retrieve → ground → generate.

    ``generate`` is injected. Construct via :meth:`from_environment` to pick the
    real Claude call when ``ANTHROPIC_API_KEY`` is set and the stub otherwise.
    """

    store: GraphStore
    generate: GenerateFn = stub_generate
    embedder: QueryEmbedder = field(default_factory=QueryEmbedder)
    top_k: int = 5
    used_llm: bool = False

    @classmethod
    def from_environment(cls, store: GraphStore, *, top_k: int = 5) -> GraphRagAnswerer:
        """Pick the real LLM when a key is present; the tested stub otherwise."""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return cls(store=store, generate=anthropic_generate, top_k=top_k, used_llm=True)
        return cls(store=store, generate=stub_generate, top_k=top_k, used_llm=False)

    def answer(self, question: str) -> GraphRagResult:
        retrieved = retrieve_subgraph(
            self.store, question, embedder=self.embedder, top_k=self.top_k
        )
        context = build_grounding_context(retrieved)
        answer = self.generate(question, context)
        return GraphRagResult(
            question=question,
            answer=answer,
            retrieved=tuple(retrieved),
            used_llm=self.used_llm,
        )


__all__ = [
    "ANTHROPIC_MODEL",
    "GenerateFn",
    "RetrievedNode",
    "GraphRagResult",
    "retrieve_subgraph",
    "build_grounding_context",
    "stub_generate",
    "anthropic_generate",
    "GraphRagAnswerer",
]
