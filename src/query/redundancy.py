"""Redundancy / waste reasoning applications over the connected graph.

Deliverables #4 (the LOCUS flagship) and #5 (federal waste/redundancy) of the
V8 pivot. These are the first reasoning *applications* on top of the substrate
-- the analyses LOCUS itself could not do, because they require the relational
graph (officials <-> votes <-> bills/ordinances <-> topics <-> jurisdictions),
not just the ordinance text.

All numpy. All cited: every finding carries the source anchors of the entities
it groups.

Analyses:

* :func:`redundant_bills_by_policy_area` (#5) -- federal bills that pile up in
  the same CRS policy area, surfacing topical over-legislation.
* :func:`reauthorization_clusters` (#5) -- measures with near-identical titles
  across congresses (the "reauthorized N times" pattern).
* :func:`near_duplicate_ordinances` (#4) -- cross-jurisdiction near-duplicate
  detection via cosine over the contract's ``dossier_embedding`` vectors: the
  same ordinance text adopted by many cities, which is exactly the
  model-legislation-diffusion signal LOCUS stopped short of.
* :func:`donor_to_vote_paths` (#5) -- given a donor employer/occupation term,
  the officials whose dossier matches and the bills they voted on (the
  donor->vote path, fully cited), as far as the current graph supports.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from src.query.graph_store import GraphStore, Node

# Tokens we strip when normalizing a bill/ordinance title for clustering.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_NUM_RE = re.compile(r"\b(?:no\.?|number|ordinance|resolution|res|hr|sb|ab|s|h)\s*\.?\s*\d+\b")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """Title normalized for near-duplicate grouping (drop years, numbers, punct)."""
    lowered = title.lower()
    lowered = _YEAR_RE.sub("", lowered)
    lowered = _NUM_RE.sub("", lowered)
    lowered = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", lowered).strip()


def _citation(node: Node) -> dict[str, str | None]:
    if node.citations:
        return node.citations[0].as_citation()
    return {"source_url": None, "content_sha256": None, "known_at": node.known_at}


# -- #5: federal redundancy --------------------------------------------


@dataclass(frozen=True)
class PolicyAreaRedundancy:
    """A CRS policy area and the bills crowding into it (cited)."""

    policy_area: str
    bill_count: int
    bills: tuple[dict[str, object], ...]


def redundant_bills_by_policy_area(
    store: GraphStore, *, min_bills: int = 5, top_areas: int = 25, sample_per_area: int = 10
) -> list[PolicyAreaRedundancy]:
    """Group federal bills by CRS policy area; rank areas by bill volume.

    Surfaces topical over-legislation: areas where many distinct bills target
    the same policy space. Each bill in the sample is cited to its source.
    """
    by_area: dict[str, list[str]] = defaultdict(list)
    for edge in store.edges:
        if edge.edge_type != "policy_area":
            continue
        name = edge.attributes.get("name")
        if name:
            by_area[name].append(edge.src_id)
    findings: list[PolicyAreaRedundancy] = []
    for area, bill_ids in by_area.items():
        unique = list(dict.fromkeys(bill_ids))
        if len(unique) < min_bills:
            continue
        sample: list[dict[str, object]] = []
        for bid in unique[:sample_per_area]:
            node = store.node(bid)
            sample.append(
                {
                    "bill_id": bid,
                    "bill_name": node.display_name if node else None,
                    "citation": _citation(node) if node else None,
                }
            )
        findings.append(
            PolicyAreaRedundancy(policy_area=area, bill_count=len(unique), bills=tuple(sample))
        )
    findings.sort(key=lambda f: (-f.bill_count, f.policy_area))
    return findings[:top_areas]


@dataclass(frozen=True)
class ReauthorizationCluster:
    """A normalized title shared by many measures across time (cited)."""

    normalized_title: str
    count: int
    measures: tuple[dict[str, object], ...]


def reauthorization_clusters(
    store: GraphStore, *, min_count: int = 2, top: int = 50
) -> list[ReauthorizationCluster]:
    """Bills/ordinances whose titles (minus year/number) collide across the corpus.

    Captures the "reauthorized / re-introduced N times" pattern: the same Act
    appearing in multiple congresses or the same ordinance across cities. Each
    member is cited.
    """
    by_title: dict[str, list[Node]] = defaultdict(list)
    for node in store.nodes_of_type("bill"):
        norm = _normalize_title(node.display_name)
        if len(norm) < 12:  # skip stubs / too-generic titles
            continue
        by_title[norm].append(node)
    clusters: list[ReauthorizationCluster] = []
    for norm, nodes in by_title.items():
        if len(nodes) < min_count:
            continue
        members: tuple[dict[str, object], ...] = tuple(
            {
                "bill_id": n.canonical_id,
                "display_name": n.display_name,
                "jurisdiction": n.jurisdiction,
                "citation": _citation(n),
            }
            for n in nodes[:20]
        )
        clusters.append(
            ReauthorizationCluster(normalized_title=norm, count=len(nodes), measures=members)
        )
    clusters.sort(key=lambda c: (-c.count, c.normalized_title))
    return clusters[:top]


# -- #4: cross-jurisdiction near-duplicate ordinances ------------------


@dataclass(frozen=True)
class DuplicatePair:
    """Two bills/ordinances whose embeddings are near-duplicate (cited)."""

    similarity: float
    a: dict[str, object]
    b: dict[str, object]
    cross_jurisdiction: bool


def near_duplicate_ordinances(
    store: GraphStore,
    *,
    threshold: float = 0.92,
    max_pairs: int = 200,
    cross_jurisdiction_only: bool = True,
) -> list[DuplicatePair]:
    """Detect near-duplicate bills/ordinances via cosine over dossier embeddings.

    This is the LOCUS flagship: the same ordinance text adopted across many
    jurisdictions (model-legislation diffusion). LOCUS stopped at text; here we
    additionally attach each duplicate's jurisdiction so a hit is a genuine
    cross-city diffusion claim, fully cited. The store must be built with
    ``load_embeddings=True``.

    Pairwise cosine is O(n^2); callers should pass a focused store (e.g. one
    jurisdiction family) for large corpora. Vectors are L2-normalized in the
    contract, so cosine is a single matrix product.
    """
    bills = [n for n in store.nodes_of_type("bill") if n.dossier_embedding is not None]
    if len(bills) < 2:
        return []
    matrix = np.vstack([n.dossier_embedding for n in bills])  # type: ignore[misc]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    sims = unit @ unit.T
    pairs: list[DuplicatePair] = []
    n = len(bills)
    for i in range(n):
        row = sims[i]
        for j in range(i + 1, n):
            score = float(row[j])
            if score < threshold:
                continue
            cross = bills[i].jurisdiction != bills[j].jurisdiction
            if cross_jurisdiction_only and not cross:
                continue
            pairs.append(
                DuplicatePair(
                    similarity=score,
                    a=_dup_brief(bills[i]),
                    b=_dup_brief(bills[j]),
                    cross_jurisdiction=cross,
                )
            )
    pairs.sort(key=lambda p: -p.similarity)
    return pairs[:max_pairs]


def _dup_brief(node: Node) -> dict[str, object]:
    return {
        "bill_id": node.canonical_id,
        "display_name": node.display_name,
        "jurisdiction": node.jurisdiction,
        "citation": _citation(node),
    }


# -- #5: donor -> vote path --------------------------------------------


@dataclass(frozen=True)
class DonorVotePath:
    """An official matched to a donor-context term, plus the bills they voted on."""

    official: dict[str, object]
    match_score: float
    votes: tuple[dict[str, object], ...]


def donor_to_vote_paths(
    store: GraphStore,
    donor_term: str,
    *,
    top_officials: int = 10,
    votes_per_official: int = 10,
    embedder_embed: object = None,
) -> list[DonorVotePath]:
    """Officials whose dossier matches a donor-context term, with their cited votes.

    Builds the donor -> official -> vote path the blueprint calls for, to the
    extent the current graph supports it: the donor side is matched by embedding
    similarity of the term against official dossiers (FEC donor-to-official edges
    land with Track A's later releases; this automatically sharpens then). Every
    vote in the path is cited.

    ``embedder_embed`` is an injected ``(str) -> list[float]`` (defaults to the
    query embedder) so this stays numpy-only and testable.
    """
    from src.query.graph_query import votes_of
    from src.query.query_embedder import QueryEmbedder

    embed = embedder_embed if callable(embedder_embed) else QueryEmbedder().embed
    officials = [n for n in store.nodes_of_type("person") if n.dossier_embedding is not None]
    if not officials:
        return []
    matrix = np.vstack([n.dossier_embedding for n in officials])  # type: ignore[misc]
    query = np.asarray(embed(donor_term), dtype=np.float64)
    if query.shape[0] != matrix.shape[1]:
        return []
    qnorm = np.linalg.norm(query)
    if qnorm == 0:
        return []
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms[row_norms == 0] = 1.0
    scores = (matrix @ query) / (row_norms * qnorm)
    order = np.argsort(-scores)[:top_officials]
    paths: list[DonorVotePath] = []
    for idx in order:
        node = officials[int(idx)]
        vote_rows = votes_of(store, node.canonical_id, limit=votes_per_official)
        paths.append(
            DonorVotePath(
                official={
                    "canonical_id": node.canonical_id,
                    "display_name": node.display_name,
                    "jurisdiction": node.jurisdiction,
                    "citation": _citation(node),
                },
                match_score=float(scores[int(idx)]),
                votes=tuple(v.as_dict() for v in vote_rows),
            )
        )
    return paths


__all__ = [
    "PolicyAreaRedundancy",
    "ReauthorizationCluster",
    "DuplicatePair",
    "DonorVotePath",
    "redundant_bills_by_policy_area",
    "reauthorization_clusters",
    "near_duplicate_ordinances",
    "donor_to_vote_paths",
]
