"""Roundtrip verification for published ontology graph artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.export.local_store import (
    load_ontology_agent_tools,
    load_ontology_edges,
    load_ontology_frontend_client,
    load_ontology_frontend_contract,
    load_ontology_frontend_types,
    load_ontology_frontend_index,
    load_ontology_index,
    load_ontology_member_features,
    load_ontology_member_edges,
    load_ontology_static_schema,
)
from src.export.manifest import SnapshotManifest
from src.export.writer import (
    ontology_agent_tools_path,
    ontology_frontend_contract_path,
    ontology_frontend_client_path,
    ontology_frontend_types_path,
    ontology_frontend_index_path,
    ontology_index_path,
    ontology_schema_path,
    plan_snapshot,
)
from src.ontology.contracts import (
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
)
from src.ontology.agent_tools import OntologyAgentToolManifestPayload
from src.ontology.frontend_contracts import OntologyFrontendContractPayload
from src.ontology.static_schema import OntologyFrontendIndexPayload, OntologyStaticSchemaPayload
from src.pipeline.publish_snapshot_run import _ontology_edge_from_row
from src.query.published_rows import fetch_all_ontology_edge_rows
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "ontology"
_ONTOLOGY_GLOBAL_PATH = "ontology/edges.json"
_ONTOLOGY_AGENT_TOOLS_PATH = ontology_agent_tools_path()
_ONTOLOGY_INDEX_PATH = ontology_index_path()
_ONTOLOGY_SCHEMA_PATH = ontology_schema_path()
_ONTOLOGY_FRONTEND_INDEX_PATH = ontology_frontend_index_path()
_ONTOLOGY_FRONTEND_CONTRACT_PATH = ontology_frontend_contract_path()
_ONTOLOGY_FRONTEND_TYPES_PATH = ontology_frontend_types_path()
_ONTOLOGY_FRONTEND_CLIENT_PATH = ontology_frontend_client_path()
_ONTOLOGY_MEMBER_PATH_RE = re.compile(r"^ontology/members/(?P<member_id>[^/]+)\.json$")
_ONTOLOGY_MEMBER_FEATURES_PATH_RE = re.compile(
    r"^ontology/member-features/(?P<member_id>[^/]+)\.json$"
)


def _issue(
    message: str,
    *,
    path: str | None = None,
    severity: IssueSeverity = "error",
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(
        stage=_STAGE,
        message=message,
        severity=severity,
        path=path,
    )


def _manifest_ontology_paths(manifest_payload: SnapshotManifest) -> list[str]:
    return [
        entry.path
        for entry in manifest_payload.entries
        if entry.path
        in {
            _ONTOLOGY_GLOBAL_PATH,
            _ONTOLOGY_AGENT_TOOLS_PATH,
            _ONTOLOGY_INDEX_PATH,
            _ONTOLOGY_SCHEMA_PATH,
            _ONTOLOGY_FRONTEND_INDEX_PATH,
            _ONTOLOGY_FRONTEND_CONTRACT_PATH,
            _ONTOLOGY_FRONTEND_TYPES_PATH,
            _ONTOLOGY_FRONTEND_CLIENT_PATH,
        }
        or _ONTOLOGY_MEMBER_FEATURES_PATH_RE.match(entry.path)
        or _ONTOLOGY_MEMBER_PATH_RE.match(entry.path)
    ]


def _expected_payloads(
    snapshot_id: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    edges = [_ontology_edge_from_row(row) for row in rows]
    expected_files = plan_snapshot(
        snapshot_id=snapshot_id,
        member_profiles=[],
        zip_feeds=[],
        evidence_cards=[],
        ontology_edges=edges,
    )
    payloads: dict[str, Any] = {}
    for planned in expected_files:
        if planned.path == _ONTOLOGY_GLOBAL_PATH:
            payloads[planned.path] = OntologyGraphPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if planned.path == _ONTOLOGY_AGENT_TOOLS_PATH:
            payloads[planned.path] = OntologyAgentToolManifestPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if planned.path == _ONTOLOGY_SCHEMA_PATH:
            payloads[planned.path] = OntologyStaticSchemaPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if planned.path == _ONTOLOGY_FRONTEND_CONTRACT_PATH:
            payloads[planned.path] = OntologyFrontendContractPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if planned.path == _ONTOLOGY_FRONTEND_TYPES_PATH:
            payloads[planned.path] = planned.content.decode("utf-8")
            continue
        if planned.path == _ONTOLOGY_FRONTEND_CLIENT_PATH:
            payloads[planned.path] = planned.content.decode("utf-8")
            continue
        if planned.path == _ONTOLOGY_INDEX_PATH:
            payloads[planned.path] = OntologyIndexPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if planned.path == _ONTOLOGY_FRONTEND_INDEX_PATH:
            payloads[planned.path] = OntologyFrontendIndexPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if _ONTOLOGY_MEMBER_PATH_RE.match(planned.path):
            payloads[planned.path] = OntologyMemberGraphPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
            continue
        if _ONTOLOGY_MEMBER_FEATURES_PATH_RE.match(planned.path):
            payloads[planned.path] = OntologyMemberFeaturesPayload.model_validate_json(
                planned.content
            ).model_dump(mode="json")
    return payloads


def _load_published_payload(root: Path, path: str) -> Any:
    if path == _ONTOLOGY_GLOBAL_PATH:
        return load_ontology_edges(root).model_dump(mode="json")
    if path == _ONTOLOGY_AGENT_TOOLS_PATH:
        return load_ontology_agent_tools(root).model_dump(mode="json")
    if path == _ONTOLOGY_SCHEMA_PATH:
        return load_ontology_static_schema(root).model_dump(mode="json")
    if path == _ONTOLOGY_FRONTEND_CONTRACT_PATH:
        return load_ontology_frontend_contract(root).model_dump(mode="json")
    if path == _ONTOLOGY_FRONTEND_TYPES_PATH:
        return load_ontology_frontend_types(root)
    if path == _ONTOLOGY_FRONTEND_CLIENT_PATH:
        return load_ontology_frontend_client(root)
    if path == _ONTOLOGY_INDEX_PATH:
        return load_ontology_index(root).model_dump(mode="json")
    if path == _ONTOLOGY_FRONTEND_INDEX_PATH:
        return load_ontology_frontend_index(root).model_dump(mode="json")
    member_match = _ONTOLOGY_MEMBER_PATH_RE.match(path)
    if member_match is not None:
        return load_ontology_member_edges(root, member_match.group("member_id")).model_dump(
            mode="json"
        )
    feature_match = _ONTOLOGY_MEMBER_FEATURES_PATH_RE.match(path)
    if feature_match is not None:
        return load_ontology_member_features(root, feature_match.group("member_id")).model_dump(
            mode="json"
        )
    raise ValueError(f"unsupported ontology path: {path}")


def verify_published_ontology_roundtrip(
    conn: Any,
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishRoundtripStageResult:
    """Verify published ontology graph artifacts against canonical DB rows."""
    manifest_paths = _manifest_ontology_paths(manifest_payload)
    rows = fetch_all_ontology_edge_rows(conn)

    try:
        expected_by_path = _expected_payloads(manifest_payload.snapshot_id, rows)
    except Exception as exc:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=0,
            issues=(
                _issue(
                    f"failed to assemble ontology graph from DB rows: {exc}",
                ),
            ),
        )

    paths = sorted(set(manifest_paths) | set(expected_by_path))
    if not paths:
        return PublishRoundtripStageResult(stage=_STAGE, checked=0, issues=())

    issues: list[PublishRoundtripIssue] = []
    checked = 0
    manifest_path_set = set(manifest_paths)

    for path in paths:
        expected = expected_by_path.get(path)
        if expected is None:
            issues.append(_issue("unexpected ontology artifact in manifest", path=path))
            checked += 1
            continue
        if path not in manifest_path_set:
            issues.append(_issue("expected ontology artifact missing from manifest", path=path))
            checked += 1
            continue

        try:
            published = _load_published_payload(root, path)
        except Exception as exc:
            issues.append(_issue(f"cannot load published ontology artifact: {exc}", path=path))
            checked += 1
            continue

        checked += 1
        if published != expected:
            issues.append(
                _issue("ontology payload mismatch between published artifact and DB", path=path)
            )

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=checked,
        issues=tuple(issues),
    )
