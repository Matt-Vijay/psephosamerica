"""Verify published ontology edge artifacts."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from src.export.local_store import (
    load_ontology_agent_tools,
    load_ontology_edges,
    load_ontology_frontend_client,
    load_ontology_frontend_contract,
    load_ontology_frontend_index,
    load_ontology_frontend_types,
    load_ontology_index,
    load_ontology_member_edges,
    load_ontology_member_features,
    load_ontology_static_schema,
)
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.ontology.artifact_paths import ontology_frontend_artifact_paths
from src.ontology.frontend_contracts import (
    build_ontology_frontend_contract,
    ontology_frontend_model_names,
)
from src.ontology.versions import (
    ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
    ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ONTOLOGY_FRONTEND_CONTRACT_VERSION,
    ONTOLOGY_FRONTEND_INDEX_VERSION,
    ONTOLOGY_STATIC_SCHEMA_VERSION,
)
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_STAGE = "ontology"
_MEMBER_ONTOLOGY_PREFIX = ("ontology", "members")
_MEMBER_FEATURES_PREFIX = ("ontology", "member-features")


def _issue(
    message: str,
    *,
    path: str | None = None,
    severity: IssueSeverity = "error",
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def verify_local_ontology_edges(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishVerifyStageResult:
    """Verify optional frontend-fast ontology edge artifacts."""
    artifact_paths = ontology_frontend_artifact_paths()
    global_path = artifact_paths["global_edges"]
    agent_tools_path = artifact_paths["agent_tools"]
    index_path = artifact_paths["index"]
    schema_path = artifact_paths["schema"]
    frontend_index_path = artifact_paths["frontend_index"]
    frontend_contract_path = artifact_paths["frontend_contract"]
    frontend_types_path = artifact_paths["frontend_types"]
    frontend_client_path = artifact_paths["frontend_client"]
    entries = [
        entry
        for entry in manifest_payload.entries
        if entry.path
        in {
            global_path,
            agent_tools_path,
            index_path,
            schema_path,
            frontend_index_path,
            frontend_contract_path,
            frontend_types_path,
            frontend_client_path,
        }
        or _member_features_id_from_path(entry.path) is not None
        or _member_ontology_id_from_path(entry.path) is not None
    ]
    issues: list[PublishVerifyIssue] = []

    if not entries:
        return PublishVerifyStageResult(stage=_STAGE, checked=0, issues=())

    global_entries = [entry for entry in entries if entry.path == global_path]
    if len(global_entries) > 1:
        issues.append(_issue("manifest lists ontology edges more than once", path=global_path))
    if global_entries:
        _verify_global_ontology_edges(root, global_path, issues)

    agent_tools_entries = [entry for entry in entries if entry.path == agent_tools_path]
    if len(agent_tools_entries) > 1:
        issues.append(
            _issue("manifest lists ontology agent tools more than once", path=agent_tools_path)
        )
    if agent_tools_entries:
        _verify_ontology_agent_tools(root, agent_tools_path, issues)

    schema_entries = [entry for entry in entries if entry.path == schema_path]
    if len(schema_entries) > 1:
        issues.append(_issue("manifest lists ontology schema more than once", path=schema_path))
    if schema_entries:
        _verify_ontology_schema(root, schema_path, issues)

    frontend_contract_entries = [entry for entry in entries if entry.path == frontend_contract_path]
    if len(frontend_contract_entries) > 1:
        issues.append(
            _issue(
                "manifest lists ontology frontend contract more than once",
                path=frontend_contract_path,
            )
        )
    if frontend_contract_entries:
        _verify_ontology_frontend_contract(root, frontend_contract_path, issues)

    frontend_types_entries = [entry for entry in entries if entry.path == frontend_types_path]
    if len(frontend_types_entries) > 1:
        issues.append(
            _issue(
                "manifest lists ontology frontend types more than once",
                path=frontend_types_path,
            )
        )
    if frontend_types_entries:
        _verify_ontology_frontend_types(root, frontend_types_path, issues)

    frontend_client_entries = [entry for entry in entries if entry.path == frontend_client_path]
    if len(frontend_client_entries) > 1:
        issues.append(
            _issue(
                "manifest lists ontology frontend client more than once",
                path=frontend_client_path,
            )
        )
    if frontend_client_entries:
        _verify_ontology_frontend_client(root, frontend_client_path, issues)

    index_entries = [entry for entry in entries if entry.path == index_path]
    if len(index_entries) > 1:
        issues.append(_issue("manifest lists ontology index more than once", path=index_path))
    if index_entries:
        _verify_ontology_index(root, index_path, issues)

    frontend_index_entries = [entry for entry in entries if entry.path == frontend_index_path]
    if len(frontend_index_entries) > 1:
        issues.append(
            _issue(
                "manifest lists ontology frontend index more than once",
                path=frontend_index_path,
            )
        )
    if frontend_index_entries:
        _verify_ontology_frontend_index(root, frontend_index_path, issues)

    if global_entries or index_entries:
        _verify_ontology_index_matches_artifacts(root, index_path, global_path, entries, issues)
    if global_entries or frontend_index_entries:
        _verify_ontology_frontend_index_matches_artifacts(
            root,
            frontend_index_path,
            global_path,
            entries,
            issues,
        )

    seen_member_paths: set[str] = set()
    seen_feature_paths: set[str] = set()
    for entry in entries:
        member_features_id = _member_features_id_from_path(entry.path)
        if member_features_id is not None:
            if entry.path in seen_feature_paths:
                issues.append(
                    _issue(
                        "manifest lists ontology member features more than once", path=entry.path
                    )
                )
                continue
            seen_feature_paths.add(entry.path)
            _verify_member_ontology_features(root, entry.path, member_features_id, issues)
            continue

        member_bioguide_id = _member_ontology_id_from_path(entry.path)
        if member_bioguide_id is None:
            continue
        if entry.path in seen_member_paths:
            issues.append(
                _issue("manifest lists ontology member edges more than once", path=entry.path)
            )
            continue
        seen_member_paths.add(entry.path)
        _verify_member_ontology_edges(root, entry.path, member_bioguide_id, issues)

    _verify_member_ontology_artifact_pairs(
        root,
        seen_member_paths=seen_member_paths,
        seen_feature_paths=seen_feature_paths,
        issues=issues,
    )

    return PublishVerifyStageResult(stage=_STAGE, checked=len(entries), issues=tuple(issues))


def _member_ontology_id_from_path(path: str) -> str | None:
    pure = PurePosixPath(path)
    if len(pure.parts) != 3 or pure.parts[:2] != _MEMBER_ONTOLOGY_PREFIX:
        return None
    name = pure.parts[2]
    if not name.endswith(".json"):
        return None
    member_bioguide_id = name.removesuffix(".json")
    return member_bioguide_id or None


def _member_features_id_from_path(path: str) -> str | None:
    pure = PurePosixPath(path)
    if len(pure.parts) != 3 or pure.parts[:2] != _MEMBER_FEATURES_PREFIX:
        return None
    name = pure.parts[2]
    if not name.endswith(".json"):
        return None
    member_bioguide_id = name.removesuffix(".json")
    return member_bioguide_id or None


def _verify_global_ontology_edges(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology edges file missing: {path}", path=path))
        return

    try:
        graph = load_ontology_edges(root)
    except Exception as exc:
        issues.append(_issue(f"ontology edges failed to load ({path}): {exc}", path=path))
        return

    edge_ids = [edge.edge_id for edge in graph.edges]
    if len(edge_ids) != len(set(edge_ids)):
        issues.append(_issue("ontology edges contain duplicate edge_id values", path=path))
    if graph.edge_count != len(graph.edges):
        issues.append(_issue("ontology edge_count does not match edges length", path=path))


def _verify_ontology_index(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology index file missing: {path}", path=path))
        return

    try:
        load_ontology_index(root)
    except Exception as exc:
        issues.append(_issue(f"ontology index failed to load ({path}): {exc}", path=path))


def _verify_ontology_agent_tools(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology agent tools file missing: {path}", path=path))
        return

    try:
        manifest = load_ontology_agent_tools(root)
    except Exception as exc:
        issues.append(_issue(f"ontology agent tools failed to load ({path}): {exc}", path=path))
        return
    if manifest.schema_version != ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION:
        issues.append(
            _issue("ontology agent tools schema_version does not match contract", path=path)
        )
    if manifest.contract_version != ONTOLOGY_FRONTEND_CONTRACT_VERSION:
        issues.append(
            _issue("ontology agent tools contract_version does not match contract", path=path)
        )
    known_artifact_paths = set(ontology_frontend_artifact_paths().values())
    for tool in manifest.tools:
        unknown_paths = [
            artifact_path
            for artifact_path in tool.required_artifact_paths
            if artifact_path not in known_artifact_paths
        ]
        if unknown_paths:
            issues.append(
                _issue(
                    "ontology agent tools reference unknown required artifact path",
                    path=path,
                )
            )
            break
    if not any(tool.source_required for tool in manifest.tools):
        issues.append(_issue("ontology agent tools must include source-required tools", path=path))


def _verify_ontology_schema(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology schema file missing: {path}", path=path))
        return

    try:
        schema = load_ontology_static_schema(root)
    except Exception as exc:
        issues.append(_issue(f"ontology schema failed to load ({path}): {exc}", path=path))
        return
    if schema.schema_version != ONTOLOGY_STATIC_SCHEMA_VERSION:
        issues.append(_issue("ontology schema_version does not match contract", path=path))


def _verify_ontology_frontend_contract(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology frontend contract file missing: {path}", path=path))
        return

    try:
        contract = load_ontology_frontend_contract(root)
    except Exception as exc:
        issues.append(
            _issue(f"ontology frontend contract failed to load ({path}): {exc}", path=path)
        )
        return
    if contract.schema_version != ONTOLOGY_FRONTEND_CONTRACT_VERSION:
        issues.append(
            _issue("ontology frontend contract schema_version does not match contract", path=path)
        )
    if contract.artifact_paths != ontology_frontend_artifact_paths():
        issues.append(_issue("ontology frontend contract artifact_paths mismatch", path=path))
    if contract.model_names != ontology_frontend_model_names():
        issues.append(_issue("ontology frontend contract model_names mismatch", path=path))
    if contract.json_schemas != build_ontology_frontend_contract().json_schemas:
        issues.append(_issue("ontology frontend contract json_schemas mismatch", path=path))


def _verify_ontology_frontend_types(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology frontend types file missing: {path}", path=path))
        return

    try:
        declarations = load_ontology_frontend_types(root)
    except Exception as exc:
        issues.append(_issue(f"ontology frontend types failed to load ({path}): {exc}", path=path))
        return
    if "export interface OntologyFrontendContractSchemas" not in declarations:
        issues.append(_issue("ontology frontend types missing contract interface", path=path))
    if ONTOLOGY_FRONTEND_CONTRACT_VERSION not in declarations:
        issues.append(_issue("ontology frontend types missing contract version", path=path))
    for artifact_key, artifact_path in ontology_frontend_artifact_paths().items():
        expected_line = f'{artifact_key}: "{artifact_path}";'
        if expected_line not in declarations:
            issues.append(_issue("ontology frontend types artifact path mismatch", path=path))
            break
    for model_name in ontology_frontend_model_names():
        expected_line = f"  {model_name}: JsonSchema;"
        if expected_line not in declarations:
            issues.append(_issue("ontology frontend types schema model mismatch", path=path))
            break
    runtime_member_types = (
        "`ontology/member-features/${string}.json`",
        "`ontology/members/${string}.json`",
    )
    if not all(runtime_path in declarations for runtime_path in runtime_member_types):
        issues.append(
            _issue("ontology frontend types missing runtime member path types", path=path)
        )


def _verify_ontology_frontend_client(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology frontend client file missing: {path}", path=path))
        return

    try:
        source = load_ontology_frontend_client(root)
    except Exception as exc:
        issues.append(_issue(f"ontology frontend client failed to load ({path}): {exc}", path=path))
        return
    if ONTOLOGY_FRONTEND_CLIENT_VERSION not in source:
        issues.append(_issue("ontology frontend client missing client version", path=path))
    if "PsephosAmericaOntologyClient" not in source:
        issues.append(_issue("ontology frontend client missing client class", path=path))
    if "ONTOLOGY_FRONTEND_ARTIFACT_PATHS" not in source:
        issues.append(_issue("ontology frontend client missing artifact paths", path=path))
    if "validateOntologyMemberBioguideId" not in source:
        issues.append(_issue("ontology frontend client missing member id guard", path=path))
    if "Invalid ontology member Bioguide ID" not in source:
        issues.append(_issue("ontology frontend client missing invalid member id error", path=path))
    for artifact_path in ontology_frontend_artifact_paths().values():
        if artifact_path not in source:
            issues.append(_issue("ontology frontend client artifact path mismatch", path=path))
            break


def _verify_ontology_frontend_index(
    root: Path,
    path: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology frontend index file missing: {path}", path=path))
        return

    try:
        frontend_index = load_ontology_frontend_index(root)
    except Exception as exc:
        issues.append(_issue(f"ontology frontend index failed to load ({path}): {exc}", path=path))
        return
    if frontend_index.schema_version != ONTOLOGY_FRONTEND_INDEX_VERSION:
        issues.append(
            _issue("ontology frontend index schema_version does not match contract", path=path)
        )


def _verify_ontology_index_matches_artifacts(
    root: Path,
    index_path: str,
    global_path: str,
    entries: list[ManifestEntry],
    issues: list[PublishVerifyIssue],
) -> None:
    manifest_paths = {entry.path for entry in entries}
    if index_path not in manifest_paths:
        return

    try:
        index = load_ontology_index(root)
    except Exception:
        return

    if global_path in manifest_paths:
        try:
            global_graph = load_ontology_edges(root)
        except Exception:
            global_graph = None
        if global_graph is not None and index.edge_count != global_graph.edge_count:
            issues.append(_issue("ontology index edge_count does not match graph", path=index_path))

    for member_bioguide_id in index.available_member_graphs:
        member_path = f"ontology/members/{member_bioguide_id}.json"
        member_features_path = f"ontology/member-features/{member_bioguide_id}.json"
        if member_path not in manifest_paths:
            issues.append(
                _issue(
                    f"ontology index member graph is not listed in manifest: {member_path}",
                    path=index_path,
                )
            )
            continue
        try:
            member_graph = load_ontology_member_edges(root, member_bioguide_id)
        # Malformed optional member graphs are handled elsewhere.
        except Exception:  # nosec B112
            continue
        expected_count = index.member_edge_counts.get(member_bioguide_id)
        if expected_count is not None and expected_count != member_graph.edge_count:
            issues.append(
                _issue(
                    "ontology index member_edge_counts does not match member graph",
                    path=index_path,
                )
            )
        if member_features_path not in manifest_paths:
            issues.append(
                _issue(
                    "ontology index member features are not listed in manifest: "
                    f"{member_features_path}",
                    path=index_path,
                )
            )


def _verify_ontology_frontend_index_matches_artifacts(
    root: Path,
    frontend_index_path: str,
    global_path: str,
    entries: list[ManifestEntry],
    issues: list[PublishVerifyIssue],
) -> None:
    manifest_paths = {entry.path for entry in entries}
    if frontend_index_path not in manifest_paths:
        return

    try:
        frontend_index = load_ontology_frontend_index(root)
    except Exception:
        return

    if global_path not in manifest_paths:
        return

    try:
        global_graph = load_ontology_edges(root)
    except Exception:
        return

    if frontend_index.edge_count != global_graph.edge_count:
        issues.append(
            _issue(
                "ontology frontend index edge_count does not match graph",
                path=frontend_index_path,
            )
        )


def _verify_member_ontology_edges(
    root: Path,
    path: str,
    member_bioguide_id: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology member edges file missing: {path}", path=path))
        return

    try:
        graph = load_ontology_member_edges(root, member_bioguide_id)
    except Exception as exc:
        issues.append(_issue(f"ontology member edges failed to load ({path}): {exc}", path=path))
        return

    if graph.member_bioguide_id != member_bioguide_id:
        issues.append(_issue("ontology member path does not match payload member", path=path))
    edge_ids = [edge.edge_id for edge in graph.edges]
    if len(edge_ids) != len(set(edge_ids)):
        issues.append(_issue("ontology member edges contain duplicate edge_id values", path=path))
    if graph.edge_count != len(graph.edges):
        issues.append(_issue("ontology member edge_count does not match edges length", path=path))


def _verify_member_ontology_features(
    root: Path,
    path: str,
    member_bioguide_id: str,
    issues: list[PublishVerifyIssue],
) -> None:
    if not path_is_confined(path):
        issues.append(_issue("entry path escapes publish root", path=path))
        return

    file = root / path
    if not file.exists():
        issues.append(_issue(f"ontology member features file missing: {path}", path=path))
        return

    try:
        features = load_ontology_member_features(root, member_bioguide_id)
    except Exception as exc:
        issues.append(_issue(f"ontology member features failed to load ({path}): {exc}", path=path))
        return

    if features.member_bioguide_id != member_bioguide_id:
        issues.append(
            _issue("ontology member feature path does not match payload member", path=path)
        )
    member_graph_path = f"ontology/members/{member_bioguide_id}.json"
    if (root / member_graph_path).is_file():
        try:
            graph = load_ontology_member_edges(root, member_bioguide_id)
        except Exception:
            return
        if features.edge_count != graph.edge_count:
            issues.append(
                _issue(
                    "ontology member feature edge_count does not match member graph",
                    path=path,
                )
            )
        expected_edge_type_counts = _member_graph_edge_type_counts(graph)
        if features.edge_type_counts != expected_edge_type_counts:
            issues.append(
                _issue(
                    "ontology member feature edge_type_counts do not match member graph",
                    path=path,
                )
            )
        expected_source_type_counts = _member_graph_source_type_counts(graph)
        if features.source_type_counts != expected_source_type_counts:
            issues.append(
                _issue(
                    "ontology member feature source_type_counts do not match member graph",
                    path=path,
                )
            )


def _verify_member_ontology_artifact_pairs(
    root: Path,
    *,
    seen_member_paths: set[str],
    seen_feature_paths: set[str],
    issues: list[PublishVerifyIssue],
) -> None:
    for member_path in sorted(seen_member_paths):
        member_bioguide_id = _member_ontology_id_from_path(member_path)
        if member_bioguide_id is None:
            continue
        features_path = f"ontology/member-features/{member_bioguide_id}.json"
        if features_path not in seen_feature_paths:
            issues.append(
                _issue(
                    "ontology member graph has no matching member features",
                    path=member_path,
                )
            )
    for features_path in sorted(seen_feature_paths):
        member_bioguide_id = _member_features_id_from_path(features_path)
        if member_bioguide_id is None:
            continue
        member_path = f"ontology/members/{member_bioguide_id}.json"
        if member_path not in seen_member_paths:
            issues.append(
                _issue(
                    "ontology member features have no matching member graph",
                    path=features_path,
                )
            )


def _member_graph_edge_type_counts(graph: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in getattr(graph, "edges", []):
        edge_type = str(edge.edge_type)
        counts[edge_type] = counts.get(edge_type, 0) + 1
    return dict(sorted(counts.items()))


def _member_graph_source_type_counts(graph: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in getattr(graph, "edges", []):
        for anchor in edge.source_anchors:
            source_type = str(anchor.source_type)
            counts[source_type] = counts.get(source_type, 0) + 1
    return dict(sorted(counts.items()))
