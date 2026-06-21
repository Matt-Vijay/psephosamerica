"""Tests for the graph query layer (filters, joins, multi-hop paths)."""

from __future__ import annotations

from src.query.graph_query import (
    bill_policy_areas,
    bills_in_policy_area,
    find_entities,
    path,
    path_as_dicts,
    voters_on,
    votes_of,
)
from src.query.graph_store import Edge, GraphStore, Node, Provenance


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _fixture() -> GraphStore:
    store = GraphStore()
    for pid, name, juris in [
        ("ce-a", "Alice Adams", "oakland"),
        ("ce-b", "Bob Brown", "oakland"),
        ("ce-c", "Carol Congress", "us-congress"),
    ]:
        store.add_node(
            Node(
                pid,
                "person",
                name,
                (f"legistar:{juris}:1",),
                "2026-06-01T00:00:00Z",
                (_prov("https://x/p"),),
                juris,
            )
        )
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Rent Ordinance",
            ("legistar:oakland:9",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/b1"),),
            "oakland",
        )
    )
    store.add_node(
        Node(
            "cb-2",
            "bill",
            "Tax Act",
            ("congress:118-hr-1",),
            "2026-06-01T00:00:00Z",
            (_prov("https://x/b2"),),
            "us-congress",
        )
    )
    store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov("https://x/v1")))
    store.add_edge(Edge("vote", "ce-b", "cb-1", {"choice": "nay"}, _prov("https://x/v2")))
    store.add_edge(Edge("vote", "ce-c", "cb-2", {"choice": "yea"}, _prov("https://x/v3")))
    store.add_edge(
        Edge("policy_area", "cb-1", "csub-h", {"name": "Housing"}, _prov("https://x/t1"))
    )
    store.add_edge(
        Edge("policy_area", "cb-2", "csub-t", {"name": "Taxation"}, _prov("https://x/t2"))
    )
    return store


def test_find_entities_filters_and_cites() -> None:
    store = _fixture()
    rows = find_entities(store, entity_type="person", jurisdiction="oakland")
    assert {r.payload["canonical_id"] for r in rows} == {"ce-a", "ce-b"}
    for r in rows:
        assert r.citation["source_url"] is not None
    contains = find_entities(store, contains="carol")
    assert len(contains) == 1 and contains[0].payload["canonical_id"] == "ce-c"


def test_find_entities_limit() -> None:
    store = _fixture()
    assert len(find_entities(store, entity_type="person", limit=1)) == 1


def test_votes_of_join() -> None:
    store = _fixture()
    rows = votes_of(store, "ce-a")
    assert len(rows) == 1
    assert rows[0].payload["bill_name"] == "Rent Ordinance"
    assert rows[0].payload["choice"] == "yea"
    assert rows[0].citation["source_url"] == "https://x/v1"


def test_voters_on_join() -> None:
    store = _fixture()
    rows = voters_on(store, "cb-1")
    choices = {r.payload["person_name"]: r.payload["choice"] for r in rows}
    assert choices == {"Alice Adams": "yea", "Bob Brown": "nay"}


def test_policy_area_joins() -> None:
    store = _fixture()
    areas = bill_policy_areas(store, "cb-1")
    assert any(a.payload["name"] == "Housing" for a in areas)
    bills = bills_in_policy_area(store, "Taxation")
    assert len(bills) == 1 and bills[0].payload["bill_id"] == "cb-2"
    assert bills[0].citation["source_url"] == "https://x/t2"


def test_multi_hop_path_official_to_topic() -> None:
    store = _fixture()
    # ce-a -> (vote) -> cb-1 -> (policy_area) -> csub-h
    paths = path(store, "ce-a", max_hops=3)
    # at least one chain reaches the Housing topic
    reached = [p for p in paths if p and p[-1].to_node.get("canonical_id") == "csub-h"]
    assert reached
    chain = path_as_dicts(reached[0])
    assert chain[0]["edge_type"] == "vote"
    assert chain[-1]["attributes"]["name"] == "Housing"
    for step in chain:
        assert step["citation"]["source_url"] is not None


def test_path_to_target() -> None:
    store = _fixture()
    paths = path(store, "ce-a", target_id="cb-1", max_hops=2, edge_types=frozenset({"vote"}))
    assert len(paths) == 1
    assert paths[0][-1].to_node["canonical_id"] == "cb-1"


def test_path_no_loops() -> None:
    store = _fixture()
    # adding a back-edge must not cause infinite traversal
    store.add_edge(Edge("vote", "cb-1", "ce-a", {"choice": "x"}, _prov("https://x/loop")))
    paths = path(store, "ce-a", max_hops=5)
    assert all(len(p) <= 5 for p in paths)
