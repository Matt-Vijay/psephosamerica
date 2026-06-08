"""The entity-resolution linker: block, score, cluster, and queue for review.

:func:`resolve` takes a flat list of source records and returns a
:class:`ResolutionResult`:

* **clusters** — every record assigned to exactly one canonical entity, each
  carrying the ``match`` edges that merged it (the per-merge audit trail the
  blueprint requires). Singletons get their own canonical entity, so *every*
  record maps to a canonical ID (a definition-of-done criterion).
* **review_queue** — the ``possible`` edges that are too uncertain to auto-merge
  and too plausible to auto-reject; these are the human-in-the-loop work items.
  Disputed pairs persist here as separate possibilities rather than being
  force-collapsed.

Blocking keeps comparison from going quadratic: records are only scored when
they share a name block *or* an external-id block (so a strong shared FEC /
bioguide ID pulls together records whose names block apart).

Merging is the transitive closure of ``match`` edges (union-find). Conflict
edges (same-namespace, disjoint IDs) simply never union — a fuller
conflict-aware correlation-clustering pass is a later, stateful slice.

``canonical_id`` is content-addressed over the cluster's sorted membership: it
is deterministic for a given membership but not stable as membership grows.
Persistent ID assignment against a prior resolution is a separate stateful
concern handled by the live graph store.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.graph.entity_resolution.ids import stable_id
from src.graph.entity_resolution.names import normalize_name_token
from src.graph.entity_resolution.records import SourceRecord
from src.graph.entity_resolution.scoring import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    MatchScore,
    MatchThresholds,
    MatchWeights,
    score_pair,
)


@dataclass(frozen=True)
class MatchEdge:
    """One scored, directed-by-id comparison between two records."""

    left_record_id: str
    right_record_id: str
    score: MatchScore


@dataclass(frozen=True)
class ResolvedCluster:
    """A canonical entity: its member records plus the merge audit trail."""

    canonical_id: str
    record_ids: tuple[str, ...]
    match_edges: tuple[MatchEdge, ...]


@dataclass(frozen=True)
class ResolutionResult:
    """The full output of one resolution pass over a record set."""

    clusters: tuple[ResolvedCluster, ...]
    review_queue: tuple[MatchEdge, ...]


def blocking_keys_for(record: SourceRecord) -> set[str]:
    """The candidate-generation keys a record belongs to.

    A name block (entity-type-specific) plus one block per external ID, so
    records that agree on a strong ID are compared even across name blocks.
    """
    if record.entity_type == "person":
        name = record.person_name()
        assert name is not None
        keys = {f"person|{name.blocking_key()}"}
    else:
        keys = {f"org|{normalize_name_token(record.display_name)}"}
    keys.update(f"extid|{ext.canonical_key}" for ext in record.external_ids)
    return keys


def candidate_pairs(records: Iterable[SourceRecord]) -> list[tuple[str, str]]:
    """Unordered (id_a < id_b) record-id pairs that share at least one block."""
    by_block: dict[str, set[str]] = {}
    for record in records:
        for key in blocking_keys_for(record):
            by_block.setdefault(key, set()).add(record.record_id)
    pairs: set[tuple[str, str]] = set()
    for ids in by_block.values():
        ordered = sorted(ids)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                pairs.add((ordered[i], ordered[j]))
    return sorted(pairs)


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            # Attach the lexicographically larger root under the smaller for
            # deterministic representatives independent of union order.
            low, high = sorted((root_a, root_b))
            self._parent[high] = low


def resolve(
    records: Iterable[SourceRecord],
    *,
    weights: MatchWeights = DEFAULT_WEIGHTS,
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
) -> ResolutionResult:
    """Block, score, cluster matches, and queue possibles for review."""
    by_id: dict[str, SourceRecord] = {}
    for record in records:
        by_id.setdefault(record.record_id, record)

    union_find = _UnionFind(by_id)
    match_edges: list[MatchEdge] = []
    review: list[MatchEdge] = []
    for left_id, right_id in candidate_pairs(by_id.values()):
        score = score_pair(by_id[left_id], by_id[right_id], weights=weights, thresholds=thresholds)
        edge = MatchEdge(left_id, right_id, score)
        if score.decision == "match":
            union_find.union(left_id, right_id)
            match_edges.append(edge)
        elif score.decision == "possible":
            review.append(edge)

    members_by_root: dict[str, list[str]] = {}
    for record_id in by_id:
        members_by_root.setdefault(union_find.find(record_id), []).append(record_id)

    clusters: list[ResolvedCluster] = []
    for members in members_by_root.values():
        member_ids = tuple(sorted(members))
        member_set = set(member_ids)
        edges = tuple(
            edge
            for edge in sorted(match_edges, key=lambda e: (e.left_record_id, e.right_record_id))
            if edge.left_record_id in member_set
        )
        clusters.append(ResolvedCluster(stable_id(list(member_ids), "ce"), member_ids, edges))

    clusters.sort(key=lambda cluster: cluster.record_ids)
    review.sort(key=lambda edge: (edge.left_record_id, edge.right_record_id))
    return ResolutionResult(tuple(clusters), tuple(review))
