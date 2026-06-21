"""In-memory connected-graph store over Track A's export artifacts.

This is the read-side substrate the V8 platform pivot exposes: a single
queryable graph that joins canonical **people** and **bills/ordinances**
(from ``data/exports/contract_records``) to the **edges** Track A emits --
roll-call votes (federal Senate + municipal), bill policy areas and
legislative subjects, floor speeches, and news mentions -- every one of
which carries ``(source_url, content_sha256, known_at)`` provenance.

Design constraints honoured here:

* **Numpy only.** Embeddings, when loaded, arrive as ``float64`` vectors;
  cosine/retrieval lives in :mod:`src.query.graph_rag`.
* **Builds on the existing release.** Nothing here writes; it reads the
  files the parallel Track A agent regenerates, so it benefits automatically
  when new (e.g. LOCUS ordinance) data lands.
* **Memory-conscious.** The 1.8 GB ``records.jsonl`` is parsed for *metadata*
  (id, type, display name, external ids, known_at, source anchors) without
  retaining the per-entity embedding vectors unless ``load_embeddings`` is
  requested -- so the store fits comfortably in RAM for serving.
* **Every result is citable.** Entities keep their source anchors; edges keep
  their provenance. The query layer surfaces those on every row.

The store is deliberately a plain dataclass index (dicts + lists), not a
database -- it is fast to build from the exports and trivially testable with a
handful of synthetic rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

# Default on-disk locations of the Track A export artifacts.
CONTRACT_RECORDS = Path("data/exports/contract_records/records.jsonl")
MUNICIPAL_PERSONS = Path("data/exports/municipal/municipal_persons.jsonl")
MUNICIPAL_VOTES = Path("data/exports/municipal/municipal_vote_edges.jsonl")
SENATE_VOTES = Path("data/exports/govinfo_bills/senate_vote_edges.jsonl")
BILL_EDGES = Path("data/exports/govinfo_bills/bill_edges.jsonl")
# Money-out + lobbying org corpora and their edge sidecars (USASpending federal
# awards, Senate LDA lobbying). The recipient/agency/registrant/client org nodes
# live in their own contract corpora; their canonical ids are the edge endpoints,
# so the org corpora are loaded as node sources and the edge sidecars as edges.
USASPENDING_ORGS = Path("data/exports/usaspending/records.jsonl")
LDA_ORGS = Path("data/exports/lda/records.jsonl")
AWARD_EDGES = Path("data/exports/usaspending/award_edges.jsonl")
LOBBYING_EDGES = Path("data/exports/lda/lobbying_edges.jsonl")
# Floor-speech edges: the per-issue member -> Congressional Record feed plus the
# typed member -> bill feed (the latter is what the said-vs-voted and
# follow-the-money lenses join on). News mentions stay opt-in (heavy, lower value).
SPEECH_EDGES = Path("data/exports/govinfo_bills/crec_edges.jsonl")
SPEECH_BILL_EDGES = Path("data/exports/govinfo_bills/crec_speech_edges.jsonl")
NEWS_EDGES = Path("data/exports/govinfo_bills/gdelt_edges.jsonl")


@dataclass(frozen=True)
class Provenance:
    """The citation attached to a fact: where it came from and when known."""

    source_url: str
    content_sha256: str
    known_at: str
    valid_from: str | None = None
    valid_to: str | None = None

    def as_citation(self) -> dict[str, str | None]:
        return {
            "source_url": self.source_url,
            "content_sha256": self.content_sha256,
            "known_at": self.known_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


@dataclass(frozen=True)
class Node:
    """A canonical person or bill/ordinance, with its provenance anchors."""

    canonical_id: str
    entity_type: str  # "person" | "bill"
    display_name: str
    external_ids: tuple[str, ...]
    known_at: str
    citations: tuple[Provenance, ...]
    jurisdiction: str | None = None
    # Populated only when the store is built with load_embeddings=True.
    dossier_embedding: Array | None = None


@dataclass(frozen=True)
class Edge:
    """A directed, cited relationship between two nodes (or a node and a literal)."""

    edge_type: str  # "vote" | "policy_area" | "legislative_subject" | ...
    src_id: str
    dst_id: str
    attributes: dict[str, str]
    provenance: Provenance
    external_key: str | None = None


@dataclass
class GraphStore:
    """Indexed, in-memory view of the connected graph."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # Adjacency: src_id -> edges out; dst_id -> edges in.
    _out: dict[str, list[int]] = field(default_factory=dict)
    _in: dict[str, list[int]] = field(default_factory=dict)
    # external_id -> canonical_id (e.g. "bioguide:M001199" -> "ce-...").
    _by_external: dict[str, str] = field(default_factory=dict)

    # -- construction ---------------------------------------------------

    def add_node(self, node: Node) -> None:
        self.nodes[node.canonical_id] = node
        for external_id in node.external_ids:
            self._by_external[external_id] = node.canonical_id

    def add_edge(self, edge: Edge) -> None:
        index = len(self.edges)
        self.edges.append(edge)
        self._out.setdefault(edge.src_id, []).append(index)
        self._in.setdefault(edge.dst_id, []).append(index)

    # -- lookups --------------------------------------------------------

    def node(self, canonical_id: str) -> Node | None:
        return self.nodes.get(canonical_id)

    def resolve_external(self, external_id: str) -> Node | None:
        canonical = self._by_external.get(external_id)
        return self.nodes.get(canonical) if canonical else None

    def out_edges(self, canonical_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._out.get(canonical_id, ())]

    def in_edges(self, canonical_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._in.get(canonical_id, ())]

    def neighbors(
        self, canonical_id: str, *, edge_type: str | None = None, direction: str = "out"
    ) -> list[tuple[Edge, Node | None]]:
        """Adjacent (edge, node) pairs, optionally filtered by edge type."""
        if direction == "out":
            edges = self.out_edges(canonical_id)
            return [(e, self.nodes.get(e.dst_id)) for e in edges if _type_ok(e, edge_type)]
        edges = self.in_edges(canonical_id)
        return [(e, self.nodes.get(e.src_id)) for e in edges if _type_ok(e, edge_type)]

    def nodes_of_type(self, entity_type: str) -> Iterator[Node]:
        for node in self.nodes.values():
            if node.entity_type == entity_type:
                yield node

    def jurisdictions(self) -> dict[str, int]:
        """Map jurisdiction slug -> person count, sorted desc by count."""
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            if node.entity_type == "person" and node.jurisdiction:
                counts[node.jurisdiction] = counts.get(node.jurisdiction, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def __len__(self) -> int:
        return len(self.nodes)


def _type_ok(edge: Edge, edge_type: str | None) -> bool:
    return edge_type is None or edge.edge_type == edge_type


# -- jurisdiction inference --------------------------------------------


def jurisdiction_of(external_ids: Iterable[str]) -> str | None:
    """Infer a jurisdiction slug from an entity's external ids.

    Municipal ids look like ``legistar:chicago:328`` -> ``chicago``; federal
    Congress ids (``bioguide:...``, ``congress:...``) map to ``us-congress``;
    California ids (``ca_leginfo:...``) map to ``ca-legislature``; OpenStates
    person ids (``openstates:ocd-person/...``) lack a jurisdiction here so are
    left ``None``.
    """
    for external_id in external_ids:
        parts = external_id.split(":")
        prefix = parts[0]
        if prefix == "legistar" and len(parts) >= 3:
            return parts[1]
        if prefix in ("bioguide", "congress", "govinfo", "lis"):
            return "us-congress"
        if prefix in ("ca_leginfo", "ca_leginfo_seat"):
            return "ca-legislature"
    return None


def _provenance_from(anchor: dict[str, object]) -> Provenance:
    return Provenance(
        source_url=str(anchor.get("source_url", "")),
        content_sha256=str(anchor.get("content_sha256", "")),
        known_at=str(anchor.get("known_at", "")),
        valid_from=_opt_str(anchor.get("valid_from")),
        valid_to=_opt_str(anchor.get("valid_to")),
    )


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _node_from_record(record: dict[str, object], *, load_embeddings: bool) -> Node:
    raw_external = record.get("external_ids") or []
    external_ids = tuple(str(x) for x in raw_external) if isinstance(raw_external, list) else ()
    raw_anchors = record.get("source_anchors") or []
    anchors = raw_anchors if isinstance(raw_anchors, list) else []
    citations = tuple(_provenance_from(a) for a in anchors if isinstance(a, dict))
    embedding: Array | None = None
    if load_embeddings:
        raw = record.get("dossier_embedding")
        if isinstance(raw, list) and raw:
            embedding = np.asarray(raw, dtype=np.float64)
    return Node(
        canonical_id=str(record["canonical_id"]),
        entity_type=str(record["entity_type"]),
        display_name=str(record.get("display_name", "")),
        external_ids=external_ids,
        known_at=str(record.get("known_at", "")),
        citations=citations,
        jurisdiction=jurisdiction_of(external_ids),
        dossier_embedding=embedding,
    )


def _edge_from_record(record: dict[str, object]) -> Edge:
    prov = record.get("provenance", {})
    provenance = _provenance_from(prov if isinstance(prov, dict) else {})
    raw_attributes = record.get("attributes") or {}
    attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
    return Edge(
        edge_type=str(record["edge_type"]),
        src_id=str(record["src_id"]),
        dst_id=str(record["dst_id"]),
        attributes={str(k): str(v) for k, v in attributes.items()},
        provenance=provenance,
        external_key=_opt_str(record.get("external_key")),
    )


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_nodes_from(path: Path, *, load_embeddings: bool = False) -> Iterator[Node]:
    """Stream :class:`Node` objects from a contract/persons JSONL file."""
    for record in _iter_jsonl(path):
        if "entity_type" in record and "canonical_id" in record:
            yield _node_from_record(record, load_embeddings=load_embeddings)


def load_edges_from(path: Path) -> Iterator[Edge]:
    """Stream :class:`Edge` objects from an edges JSONL file."""
    for record in _iter_jsonl(path):
        if "edge_type" in record and "src_id" in record and "dst_id" in record:
            yield _edge_from_record(record)


def build_store(
    *,
    node_paths: Iterable[Path] = (
        CONTRACT_RECORDS,
        MUNICIPAL_PERSONS,
        USASPENDING_ORGS,
        LDA_ORGS,
    ),
    edge_paths: Iterable[Path] = (
        MUNICIPAL_VOTES,
        SENATE_VOTES,
        BILL_EDGES,
        AWARD_EDGES,
        LOBBYING_EDGES,
        SPEECH_BILL_EDGES,
    ),
    load_embeddings: bool = False,
    edge_limit: int | None = None,
) -> GraphStore:
    """Build a :class:`GraphStore` from the on-disk Track A exports.

    ``node_paths`` later in the iterable win on id collisions (so the richer
    contract record beats a bare municipal-person stub). Missing files are
    skipped so the store builds even before every export has landed.

    The money-out / lobbying / floor-speech sidecars (federal award edges, Senate
    LDA lobbying edges, typed member -> bill floor-speech edges) are loaded by
    default so the explorer, the said-vs-voted and follow-the-money lenses, and the
    ask CLI see them with no opt-in env var. The heavy per-issue CREC feed
    (:data:`SPEECH_EDGES`, ~800k edges) and news mentions stay opt-in via an
    explicit ``edge_paths``.
    """
    store = GraphStore()
    for path in node_paths:
        if not path.exists():
            continue
        for node in load_nodes_from(path, load_embeddings=load_embeddings):
            store.add_node(node)
    for path in edge_paths:
        if not path.exists():
            continue
        added = 0
        for edge in load_edges_from(path):
            store.add_edge(edge)
            added += 1
            if edge_limit is not None and added >= edge_limit:
                break
    return store


__all__ = [
    "Array",
    "Provenance",
    "Node",
    "Edge",
    "GraphStore",
    "jurisdiction_of",
    "load_nodes_from",
    "load_edges_from",
    "build_store",
    "CONTRACT_RECORDS",
    "MUNICIPAL_PERSONS",
    "MUNICIPAL_VOTES",
    "SENATE_VOTES",
    "BILL_EDGES",
    "USASPENDING_ORGS",
    "LDA_ORGS",
    "AWARD_EDGES",
    "LOBBYING_EDGES",
    "SPEECH_EDGES",
    "SPEECH_BILL_EDGES",
    "NEWS_EDGES",
]
