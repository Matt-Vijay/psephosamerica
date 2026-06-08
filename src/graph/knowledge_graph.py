"""An in-memory, time-travelable view of the heterogeneous knowledge graph.

:class:`KnowledgeGraph` holds resolved canonical entities (as published
:class:`~src.graph.contracts.EntityResolutionOutput` rows) and the
provenance-carrying :class:`~src.graph.edges.GraphEdge` relations between them,
with adjacency lookups and — the point of the whole provenance discipline — an
:meth:`~KnowledgeGraph.as_of` snapshot that returns exactly the nodes and edges
knowable at a past time ``t``. That snapshot is what a leakage-safe feature
build or backtest reads: nothing observed after ``t`` can appear in it.

This is the queryable read-model the live graph store (Neo4j / Memgraph) backs;
keeping it pure and deterministic lets every replay be tested.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from src.graph.contracts import EntityResolutionOutput
from src.graph.edges import GraphEdge, edges_known_as_of


def _require_aware(cutoff: datetime) -> datetime:
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    return cutoff


@dataclass(frozen=True)
class KnowledgeGraph:
    """Canonical entity nodes + provenance-carrying edges, queryable by time."""

    nodes: tuple[EntityResolutionOutput, ...]
    edges: tuple[GraphEdge, ...]

    def __init__(
        self,
        nodes: Iterable[EntityResolutionOutput],
        edges: Iterable[GraphEdge],
    ) -> None:
        node_tuple = tuple(nodes)
        seen: set[str] = set()
        for node in node_tuple:
            if node.canonical_id in seen:
                raise ValueError(f"duplicate node canonical_id: {node.canonical_id}")
            seen.add(node.canonical_id)
        object.__setattr__(self, "nodes", node_tuple)
        object.__setattr__(self, "edges", tuple(edges))

    def node(self, canonical_id: str) -> EntityResolutionOutput | None:
        """The node with this canonical ID, or ``None``."""
        for node in self.nodes:
            if node.canonical_id == canonical_id:
                return node
        return None

    def edges_from(self, canonical_id: str) -> tuple[GraphEdge, ...]:
        """Edges whose source is ``canonical_id``."""
        return tuple(edge for edge in self.edges if edge.src_id == canonical_id)

    def edges_to(self, canonical_id: str) -> tuple[GraphEdge, ...]:
        """Edges whose destination is ``canonical_id``."""
        return tuple(edge for edge in self.edges if edge.dst_id == canonical_id)

    def edges_of_type(self, edge_type: str) -> tuple[GraphEdge, ...]:
        """Edges of a given (normalized) relation type."""
        return tuple(edge for edge in self.edges if edge.edge_type == edge_type)

    def neighbors(self, canonical_id: str) -> frozenset[str]:
        """All canonical IDs directly connected to ``canonical_id`` (either way)."""
        out = {edge.dst_id for edge in self.edges if edge.src_id == canonical_id}
        out |= {edge.src_id for edge in self.edges if edge.dst_id == canonical_id}
        return frozenset(out)

    def as_of(self, cutoff: datetime) -> KnowledgeGraph:
        """Return the leakage-safe snapshot of nodes and edges knowable by ``cutoff``."""
        aware = _require_aware(cutoff)
        nodes = tuple(node for node in self.nodes if node.known_at <= aware)
        return KnowledgeGraph(nodes=nodes, edges=edges_known_as_of(self.edges, aware))
