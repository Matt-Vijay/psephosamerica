from __future__ import annotations

from src.export.contracts import SourceAnchor
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyMemberGraphPayload,
    OntologyMemberFeaturesPayload,
    OntologyNodeRef,
    OntologySectorExposurePayload,
)
from src.ontology.member_features import build_member_feature_slices


def _anchor(source_type: str, source_id: str, url: str) -> SourceAnchor:
    return SourceAnchor(source_type=source_type, source_id=source_id, url=url, label=source_id)


def _member_committee_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-member-committee",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy"),
        source_anchors=[
            _anchor(
                "committee_membership",
                "cm-feature-1",
                "https://api.congress.gov/v3/committee/house/HSEC?format=json",
            )
        ],
    )


def _committee_sector_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-committee-sector",
        edge_type="committee_sector_jurisdiction",
        subject=OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            _anchor(
                "committee_membership",
                "cm-feature-sector",
                "https://api.congress.gov/v3/committee/house/HSEC?format=json",
            )
        ],
    )


def _holding_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-holding",
        edge_type="member_sector_holding_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            _anchor(
                "financial_disclosure",
                "fd-feature-1",
                "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-feature-1.pdf",
            )
        ],
    )


def _contribution_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-contribution",
        edge_type="member_sector_contribution_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            _anchor(
                "fec_contribution",
                "fec-feature-1",
                "https://www.fec.gov/data/receipts/individual-contributions/",
            )
        ],
        attributes={"contribution_date": "2024-10-15", "alignment_score": 0.8},
    )


def _statement_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="ont-edge-statement",
        edge_type="member_sector_public_statement_alignment",
        subject=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            _anchor(
                "public_statement",
                "stmt-feature-1",
                "https://pelosi.house.gov/news/press-releases/energy-statement",
            )
        ],
        attributes={"statement_date": "2024-10-16", "alignment_score": 0.7},
    )


def test_build_member_feature_slices_summarizes_prediction_ready_member_context() -> None:
    features = build_member_feature_slices(
        "2026-04-25",
        [
            _member_committee_edge(),
            _committee_sector_edge(),
            _holding_edge(),
            _contribution_edge(),
            _statement_edge(),
        ],
    )

    result = features["P000197"]

    assert result.readiness_status == "ready"
    assert result.readiness_reasons == []
    assert result.edge_count == 5
    assert [committee.node_id for committee in result.committees] == ["HSEC"]
    assert result.edge_type_counts == {
        "committee_sector_jurisdiction": 1,
        "member_committee_assignment": 1,
        "member_sector_contribution_exposure": 1,
        "member_sector_holding_exposure": 1,
        "member_sector_public_statement_alignment": 1,
    }
    assert result.source_type_counts == {
        "committee_membership": 2,
        "fec_contribution": 1,
        "financial_disclosure": 1,
        "public_statement": 1,
    }
    assert result.sector_exposures[0].sector_id == "energy"
    assert result.sector_exposures[0].committee_jurisdiction_edge_count == 1
    assert result.sector_exposures[0].contribution_edge_count == 1
    assert result.sector_exposures[0].holding_edge_count == 1
    assert result.sector_exposures[0].statement_edge_count == 1


def test_build_member_feature_slices_marks_member_partial_without_financial_exposure() -> None:
    features = build_member_feature_slices(
        "2026-04-25",
        [_member_committee_edge(), _committee_sector_edge()],
    )

    result = features["P000197"]

    assert result.readiness_status == "partial"
    assert result.readiness_reasons == ["missing_member_financial_exposure"]


def test_build_member_feature_slices_omits_graph_without_member_edges() -> None:
    features = build_member_feature_slices("2026-04-25", [_committee_sector_edge()])

    assert features == {}


def test_ontology_member_features_reject_negative_count_maps() -> None:
    try:
        OntologyMemberFeaturesPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="P000197",
            edge_count=0,
            source_count=0,
            edge_type_counts={
                "member_committee_assignment": -1,
                "committee_sector_jurisdiction": 1,
            },
            source_type_counts={},
            readiness_status="blocked",
            readiness_reasons=["missing_member_committee_assignment"],
        )
    except ValueError as exc:
        assert "edge_type_counts values must be nonnegative" in str(exc)
    else:
        raise AssertionError("member features with negative edge_type_counts should fail")


def test_ontology_member_features_reject_boolean_counts() -> None:
    try:
        OntologyMemberFeaturesPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="P000197",
            edge_count=True,
            source_count=0,
            edge_type_counts={"member_committee_assignment": True},
            source_type_counts={},
            readiness_status="blocked",
            readiness_reasons=["missing_member_financial_exposure"],
        )
    except ValueError as exc:
        assert "edge_count must be an integer" in str(exc)
    else:
        raise AssertionError("member features with boolean counts should fail")


def test_ontology_member_features_reject_boolean_count_map_values() -> None:
    try:
        OntologyMemberFeaturesPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="P000197",
            edge_count=1,
            source_count=0,
            edge_type_counts={"member_committee_assignment": True},
            source_type_counts={},
            readiness_status="blocked",
            readiness_reasons=["missing_member_financial_exposure"],
        )
    except ValueError as exc:
        assert "edge_type_counts values must be integers" in str(exc)
    else:
        raise AssertionError("member features with boolean edge_type_counts should fail")


def test_ontology_member_features_reject_duplicate_committees_and_sectors() -> None:
    committee = OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy")
    sector = OntologySectorExposurePayload(sector_id="energy", label="Energy")

    try:
        OntologyMemberFeaturesPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="P000197",
            edge_count=0,
            source_count=0,
            edge_type_counts={},
            source_type_counts={},
            committees=[committee, committee],
            sector_exposures=[sector, sector],
            readiness_status="blocked",
            readiness_reasons=["missing_member_committee_assignment"],
        )
    except ValueError as exc:
        assert "committees must be sorted and unique" in str(exc)
    else:
        raise AssertionError("member features with duplicate committees should fail")


def test_ontology_sector_exposure_normalizes_and_rejects_blank_sector_id() -> None:
    sector = OntologySectorExposurePayload(sector_id=" energy ", label=" Energy ")

    assert sector.sector_id == "energy"
    assert sector.label == "Energy"

    try:
        OntologySectorExposurePayload(sector_id="   ", label="Energy")
    except ValueError as exc:
        assert "sector_id must not be blank" in str(exc)
    else:
        raise AssertionError("sector exposure with blank sector_id should fail")


def test_ontology_sector_exposure_rejects_boolean_counts() -> None:
    try:
        OntologySectorExposurePayload(
            sector_id="energy",
            committee_jurisdiction_edge_count=True,
        )
    except ValueError as exc:
        assert "committee_jurisdiction_edge_count must be an integer" in str(exc)
    else:
        raise AssertionError("sector exposure with boolean counts should fail")


def test_ontology_member_features_reject_blank_member_bioguide_id() -> None:
    try:
        OntologyMemberFeaturesPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="   ",
            edge_count=0,
            source_count=0,
            edge_type_counts={},
            source_type_counts={},
            readiness_status="blocked",
            readiness_reasons=["missing_member_committee_assignment"],
        )
    except ValueError as exc:
        assert "member_bioguide_id must not be blank" in str(exc)
    else:
        raise AssertionError("member features with blank bioguide ID should fail")


def test_ontology_edge_rejects_wrong_node_types_for_edge_type() -> None:
    try:
        OntologyEdgePayload(
            edge_id="ont-edge-invalid-shape",
            edge_type="member_committee_assignment",
            subject=OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy"),
            object=OntologyNodeRef(node_type="member", node_id="P000197", label="Nancy Pelosi"),
            source_anchors=[
                _anchor(
                    "committee_membership",
                    "cm-invalid-shape",
                    "https://api.congress.gov/v3/committee/house/HSEC?format=json",
                )
            ],
        )
    except ValueError as exc:
        assert "member_committee_assignment must connect member to committee" in str(exc)
    else:
        raise AssertionError("ontology edge with wrong node types should fail")


def test_ontology_member_graph_rejects_edges_for_other_members() -> None:
    other_member_edge = _member_committee_edge().model_copy(
        update={
            "edge_id": "ont-edge-other-member",
            "subject": OntologyNodeRef(node_type="member", node_id="A000001", label="Alex Adams"),
        }
    )

    try:
        OntologyMemberGraphPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="P000197",
            edge_count=2,
            edges=[_member_committee_edge(), other_member_edge],
        )
    except ValueError as exc:
        assert "all member graph edges must match member_bioguide_id" in str(exc)
    else:
        raise AssertionError("member graph with unrelated member edge should fail")


def test_ontology_member_graph_normalizes_and_rejects_blank_member_id() -> None:
    graph = OntologyMemberGraphPayload(
        snapshot_id="2026-04-25",
        member_bioguide_id=" P000197 ",
        edge_count=1,
        edges=[_member_committee_edge()],
    )

    assert graph.member_bioguide_id == "P000197"

    try:
        OntologyMemberGraphPayload(
            snapshot_id="2026-04-25",
            member_bioguide_id="   ",
            edge_count=0,
            edges=[],
        )
    except ValueError as exc:
        assert "member_bioguide_id must not be blank" in str(exc)
    else:
        raise AssertionError("member graph with blank member_bioguide_id should fail")
