from __future__ import annotations

ONTOLOGY_FRONTEND_ARTIFACT_PATHS: dict[str, str] = {
    "agent_tools": "ontology/agent-tools.json",
    "frontend_client": "ontology/client.ts",
    "frontend_contract": "ontology/contracts.json",
    "frontend_index": "ontology/frontend-index.json",
    "frontend_types": "ontology/contracts.d.ts",
    "global_edges": "ontology/edges.json",
    "index": "ontology/index.json",
    "member_features": "ontology/member-features/{member_bioguide_id}.json",
    "member_graph": "ontology/members/{member_bioguide_id}.json",
    "schema": "ontology/schema.json",
}

_REQUIRED_ARTIFACT_PATH_KEYS = frozenset(
    {
        "agent_tools",
        "frontend_client",
        "frontend_contract",
        "frontend_index",
        "frontend_types",
        "global_edges",
        "index",
        "member_features",
        "member_graph",
        "schema",
    }
)


def ontology_frontend_artifact_paths() -> dict[str, str]:
    """Return a copy of the canonical frontend/LLM ontology artifact ABI."""
    return validate_ontology_frontend_artifact_paths(ONTOLOGY_FRONTEND_ARTIFACT_PATHS)


def validate_ontology_frontend_artifact_paths(paths: dict[str, str]) -> dict[str, str]:
    """Validate and return a sorted copy of the ontology artifact path ABI."""
    missing_keys = _REQUIRED_ARTIFACT_PATH_KEYS - set(paths)
    if missing_keys:
        raise ValueError("missing required artifact path keys")
    extra_keys = set(paths) - _REQUIRED_ARTIFACT_PATH_KEYS
    if extra_keys:
        raise ValueError("unknown artifact path keys")
    sorted_paths = dict(sorted(paths.items()))
    if paths != sorted_paths:
        raise ValueError("artifact path keys must be sorted")
    if len(set(paths.values())) != len(paths):
        raise ValueError("ontology artifact paths must use unique artifact paths")
    for key, path in paths.items():
        if not path.startswith("ontology/"):
            raise ValueError(f"ontology artifact path must stay under ontology/: {key}")
        path_parts = path.split("/")
        if "\x00" in path or ".." in path_parts:
            raise ValueError(f"ontology artifact path is not confined: {key}")
        if "" in path_parts:
            raise ValueError(f"ontology artifact path has malformed segments: {key}")
        if key not in {"member_features", "member_graph"} and "{" in path:
            raise ValueError(f"static artifact paths must not contain templates: {key}")
    for key in ("member_features", "member_graph"):
        if "{member_bioguide_id}" not in paths[key]:
            raise ValueError("member artifact path templates must include member_bioguide_id")
    return dict(sorted_paths)
