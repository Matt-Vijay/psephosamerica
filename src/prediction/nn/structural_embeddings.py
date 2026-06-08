"""RGCN-lite structural embeddings over the ontology graph (numpy).

OVERALL_GOAL.md asks for a graph-derived structural embedding capturing "who is
this person similar to in network position," to sit alongside the LLM dossier
embedding in the cross-attention transformer. This computes one by relational
message-passing (the RGCN propagation rule, Schlichtkrull et al.) over the
existing typed ontology graph -- so it is validatable against data the repo
already produces, without waiting on Track A's dossier embeddings.

It is *untrained*: identical initial node features and shared, seeded
per-relation weight matrices make each node's embedding a deterministic
function of its rooted relational neighborhood. Structurally-isomorphic nodes
therefore receive equal embeddings -- a structural encoding, not a learned one.
Training (the multi-task heads) plugs in later by replacing the fixed weights
with learned ones; the propagation contract is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

_FORWARD = ">"
_INVERSE = "<"


@dataclass(frozen=True)
class StructuralEdge:
    """A typed directed edge between two string node keys."""

    relation: str
    source: str
    target: str


@dataclass(frozen=True)
class StructuralEmbeddings:
    """Fixed-dimension structural embeddings keyed by node key."""

    embeddings: dict[str, Array]
    dim: int

    def get(self, node_key: str) -> Array | None:
        return self.embeddings.get(node_key)

    def similarity(self, left_key: str, right_key: str) -> float:
        left = self.embeddings.get(left_key)
        right = self.embeddings.get(right_key)
        if left is None or right is None:
            raise KeyError("both node keys must exist to compare")
        return float(np.dot(left, right))


def _normalize_rows(matrix: Array) -> Array:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    safe = np.where(norms > 0.0, norms, 1.0)
    normalized: Array = matrix / safe
    return normalized


def compute_structural_embeddings(
    edges: Iterable[StructuralEdge],
    *,
    dim: int = 32,
    num_layers: int = 2,
    seed: int = 0,
) -> StructuralEmbeddings:
    """Relational message-passing embeddings, deterministic given ``seed``.

    Each undirected relation contributes a forward and an inverse typed message
    channel (so endorsed and endorser positions are distinguished), plus a
    self-channel. Messages are degree-normalized per channel, summed, passed
    through ``tanh``, and the layer output is L2-normalized.
    """
    if dim <= 0:
        raise ValueError("dim must be positive")
    if num_layers <= 0:
        raise ValueError("num_layers must be positive")

    materialized = list(edges)
    node_keys = sorted(
        {edge.source for edge in materialized} | {edge.target for edge in materialized}
    )
    if not node_keys:
        return StructuralEmbeddings(embeddings={}, dim=dim)
    index_of = {key: position for position, key in enumerate(node_keys)}

    # gathers[channel][i] = list of neighbor indices j contributing to node i.
    gathers: dict[str, list[list[int]]] = {}

    def channel(name: str) -> list[list[int]]:
        if name not in gathers:
            gathers[name] = [[] for _ in node_keys]
        return gathers[name]

    for edge in materialized:
        source = index_of[edge.source]
        target = index_of[edge.target]
        channel(edge.relation + _FORWARD)[source].append(target)
        channel(edge.relation + _INVERSE)[target].append(source)

    rng = np.random.default_rng(seed)
    scale = 1.0 / np.sqrt(dim)
    self_weight: Array = rng.normal(size=(dim, dim)) * scale
    relation_weights = {name: rng.normal(size=(dim, dim)) * scale for name in sorted(gathers)}

    # Identical initial features: structure, not identity, drives the embedding.
    hidden: Array = np.ones((len(node_keys), dim)) * scale

    for _ in range(num_layers):
        messages: Array = hidden @ self_weight.T
        for name in sorted(gathers):
            weight = relation_weights[name]
            transformed: Array = hidden @ weight.T
            channel_message = np.zeros_like(hidden)
            for node_index, neighbors in enumerate(gathers[name]):
                if neighbors:
                    channel_message[node_index] = transformed[neighbors].mean(axis=0)
            messages = messages + channel_message
        hidden = _normalize_rows(np.tanh(messages))

    return StructuralEmbeddings(
        embeddings={key: hidden[index_of[key]] for key in node_keys},
        dim=dim,
    )


def structural_edges_from_ontology(ontology_edges: Iterable[object]) -> list[StructuralEdge]:
    """Adapt ``OntologyEdgePayload`` rows into structural edges.

    Node keys are ``f"{node_type}:{node_id}"`` and the relation is the
    ontology edge type, so the structural graph mirrors the live ontology.
    """
    structural: list[StructuralEdge] = []
    for edge in ontology_edges:
        subject = edge.subject  # type: ignore[attr-defined]
        target = edge.object  # type: ignore[attr-defined]
        structural.append(
            StructuralEdge(
                relation=str(edge.edge_type),  # type: ignore[attr-defined]
                source=f"{subject.node_type}:{subject.node_id}",
                target=f"{target.node_type}:{target.node_id}",
            )
        )
    return structural
