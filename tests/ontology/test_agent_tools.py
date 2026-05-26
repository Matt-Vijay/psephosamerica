from __future__ import annotations

import pytest

from src.ontology.agent_tools import (
    ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
    OntologyAgentToolManifestPayload,
    OntologyAgentToolPayload,
    build_ontology_agent_tool_manifest,
)
from src.ontology.frontend_contracts import ONTOLOGY_FRONTEND_CONTRACT_VERSION
from src.ontology.frontend_contracts import build_ontology_frontend_contract


def test_build_ontology_agent_tool_manifest_exports_read_only_source_tools() -> None:
    manifest = build_ontology_agent_tool_manifest()

    assert manifest.schema_version == ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION
    assert manifest.contract_version == ONTOLOGY_FRONTEND_CONTRACT_VERSION
    assert [tool.tool_name for tool in manifest.tools] == sorted(
        tool.tool_name for tool in manifest.tools
    )
    assert "get_member_ontology_context" in {tool.tool_name for tool in manifest.tools}
    member_tool = next(
        tool for tool in manifest.tools if tool.tool_name == "get_member_ontology_context"
    )
    assert member_tool.mode == "read_context"
    assert member_tool.source_required is True
    assert member_tool.required_artifact_paths == [
        "ontology/member-features/{member_bioguide_id}.json",
        "ontology/members/{member_bioguide_id}.json",
    ]


def test_agent_tool_manifest_rejects_unsorted_tools() -> None:
    tool = OntologyAgentToolPayload(
        tool_name="z_tool",
        label="Z Tool",
        mode="read_artifact",
        description="Read an artifact.",
        required_artifact_paths=[],
        source_required=False,
    )
    earlier_tool = tool.model_copy(update={"tool_name": "a_tool", "label": "A Tool"})

    with pytest.raises(ValueError, match="tools must be sorted"):
        OntologyAgentToolManifestPayload(
            schema_version=ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
            contract_version=ONTOLOGY_FRONTEND_CONTRACT_VERSION,
            tools=[tool, earlier_tool],
        )


def test_agent_tool_rejects_duplicate_required_paths() -> None:
    with pytest.raises(ValueError, match="required_artifact_paths"):
        OntologyAgentToolPayload(
            tool_name="bad_tool",
            label="Bad Tool",
            mode="read_context",
            description="Bad paths.",
            required_artifact_paths=["ontology/edges.json", "ontology/edges.json"],
            source_required=True,
        )


def test_agent_tool_rejects_source_optional_context_tools() -> None:
    with pytest.raises(ValueError, match="context and audit tools require source backing"):
        OntologyAgentToolPayload(
            tool_name="unsafe_context",
            label="Unsafe Context",
            mode="read_context",
            description="Unsafe source-optional context read.",
            required_artifact_paths=["ontology/edges.json"],
            source_required=False,
        )


def test_agent_tool_required_paths_are_covered_by_frontend_contract() -> None:
    contract_paths = set(build_ontology_frontend_contract().artifact_paths.values())
    manifest = build_ontology_agent_tool_manifest()

    required_paths = {path for tool in manifest.tools for path in tool.required_artifact_paths}

    assert required_paths.issubset(contract_paths)
