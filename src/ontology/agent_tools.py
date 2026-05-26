from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from src.ontology.artifact_paths import ontology_frontend_artifact_paths
from src.ontology.versions import (
    ONTOLOGY_AGENT_TOOL_CONTRACT_VERSION,
    ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
)

OntologyAgentToolMode = Literal["read_artifact", "read_context", "audit_sources"]


class OntologyAgentToolPayload(BaseModel):
    """Read-only ontology tool definition for LLM and agent consumers."""

    tool_name: str
    label: str
    mode: OntologyAgentToolMode
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    required_artifact_paths: list[str] = Field(default_factory=list)
    source_required: bool

    @model_validator(mode="after")
    def required_paths_are_sorted_unique(self) -> Self:
        if self.required_artifact_paths != sorted(set(self.required_artifact_paths)):
            raise ValueError("required_artifact_paths must be sorted and unique")
        if self.mode in {"read_context", "audit_sources"} and not self.source_required:
            raise ValueError("context and audit tools require source backing")
        return self


class OntologyAgentToolManifestPayload(BaseModel):
    """Machine-readable read surface for source-backed ontology context."""

    schema_version: str
    contract_version: str
    tools: list[OntologyAgentToolPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def tools_are_sorted_unique(self) -> Self:
        tool_names = [tool.tool_name for tool in self.tools]
        if tool_names != sorted(set(tool_names)):
            raise ValueError("tools must be sorted by unique tool_name")
        return self


def build_ontology_agent_tool_manifest() -> OntologyAgentToolManifestPayload:
    """Build the deterministic read-only tool manifest for ontology consumers."""
    paths = ontology_frontend_artifact_paths()
    return OntologyAgentToolManifestPayload(
        schema_version=ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
        contract_version=ONTOLOGY_AGENT_TOOL_CONTRACT_VERSION,
        tools=sorted(
            [
                OntologyAgentToolPayload(
                    tool_name="audit_ontology_claim_sources",
                    label="Audit Ontology Claim Sources",
                    mode="audit_sources",
                    description=(
                        "Inspect source keys and source-backed edges before using an ontology "
                        "claim in a product or LLM answer."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "source_key": {
                                "type": "string",
                                "description": "Optional source key from ontology/frontend-index.json.",
                            }
                        },
                        "additionalProperties": False,
                    },
                    required_artifact_paths=_required_artifact_paths(
                        paths["global_edges"],
                        paths["frontend_index"],
                    ),
                    source_required=True,
                ),
                OntologyAgentToolPayload(
                    tool_name="get_member_ontology_context",
                    label="Get Member Ontology Context",
                    mode="read_context",
                    description=(
                        "Load the member graph and member feature summary for a Bioguide ID."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["member_bioguide_id"],
                        "properties": {
                            "member_bioguide_id": {
                                "type": "string",
                                "description": "Official Bioguide ID.",
                            }
                        },
                        "additionalProperties": False,
                    },
                    required_artifact_paths=_required_artifact_paths(
                        paths["member_features"],
                        paths["member_graph"],
                    ),
                    source_required=True,
                ),
                OntologyAgentToolPayload(
                    tool_name="get_ontology_contract",
                    label="Get Ontology Contract",
                    mode="read_artifact",
                    description=(
                        "Load the static ontology schema, JSON Schema bundle, generated "
                        "declarations, and generated static client."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    required_artifact_paths=_required_artifact_paths(
                        paths["frontend_client"],
                        paths["frontend_contract"],
                        paths["frontend_types"],
                        paths["schema"],
                    ),
                    source_required=False,
                ),
                OntologyAgentToolPayload(
                    tool_name="list_source_backed_ontology_edges",
                    label="List Source-Backed Ontology Edges",
                    mode="read_context",
                    description=(
                        "Load the global graph plus frontend lookup tables for source-backed "
                        "edge discovery by member, committee, sector, issuer, link type, or source key."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "object_id": {
                                "type": "string",
                                "description": "Optional member, committee, sector, or issuer ID.",
                            },
                            "object_type": {
                                "type": "string",
                                "enum": ["member", "committee", "sector", "issuer"],
                            },
                        },
                        "additionalProperties": False,
                    },
                    required_artifact_paths=_required_artifact_paths(
                        paths["global_edges"],
                        paths["frontend_index"],
                    ),
                    source_required=True,
                ),
            ],
            key=lambda tool: tool.tool_name,
        ),
    )


def _required_artifact_paths(*paths: str) -> list[str]:
    return sorted(paths)
