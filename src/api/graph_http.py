"""HTTP handlers for the connected-graph query API, GraphRAG, and explorer.

This is the serving layer for V8 deliverables #1 (query API), #2 (GraphRAG
reasoning), and #3 (public explorer). It is framework-neutral: each handler
takes a parsed request and returns a :class:`JsonHttpResponse`, so it drops
into the existing WSGI app (:mod:`src.api.wsgi_app`) with no new dependency.

Every JSON response that asserts a fact carries provenance (``source_url`` +
``content_sha256`` + ``known_at``); the explorer HTML renders those citations
inline. The :class:`GraphService` holds the (lazily built) in-memory store and
a key-gated GraphRAG answerer so the routes stay thin.

Routes wired in the WSGI app:
  GET /v1/graph/entities?type&jurisdiction&q&limit       -> cited entity list
  GET /v1/graph/votes?person_id | ?bill_id               -> the vote join
  GET /v1/graph/policy_area?bill_id | ?name              -> bill<->topic join
  GET /v1/graph/path?from&to&max_hops&edge_types         -> cited multi-hop paths
  GET /v1/graph/jurisdictions                            -> jurisdiction index
  GET /v1/graph/ask?q=...                                -> GraphRAG grounded answer
  GET /v1/explorer/official/<person_id>                  -> per-official HTML page
  GET /v1/explorer/jurisdiction/<slug>                   -> per-jurisdiction HTML page
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from src.api.http import JsonHttpResponse
from src.query import graph_query as gq
from src.query import redundancy
from src.query.graph_rag import GraphRagAnswerer
from src.query.graph_store import GraphStore, Node, build_store

_JSON_CT = "application/json; charset=utf-8"
_HTML_CT = "text/html; charset=utf-8"


def _json_response(payload: object, *, status: int = 200) -> JsonHttpResponse:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return JsonHttpResponse(
        status_code=status,
        headers={"Content-Type": _JSON_CT, "Cache-Control": "public, max-age=120"},
        body=body,
    )


def _error(status: int, error: str, detail: str) -> JsonHttpResponse:
    body = json.dumps({"error": error, "detail": detail}).encode("utf-8")
    return JsonHttpResponse(
        status_code=status,
        headers={"Content-Type": _JSON_CT, "Cache-Control": "no-store"},
        body=body,
    )


def _html_response(markup: str) -> JsonHttpResponse:
    return JsonHttpResponse(
        status_code=200,
        headers={"Content-Type": _HTML_CT, "Cache-Control": "public, max-age=120"},
        body=markup.encode("utf-8"),
    )


@dataclass
class GraphService:
    """Lazily-built graph store + GraphRAG answerer behind the HTTP handlers."""

    store_factory: Callable[[], GraphStore] = lambda: build_store(load_embeddings=True)
    _store: GraphStore | None = field(default=None, repr=False)
    _answerer: GraphRagAnswerer | None = field(default=None, repr=False)

    @property
    def store(self) -> GraphStore:
        if self._store is None:
            self._store = self.store_factory()
        return self._store

    @property
    def answerer(self) -> GraphRagAnswerer:
        if self._answerer is None:
            self._answerer = GraphRagAnswerer.from_environment(self.store)
        return self._answerer

    # -- query API ------------------------------------------------------

    def serve_entities(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        entity_type = _first(query, "type")
        jurisdiction = _first(query, "jurisdiction")
        contains = _first(query, "q")
        limit = _int(query, "limit", default=50, maximum=500)
        rows = gq.find_entities(
            self.store,
            entity_type=entity_type,
            jurisdiction=jurisdiction,
            contains=contains,
            limit=limit,
        )
        return _json_response({"count": len(rows), "results": [r.as_dict() for r in rows]})

    def serve_votes(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        person = _first(query, "person_id")
        bill = _first(query, "bill_id")
        if person:
            rows = gq.votes_of(self.store, person, limit=_int(query, "limit", 100, 1000))
        elif bill:
            rows = gq.voters_on(self.store, bill, limit=_int(query, "limit", 500, 2000))
        else:
            return _error(400, "bad_request", "person_id or bill_id is required")
        return _json_response({"count": len(rows), "results": [r.as_dict() for r in rows]})

    def serve_policy_area(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        bill = _first(query, "bill_id")
        name = _first(query, "name")
        if bill:
            rows = gq.bill_policy_areas(self.store, bill)
        elif name:
            rows = gq.bills_in_policy_area(self.store, name, limit=_int(query, "limit", 200, 1000))
        else:
            return _error(400, "bad_request", "bill_id or name is required")
        return _json_response({"count": len(rows), "results": [r.as_dict() for r in rows]})

    def serve_path(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        start = _first(query, "from")
        if not start:
            return _error(400, "bad_request", "from is required")
        target = _first(query, "to")
        max_hops = _int(query, "max_hops", default=3, maximum=6)
        edge_types_raw = _first(query, "edge_types")
        edge_types = (
            frozenset(t for t in edge_types_raw.split(",") if t) if edge_types_raw else None
        )
        paths = gq.path(
            self.store,
            start,
            max_hops=max_hops,
            edge_types=edge_types,
            target_id=target,
        )
        return _json_response(
            {
                "from": start,
                "to": target,
                "count": len(paths),
                "paths": [gq.path_as_dicts(p) for p in paths],
            }
        )

    def serve_jurisdictions(self) -> JsonHttpResponse:
        counts = self.store.jurisdictions()
        return _json_response(
            {
                "count": len(counts),
                "jurisdictions": [{"slug": k, "officials": v} for k, v in counts.items()],
            }
        )

    def serve_ask(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        question = _first(query, "q")
        if not question:
            return _error(400, "bad_request", "q (the question) is required")
        result = self.answerer.answer(question)
        return _json_response(result.as_dict())

    # -- #4/#5 redundancy / waste applications --------------------------

    def serve_redundant_policy_areas(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        min_bills = _int(query, "min_bills", default=5, maximum=1000)
        findings = redundancy.redundant_bills_by_policy_area(self.store, min_bills=min_bills)
        return _json_response(
            {
                "count": len(findings),
                "policy_areas": [
                    {
                        "policy_area": f.policy_area,
                        "bill_count": f.bill_count,
                        "bills": list(f.bills),
                    }
                    for f in findings
                ],
            }
        )

    def serve_reauthorizations(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        min_count = _int(query, "min_count", default=2, maximum=100)
        clusters = redundancy.reauthorization_clusters(self.store, min_count=min_count)
        return _json_response(
            {
                "count": len(clusters),
                "clusters": [
                    {
                        "normalized_title": c.normalized_title,
                        "count": c.count,
                        "measures": list(c.measures),
                    }
                    for c in clusters
                ],
            }
        )

    def serve_duplicate_ordinances(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        threshold = _float(query, "threshold", default=0.92)
        cross_only = _first(query, "cross_jurisdiction_only") != "false"
        pairs = redundancy.near_duplicate_ordinances(
            self.store, threshold=threshold, cross_jurisdiction_only=cross_only
        )
        return _json_response(
            {
                "count": len(pairs),
                "threshold": threshold,
                "pairs": [
                    {
                        "similarity": round(p.similarity, 4),
                        "cross_jurisdiction": p.cross_jurisdiction,
                        "a": p.a,
                        "b": p.b,
                    }
                    for p in pairs
                ],
            }
        )

    def serve_donor_paths(self, query: dict[str, list[str]]) -> JsonHttpResponse:
        term = _first(query, "term")
        if not term:
            return _error(400, "bad_request", "term (donor employer/occupation) is required")
        paths = redundancy.donor_to_vote_paths(self.store, term)
        return _json_response(
            {
                "term": term,
                "count": len(paths),
                "paths": [
                    {
                        "official": p.official,
                        "match_score": round(p.match_score, 4),
                        "votes": list(p.votes),
                    }
                    for p in paths
                ],
            }
        )

    # -- explorer (HTML) ------------------------------------------------

    def serve_official_page(self, person_id: str) -> JsonHttpResponse:
        node = self.store.node(person_id)
        if node is None or node.entity_type != "person":
            return _error(404, "not_found", f"no official {person_id}")
        votes = gq.votes_of(self.store, person_id, limit=200)
        return _html_response(render_official_page(node_brief(node), votes))

    def serve_jurisdiction_page(self, slug: str) -> JsonHttpResponse:
        officials = gq.find_entities(self.store, entity_type="person", jurisdiction=slug, limit=500)
        bills = gq.find_entities(self.store, entity_type="bill", jurisdiction=slug, limit=200)
        if not officials and not bills:
            return _error(404, "not_found", f"no jurisdiction {slug}")
        return _html_response(render_jurisdiction_page(slug, officials, bills))


# -- small request-parsing helpers -------------------------------------


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _int(query: dict[str, list[str]], key: str, default: int, maximum: int) -> int:
    raw = _first(query, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, maximum))


def _float(query: dict[str, list[str]], key: str, default: float) -> float:
    raw = _first(query, key)
    if raw is None:
        return default
    try:
        return max(0.0, min(float(raw), 1.0))
    except ValueError:
        return default


def node_brief(node: Node) -> dict[str, object]:
    return {
        "canonical_id": node.canonical_id,
        "display_name": node.display_name,
        "jurisdiction": node.jurisdiction,
        "external_ids": list(node.external_ids),
        "citation": _node_citation(node),
    }


def _node_citation(node: Node) -> dict[str, str | None]:
    if node.citations:
        return node.citations[0].as_citation()
    return {"source_url": None, "content_sha256": None, "known_at": node.known_at}


# -- HTML renderers (citations everywhere) ------------------------------

_PAGE_CSS = (
    "body{font-family:system-ui,-apple-system,sans-serif;max-width:960px;margin:2rem auto;"
    "padding:0 1rem;color:#1a1a1a;line-height:1.5}"
    "h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:2rem}"
    "table{border-collapse:collapse;width:100%;font-size:.9rem}"
    "td,th{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}"
    "th{background:#f4f4f4}a{color:#0645ad}.cite{font-size:.75rem;color:#666}"
    ".choice-yea{color:#137333}.choice-nay{color:#b3261e}"
)


def _cite_link(citation: dict[str, str | None]) -> str:
    url = citation.get("source_url")
    known = html.escape(str(citation.get("known_at") or ""))
    if not url:
        return f'<span class="cite">(known_at {known})</span>'
    safe = html.escape(url)
    return f'<a class="cite" href="{safe}" rel="nofollow noopener">source</a> <span class="cite">@ {known}</span>'


def render_official_page(brief: dict[str, object], votes: list[gq.QueryResult]) -> str:
    name = html.escape(str(brief["display_name"]))
    juris = html.escape(str(brief["jurisdiction"] or "unknown"))
    rows = []
    for v in votes:
        p = v.payload
        choice = html.escape(str(p.get("choice") or ""))
        css = "choice-yea" if choice == "yea" else ("choice-nay" if choice == "nay" else "")
        rows.append(
            f"<tr><td>{html.escape(str(p.get('bill_name') or p.get('bill_id')))}</td>"
            f'<td class="{css}">{choice}</td>'
            f"<td>{_cite_link(v.citation)}</td></tr>"
        )
    table = (
        "<table><tr><th>Bill / ordinance</th><th>Vote</th><th>Citation</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p>No roll-call votes on record in the graph yet.</p>"
    )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{name} — OpenPact</title>"
        f"<style>{_PAGE_CSS}</style></head><body>"
        f"<h1>{name}</h1>"
        f"<p>Jurisdiction: <strong>{juris}</strong> · {_cite_link(brief['citation'])}</p>"  # type: ignore[arg-type]
        f"<h2>Roll-call votes ({len(votes)})</h2>{table}"
        "<p class='cite'>Every row is source-anchored to its originating record "
        "(URL + content hash + known_at).</p>"
        "</body></html>"
    )


def render_jurisdiction_page(
    slug: str, officials: list[gq.QueryResult], bills: list[gq.QueryResult]
) -> str:
    slug_e = html.escape(slug)
    official_rows = "".join(
        f"<tr><td><a href='/v1/explorer/official/"
        f"{html.escape(str(o.payload['canonical_id']))}'>"
        f"{html.escape(str(o.payload['display_name']))}</a></td>"
        f"<td>{_cite_link(o.citation)}</td></tr>"
        for o in officials
    )
    bill_rows = "".join(
        f"<tr><td>{html.escape(str(b.payload['display_name']))}</td>"
        f"<td>{_cite_link(b.citation)}</td></tr>"
        for b in bills
    )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{slug_e} — OpenPact</title><style>{_PAGE_CSS}</style></head><body>"
        f"<h1>Jurisdiction: {slug_e}</h1>"
        f"<h2>Officials ({len(officials)})</h2>"
        f"<table><tr><th>Official</th><th>Citation</th></tr>{official_rows}</table>"
        f"<h2>Bills / ordinances ({len(bills)})</h2>"
        f"<table><tr><th>Measure</th><th>Citation</th></tr>{bill_rows}</table>"
        "<p class='cite'>Every entity links back to its source record.</p>"
        "</body></html>"
    )


__all__ = [
    "GraphService",
    "render_official_page",
    "render_jurisdiction_page",
    "node_brief",
]
