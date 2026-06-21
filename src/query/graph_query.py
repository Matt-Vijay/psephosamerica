"""Query layer over :class:`~src.query.graph_store.GraphStore`.

Deliverable #1 of the V8 platform pivot: a queryable view of the connected
graph -- **filters, joins, and multi-hop paths** -- where *every* returned
row carries its provenance (``source_url`` + ``content_sha256``) and
``known_at``. The query primitives are deliberately small and composable so
the HTTP layer (:mod:`src.api.graph_http`) and the reasoning layer
(:mod:`src.query.graph_rag`) can both build on them.

Supported shapes:

* ``find_entities`` -- filter people/bills by type, jurisdiction, and a
  substring over display name / external ids.
* ``votes_of`` / ``voters_on`` -- the person<->bill join, both directions.
* ``bill_policy_areas`` / ``bills_in_policy_area`` -- the bill<->topic join.
* ``path`` -- a bounded multi-hop walk (official -> vote -> bill -> subject,
  etc.) returning a fully-cited chain.

Nothing here mutates the store or hits the network; results are plain dicts
ready to JSON-serialise.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from src.query.graph_store import Edge, GraphStore, Node

# Edge types that connect a person to a bill (the core legislative join).
VOTE_EDGE_TYPES = frozenset({"vote"})


@dataclass(frozen=True)
class QueryResult:
    """A single cited row: the payload plus its provenance/known_at."""

    payload: dict[str, object]
    citation: dict[str, str | None]

    def as_dict(self) -> dict[str, object]:
        return {**self.payload, "citation": self.citation}


def _node_brief(node: Node) -> dict[str, object]:
    return {
        "canonical_id": node.canonical_id,
        "entity_type": node.entity_type,
        "display_name": node.display_name,
        "jurisdiction": node.jurisdiction,
        "external_ids": list(node.external_ids),
        "known_at": node.known_at,
    }


def _node_citation(node: Node) -> dict[str, str | None]:
    if node.citations:
        return node.citations[0].as_citation()
    return {"source_url": None, "content_sha256": None, "known_at": node.known_at}


def find_entities(
    store: GraphStore,
    *,
    entity_type: str | None = None,
    jurisdiction: str | None = None,
    contains: str | None = None,
    limit: int = 50,
) -> list[QueryResult]:
    """Filter nodes by type/jurisdiction/substring; each row is cited."""
    needle = contains.lower() if contains else None
    results: list[QueryResult] = []
    for node in store.nodes.values():
        if entity_type is not None and node.entity_type != entity_type:
            continue
        if jurisdiction is not None and node.jurisdiction != jurisdiction:
            continue
        if needle is not None:
            haystack = (node.display_name + " " + " ".join(node.external_ids)).lower()
            if needle not in haystack:
                continue
        results.append(QueryResult(payload=_node_brief(node), citation=_node_citation(node)))
        if len(results) >= limit:
            break
    return results


def votes_of(store: GraphStore, person_id: str, *, limit: int = 100) -> list[QueryResult]:
    """Every bill this person voted on, with the choice and the vote's citation."""
    results: list[QueryResult] = []
    for edge, bill in store.neighbors(person_id, direction="out"):
        if edge.edge_type not in VOTE_EDGE_TYPES:
            continue
        payload: dict[str, object] = {
            "person_id": person_id,
            "bill_id": edge.dst_id,
            "bill_name": bill.display_name if bill else None,
            "jurisdiction": bill.jurisdiction if bill else None,
            "choice": edge.attributes.get("choice"),
            "edge_type": edge.edge_type,
        }
        results.append(QueryResult(payload=payload, citation=edge.provenance.as_citation()))
        if len(results) >= limit:
            break
    return results


def voters_on(store: GraphStore, bill_id: str, *, limit: int = 500) -> list[QueryResult]:
    """Every person who voted on this bill, with their choice and citation."""
    results: list[QueryResult] = []
    for edge, person in store.neighbors(bill_id, direction="in"):
        if edge.edge_type not in VOTE_EDGE_TYPES:
            continue
        payload: dict[str, object] = {
            "bill_id": bill_id,
            "person_id": edge.src_id,
            "person_name": person.display_name if person else None,
            "jurisdiction": person.jurisdiction if person else None,
            "choice": edge.attributes.get("choice"),
        }
        results.append(QueryResult(payload=payload, citation=edge.provenance.as_citation()))
        if len(results) >= limit:
            break
    return results


def bill_policy_areas(store: GraphStore, bill_id: str) -> list[QueryResult]:
    """The CRS policy area(s) and legislative subjects attached to a bill."""
    results: list[QueryResult] = []
    for edge in store.out_edges(bill_id):
        if edge.edge_type not in ("policy_area", "legislative_subject"):
            continue
        payload: dict[str, object] = {
            "bill_id": bill_id,
            "edge_type": edge.edge_type,
            "name": edge.attributes.get("name"),
        }
        results.append(QueryResult(payload=payload, citation=edge.provenance.as_citation()))
    return results


def bills_in_policy_area(
    store: GraphStore, policy_area: str, *, limit: int = 200
) -> list[QueryResult]:
    """Every bill tagged with a given CRS policy-area name (cited)."""
    needle = policy_area.lower()
    results: list[QueryResult] = []
    for edge in store.edges:
        if edge.edge_type != "policy_area":
            continue
        name = edge.attributes.get("name", "")
        if name.lower() != needle:
            continue
        bill = store.node(edge.src_id)
        payload: dict[str, object] = {
            "bill_id": edge.src_id,
            "bill_name": bill.display_name if bill else None,
            "policy_area": name,
        }
        results.append(QueryResult(payload=payload, citation=edge.provenance.as_citation()))
        if len(results) >= limit:
            break
    return results


# -- multi-hop path -----------------------------------------------------


@dataclass(frozen=True)
class PathStep:
    """One hop in a cited path: the edge traversed and the node landed on."""

    edge_type: str
    attributes: dict[str, str]
    to_node: dict[str, object]
    citation: dict[str, str | None]


def _edge_step(store: GraphStore, edge: Edge, landed_id: str) -> PathStep:
    landed = store.node(landed_id)
    to_node: dict[str, object] = _node_brief(landed) if landed else {"canonical_id": landed_id}
    return PathStep(
        edge_type=edge.edge_type,
        attributes=dict(edge.attributes),
        to_node=to_node,
        citation=edge.provenance.as_citation(),
    )


def path(
    store: GraphStore,
    start_id: str,
    *,
    max_hops: int = 3,
    edge_types: frozenset[str] | None = None,
    target_id: str | None = None,
    max_paths: int = 25,
) -> list[list[PathStep]]:
    """Bounded BFS from ``start_id`` returning fully-cited hop chains.

    With ``target_id`` set it returns only paths that reach that node (e.g.
    "how is this official connected to that bill?"); otherwise it enumerates
    every reachable chain up to ``max_hops``. ``edge_types`` restricts which
    relationships may be traversed (default: all). Visited-set keeps it from
    looping.
    """
    found: list[list[PathStep]] = []
    queue: deque[tuple[str, list[PathStep], frozenset[str]]] = deque()
    queue.append((start_id, [], frozenset({start_id})))
    while queue and len(found) < max_paths:
        current, steps, seen = queue.popleft()
        if len(steps) >= max_hops:
            continue
        for edge in store.out_edges(current):
            if edge_types is not None and edge.edge_type not in edge_types:
                continue
            if edge.dst_id in seen:
                continue
            step = _edge_step(store, edge, edge.dst_id)
            new_steps = [*steps, step]
            if target_id is None or edge.dst_id == target_id:
                found.append(new_steps)
                if len(found) >= max_paths:
                    break
            if edge.dst_id in store.nodes:
                queue.append((edge.dst_id, new_steps, seen | {edge.dst_id}))
    return found


def path_as_dicts(steps: list[PathStep]) -> list[dict[str, object]]:
    return [
        {
            "edge_type": s.edge_type,
            "attributes": s.attributes,
            "to_node": s.to_node,
            "citation": s.citation,
        }
        for s in steps
    ]


__all__ = [
    "QueryResult",
    "PathStep",
    "find_entities",
    "votes_of",
    "voters_on",
    "bill_policy_areas",
    "bills_in_policy_area",
    "path",
    "path_as_dicts",
]
