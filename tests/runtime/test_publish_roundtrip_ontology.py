from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.export.contracts import SourceAnchor
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import (
    PlannedFile,
    ontology_agent_tools_path,
    ontology_edges_path,
    ontology_frontend_client_path,
    ontology_frontend_contract_path,
    ontology_frontend_types_path,
    ontology_frontend_index_path,
    ontology_index_path,
    ontology_member_edges_path,
    ontology_schema_path,
    plan_snapshot,
)
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.runtime.publish_roundtrip_ontology import verify_published_ontology_roundtrip

_SNAPSHOT_ID = "2026-04-25"
_CREATED_AT = datetime(2026, 4, 25, 0, 0, 0)
_PATCH_TARGET = "src.runtime.publish_roundtrip_ontology.fetch_all_ontology_edge_rows"


def _edge(edge_id: str = "ont-edge-roundtrip-001") -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id=edge_id,
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
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="cm-roundtrip-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _row(edge: OntologyEdgePayload) -> dict[str, object]:
    return {
        "edge_id": edge.edge_id,
        "edge_type": edge.edge_type,
        "subject_node_type": edge.subject.node_type,
        "subject_node_id": edge.subject.node_id,
        "subject_node_label": edge.subject.label,
        "object_node_type": edge.object.node_type,
        "object_node_id": edge.object.node_id,
        "object_node_label": edge.object.label,
        "source_anchors": [anchor.model_dump(mode="json") for anchor in edge.source_anchors],
        "confidence": edge.confidence,
        "attributes": edge.attributes,
    }


def _manifest_from_files(files: list[PlannedFile]) -> SnapshotManifest:
    entries = [
        ManifestEntry(path=file.path, sha256=file.sha256, size_bytes=file.size_bytes)
        for file in files
    ]
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=_CREATED_AT,
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(file.size_bytes for file in files),
        root_sha256=manifest_root_sha256(entries),
    )


def _write_ontology_tree(
    root: Path,
    edges: list[OntologyEdgePayload],
) -> SnapshotManifest:
    planned = [
        file
        for file in plan_snapshot(
            _SNAPSHOT_ID,
            [],
            [],
            [],
            ontology_edges=edges,
        )
        if file.path == ontology_edges_path()
        or file.path == ontology_agent_tools_path()
        or file.path == ontology_frontend_client_path()
        or file.path == ontology_frontend_contract_path()
        or file.path == ontology_frontend_types_path()
        or file.path == ontology_schema_path()
        or file.path == ontology_frontend_index_path()
        or file.path.startswith("ontology/members/")
        or file.path.startswith("ontology/member-features/")
        or file.path == ontology_index_path()
    ]
    write_planned_files(planned, root)
    return _manifest_from_files(planned)


def test_verify_published_ontology_roundtrip_passes_matching_global_and_member_graphs(
    tmp_path: Path,
) -> None:
    edge = _edge()
    manifest = _write_ontology_tree(tmp_path, [edge])

    with patch(_PATCH_TARGET, return_value=[_row(edge)]):
        result = verify_published_ontology_roundtrip(object(), tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 10


def test_verify_published_ontology_roundtrip_reports_stale_global_graph(
    tmp_path: Path,
) -> None:
    published_edge = _edge("ont-edge-published")
    db_edge = _edge("ont-edge-db")
    manifest = _write_ontology_tree(tmp_path, [published_edge])

    with patch(_PATCH_TARGET, return_value=[_row(db_edge)]):
        result = verify_published_ontology_roundtrip(object(), tmp_path, manifest)

    assert result.ok is False
    assert any(issue.path == ontology_edges_path() for issue in result.issues)
    assert any("mismatch" in issue.message for issue in result.issues)


def test_verify_published_ontology_roundtrip_reports_missing_member_graph(
    tmp_path: Path,
) -> None:
    edge = _edge()
    manifest = _write_ontology_tree(tmp_path, [edge])
    entries = [entry for entry in manifest.entries if entry.path == ontology_edges_path()]
    manifest_without_member = SnapshotManifest(
        snapshot_id=manifest.snapshot_id,
        created_at=manifest.created_at,
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(entry.size_bytes for entry in entries),
        root_sha256=manifest_root_sha256(entries),
    )

    with patch(_PATCH_TARGET, return_value=[_row(edge)]):
        result = verify_published_ontology_roundtrip(
            object(),
            tmp_path,
            manifest_without_member,
        )

    assert result.ok is False
    assert any(
        issue.path == ontology_member_edges_path("P000197")
        and "missing from manifest" in issue.message
        for issue in result.issues
    )


def test_verify_published_ontology_roundtrip_reports_unexpected_manifest_graph(
    tmp_path: Path,
) -> None:
    edge = _edge()
    manifest = _write_ontology_tree(tmp_path, [edge])

    with patch(_PATCH_TARGET, return_value=[]):
        result = verify_published_ontology_roundtrip(object(), tmp_path, manifest)

    assert result.ok is False
    assert any("unexpected ontology artifact" in issue.message for issue in result.issues)


def test_verify_published_ontology_roundtrip_accepts_explicit_empty_graph(
    tmp_path: Path,
) -> None:
    planned = [
        file
        for file in plan_snapshot(_SNAPSHOT_ID, [], [], [], ontology_edges=[])
        if file.path
        in {
            ontology_edges_path(),
            ontology_agent_tools_path(),
            ontology_frontend_client_path(),
            ontology_frontend_contract_path(),
            ontology_frontend_types_path(),
            ontology_index_path(),
            ontology_schema_path(),
            ontology_frontend_index_path(),
        }
    ]
    write_planned_files(planned, tmp_path)
    manifest = _manifest_from_files(planned)

    with patch(_PATCH_TARGET, return_value=[]):
        result = verify_published_ontology_roundtrip(object(), tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 8
