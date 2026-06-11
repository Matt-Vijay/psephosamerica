"""A deterministic, untrained RGCN-lite structural embedding.

The degree-count :func:`~src.graph.enrichment.structural_features.\
structural_feature_vector` gives every node a vector, but it is zero for an
isolated node and ignores *who* the neighbours are. This module computes a
relational message-passing embedding over the leakage-safe ``as_of`` graph:

* each node is seeded with a deterministic identity+type base vector (so even an
  isolated node has a non-zero, normalized embedding — no zero-vector fallback);
* ``hops`` rounds of message passing propagate neighbour information, projecting
  each neighbour through a fixed per-relation matrix (the "R" in RGCN) before
  mean-aggregating, then ``tanh`` + L2-normalizing.

It is **untrained but fully deterministic** — all randomness comes from BLAKE2b
seeds (node ID, relation name), so the same graph + ``as_of`` always yields the
same embeddings, and the result is replayable. A trained RGCN/HGT (GPU) would
replace the fixed projections without changing the interface. Pure numpy; no
torch.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np

from src.graph.knowledge_graph import KnowledgeGraph

_DEFAULT_DIM = 64
_DEFAULT_HOPS = 2


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0.0 else vector


def rgcn_lite_embeddings(
    graph: KnowledgeGraph,
    *,
    as_of: datetime,
    dim: int = _DEFAULT_DIM,
    hops: int = _DEFAULT_HOPS,
) -> dict[str, list[float]]:
    """Deterministic relational message-passing embeddings for the ``as_of`` graph."""
    snapshot = graph.as_of(as_of)
    node_ids = [node.canonical_id for node in snapshot.nodes]
    present = set(node_ids)

    state: dict[str, np.ndarray] = {}
    for node in snapshot.nodes:
        rng = np.random.default_rng(_seed(f"node|{node.entity_type}|{node.canonical_id}"))
        state[node.canonical_id] = _normalize(rng.standard_normal(dim))

    edges = [e for e in snapshot.edges if e.src_id in present and e.dst_id in present]
    relation_matrices: dict[str, np.ndarray] = {}

    def projection(relation: str) -> np.ndarray:
        matrix = relation_matrices.get(relation)
        if matrix is None:
            rng = np.random.default_rng(_seed(f"rel|{relation}"))
            matrix = rng.standard_normal((dim, dim)) / np.sqrt(dim)
            relation_matrices[relation] = matrix
        return matrix

    for _ in range(hops):
        messages = {node_id: np.zeros(dim) for node_id in node_ids}
        degree = dict.fromkeys(node_ids, 0)
        for edge in edges:
            weight = projection(edge.edge_type)
            messages[edge.src_id] += weight @ state[edge.dst_id]
            messages[edge.dst_id] += weight @ state[edge.src_id]
            degree[edge.src_id] += 1
            degree[edge.dst_id] += 1
        next_state: dict[str, np.ndarray] = {}
        for node_id in node_ids:
            if degree[node_id] > 0:
                combined = np.tanh(state[node_id] + messages[node_id] / degree[node_id])
                next_state[node_id] = _normalize(combined)
            else:
                next_state[node_id] = state[node_id]
        state = next_state

    return {node_id: [float(value) for value in state[node_id]] for node_id in node_ids}
