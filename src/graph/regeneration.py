"""The per-entity regeneration cycle — the daily unattended unit.

Composes the whole Track A pipeline into one leakage-safe pass:

1. resolve source records and assign **persistent** canonical IDs
   (:func:`~src.graph.materialize.materialize_person_nodes`);
2. build the knowledge graph from the resolved nodes + the provenance edges;
3. for each entity *known as of ``t``*, assemble its source-anchored dossier
   context, generate the dossier (injected model), compute structural features,
   and enrich the output row to ``ready``;
4. diff against the prior snapshot to emit the CDC delta feed.

Everything that touches an external service — the dossier model and the text
embedder — is an injected boundary, so the cycle runs deterministically in tests
and swaps in real providers in production. Running it on a schedule is the
"per-entity dossier+embedding regeneration unattended" criterion; the leakage
discipline (only facts knowable by ``t``) holds throughout.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from src.graph.cdc import EntityDelta, diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.edges import GraphEdge
from src.graph.enrichment.dossier import DossierModel, generate_dossier
from src.graph.enrichment.dossier_context import build_dossier_context
from src.graph.enrichment.enrich import DossierEmbedder, enrich_output
from src.graph.enrichment.structural_features import structural_feature_vector
from src.graph.entity_resolution.assignment import CanonicalAssignment
from src.graph.entity_resolution.records import SourceRecord
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.materialize import materialize_person_nodes


@dataclass(frozen=True)
class RegenerationResult:
    """The output of one regeneration cycle."""

    outputs: list[EntityResolutionOutput]
    deltas: list[EntityDelta]
    assignment: CanonicalAssignment


def regenerate_entities(
    *,
    person_records: Iterable[SourceRecord],
    edges: Iterable[GraphEdge],
    dossier_model: DossierModel,
    embedder: DossierEmbedder,
    as_of: datetime,
    prior_assignment: CanonicalAssignment | None = None,
    prior_outputs: Mapping[str, EntityResolutionOutput] | None = None,
) -> RegenerationResult:
    """Run one leakage-safe enrichment cycle and return rows + CDC deltas."""
    nodes, assignment = materialize_person_nodes(person_records, prior_assignment)
    graph = KnowledgeGraph(nodes=nodes, edges=list(edges))
    snapshot = graph.as_of(as_of)

    enriched: list[EntityResolutionOutput] = []
    for node in snapshot.nodes:
        context = build_dossier_context(graph, node.canonical_id, as_of=as_of)
        assert context is not None  # node is in the as_of snapshot, so it is known
        dossier = generate_dossier(context, dossier_model)
        structural = structural_feature_vector(graph, node.canonical_id, as_of=as_of)
        assert structural is not None  # same reason
        enriched.append(
            enrich_output(node, dossier=dossier, structural_embedding=structural, embedder=embedder)
        )

    deltas = diff_outputs(
        dict(prior_outputs) if prior_outputs is not None else {},
        {output.canonical_id: output for output in enriched},
    )
    return RegenerationResult(outputs=enriched, deltas=deltas, assignment=assignment)
