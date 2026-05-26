from __future__ import annotations

from src.ontology.contracts import OntologyEdgePayload, OntologyIndexPayload


def build_ontology_index(
    snapshot_id: str,
    edges: list[OntologyEdgePayload],
) -> OntologyIndexPayload:
    """Build a compact routing/readiness index over source-backed ontology edges."""
    edge_type_counts: dict[str, int] = {}
    node_type_counts: dict[str, int] = {}
    source_type_counts: dict[str, int] = {}
    member_edge_counts: dict[str, int] = {}
    seen_nodes: set[tuple[str, str]] = set()

    for edge in edges:
        edge_type_counts[edge.edge_type] = edge_type_counts.get(edge.edge_type, 0) + 1
        for anchor in edge.source_anchors:
            source_type_counts[anchor.source_type] = (
                source_type_counts.get(anchor.source_type, 0) + 1
            )
        for node in (edge.subject, edge.object):
            key = (node.node_type, node.node_id)
            if key not in seen_nodes:
                seen_nodes.add(key)
                node_type_counts[node.node_type] = node_type_counts.get(node.node_type, 0) + 1
            if node.node_type == "member":
                member_edge_counts[node.node_id] = member_edge_counts.get(node.node_id, 0) + 1

    available_member_graphs = sorted(member_edge_counts)
    return OntologyIndexPayload(
        snapshot_id=snapshot_id,
        edge_count=len(edges),
        node_count=len(seen_nodes),
        member_count=len(available_member_graphs),
        edge_type_counts=dict(sorted(edge_type_counts.items())),
        node_type_counts=dict(sorted(node_type_counts.items())),
        source_type_counts=dict(sorted(source_type_counts.items())),
        member_edge_counts=dict(sorted(member_edge_counts.items())),
        available_member_graphs=available_member_graphs,
    )
