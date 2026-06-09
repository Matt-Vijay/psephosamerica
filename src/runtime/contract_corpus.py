"""Loader for Track A's entity-resolution contract corpus.

Track A emits ``EntityResolutionOutput`` rows (``data/exports/contract_records``)
carrying, for each canonical entity, its source anchors and -- once enriched --
``dossier_embedding`` (256-d) and ``structural_embedding`` (64-d). This is the
integration seam the four-stream model was gated on; this module reads that
corpus and indexes the entities by external id (e.g. ``bioguide:M001199``) so
politician/bill embeddings can be joined to the vote feed.

Only ``ready`` rows carry both embeddings (the contract validates that), so the
loader returns those as ``ContractEntity`` with numpy embedding vectors; rows
still ``pending`` enrichment are skipped. We stay on numpy (this goal does not
adopt torch) -- the 256-d/64-d vectors feed ``token_projection`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from src.graph.contracts import EntityResolutionOutput

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ContractEntity:
    """One enriched canonical entity with its dossier + structural embeddings."""

    canonical_id: str
    entity_type: str
    external_ids: tuple[str, ...]
    dossier_embedding: Array
    structural_embedding: Array


def load_contract_entities(path: Path) -> list[ContractEntity]:
    """Load enriched (``ready``) entities from a contract-records JSONL file."""
    entities: list[ContractEntity] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = EntityResolutionOutput.model_validate_json(line)
        if (
            record.enrichment_status != "ready"
            or record.dossier_embedding is None
            or record.structural_embedding is None
        ):
            continue
        entities.append(
            ContractEntity(
                canonical_id=record.canonical_id,
                entity_type=record.entity_type,
                external_ids=tuple(record.external_ids),
                dossier_embedding=np.asarray(record.dossier_embedding, dtype=np.float64),
                structural_embedding=np.asarray(record.structural_embedding, dtype=np.float64),
            )
        )
    return entities


def _external_value(external_id: str, prefix: str) -> str | None:
    head, separator, value = external_id.partition(":")
    if separator and head == prefix:
        return value
    return None


def bioguide_embedding_index(entities: list[ContractEntity]) -> dict[str, ContractEntity]:
    """Index person entities by uppercased bioguide id (the House feed's key)."""
    index: dict[str, ContractEntity] = {}
    for entity in entities:
        if entity.entity_type != "person":
            continue
        for external_id in entity.external_ids:
            value = _external_value(external_id, "bioguide")
            if value:
                index[value.upper()] = entity
                break
    return index
