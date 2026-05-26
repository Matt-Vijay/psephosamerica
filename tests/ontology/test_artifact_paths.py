from __future__ import annotations

import pytest

from src.ontology.artifact_paths import (
    ONTOLOGY_FRONTEND_ARTIFACT_PATHS,
    ontology_frontend_artifact_paths,
    validate_ontology_frontend_artifact_paths,
)


def test_ontology_frontend_artifact_paths_are_sorted_and_complete() -> None:
    paths = ontology_frontend_artifact_paths()

    assert paths == dict(sorted(paths.items()))
    assert paths == {
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


def test_ontology_frontend_artifact_paths_returns_copy() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = "ontology/stale-schema.json"

    assert ONTOLOGY_FRONTEND_ARTIFACT_PATHS["schema"] == "ontology/schema.json"
    assert ontology_frontend_artifact_paths()["schema"] == "ontology/schema.json"


def test_validate_ontology_frontend_artifact_paths_rejects_missing_key() -> None:
    paths = ontology_frontend_artifact_paths()
    paths.pop("agent_tools")

    with pytest.raises(ValueError, match="missing required artifact path keys"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_duplicate_values() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = paths["index"]

    with pytest.raises(ValueError, match="unique artifact paths"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_unconfined_paths() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = "ontology/../schema.json"

    with pytest.raises(ValueError, match="not confined"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_malformed_segments() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = "ontology//schema.json"

    with pytest.raises(ValueError, match="malformed"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_backslashes() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = "ontology\\schema.json"

    with pytest.raises(ValueError, match="ontology/"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_static_templates() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["schema"] = "ontology/schema-{member_bioguide_id}.json"

    with pytest.raises(ValueError, match="static artifact paths"):
        validate_ontology_frontend_artifact_paths(paths)


def test_validate_ontology_frontend_artifact_paths_rejects_bad_member_template() -> None:
    paths = ontology_frontend_artifact_paths()
    paths["member_graph"] = "ontology/members/{member_id}.json"

    with pytest.raises(ValueError, match="member_bioguide_id"):
        validate_ontology_frontend_artifact_paths(paths)
