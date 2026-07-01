from __future__ import annotations

from src.ontology.frontend_contracts import (
    ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ONTOLOGY_FRONTEND_CONTRACT_VERSION,
    build_ontology_frontend_contract,
    build_ontology_typescript_client,
    build_ontology_typescript_declarations,
    ontology_frontend_model_names,
)


def test_build_ontology_frontend_contract_exports_json_schemas_for_public_artifacts() -> None:
    contract = build_ontology_frontend_contract()

    assert contract.schema_version == "psephosamerica-ontology-contract-v1"
    assert contract.artifact_paths == {
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
    assert contract.model_names == sorted(contract.model_names)
    assert contract.model_names == ontology_frontend_model_names()
    assert {
        "OntologyAgentToolManifestPayload",
        "OntologyEdgePayload",
        "OntologyFrontendIndexPayload",
        "OntologyGraphPayload",
        "OntologyMemberFeaturesPayload",
        "OntologyStaticSchemaPayload",
    }.issubset(set(contract.model_names))

    edge_schema = contract.json_schemas["OntologyEdgePayload"]
    assert edge_schema["title"] == "OntologyEdgePayload"
    assert "edge_id" in edge_schema["properties"]
    assert "source_anchors" in edge_schema["required"]

    frontend_index_schema = contract.json_schemas["OntologyFrontendIndexPayload"]
    assert "by_link_type" in frontend_index_schema["properties"]
    assert "by_source_key" in frontend_index_schema["properties"]


def test_build_ontology_typescript_declarations_exports_stable_frontend_surface() -> None:
    declarations = build_ontology_typescript_declarations()

    assert declarations.startswith("// Generated from Psephos America ontology Pydantic contracts.")
    assert (
        f'export const ONTOLOGY_FRONTEND_CONTRACT_VERSION = "{ONTOLOGY_FRONTEND_CONTRACT_VERSION}" as const;'
        in declarations
    )
    assert "export type OntologyFrontendArtifactPath =" in declarations
    assert '"ontology/contracts.json"' in declarations
    assert '"ontology/contracts.d.ts"' in declarations
    assert '"ontology/frontend-index.json"' in declarations
    assert '"ontology/agent-tools.json"' in declarations
    artifact_path_line = next(
        line
        for line in declarations.splitlines()
        if line.startswith("export type OntologyFrontendArtifactPath")
    )
    assert "`ontology/member-features/${string}.json`" in declarations
    assert "`ontology/members/${string}.json`" in declarations
    assert '"ontology/member-features/{member_bioguide_id}.json"' not in artifact_path_line
    assert '"ontology/members/{member_bioguide_id}.json"' not in artifact_path_line
    assert "export interface OntologyFrontendContractSchemas" in declarations
    assert "OntologyFrontendIndexPayload: JsonSchema;" in declarations
    assert "OntologyStaticSchemaPayload: JsonSchema;" in declarations
    assert declarations.endswith("\n")


def test_build_ontology_typescript_client_exports_static_fetch_surface() -> None:
    client = build_ontology_typescript_client()

    assert client.startswith("// Generated from Psephos America ontology Pydantic contracts.")
    assert (
        f'export const ONTOLOGY_FRONTEND_CLIENT_VERSION = "{ONTOLOGY_FRONTEND_CLIENT_VERSION}" as const;'
        in client
    )
    assert "export class PsephosAmericaOntologyClient" in client
    assert "ONTOLOGY_FRONTEND_ARTIFACT_PATHS" in client
    assert 'agent_tools: "ontology/agent-tools.json"' in client
    assert 'frontend_contract: "ontology/contracts.json"' in client
    assert 'frontend_client: "ontology/client.ts"' in client
    assert "getAgentTools<T>(): Promise<T>" in client
    assert "getTypes(): Promise<string>" in client
    assert "getClientSource(): Promise<string>" in client
    assert "getMemberGraph<T>(memberBioguideId: string): Promise<T>" in client
    assert "ontologyMemberFeaturesPath(memberBioguideId: string)" in client
    assert "validateOntologyMemberBioguideId(memberBioguideId)" in client
    assert "Invalid ontology member Bioguide ID" in client
    assert "encodeURIComponent(memberBioguideId)" not in client
    assert client.endswith("\n")
