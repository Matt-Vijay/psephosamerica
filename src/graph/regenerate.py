"""Production regeneration driver: pending rows -> ready contract rows.

The resolution path (:func:`~src.graph.materialize.materialize_person_nodes`)
emits ``pending`` rows with null enrichment fields. This driver walks every
emitted Person *and* Bill, builds the leakage-safe graph, assembles a
deterministic source-anchored dossier, computes the structural embedding, and
runs :func:`~src.graph.enrichment.enrich.enrich_output` with the in-repo
:class:`~src.graph.enrichment.local_embedder.LocalTextEmbedder` — producing
``ready`` rows with populated ``dossier_json`` / ``dossier_embedding`` /
``structural_embedding`` — then diffs against the prior snapshot for the CDC
feed.

:func:`regenerate_corpus` is pure (no I/O); the CLI in this module wires it to
disk (JSONL corpus + delta feed via :mod:`src.graph.export`). It is idempotent:
same inputs + ``as_of`` + prior assignment -> identical rows, and only changed
entities appear in the delta feed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from src.graph.cdc import EntityDelta, diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.edges import GraphEdge
from src.graph.enrichment.deterministic_dossier import build_deterministic_dossier
from src.graph.enrichment.dossier_context import build_dossier_context
from src.graph.enrichment.enrich import DossierEmbedder, enrich_output
from src.graph.enrichment.local_embedder import default_text_embedder
from src.graph.enrichment.structural_features import structural_feature_vector
from src.graph.entity_resolution.assignment import CanonicalAssignment
from src.graph.entity_resolution.records import SourceRecord
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.materialize import materialize_person_nodes


@dataclass(frozen=True)
class CorpusResult:
    """The output of one regeneration pass over a Person+Bill corpus."""

    rows: list[EntityResolutionOutput]
    deltas: list[EntityDelta]
    assignment: CanonicalAssignment


def regenerate_corpus(
    *,
    person_records: Iterable[SourceRecord],
    bill_outputs: Iterable[EntityResolutionOutput],
    edges: Iterable[GraphEdge],
    as_of: datetime,
    embedder: DossierEmbedder | None = None,
    prior_assignment: CanonicalAssignment | None = None,
    prior_outputs: Mapping[str, EntityResolutionOutput] | None = None,
) -> CorpusResult:
    """Resolve, materialize, and enrich every known Person+Bill to a ready row."""
    embed = embedder if embedder is not None else default_text_embedder()
    person_nodes, assignment = materialize_person_nodes(person_records, prior_assignment)
    nodes = [*person_nodes, *bill_outputs]
    graph = KnowledgeGraph(nodes=nodes, edges=list(edges))
    snapshot = graph.as_of(as_of)

    ready: list[EntityResolutionOutput] = []
    for node in snapshot.nodes:
        context = build_dossier_context(graph, node.canonical_id, as_of=as_of)
        assert context is not None  # node is in the as_of snapshot
        structural = structural_feature_vector(graph, node.canonical_id, as_of=as_of)
        assert structural is not None
        dossier = build_deterministic_dossier(context)
        ready.append(
            enrich_output(node, dossier=dossier, structural_embedding=structural, embedder=embed)
        )

    ready.sort(key=lambda row: row.canonical_id)
    deltas = diff_outputs(
        dict(prior_outputs) if prior_outputs is not None else {},
        {row.canonical_id: row for row in ready},
    )
    return CorpusResult(rows=ready, deltas=deltas, assignment=assignment)


def _l2(vector: list[float] | None) -> float:
    if not vector:
        return 0.0
    return math.sqrt(math.fsum(value * value for value in vector))


def corpus_stats(rows: list[EntityResolutionOutput]) -> dict[str, float]:
    """Coverage statistics over a regenerated corpus (for reporting)."""
    total = len(rows)
    if total == 0:
        return {
            "rows": 0.0,
            "ready": 0.0,
            "persons": 0.0,
            "bills": 0.0,
            "pct_dossier_json_non_null": 0.0,
            "pct_dossier_embedding_non_null": 0.0,
            "pct_structural_embedding_non_null": 0.0,
            "mean_dossier_embedding_l2": 0.0,
        }
    ready = sum(1 for row in rows if row.enrichment_status == "ready")
    dossier = sum(1 for row in rows if row.dossier_json is not None)
    dossier_emb = sum(1 for row in rows if row.dossier_embedding is not None)
    struct_emb = sum(1 for row in rows if row.structural_embedding is not None)
    l2s = [_l2(row.dossier_embedding) for row in rows if row.dossier_embedding is not None]
    return {
        "rows": float(total),
        "ready": float(ready),
        "persons": float(sum(1 for row in rows if row.entity_type == "person")),
        "bills": float(sum(1 for row in rows if row.entity_type == "bill")),
        "pct_dossier_json_non_null": round(100.0 * dossier / total, 2),
        "pct_dossier_embedding_non_null": round(100.0 * dossier_emb / total, 2),
        "pct_structural_embedding_non_null": round(100.0 * struct_emb / total, 2),
        "mean_dossier_embedding_l2": round(math.fsum(l2s) / len(l2s), 4) if l2s else 0.0,
    }
