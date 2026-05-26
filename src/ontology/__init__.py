"""Source-backed ontology builders for congressional intelligence features."""

from src.ontology.agent_tools import (
    OntologyAgentToolManifestPayload,
    OntologyAgentToolPayload,
    build_ontology_agent_tool_manifest,
)
from src.ontology.artifact_paths import (
    ONTOLOGY_FRONTEND_ARTIFACT_PATHS,
    ontology_frontend_artifact_paths,
)
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.ontology.frontend_contracts import (
    OntologyFrontendContractPayload,
    build_ontology_frontend_contract,
    build_ontology_typescript_client,
    build_ontology_typescript_declarations,
)
from src.ontology.member_interest_edges import build_member_interest_edges
from src.ontology.static_schema import (
    OntologyFrontendIndexPayload,
    OntologyStaticSchemaPayload,
    build_ontology_frontend_index,
    build_ontology_static_schema,
)
from src.ontology.versions import (
    ONTOLOGY_AGENT_TOOL_CONTRACT_VERSION,
    ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
    ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ONTOLOGY_FRONTEND_CONTRACT_VERSION,
    ONTOLOGY_FRONTEND_INDEX_VERSION,
    ONTOLOGY_STATIC_SCHEMA_VERSION,
)

__all__ = [
    "OntologyEdgePayload",
    "ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION",
    "ONTOLOGY_AGENT_TOOL_CONTRACT_VERSION",
    "ONTOLOGY_FRONTEND_CLIENT_VERSION",
    "ONTOLOGY_FRONTEND_CONTRACT_VERSION",
    "ONTOLOGY_FRONTEND_INDEX_VERSION",
    "ONTOLOGY_STATIC_SCHEMA_VERSION",
    "ONTOLOGY_FRONTEND_ARTIFACT_PATHS",
    "OntologyAgentToolManifestPayload",
    "OntologyAgentToolPayload",
    "OntologyFrontendContractPayload",
    "OntologyFrontendIndexPayload",
    "OntologyNodeRef",
    "OntologyStaticSchemaPayload",
    "build_ontology_agent_tool_manifest",
    "ontology_frontend_artifact_paths",
    "build_ontology_frontend_index",
    "build_ontology_frontend_contract",
    "build_ontology_typescript_client",
    "build_ontology_typescript_declarations",
    "build_member_interest_edges",
    "build_ontology_static_schema",
]
