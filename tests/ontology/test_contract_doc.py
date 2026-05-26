from __future__ import annotations

from pathlib import Path

from src.ontology.artifact_paths import ontology_frontend_artifact_paths
from src.ontology.versions import (
    ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
    ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ONTOLOGY_FRONTEND_CONTRACT_VERSION,
    ONTOLOGY_FRONTEND_INDEX_VERSION,
    ONTOLOGY_STATIC_SCHEMA_VERSION,
)

_ROOT = Path(__file__).resolve().parents[2]
_DOC_PATH = _ROOT / "docs" / "ontology-contract.md"


def test_ontology_contract_doc_mentions_all_public_artifact_paths() -> None:
    doc = _DOC_PATH.read_text(encoding="utf-8")

    for artifact_path in ontology_frontend_artifact_paths().values():
        assert f"`{artifact_path}`" in doc


def test_ontology_contract_doc_mentions_all_public_versions() -> None:
    doc = _DOC_PATH.read_text(encoding="utf-8")

    for version in (
        ONTOLOGY_STATIC_SCHEMA_VERSION,
        ONTOLOGY_AGENT_TOOL_MANIFEST_VERSION,
        ONTOLOGY_FRONTEND_CONTRACT_VERSION,
        ONTOLOGY_FRONTEND_INDEX_VERSION,
        ONTOLOGY_FRONTEND_CLIENT_VERSION,
    ):
        assert f"`{version}`" in doc
