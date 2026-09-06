from __future__ import annotations

import json
from pathlib import Path

from src.export.contracts import SourceAnchor
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import (
    PlannedFile,
    ontology_agent_tools_path,
    ontology_edges_path,
    ontology_frontend_client_path,
    ontology_frontend_contract_path,
    ontology_frontend_index_path,
    ontology_frontend_types_path,
    ontology_index_path,
    ontology_member_edges_path,
    ontology_member_features_path,
    ontology_schema_path,
    serialize_payload,
)
from src.ontology.agent_tools import build_ontology_agent_tool_manifest
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyGraphPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
    OntologyNodeRef,
)
from src.ontology.frontend_contracts import (
    build_ontology_frontend_contract,
    build_ontology_typescript_client,
    build_ontology_typescript_declarations,
)
from src.ontology.static_schema import build_ontology_frontend_index, build_ontology_static_schema
from src.runtime.publish_verify_ontology import verify_local_ontology_edges


def _edge(edge_id: str = "ont-edge-verify-001") -> OntologyEdgePayload:
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
                source_id="cm-verify-1",
                url="https://api.congress.gov/v3/committee/house/HSEC?format=json",
                label="Committee membership",
            )
        ],
    )


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    entries = [
        ManifestEntry(path=file.path, sha256=file.sha256, size_bytes=file.size_bytes)
        for file in files
    ]
    return SnapshotManifest(
        snapshot_id="2026-04-24",
        created_at="2026-04-24T00:00:00Z",
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(file.size_bytes for file in files),
        root_sha256=manifest_root_sha256(entries),
    )


def _write_graph(root: Path, graph: OntologyGraphPayload) -> SnapshotManifest:
    file = PlannedFile.from_bytes(ontology_edges_path(), serialize_payload(graph))
    write_planned_files([file], root)
    return _manifest([file])


def _write_member_graph(
    root: Path,
    graph: OntologyMemberGraphPayload,
    *,
    path_member_bioguide_id: str | None = None,
) -> SnapshotManifest:
    member_bioguide_id = path_member_bioguide_id or graph.member_bioguide_id
    file = PlannedFile.from_bytes(
        ontology_member_edges_path(member_bioguide_id),
        serialize_payload(graph),
    )
    write_planned_files([file], root)
    return _manifest([file])


def _member_features(
    *,
    edge_count: int = 1,
    readiness_status: str = "ready",
    readiness_reasons: list[str] | None = None,
) -> OntologyMemberFeaturesPayload:
    if readiness_reasons is None:
        readiness_reasons = (
            [] if readiness_status == "ready" else ["missing_member_financial_exposure"]
        )
    return OntologyMemberFeaturesPayload(
        snapshot_id="2026-04-24",
        member_bioguide_id="P000197",
        edge_count=edge_count,
        source_count=edge_count,
        edge_type_counts={"member_committee_assignment": edge_count} if edge_count else {},
        source_type_counts={"committee_membership": edge_count} if edge_count else {},
        readiness_status=readiness_status,
        readiness_reasons=readiness_reasons,
    )


def test_verify_local_ontology_edges_passes_valid_graph(tmp_path: Path) -> None:
    manifest = _write_graph(
        tmp_path,
        OntologyGraphPayload(
            snapshot_id="2026-04-24",
            edge_count=1,
            edges=[_edge()],
        ),
    )

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 1


def test_verify_local_ontology_edges_passes_valid_schema_and_frontend_index(
    tmp_path: Path,
) -> None:
    edge = _edge()
    files = [
        PlannedFile.from_bytes(
            ontology_schema_path(),
            serialize_payload(build_ontology_static_schema()),
        ),
        PlannedFile.from_bytes(
            ontology_frontend_index_path(),
            serialize_payload(
                build_ontology_frontend_index(
                    snapshot_id="2026-04-24",
                    edges=[edge],
                )
            ),
        ),
        PlannedFile.from_bytes(
            ontology_edges_path(),
            serialize_payload(
                OntologyGraphPayload(
                    snapshot_id="2026-04-24",
                    edge_count=1,
                    edges=[edge],
                )
            ),
        ),
    ]
    write_planned_files(files, tmp_path)
    manifest = _manifest(files)

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 3


def test_verify_local_ontology_edges_passes_valid_frontend_contract_artifacts(
    tmp_path: Path,
) -> None:
    files = [
        PlannedFile.from_bytes(
            ontology_agent_tools_path(),
            serialize_payload(build_ontology_agent_tool_manifest()),
        ),
        PlannedFile.from_bytes(
            ontology_frontend_contract_path(),
            serialize_payload(build_ontology_frontend_contract()),
        ),
        PlannedFile.from_bytes(
            ontology_frontend_types_path(),
            build_ontology_typescript_declarations().encode("utf-8"),
        ),
        PlannedFile.from_bytes(
            ontology_frontend_client_path(),
            build_ontology_typescript_client().encode("utf-8"),
        ),
    ]
    write_planned_files(files, tmp_path)
    manifest = _manifest(files)

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 4


def test_verify_local_ontology_edges_rejects_stale_frontend_contract_paths(
    tmp_path: Path,
) -> None:
    payload = build_ontology_frontend_contract().model_dump(mode="json")
    payload["artifact_paths"]["frontend_index"] = "ontology/old-index.json"
    file = PlannedFile.from_bytes(
        ontology_frontend_contract_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "artifact_paths mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_contract_missing_model(
    tmp_path: Path,
) -> None:
    payload = build_ontology_frontend_contract().model_dump(mode="json")
    payload["model_names"].remove("OntologyAgentToolManifestPayload")
    payload["json_schemas"].pop("OntologyAgentToolManifestPayload")
    file = PlannedFile.from_bytes(
        ontology_frontend_contract_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "model_names mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_contract_stale_schema(
    tmp_path: Path,
) -> None:
    payload = build_ontology_frontend_contract().model_dump(mode="json")
    payload["json_schemas"]["OntologyEdgePayload"]["title"] = "StaleOntologyEdgePayload"
    file = PlannedFile.from_bytes(
        ontology_frontend_contract_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "json_schemas mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_unknown_agent_tools_version(
    tmp_path: Path,
) -> None:
    payload = build_ontology_agent_tool_manifest().model_dump(mode="json")
    payload["schema_version"] = "unknown-version"
    file = PlannedFile.from_bytes(
        ontology_agent_tools_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "schema_version does not match contract" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_agent_tool_unknown_artifact_path(
    tmp_path: Path,
) -> None:
    payload = build_ontology_agent_tool_manifest().model_dump(mode="json")
    payload["tools"][0]["required_artifact_paths"].append("ontology/missing.json")
    payload["tools"][0]["required_artifact_paths"].sort()
    file = PlannedFile.from_bytes(
        ontology_agent_tools_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "unknown required artifact path" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_types_without_version(
    tmp_path: Path,
) -> None:
    file = PlannedFile.from_bytes(
        ontology_frontend_types_path(),
        b"export interface OntologyFrontendContractSchemas {}\n",
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "missing contract version" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_types_with_stale_paths(
    tmp_path: Path,
) -> None:
    declarations = build_ontology_typescript_declarations().replace(
        'agent_tools: "ontology/agent-tools.json"',
        'agent_tools: "ontology/old-agent-tools.json"',
    )
    file = PlannedFile.from_bytes(
        ontology_frontend_types_path(),
        declarations.encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "artifact path mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_types_missing_schema_model(
    tmp_path: Path,
) -> None:
    declarations = build_ontology_typescript_declarations().replace(
        "  OntologyAgentToolManifestPayload: JsonSchema;\n",
        "",
    )
    file = PlannedFile.from_bytes(
        ontology_frontend_types_path(),
        declarations.encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "schema model mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_types_without_runtime_member_paths(
    tmp_path: Path,
) -> None:
    declarations = (
        build_ontology_typescript_declarations()
        .replace(
            "`ontology/member-features/${string}.json`",
            '"ontology/member-features/{member_bioguide_id}.json"',
        )
        .replace(
            "`ontology/members/${string}.json`",
            '"ontology/members/{member_bioguide_id}.json"',
        )
    )
    file = PlannedFile.from_bytes(
        ontology_frontend_types_path(),
        declarations.encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "missing runtime member path types" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_client_without_version(
    tmp_path: Path,
) -> None:
    file = PlannedFile.from_bytes(
        ontology_frontend_client_path(),
        b"export class PsephosAmericaOntologyClient {}\n",
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "missing client version" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_client_with_stale_paths(
    tmp_path: Path,
) -> None:
    source = build_ontology_typescript_client().replace(
        'agent_tools: "ontology/agent-tools.json"',
        'agent_tools: "ontology/old-agent-tools.json"',
    )
    file = PlannedFile.from_bytes(ontology_frontend_client_path(), source.encode("utf-8"))
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "artifact path mismatch" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_frontend_client_without_member_id_guard(
    tmp_path: Path,
) -> None:
    source = build_ontology_typescript_client().replace(
        "validateOntologyMemberBioguideId",
        "unsafeMemberId",
    )
    file = PlannedFile.from_bytes(ontology_frontend_client_path(), source.encode("utf-8"))
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "missing member id guard" in result.issues[0].message


def test_verify_local_ontology_edges_rejects_unknown_frontend_index_version(
    tmp_path: Path,
) -> None:
    payload = build_ontology_frontend_index(
        snapshot_id="2026-04-24",
        edges=[],
    ).model_dump(mode="json")
    payload["schema_version"] = "unknown-version"
    file = PlannedFile.from_bytes(
        ontology_frontend_index_path(),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([file], tmp_path)
    manifest = _manifest([file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "schema_version must match frontend index contract" in result.issues[0].message


def test_verify_local_ontology_edges_passes_valid_member_graph(tmp_path: Path) -> None:
    files = [
        PlannedFile.from_bytes(
            ontology_member_edges_path("P000197"),
            serialize_payload(
                OntologyMemberGraphPayload(
                    snapshot_id="2026-04-24",
                    member_bioguide_id="P000197",
                    edge_count=1,
                    edges=[_edge()],
                )
            ),
        ),
        PlannedFile.from_bytes(
            ontology_member_features_path("P000197"),
            serialize_payload(_member_features()),
        ),
    ]
    write_planned_files(files, tmp_path)
    manifest = _manifest(files)

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 2


def test_verify_local_ontology_edges_reports_member_graph_without_features(
    tmp_path: Path,
) -> None:
    manifest = _write_member_graph(
        tmp_path,
        OntologyMemberGraphPayload(
            snapshot_id="2026-04-24",
            member_bioguide_id="P000197",
            edge_count=1,
            edges=[_edge()],
        ),
    )

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology member graph has no matching member features" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_member_features_without_graph(
    tmp_path: Path,
) -> None:
    feature_file = PlannedFile.from_bytes(
        ontology_member_features_path("P000197"),
        serialize_payload(_member_features()),
    )
    write_planned_files([feature_file], tmp_path)
    manifest = _manifest([feature_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology member features have no matching member graph" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_is_ok_when_artifact_absent(tmp_path: Path) -> None:
    manifest = _manifest([])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 0


def test_verify_local_ontology_edges_reports_missing_manifested_file(tmp_path: Path) -> None:
    manifest = _manifest([PlannedFile.from_bytes(ontology_edges_path(), b"{}")])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "missing" in result.issues[0].message


def test_verify_local_ontology_edges_reports_missing_manifested_schema(tmp_path: Path) -> None:
    manifest = _manifest([PlannedFile.from_bytes(ontology_schema_path(), b"{}")])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "ontology schema file missing" in result.issues[0].message


def test_verify_local_ontology_edges_reports_invalid_frontend_types(tmp_path: Path) -> None:
    types_file = PlannedFile.from_bytes(ontology_frontend_types_path(), b"not a declaration")
    write_planned_files([types_file], tmp_path)
    manifest = _manifest([types_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "ontology frontend types missing contract interface" in result.issues[0].message


def test_verify_local_ontology_edges_reports_invalid_payload(tmp_path: Path) -> None:
    path = tmp_path / ontology_edges_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "snapshot_id": "2026-04-24",
                "edge_count": 1,
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = _manifest([PlannedFile.from_bytes(ontology_edges_path(), path.read_bytes())])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "failed to load" in result.issues[0].message


def test_verify_local_ontology_edges_reports_member_path_payload_mismatch(
    tmp_path: Path,
) -> None:
    payload = OntologyMemberGraphPayload(
        snapshot_id="2026-04-24",
        member_bioguide_id="P000197",
        edge_count=1,
        edges=[_edge()],
    ).model_dump(mode="json")
    payload["member_bioguide_id"] = "S000148"
    path = tmp_path / ontology_member_edges_path("P000197")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = _manifest(
        [
            PlannedFile.from_bytes(
                ontology_member_edges_path("P000197"),
                path.read_bytes(),
            )
        ]
    )

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert "failed to load" in result.issues[0].message


def test_verify_local_ontology_edges_reports_index_graph_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_edges_path(),
        serialize_payload(
            OntologyGraphPayload(
                snapshot_id="2026-04-24",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    stale_index = {
        "snapshot_id": "2026-04-24",
        "edge_count": 2,
        "node_count": 2,
        "member_count": 1,
        "edge_type_counts": {"member_committee_assignment": 2},
        "node_type_counts": {"member": 1, "committee": 1},
        "source_type_counts": {"committee_membership": 1},
        "member_edge_counts": {"P000197": 1},
        "available_member_graphs": ["P000197"],
    }
    index_file = PlannedFile.from_bytes(
        ontology_index_path(),
        json.dumps(stale_index, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([graph_file, index_file], tmp_path)
    manifest = _manifest([graph_file, index_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology index edge_count does not match graph" in issue.message for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_frontend_index_graph_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_edges_path(),
        serialize_payload(
            OntologyGraphPayload(
                snapshot_id="2026-04-24",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    stale_frontend_index = {
        "snapshot_id": "2026-04-24",
        "edge_count": 2,
        "object_type_counts": {"member": 1, "committee": 1},
        "link_type_counts": {"member_committee_assignment": 2},
        "source_type_counts": {"committee_membership": 1},
        "member_ids": ["P000197"],
        "committee_ids": ["HSEC"],
        "sector_ids": [],
        "issuer_ids": [],
        "source_keys": ["committee_membership:cm-verify-1"],
        "by_member": {
            "P000197": {
                "object_id": "P000197",
                "edge_ids": ["ont-edge-verify-001"],
                "linked_object_ids_by_type": {"committee": ["HSEC"]},
                "source_keys": ["committee_membership:cm-verify-1"],
            }
        },
        "by_committee": {
            "HSEC": {
                "object_id": "HSEC",
                "edge_ids": ["ont-edge-verify-001"],
                "linked_object_ids_by_type": {"member": ["P000197"]},
                "source_keys": ["committee_membership:cm-verify-1"],
            }
        },
        "by_sector": {},
        "by_issuer": {},
        "by_link_type": {
            "member_committee_assignment": [
                "ont-edge-verify-001",
                "ont-edge-verify-002",
            ]
        },
        "by_source_key": {
            "committee_membership:cm-verify-1": {
                "source_key": "committee_membership:cm-verify-1",
                "source_type": "committee_membership",
                "source_id": "cm-verify-1",
                "edge_ids": ["ont-edge-verify-001"],
            }
        },
    }
    frontend_index_file = PlannedFile.from_bytes(
        ontology_frontend_index_path(),
        json.dumps(stale_frontend_index, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([graph_file, frontend_index_file], tmp_path)
    manifest = _manifest([graph_file, frontend_index_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology frontend index edge_count does not match graph" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_index_member_graph_missing_from_manifest(
    tmp_path: Path,
) -> None:
    index_payload = {
        "snapshot_id": "2026-04-24",
        "edge_count": 1,
        "node_count": 2,
        "member_count": 1,
        "edge_type_counts": {"member_committee_assignment": 1},
        "node_type_counts": {"member": 1, "committee": 1},
        "source_type_counts": {"committee_membership": 1},
        "member_edge_counts": {"P000197": 1},
        "available_member_graphs": ["P000197"],
    }
    index_file = PlannedFile.from_bytes(
        ontology_index_path(),
        json.dumps(index_payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([index_file], tmp_path)
    manifest = _manifest([index_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology index member graph is not listed in manifest" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_index_member_edge_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_member_edges_path("P000197"),
        serialize_payload(
            OntologyMemberGraphPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    index_payload = {
        "snapshot_id": "2026-04-24",
        "edge_count": 2,
        "node_count": 2,
        "member_count": 1,
        "edge_type_counts": {"member_committee_assignment": 2},
        "node_type_counts": {"member": 1, "committee": 1},
        "source_type_counts": {"committee_membership": 1},
        "member_edge_counts": {"P000197": 2},
        "available_member_graphs": ["P000197"],
    }
    index_file = PlannedFile.from_bytes(
        ontology_index_path(),
        json.dumps(index_payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([graph_file, index_file], tmp_path)
    manifest = _manifest([graph_file, index_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology index member_edge_counts does not match member graph" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_member_features_missing_from_manifest(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_member_edges_path("P000197"),
        serialize_payload(
            OntologyMemberGraphPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    index_payload = {
        "snapshot_id": "2026-04-24",
        "edge_count": 1,
        "node_count": 2,
        "member_count": 1,
        "edge_type_counts": {"member_committee_assignment": 1},
        "node_type_counts": {"member": 1, "committee": 1},
        "source_type_counts": {"committee_membership": 1},
        "member_edge_counts": {"P000197": 1},
        "available_member_graphs": ["P000197"],
    }
    index_file = PlannedFile.from_bytes(
        ontology_index_path(),
        json.dumps(index_payload, sort_keys=True).encode("utf-8"),
    )
    write_planned_files([graph_file, index_file], tmp_path)
    manifest = _manifest([graph_file, index_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology index member features are not listed in manifest" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_member_features_graph_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_member_edges_path("P000197"),
        serialize_payload(
            OntologyMemberGraphPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    features_file = PlannedFile.from_bytes(
        ontology_member_features_path("P000197"),
        serialize_payload(
            _member_features(
                edge_count=0,
                readiness_status="blocked",
                readiness_reasons=["missing_member_financial_exposure"],
            )
        ),
    )
    write_planned_files([graph_file, features_file], tmp_path)
    manifest = _manifest([graph_file, features_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology member feature edge_count does not match member graph" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_member_features_edge_type_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_member_edges_path("P000197"),
        serialize_payload(
            OntologyMemberGraphPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    features_file = PlannedFile.from_bytes(
        ontology_member_features_path("P000197"),
        serialize_payload(
            OntologyMemberFeaturesPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                source_count=1,
                edge_type_counts={"member_sector_holding_exposure": 1},
                source_type_counts={"committee_membership": 1},
                readiness_status="ready",
            )
        ),
    )
    write_planned_files([graph_file, features_file], tmp_path)
    manifest = _manifest([graph_file, features_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology member feature edge_type_counts do not match member graph" in issue.message
        for issue in result.issues
    )


def test_verify_local_ontology_edges_reports_member_features_source_type_count_mismatch(
    tmp_path: Path,
) -> None:
    graph_file = PlannedFile.from_bytes(
        ontology_member_edges_path("P000197"),
        serialize_payload(
            OntologyMemberGraphPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                edges=[_edge()],
            )
        ),
    )
    features_file = PlannedFile.from_bytes(
        ontology_member_features_path("P000197"),
        serialize_payload(
            OntologyMemberFeaturesPayload(
                snapshot_id="2026-04-24",
                member_bioguide_id="P000197",
                edge_count=1,
                source_count=1,
                edge_type_counts={"member_committee_assignment": 1},
                source_type_counts={"financial_disclosure": 1},
                readiness_status="ready",
            )
        ),
    )
    write_planned_files([graph_file, features_file], tmp_path)
    manifest = _manifest([graph_file, features_file])

    result = verify_local_ontology_edges(tmp_path, manifest)

    assert result.ok is False
    assert any(
        "ontology member feature source_type_counts do not match member graph" in issue.message
        for issue in result.issues
    )
