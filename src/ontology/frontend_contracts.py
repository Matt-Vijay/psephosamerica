from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from src.ontology.agent_tools import OntologyAgentToolManifestPayload
from src.ontology.artifact_paths import ontology_frontend_artifact_paths
from src.ontology.contracts import (
    OntologyEdgePayload,
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
)
from src.ontology.static_schema import (
    OntologyFrontendIndexPayload,
    OntologyStaticSchemaPayload,
)
from src.ontology.versions import (
    ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ONTOLOGY_FRONTEND_CONTRACT_VERSION,
)


class OntologyFrontendContractPayload(BaseModel):
    """Generated schema bundle for frontend and LLM ontology clients."""

    schema_version: str
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    model_names: list[str] = Field(default_factory=list)
    json_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def contract_is_sorted_and_complete(self) -> OntologyFrontendContractPayload:
        if self.model_names != sorted(set(self.model_names)):
            raise ValueError("model_names must be sorted and unique")
        if set(self.model_names) != set(self.json_schemas):
            raise ValueError("model_names must match json_schemas keys")
        if self.artifact_paths != dict(sorted(self.artifact_paths.items())):
            raise ValueError("artifact_paths must be sorted by key")
        return self


_FRONTEND_MODEL_TYPES: tuple[type[BaseModel], ...] = (
    OntologyAgentToolManifestPayload,
    OntologyEdgePayload,
    OntologyFrontendIndexPayload,
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
    OntologyStaticSchemaPayload,
)


def ontology_frontend_model_names() -> list[str]:
    """Return the public Pydantic payload names covered by ontology contracts."""
    return sorted(model.__name__ for model in _FRONTEND_MODEL_TYPES)


def build_ontology_frontend_contract() -> OntologyFrontendContractPayload:
    """Generate JSON Schema directly from the public ontology Pydantic models."""
    schemas = {
        model.__name__: TypeAdapter(model).json_schema()
        for model in sorted(_FRONTEND_MODEL_TYPES, key=lambda item: item.__name__)
    }
    return OntologyFrontendContractPayload(
        schema_version=ONTOLOGY_FRONTEND_CONTRACT_VERSION,
        artifact_paths=dict(sorted(ontology_frontend_artifact_paths().items())),
        model_names=ontology_frontend_model_names(),
        json_schemas=schemas,
    )


def build_ontology_typescript_declarations() -> str:
    """Build a deterministic TypeScript declaration surface for ontology artifacts."""
    contract = build_ontology_frontend_contract()
    artifact_path_union = " | ".join(
        _typescript_artifact_path_type(path) for path in sorted(contract.artifact_paths.values())
    )
    model_lines = "\n".join(f"  {name}: JsonSchema;" for name in contract.model_names)
    path_lines = "\n".join(f'  {key}: "{value}";' for key, value in contract.artifact_paths.items())
    return (
        "// Generated from Psephos America ontology Pydantic contracts.\n"
        "// Do not edit by hand; regenerate from src.ontology.frontend_contracts.\n\n"
        f'export const ONTOLOGY_FRONTEND_CONTRACT_VERSION = "{contract.schema_version}" as const;\n\n'
        "export type JsonSchema = Record<string, unknown>;\n\n"
        f"export type OntologyFrontendArtifactPath = {artifact_path_union};\n\n"
        "export interface OntologyFrontendArtifactPaths {\n"
        f"{path_lines}\n"
        "}\n\n"
        "export interface OntologyFrontendContractSchemas {\n"
        f"{model_lines}\n"
        "}\n"
    )


def _typescript_artifact_path_type(path: str) -> str:
    if "{" not in path:
        return f'"{path}"'
    template_path = path
    for field_name in ("member_bioguide_id",):
        template_path = template_path.replace(f"{{{field_name}}}", "${string}")
    return f"`{template_path}`"


def build_ontology_typescript_client() -> str:
    """Build a deterministic static TypeScript client for published ontology artifacts."""
    contract = build_ontology_frontend_contract()
    path_lines = "\n".join(f'  {key}: "{value}",' for key, value in contract.artifact_paths.items())
    return (
        "// Generated from Psephos America ontology Pydantic contracts.\n"
        "// Do not edit by hand; regenerate from src.ontology.frontend_contracts.\n\n"
        'import type { OntologyFrontendArtifactPath } from "./contracts";\n\n'
        f'export const ONTOLOGY_FRONTEND_CLIENT_VERSION = "{ONTOLOGY_FRONTEND_CLIENT_VERSION}" as const;\n'
        f'export const ONTOLOGY_FRONTEND_CONTRACT_VERSION = "{contract.schema_version}" as const;\n\n'
        "export const ONTOLOGY_FRONTEND_ARTIFACT_PATHS = {\n"
        f"{path_lines}\n"
        "} as const;\n\n"
        "export type OntologyFrontendArtifactKey = keyof typeof ONTOLOGY_FRONTEND_ARTIFACT_PATHS;\n\n"
        "export interface PsephosAmericaOntologyClientOptions {\n"
        "  baseUrl?: string;\n"
        "  fetchImpl?: typeof fetch;\n"
        "}\n\n"
        "export class PsephosAmericaOntologyClient {\n"
        "  readonly baseUrl: string;\n"
        "  readonly fetchImpl: typeof fetch;\n\n"
        "  constructor(options: PsephosAmericaOntologyClientOptions = {}) {\n"
        '    this.baseUrl = options.baseUrl ?? "";\n'
        "    this.fetchImpl = options.fetchImpl ?? fetch;\n"
        "  }\n\n"
        "  artifactUrl(path: OntologyFrontendArtifactPath): string {\n"
        '    const base = this.baseUrl.replace(/\\/$/, "");\n'
        '    const relative = path.replace(/^\\//, "");\n'
        "    return base ? `${base}/${relative}` : relative;\n"
        "  }\n\n"
        "  async readJson<T>(path: OntologyFrontendArtifactPath): Promise<T> {\n"
        "    const response = await this.fetchImpl(this.artifactUrl(path));\n"
        "    if (!response.ok) {\n"
        "      throw new Error(`Failed to fetch ${path}: ${response.status}`);\n"
        "    }\n"
        "    return (await response.json()) as T;\n"
        "  }\n\n"
        "  async readText(path: OntologyFrontendArtifactPath): Promise<string> {\n"
        "    const response = await this.fetchImpl(this.artifactUrl(path));\n"
        "    if (!response.ok) {\n"
        "      throw new Error(`Failed to fetch ${path}: ${response.status}`);\n"
        "    }\n"
        "    return await response.text();\n"
        "  }\n\n"
        "  getContract<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.frontend_contract);\n"
        "  }\n\n"
        "  getAgentTools<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.agent_tools);\n"
        "  }\n\n"
        "  getTypes(): Promise<string> {\n"
        "    return this.readText(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.frontend_types);\n"
        "  }\n\n"
        "  getClientSource(): Promise<string> {\n"
        "    return this.readText(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.frontend_client);\n"
        "  }\n\n"
        "  getFrontendIndex<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.frontend_index);\n"
        "  }\n\n"
        "  getGlobalEdges<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.global_edges);\n"
        "  }\n\n"
        "  getIndex<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.index);\n"
        "  }\n\n"
        "  getSchema<T>(): Promise<T> {\n"
        "    return this.readJson<T>(ONTOLOGY_FRONTEND_ARTIFACT_PATHS.schema);\n"
        "  }\n\n"
        "  getMemberFeatures<T>(memberBioguideId: string): Promise<T> {\n"
        "    return this.readJson<T>(ontologyMemberFeaturesPath(memberBioguideId));\n"
        "  }\n\n"
        "  getMemberGraph<T>(memberBioguideId: string): Promise<T> {\n"
        "    return this.readJson<T>(ontologyMemberGraphPath(memberBioguideId));\n"
        "  }\n"
        "}\n\n"
        "export function ontologyMemberFeaturesPath(memberBioguideId: string): OntologyFrontendArtifactPath {\n"
        "  validateOntologyMemberBioguideId(memberBioguideId);\n"
        "  return `ontology/member-features/${memberBioguideId}.json` as OntologyFrontendArtifactPath;\n"
        "}\n\n"
        "export function ontologyMemberGraphPath(memberBioguideId: string): OntologyFrontendArtifactPath {\n"
        "  validateOntologyMemberBioguideId(memberBioguideId);\n"
        "  return `ontology/members/${memberBioguideId}.json` as OntologyFrontendArtifactPath;\n"
        "}\n\n"
        "export function validateOntologyMemberBioguideId(memberBioguideId: string): void {\n"
        "  if (!memberBioguideId || !/^[A-Za-z0-9-]+$/.test(memberBioguideId)) {\n"
        "    throw new Error(`Invalid ontology member Bioguide ID: ${memberBioguideId}`);\n"
        "  }\n"
        "}\n\n"
        "export function createPsephosAmericaOntologyClient(\n"
        "  options: PsephosAmericaOntologyClientOptions = {},\n"
        "): PsephosAmericaOntologyClient {\n"
        "  return new PsephosAmericaOntologyClient(options);\n"
        "}\n"
    )
