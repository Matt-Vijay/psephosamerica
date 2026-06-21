"""Tests for the graph query API / GraphRAG / explorer HTTP handlers."""

from __future__ import annotations

import json

import numpy as np

from src.api.graph_http import GraphService
from src.query.graph_store import Edge, GraphStore, Node, Provenance
from src.query.query_embedder import QueryEmbedder


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _embed(text: str) -> np.ndarray:
    return np.asarray(QueryEmbedder().embed(text), dtype=np.float64)


def _store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-a",
            "person",
            "Alice Adams",
            ("legistar:oakland:1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/p"),),
            "oakland",
            dossier_embedding=_embed("housing rent control"),
        )
    )
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Rent Ordinance",
            ("legistar:oakland:9",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/b"),),
            "oakland",
            dossier_embedding=_embed("housing rent control ordinance"),
        )
    )
    store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov("https://x/v")))
    store.add_edge(Edge("policy_area", "cb-1", "csub-h", {"name": "Housing"}, _prov("https://x/t")))
    return store


def _service() -> GraphService:
    return GraphService(store_factory=_store)


def _body(resp) -> dict:
    return json.loads(resp.body.decode("utf-8"))


def test_entities_filtered_and_cited() -> None:
    resp = _service().serve_entities({"type": ["person"], "jurisdiction": ["oakland"]})
    assert resp.status_code == 200
    data = _body(resp)
    assert data["count"] == 1
    assert data["results"][0]["display_name"] == "Alice Adams"
    assert data["results"][0]["citation"]["source_url"] == "https://x/p"


def test_votes_person_and_bill() -> None:
    svc = _service()
    by_person = _body(svc.serve_votes({"person_id": ["ce-a"]}))
    assert by_person["results"][0]["bill_name"] == "Rent Ordinance"
    assert by_person["results"][0]["citation"]["source_url"] == "https://x/v"
    by_bill = _body(svc.serve_votes({"bill_id": ["cb-1"]}))
    assert by_bill["results"][0]["person_name"] == "Alice Adams"


def test_votes_requires_a_param() -> None:
    resp = _service().serve_votes({})
    assert resp.status_code == 400


def test_policy_area_both_directions() -> None:
    svc = _service()
    by_bill = _body(svc.serve_policy_area({"bill_id": ["cb-1"]}))
    assert any(r["name"] == "Housing" for r in by_bill["results"])
    by_name = _body(svc.serve_policy_area({"name": ["Housing"]}))
    assert by_name["results"][0]["bill_id"] == "cb-1"


def test_path_endpoint() -> None:
    resp = _service().serve_path({"from": ["ce-a"], "max_hops": ["3"]})
    data = _body(resp)
    assert data["count"] >= 1
    # the first hop is the cited vote edge
    assert data["paths"][0][0]["edge_type"] == "vote"
    assert data["paths"][0][0]["citation"]["source_url"] == "https://x/v"


def test_path_requires_from() -> None:
    assert _service().serve_path({}).status_code == 400


def test_jurisdictions_index() -> None:
    data = _body(_service().serve_jurisdictions())
    assert {"slug": "oakland", "officials": 1} in data["jurisdictions"]


def test_ask_grounded_and_cited() -> None:
    resp = _service().serve_ask({"q": ["who supports rent control housing"]})
    data = _body(resp)
    assert data["question"] == "who supports rent control housing"
    assert data["citations"]
    assert data["used_llm"] is False


def test_ask_requires_question() -> None:
    assert _service().serve_ask({}).status_code == 400


def test_official_page_html_with_citations() -> None:
    resp = _service().serve_official_page("ce-a")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    markup = resp.body.decode("utf-8")
    assert "Alice Adams" in markup
    assert "Rent Ordinance" in markup
    assert "https://x/v" in markup  # vote citation rendered inline


def test_official_page_404() -> None:
    assert _service().serve_official_page("ce-missing").status_code == 404


def test_jurisdiction_page_html() -> None:
    resp = _service().serve_jurisdiction_page("oakland")
    assert resp.status_code == 200
    markup = resp.body.decode("utf-8")
    assert "Alice Adams" in markup
    assert "/v1/explorer/official/ce-a" in markup
    assert "Rent Ordinance" in markup


def test_jurisdiction_page_404() -> None:
    assert _service().serve_jurisdiction_page("nowhere").status_code == 404


def test_store_built_once() -> None:
    calls = {"n": 0}

    def factory() -> GraphStore:
        calls["n"] += 1
        return _store()

    svc = GraphService(store_factory=factory)
    svc.serve_jurisdictions()
    svc.serve_entities({})
    assert calls["n"] == 1  # lazily built and cached


def test_redundant_policy_areas_endpoint() -> None:
    svc = _service()
    data = _body(svc.serve_redundant_policy_areas({"min_bills": ["1"]}))
    assert any(pa["policy_area"] == "Housing" for pa in data["policy_areas"])


def test_reauthorizations_endpoint() -> None:
    def factory() -> GraphStore:
        store = GraphStore()
        for cid, name in [
            ("cb-1", "Older Americans Act Reauthorization of 2019"),
            ("cb-2", "Older Americans Act Reauthorization of 2023"),
        ]:
            store.add_node(
                Node(
                    cid,
                    "bill",
                    name,
                    (),
                    "2026-06-01T00:00:00Z",
                    (_prov("https://x/b"),),
                    "us-congress",
                )
            )
        return store

    data = _body(GraphService(store_factory=factory).serve_reauthorizations({"min_count": ["2"]}))
    assert data["count"] == 1
    assert data["clusters"][0]["count"] == 2


def test_duplicate_ordinances_endpoint() -> None:
    def factory() -> GraphStore:
        store = GraphStore()
        text = "plastic bag ban single use"
        for cid, juris in [("cb-oak", "oakland"), ("cb-sf", "sanfrancisco")]:
            store.add_node(
                Node(
                    cid,
                    "bill",
                    "Bag Ban",
                    (),
                    "2026-06-01T00:00:00Z",
                    (_prov("https://x/b"),),
                    juris,
                    dossier_embedding=_embed(text),
                )
            )
        return store

    data = _body(
        GraphService(store_factory=factory).serve_duplicate_ordinances({"threshold": ["0.9"]})
    )
    assert data["count"] == 1
    assert data["pairs"][0]["cross_jurisdiction"] is True


def test_donor_paths_endpoint_requires_term() -> None:
    assert _service().serve_donor_paths({}).status_code == 400


def test_donor_paths_endpoint() -> None:
    data = _body(_service().serve_donor_paths({"term": ["housing rent control"]}))
    assert data["term"] == "housing rent control"
    assert data["count"] >= 1
    assert data["paths"][0]["official"]["canonical_id"] == "ce-a"
