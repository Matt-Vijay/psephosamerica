from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyMemberFeaturesPayload,
    OntologyNodeRef,
    OntologySectorExposurePayload,
)


@dataclass
class _SectorAccumulator:
    sector_id: str
    label: str | None = None
    committee_jurisdiction_edge_count: int = 0
    holding_edge_count: int = 0
    transaction_edge_count: int = 0
    contribution_edge_count: int = 0
    statement_edge_count: int = 0
    source_count: int = 0


def slice_member_ontology_edges(
    ontology_edges: list[OntologyEdgePayload],
) -> dict[str, list[OntologyEdgePayload]]:
    """Return member graph slices, including second-hop committee-sector context."""
    slices: dict[str, dict[str, OntologyEdgePayload]] = {}
    member_committee_ids: dict[str, set[str]] = {}
    committee_context_edges: dict[str, list[OntologyEdgePayload]] = {}

    for edge in ontology_edges:
        member_ids: list[str] = []
        for node in (edge.subject, edge.object):
            if node.node_type != "member" or node.node_id in member_ids:
                continue
            member_ids.append(node.node_id)
        for member_bioguide_id in member_ids:
            slices.setdefault(member_bioguide_id, {})[edge.edge_id] = edge
            for node in (edge.subject, edge.object):
                if node.node_type == "committee":
                    member_committee_ids.setdefault(member_bioguide_id, set()).add(node.node_id)
        if edge.edge_type == "committee_sector_jurisdiction":
            for node in (edge.subject, edge.object):
                if node.node_type == "committee":
                    committee_context_edges.setdefault(node.node_id, []).append(edge)

    for member_bioguide_id, committee_ids in member_committee_ids.items():
        for committee_id in committee_ids:
            for edge in committee_context_edges.get(committee_id, []):
                slices.setdefault(member_bioguide_id, {})[edge.edge_id] = edge

    return {
        member_bioguide_id: list(edges_by_id.values())
        for member_bioguide_id, edges_by_id in sorted(slices.items())
    }


def build_member_feature_slices(
    snapshot_id: str,
    ontology_edges: list[OntologyEdgePayload],
) -> dict[str, OntologyMemberFeaturesPayload]:
    """Build compact per-member prediction features from source-backed ontology edges."""
    return {
        member_bioguide_id: _build_member_features(snapshot_id, member_bioguide_id, edges)
        for member_bioguide_id, edges in slice_member_ontology_edges(ontology_edges).items()
    }


def _build_member_features(
    snapshot_id: str,
    member_bioguide_id: str,
    edges: list[OntologyEdgePayload],
) -> OntologyMemberFeaturesPayload:
    edge_type_counts: dict[str, int] = {}
    source_type_counts: dict[str, int] = {}
    committees_by_id: dict[str, OntologyNodeRef] = {}
    sectors: dict[str, _SectorAccumulator] = {}

    for edge in edges:
        edge_type_counts[edge.edge_type] = edge_type_counts.get(edge.edge_type, 0) + 1
        for anchor in edge.source_anchors:
            source_type_counts[anchor.source_type] = (
                source_type_counts.get(anchor.source_type, 0) + 1
            )

        if edge.edge_type == "member_committee_assignment":
            committee = _node_of_type(edge, "committee")
            if committee is not None:
                committees_by_id[committee.node_id] = committee
        elif edge.edge_type == "committee_sector_jurisdiction":
            sector = _node_of_type(edge, "sector")
            if sector is not None:
                acc = _sector_accumulator(sectors, sector)
                acc.committee_jurisdiction_edge_count += 1
                acc.source_count += len(edge.source_anchors)
        elif edge.edge_type == "member_sector_holding_exposure":
            sector = _node_of_type(edge, "sector")
            if sector is not None:
                acc = _sector_accumulator(sectors, sector)
                acc.holding_edge_count += 1
                acc.source_count += len(edge.source_anchors)
        elif edge.edge_type == "member_sector_transaction_exposure":
            sector = _node_of_type(edge, "sector")
            if sector is not None:
                acc = _sector_accumulator(sectors, sector)
                acc.transaction_edge_count += 1
                acc.source_count += len(edge.source_anchors)
        elif edge.edge_type == "member_sector_contribution_exposure":
            sector = _node_of_type(edge, "sector")
            if sector is not None:
                acc = _sector_accumulator(sectors, sector)
                acc.contribution_edge_count += 1
                acc.source_count += len(edge.source_anchors)
        elif edge.edge_type in {
            "member_sector_statement_alignment",
            "member_sector_public_statement_alignment",
        }:
            sector = _node_of_type(edge, "sector")
            if sector is not None:
                acc = _sector_accumulator(sectors, sector)
                acc.statement_edge_count += 1
                acc.source_count += len(edge.source_anchors)

    reasons = _readiness_reasons(edge_type_counts)
    readiness_status: Literal["ready", "partial", "blocked"] = (
        "ready" if not reasons else ("blocked" if not edges else "partial")
    )

    return OntologyMemberFeaturesPayload(
        snapshot_id=snapshot_id,
        member_bioguide_id=member_bioguide_id,
        edge_count=len(edges),
        source_count=sum(source_type_counts.values()),
        edge_type_counts=dict(sorted(edge_type_counts.items())),
        source_type_counts=dict(sorted(source_type_counts.items())),
        committees=[committees_by_id[committee_id] for committee_id in sorted(committees_by_id)],
        sector_exposures=[
            OntologySectorExposurePayload(
                sector_id=sector.sector_id,
                label=sector.label,
                committee_jurisdiction_edge_count=sector.committee_jurisdiction_edge_count,
                holding_edge_count=sector.holding_edge_count,
                transaction_edge_count=sector.transaction_edge_count,
                contribution_edge_count=sector.contribution_edge_count,
                statement_edge_count=sector.statement_edge_count,
                source_count=sector.source_count,
            )
            for sector in sorted(sectors.values(), key=lambda item: item.sector_id)
        ],
        readiness_status=readiness_status,
        readiness_reasons=reasons,
    )


def _node_of_type(edge: OntologyEdgePayload, node_type: str) -> OntologyNodeRef | None:
    for node in (edge.subject, edge.object):
        if node.node_type == node_type:
            return node
    return None


def _sector_accumulator(
    sectors: dict[str, _SectorAccumulator],
    sector: OntologyNodeRef,
) -> _SectorAccumulator:
    if sector.node_id not in sectors:
        sectors[sector.node_id] = _SectorAccumulator(sector_id=sector.node_id, label=sector.label)
    return sectors[sector.node_id]


def _readiness_reasons(edge_type_counts: dict[str, int]) -> list[str]:
    reasons: list[str] = []
    if edge_type_counts.get("member_committee_assignment", 0) == 0:
        reasons.append("missing_member_committee_assignment")
    if edge_type_counts.get("committee_sector_jurisdiction", 0) == 0:
        reasons.append("missing_committee_sector_context")
    if (
        edge_type_counts.get("member_sector_holding_exposure", 0)
        + edge_type_counts.get("member_sector_transaction_exposure", 0)
        == 0
    ):
        reasons.append("missing_member_financial_exposure")
    return reasons
