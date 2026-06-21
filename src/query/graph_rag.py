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
  summary that cites the retrieved nodes. The **primary** live backend is
  :func:`openrouter_generate` — an OpenAI-compatible chat-completions call to
  OpenRouter (``OPENPACT_LLM_MODEL`` with a free-tier fallback chain), used when
  ``OPENROUTER_API_KEY`` is set. :func:`anthropic_generate` (``claude-opus-4-8``,
  adaptive thinking) remains as an optional alternate behind
  ``ANTHROPIC_API_KEY``. All paths obey the same contract: answer *only* from the
  cited context, return the citation list, and never invent facts. If every live
  model is rate-limited or unreachable, the answerer degrades to the stub.

Staying numpy keeps this independent of Track A's embedding backend — the
vectors arrive in the contract and we only do cosine here.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from src.query.graph_store import GraphStore, Node
from src.query.query_embedder import QueryEmbedder

_LOG = logging.getLogger("openpact.graph_rag")

# Optional Anthropic alternate (behind ANTHROPIC_API_KEY). Per the claude-api
# skill: claude-opus-4-8 with adaptive thinking.
ANTHROPIC_MODEL = "claude-opus-4-8"

# OpenRouter (OpenAI-compatible) is the default/primary LLM backend.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-oss-120b:free"

# Where to look for a gitignored .env so `python -m src.query ask` works without
# the user exporting anything. Repo root is two levels up from this file.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

GenerateFn = Callable[[str, str], str]
"""(question, grounding_context) -> answer text. Injected into the answerer."""


def load_dotenv(path: Path = _ENV_PATH) -> None:
    """Populate os.environ from a simple KEY=VALUE .env file (existing vars win).

    Deliberately minimal (no new dependency): blank lines and ``#`` comments are
    skipped, optional surrounding quotes on values are stripped, and a variable
    already present in the environment is never overwritten. Secrets stay in the
    gitignored .env and are never printed.
    """
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


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
    # Which backend actually produced the answer, for observability: a model id
    # like "nvidia/nemotron-3-ultra-550b-a55b:free" when a fallback served it,
    # "stub" offline, or "anthropic:claude-opus-4-8".
    served_by: str = "stub"

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
            "served_by": self.served_by,
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
    "You are OpenPact's grounded analyst over a connected legislative knowledge "
    "graph spanning federal, state, and municipal officials, bills/ordinances, "
    "roll-call votes, and policy topics. Answer the user's question USING ONLY "
    "the numbered, source-anchored evidence provided in the user message.\n\n"
    "Rules:\n"
    "- Every factual claim MUST cite the evidence item number(s) it rests on, "
    "e.g. '(see [2])'.\n"
    "- If the evidence is insufficient to answer, say so plainly and state what "
    "additional records would be needed — do NOT fill the gap from prior "
    "knowledge.\n"
    "- Never invent votes, donors, officials, jurisdictions, or sources.\n"
    "- Be concise and concrete: name the officials, bills, and choices in the "
    "evidence rather than speaking in generalities.\n"
    "- Distinguish what the evidence directly shows from any reasonable "
    "inference you draw from it."
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
            "Nothing can be answered without supporting sources. Try naming a "
            "specific official, bill, policy area, or jurisdiction."
        )
    return (
        f"The most relevant source-anchored evidence in the graph for "
        f"{question!r} is below. (Set OPENROUTER_API_KEY for a synthesised "
        "narrative answer; this offline mode uses no external knowledge and "
        "reports only the cited facts, never inventing beyond them.)\n\n"
        f"{grounding_context}\n\n"
        "Each numbered item and indented fact is anchored to a source URL + "
        "content hash + known_at — see the citation list."
    )


def _build_user_content(question: str, grounding_context: str) -> str:
    return (
        f"Question:\n{question}\n\n"
        "Cited evidence (each numbered item is source-anchored):\n"
        f"{grounding_context}\n\n"
        "Answer using only this evidence and reference the item numbers you relied on."
    )


def _openrouter_models() -> list[str]:
    """Primary model + ordered free-tier fallbacks, from env."""
    primary = os.environ.get("OPENPACT_LLM_MODEL", OPENROUTER_DEFAULT_MODEL)
    raw = os.environ.get("OPENPACT_LLM_FALLBACKS", "")
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()]
    # De-duplicate, preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for model in [primary, *fallbacks]:
        if model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


_REASONING_PREAMBLE_MARKERS = (
    "the user wants",
    "the user is asking",
    "the user asks",
    "let me analyze",
    "let me look",
    "let me think",
    "i need to",
    "first, i",
    "okay, ",
    "we need to",
    "let's ",
    "the question asks",
    "i should",
    "looking at the evidence",
    "based on the instructions",
)


def _extract_message_text(message: dict[str, object]) -> str:
    """Pull the user-facing answer from a chat message, ignoring `reasoning`.

    Some large free models return chain-of-thought in a separate ``reasoning``
    field (which we deliberately ignore) and the answer in ``content``. ``content``
    may be a plain string or an OpenAI-style list of ``{type, text}`` parts.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text", "output_text")
        ]
        return "".join(parts)
    return ""


def _strip_reasoning_preamble(text: str) -> str:
    """Drop leaked chain-of-thought that precedes the real answer.

    Large "thinking" models sometimes prepend reasoning ("The user wants me
    to...") before the answer. If early lines look like reasoning, skip to the
    first line that reads like the answer body. Conservative: only strips when a
    clear later answer paragraph exists, so genuine answers are never truncated.
    """
    stripped = text.strip()
    lowered = stripped.lstrip().lower()
    if not any(lowered.startswith(marker) for marker in _REASONING_PREAMBLE_MARKERS):
        return stripped
    # Split into paragraphs; return from the first paragraph that doesn't look
    # like reasoning, if one exists after the preamble.
    paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
    for index, para in enumerate(paragraphs):
        if not any(para.lower().startswith(marker) for marker in _REASONING_PREAMBLE_MARKERS):
            tail = "\n\n".join(paragraphs[index:]).strip()
            if tail:
                return tail
    return stripped


class HttpResponseLike(Protocol):
    """Minimal duck-type of an httpx.Response, so tests can inject a fake."""

    status_code: int
    headers: Any

    def json(self) -> Any: ...


class HttpPoster(Protocol):
    """Injectable POST so the fallback logic is unit-testable without network."""

    def __call__(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> HttpResponseLike: ...


_REFUSAL_MARKERS = (
    "i cannot help",
    "i can't help",
    "i cannot assist",
    "i can't assist",
    "i'm sorry, but i can't",
    "i am unable to help",
    "as an ai language model",
)


@dataclass(frozen=True)
class _Attempt:
    """Outcome of one model attempt: a good answer, or a (retryable) failure."""

    ok: bool
    text: str = ""
    retryable: bool = False
    reason: str = ""
    retry_after: float | None = None


def _detect_response(model: str, response: HttpResponseLike) -> _Attempt:
    """Classify a single HTTP response into a good answer or a typed failure.

    Covers: 429/5xx (retryable), other non-2xx, body-embedded ``error`` on a 200,
    missing/empty ``choices``, empty/whitespace content, reasoning-only content,
    and obvious refusals.
    """
    status = response.status_code
    if status == 429 or status >= 500:
        retry_after = None
        try:
            raw = response.headers.get("retry-after")
            retry_after = float(raw) if raw else None
        except (ValueError, AttributeError):
            retry_after = None
        return _Attempt(False, retryable=True, reason=f"HTTP {status}", retry_after=retry_after)
    if status >= 400:
        return _Attempt(False, retryable=False, reason=f"HTTP {status}")
    try:
        payload = response.json()
    except Exception as exc:  # malformed body
        return _Attempt(False, retryable=False, reason=f"unparseable body: {exc}")
    # OpenRouter returns errors inside a 200 body.
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        # Embedded rate-limit / provider overload is worth a fall-through.
        retryable = isinstance(err, dict) and int(err.get("code", 0) or 0) in (429, 502, 503)
        return _Attempt(False, retryable=retryable, reason=f"body error: {msg}")
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return _Attempt(False, retryable=False, reason="no choices")
    message = choices[0].get("message") or {}
    raw_text = _extract_message_text(message)
    if not raw_text.strip():
        return _Attempt(False, retryable=False, reason="empty content")
    text = _strip_reasoning_preamble(raw_text)
    if not text.strip():
        return _Attempt(False, retryable=False, reason="reasoning-only content")
    # Reasoning-only with no answer paragraph: every paragraph reads like CoT.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if paragraphs and all(
        any(p.lower().startswith(m) for m in _REASONING_PREAMBLE_MARKERS) for p in paragraphs
    ):
        return _Attempt(False, retryable=False, reason="reasoning-only content")
    if any(marker in text.lower()[:200] for marker in _REFUSAL_MARKERS):
        return _Attempt(False, retryable=False, reason="refusal/off-task")
    return _Attempt(True, text=text)


def _default_poster() -> HttpPoster:
    import httpx

    def post(url: str, *, headers: dict[str, str], json: dict[str, Any]) -> HttpResponseLike:
        return httpx.post(url, headers=headers, json=json, timeout=120.0)

    return post


def openrouter_chat_with_fallback(
    question: str,
    grounding_context: str,
    *,
    poster: HttpPoster | None = None,
    max_retries: int = 3,
) -> tuple[str, str]:
    """Try [primary, *fallbacks] in order, return ``(answer, served_by)``.

    Applies the full failure-detection (`_detect_response`) at every step:
    network errors / 429 / 5xx (retried with backoff honoring Retry-After, then
    fall through), 200-body errors, empty / reasoning-only content, and obvious
    refusals. The first GOOD answer wins; fall-through events are logged. Raises
    only when *every* model fails (the answerer then degrades to the stub).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set; inject the stub instead")
    base_url = os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/openpact",
        "X-Title": "OpenPact GraphRAG",
    }
    user_content = _build_user_content(question, grounding_context)
    post = poster or _default_poster()
    errors: list[str] = []
    for model in _openrouter_models():
        body = {
            "model": model,
            "max_tokens": 1600,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_content},
            ],
        }
        for attempt in range(max_retries):
            last = attempt == max_retries - 1
            try:
                response = post(url, headers=headers, json=body)
            except Exception as exc:  # connection error / timeout — retryable
                errors.append(f"{model}: transport {exc}")
                if last:
                    _LOG.warning("graph_rag: %s transport error (%s); falling through", model, exc)
                    break
                _LOG.warning("graph_rag: %s transport error (%s); retrying", model, exc)
                time.sleep(min(1.0 * (2**attempt), 5.0))
                continue
            outcome = _detect_response(model, response)
            if outcome.ok:
                _LOG.info("graph_rag: answer served by %s", model)
                return outcome.text, model
            errors.append(f"{model}: {outcome.reason}")
            if outcome.retryable and not last:
                delay = (
                    outcome.retry_after
                    if outcome.retry_after is not None
                    else min(1.0 * (2**attempt), 5.0)
                )
                _LOG.warning(
                    "graph_rag: %s %s; retry %d after %.1fs",
                    model,
                    outcome.reason,
                    attempt + 1,
                    delay,
                )
                time.sleep(delay)
                continue
            _LOG.warning("graph_rag: %s failed (%s); falling through", model, outcome.reason)
            break  # non-retryable, or retries exhausted — next model
    raise RuntimeError("all OpenRouter models failed: " + "; ".join(errors))


def openrouter_generate(question: str, grounding_context: str) -> str:
    """Primary grounded answer via OpenRouter; returns only the answer text.

    Thin wrapper over :func:`openrouter_chat_with_fallback` for the simple
    ``GenerateFn`` interface. The answerer prefers the richer fallback function
    so it can record ``served_by``; this remains for direct callers.
    """
    answer, _served_by = openrouter_chat_with_fallback(question, grounding_context)
    return answer


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

    user_content = _build_user_content(question, grounding_context)
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
            # Adaptive thinking + high effort per the claude-api skill: this is a
            # reasoning-over-evidence task where correctness matters more than cost.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
            "system": _SYSTEM_INSTRUCTION,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=120.0,
    )
    response.raise_for_status()
    blocks = response.json().get("content", [])
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    return text.strip()


@dataclass
class GraphRagAnswerer:
    """Answers NL questions over the graph: retrieve → ground → generate.

    ``generate`` is injected. Construct via :meth:`from_environment` to pick
    OpenRouter (primary) or Anthropic (optional alternate) when keys are present,
    falling back to the never-hallucinating stub. If a live call fails at answer
    time, it degrades to the stub rather than crashing.
    """

    store: GraphStore
    generate: GenerateFn = stub_generate
    embedder: QueryEmbedder = field(default_factory=QueryEmbedder)
    top_k: int = 5
    used_llm: bool = False
    # "openrouter" uses the fallback chain and records the model that served;
    # "anthropic" / "stub" use the injected ``generate`` with a fixed label.
    backend: str = "stub"

    @classmethod
    def from_environment(cls, store: GraphStore, *, top_k: int = 5) -> GraphRagAnswerer:
        """Pick the live LLM when a key is present; the tested stub otherwise.

        Loads the gitignored repo ``.env`` first so the CLI works without the
        user exporting anything. OpenRouter is the default/primary backend;
        Anthropic is an optional alternate behind ``ANTHROPIC_API_KEY``.
        """
        load_dotenv()
        if os.environ.get("OPENROUTER_API_KEY"):
            return cls(
                store=store,
                generate=openrouter_generate,
                top_k=top_k,
                used_llm=True,
                backend="openrouter",
            )
        if os.environ.get("ANTHROPIC_API_KEY"):
            return cls(
                store=store,
                generate=anthropic_generate,
                top_k=top_k,
                used_llm=True,
                backend="anthropic",
            )
        return cls(store=store, generate=stub_generate, top_k=top_k, used_llm=False, backend="stub")

    def answer(self, question: str) -> GraphRagResult:
        retrieved = retrieve_subgraph(
            self.store, question, embedder=self.embedder, top_k=self.top_k
        )
        context = build_grounding_context(retrieved)
        used_llm = self.used_llm
        served_by = self.backend
        try:
            if self.backend == "openrouter":
                # Use the fallback chain directly so we can record which model served.
                answer, served_by = openrouter_chat_with_fallback(question, context)
            else:
                answer = self.generate(question, context)
                if self.backend == "anthropic":
                    served_by = f"anthropic:{ANTHROPIC_MODEL}"
        except Exception:
            # Every live model failed (all rate-limited / network down): degrade
            # to the honest grounded stub rather than crash.
            if self.used_llm:
                answer = stub_generate(question, context)
                used_llm = False
                served_by = "stub"
                _LOG.warning("graph_rag: all live backends failed; served grounded stub")
            else:
                raise
        return GraphRagResult(
            question=question,
            answer=answer,
            retrieved=tuple(retrieved),
            used_llm=used_llm,
            served_by=served_by,
        )


__all__ = [
    "ANTHROPIC_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_DEFAULT_MODEL",
    "GenerateFn",
    "RetrievedNode",
    "GraphRagResult",
    "retrieve_subgraph",
    "build_grounding_context",
    "stub_generate",
    "openrouter_generate",
    "openrouter_chat_with_fallback",
    "anthropic_generate",
    "load_dotenv",
    "GraphRagAnswerer",
]
