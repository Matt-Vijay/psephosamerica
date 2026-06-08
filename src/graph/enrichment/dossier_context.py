"""Assemble the leakage-safe, source-anchored evidence for an entity dossier.

The blueprint's politician encoder is an LLM-generated dossier where *every
claim is source-anchored*. Before any model is prompted, the grounding evidence
has to be gathered — and gathered safely: only facts knowable as of the dossier
time ``t`` may appear, or the dossier leaks the future.

:func:`build_dossier_context` snapshots a :class:`~src.graph.knowledge_graph.\
KnowledgeGraph` as of ``t`` and packages the entity's identity plus its
relational facts (votes, donations, sponsorships, …) — each carrying the source
URL and content hash that backs it — into a typed, serializable
:class:`DossierContext`. That context is the input the LLM dossier step consumes
(the model call is a thin boundary added in a later slice); keeping assembly
pure and leakage-safe means the grounding is testable and replayable.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from src.export.contracts import ExportContractModel
from src.graph.knowledge_graph import KnowledgeGraph


class DossierFact(ExportContractModel):
    """One source-anchored relational fact about the entity."""

    relation: str
    target_id: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source_url: str
    content_sha256: str
    known_at: datetime
    valid_from: date
    valid_to: date | None = None


class DossierContext(ExportContractModel):
    """The leakage-safe evidence package grounding an entity's dossier."""

    canonical_id: str
    display_name: str
    external_ids: list[str] = Field(default_factory=list)
    as_of: datetime
    facts: list[DossierFact] = Field(default_factory=list)


def build_dossier_context(
    graph: KnowledgeGraph,
    canonical_id: str,
    *,
    as_of: datetime,
) -> DossierContext | None:
    """Gather the source-anchored facts knowable about an entity as of ``as_of``.

    Returns ``None`` when the entity is not yet known at ``as_of`` (or absent).
    """
    snapshot = graph.as_of(as_of)
    node = snapshot.node(canonical_id)
    if node is None:
        return None

    facts = [
        DossierFact(
            relation=edge.edge_type,
            target_id=edge.dst_id,
            attributes=dict(edge.attributes),
            source_url=edge.provenance.source_url,
            content_sha256=edge.provenance.content_sha256,
            known_at=edge.provenance.known_at,
            valid_from=edge.provenance.valid_from,
            valid_to=edge.provenance.valid_to,
        )
        for edge in snapshot.edges_from(canonical_id)
    ]
    facts.sort(key=lambda fact: (fact.known_at, fact.relation, fact.target_id))

    return DossierContext(
        canonical_id=node.canonical_id,
        display_name=node.display_name,
        external_ids=list(node.external_ids),
        as_of=as_of,
        facts=facts,
    )
