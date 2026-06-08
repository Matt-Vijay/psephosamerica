"""A deterministic, leakage-safe structural feature vector per graph node.

The blueprint's structural embedding is a learned RGCN/HGT capturing "who is
this person similar to in network position." That needs a GNN and a training
loop; this module is the deterministic baseline that stands in until then — and
the leakage-safe graph-feature assembly the learned model will also consume.

For an entity as of time ``t``, :func:`structural_feature_vector` returns a
fixed-length vector of network-position counts computed from the ``as_of(t)``
snapshot — per-edge-type out/in degree plus totals and distinct-neighbor counts
— so nothing observed after ``t`` leaks in. The layout is fixed and introspect-
able via :func:`structural_feature_index`, so the vector can populate the output
contract's ``structural_embedding`` today and be swapped for learned embeddings
later without changing consumers' indexing assumptions.
"""

from __future__ import annotations

from datetime import datetime

from src.graph.edges import RECOMMENDED_EDGE_TYPES
from src.graph.knowledge_graph import KnowledgeGraph

STRUCTURAL_EDGE_VOCAB: tuple[str, ...] = tuple(sorted(RECOMMENDED_EDGE_TYPES))

_AGGREGATE_FEATURES = (
    "total_out",
    "total_in",
    "distinct_out_neighbors",
    "distinct_in_neighbors",
)


def structural_feature_index() -> dict[str, int]:
    """The name -> position map describing the vector layout."""
    index: dict[str, int] = {}
    position = 0
    for edge_type in STRUCTURAL_EDGE_VOCAB:
        index[f"{edge_type}_out"] = position
        index[f"{edge_type}_in"] = position + 1
        position += 2
    for name in _AGGREGATE_FEATURES:
        index[name] = position
        position += 1
    return index


def structural_feature_vector(
    graph: KnowledgeGraph,
    canonical_id: str,
    *,
    as_of: datetime,
) -> list[float] | None:
    """Network-position features for an entity as of ``as_of`` (``None`` if unknown)."""
    snapshot = graph.as_of(as_of)
    if snapshot.node(canonical_id) is None:
        return None

    out_edges = snapshot.edges_from(canonical_id)
    in_edges = snapshot.edges_to(canonical_id)
    out_by_type: dict[str, int] = {}
    in_by_type: dict[str, int] = {}
    for edge in out_edges:
        out_by_type[edge.edge_type] = out_by_type.get(edge.edge_type, 0) + 1
    for edge in in_edges:
        in_by_type[edge.edge_type] = in_by_type.get(edge.edge_type, 0) + 1

    vector: list[float] = []
    for edge_type in STRUCTURAL_EDGE_VOCAB:
        vector.append(float(out_by_type.get(edge_type, 0)))
        vector.append(float(in_by_type.get(edge_type, 0)))
    vector.append(float(len(out_edges)))
    vector.append(float(len(in_edges)))
    vector.append(float(len({edge.dst_id for edge in out_edges})))
    vector.append(float(len({edge.src_id for edge in in_edges})))
    return vector
