from __future__ import annotations

from src.export.contracts import SourceAnchor
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyIndexPayload,
    OntologyNodeRef,
)
from src.ontology.index import build_ontology_index


def _committee_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-index-committee",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-index-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _holding_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-index-holding",
        edge_type="member_sector_holding_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-index-1",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-index-1.pdf",
                label="Financial disclosure",
            )
        ],
    )


def test_build_ontology_index_summarizes_edges_nodes_sources_and_member_coverage() -> None:
    result = build_ontology_index("2026-04-25", [_committee_edge(), _holding_edge()])

    assert result.snapshot_id == "2026-04-25"
    assert result.edge_count == 2
    assert result.node_count == 3
    assert result.member_count == 1
    assert result.edge_type_counts == {
        "member_committee_assignment": 1,
        "member_sector_holding_exposure": 1,
    }
    assert result.node_type_counts == {"committee": 1, "member": 1, "sector": 1}
    assert result.source_type_counts == {"committee_membership": 1, "financial_disclosure": 1}
    assert result.member_edge_counts == {"P000197": 2}
    assert result.available_member_graphs == ["P000197"]


def test_build_ontology_index_accepts_explicit_empty_graph() -> None:
    result = build_ontology_index("2026-04-25", [])

    assert result.edge_count == 0
    assert result.node_count == 0
    assert result.member_count == 0
    assert result.edge_type_counts == {}
    assert result.available_member_graphs == []


def test_ontology_index_rejects_negative_count_maps() -> None:
    try:
        OntologyIndexPayload(
            snapshot_id="2026-04-25",
            edge_count=0,
            node_count=0,
            member_count=0,
            edge_type_counts={
                "member_committee_assignment": -1,
                "committee_sector_jurisdiction": 1,
            },
            node_type_counts={},
            source_type_counts={},
            member_edge_counts={},
            available_member_graphs=[],
        )
    except ValueError as exc:
        assert "edge_type_counts values must be nonnegative" in str(exc)
    else:
        raise AssertionError("ontology index with negative edge_type_counts should fail")


def test_ontology_index_rejects_boolean_counts() -> None:
    try:
        OntologyIndexPayload(
            snapshot_id="2026-04-25",
            edge_count=True,
            node_count=0,
            member_count=0,
            edge_type_counts={"member_committee_assignment": True},
            node_type_counts={},
            source_type_counts={},
            member_edge_counts={},
            available_member_graphs=[],
        )
    except ValueError as exc:
        assert "edge_count must be an integer" in str(exc)
    else:
        raise AssertionError("ontology index with boolean counts should fail")


def test_ontology_index_rejects_boolean_count_map_values() -> None:
    try:
        OntologyIndexPayload(
            snapshot_id="2026-04-25",
            edge_count=1,
            node_count=0,
            member_count=0,
            edge_type_counts={"member_committee_assignment": True},
            node_type_counts={},
            source_type_counts={},
            member_edge_counts={},
            available_member_graphs=[],
        )
    except ValueError as exc:
        assert "edge_type_counts values must be integers" in str(exc)
    else:
        raise AssertionError("ontology index with boolean edge_type_counts should fail")


def test_ontology_index_rejects_duplicate_available_member_graphs() -> None:
    try:
        OntologyIndexPayload(
            snapshot_id="2026-04-25",
            edge_count=1,
            node_count=1,
            member_count=2,
            edge_type_counts={"member_committee_assignment": 1},
            node_type_counts={"member": 1},
            source_type_counts={"committee_membership": 1},
            member_edge_counts={"P000197": 1},
            available_member_graphs=["P000197", "P000197"],
        )
    except ValueError as exc:
        assert "available_member_graphs must be sorted and unique" in str(exc)
    else:
        raise AssertionError("ontology index with duplicate member graph keys should fail")


def test_ontology_index_rejects_blank_available_member_graphs() -> None:
    try:
        OntologyIndexPayload(
            snapshot_id="2026-04-25",
            edge_count=0,
            node_count=0,
            member_count=1,
            edge_type_counts={},
            node_type_counts={},
            source_type_counts={},
            member_edge_counts={" ": 0},
            available_member_graphs=[" "],
        )
    except ValueError as exc:
        assert "available_member_graphs must be sorted, unique, and nonblank" in str(exc)
    else:
        raise AssertionError("ontology index with blank member graph key should fail")


def test_ontology_graph_rejects_duplicate_edge_ids() -> None:
    from src.ontology.contracts import OntologyGraphPayload

    edge = _committee_edge()

    try:
        OntologyGraphPayload(
            snapshot_id="2026-04-25",
            edge_count=2,
            edges=[edge, edge],
        )
    except ValueError as exc:
        assert "ontology graph edge IDs must be unique" in str(exc)
    else:
        raise AssertionError("ontology graph with duplicate edge IDs should fail")


def test_ontology_edge_rejects_duplicate_source_anchors() -> None:
    duplicate = SourceAnchor(
        source_type="committee_membership",
        source_id="cm-index-1",
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
        label="Duplicate committee membership",
    )

    try:
        OntologyEdgePayload(
            edge_id="ont-edge-duplicate-source",
            edge_type="member_committee_assignment",
            subject=OntologyNodeRef(
                node_type="member",
                node_id="P000197",
                label="Nancy Pelosi",
            ),
            object=OntologyNodeRef(
                node_type="committee",
                node_id="HSEC",
                label="Energy",
            ),
            source_anchors=[_committee_edge().source_anchors[0], duplicate],
        )
    except ValueError as exc:
        assert "ontology edges reject duplicate source anchors" in str(exc)
    else:
        raise AssertionError("ontology edge with duplicate source anchors should fail")


def test_ontology_node_ref_normalizes_and_rejects_blank_ids() -> None:
    node = OntologyNodeRef(node_type="member", node_id=" P000197 ", label=" Nancy Pelosi ")

    assert node.node_id == "P000197"
    assert node.label == "Nancy Pelosi"

    try:
        OntologyNodeRef(node_type="member", node_id="   ", label="Nancy Pelosi")
    except ValueError as exc:
        assert "node_id must not be blank" in str(exc)
    else:
        raise AssertionError("ontology node with blank node_id should fail")


def test_ontology_edge_rejects_blank_edge_id() -> None:
    try:
        OntologyEdgePayload(
            edge_id="   ",
            edge_type="member_committee_assignment",
            subject=OntologyNodeRef(
                node_type="member",
                node_id="P000197",
                label="Nancy Pelosi",
            ),
            object=OntologyNodeRef(
                node_type="committee",
                node_id="HSEC",
                label="Energy",
            ),
            source_anchors=[_committee_edge().source_anchors[0]],
        )
    except ValueError as exc:
        assert "edge_id must not be blank" in str(exc)
    else:
        raise AssertionError("ontology edge with blank edge_id should fail")
