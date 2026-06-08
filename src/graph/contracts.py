"""The Track A -> Track B output contract.

This is the typed seam where the data/graph/entity-resolution track hands off to
the model/inference/API track. Track A emits one
:class:`EntityResolutionOutput` per canonical entity, continuously; Track B
consumes it. Keeping the shape stable lets the two tracks proceed in parallel —
the dossier and embedding fields are present but ``pending`` until the (later)
LLM-dossier and GNN-structural-embedding slices fill them, so Track B can build
against the final contract today.

Every row carries:

* ``canonical_id`` + ``entity_type`` (use ``canonical_person_id`` for the
  person handoff), ``display_name``, and the sorted external-ID keys;
* ``known_at`` — the leakage stamp, equal to the earliest source observation;
* ``source_anchors`` — one provenance-rich anchor per merged source (URL +
  content hash + content-address + bitemporal validity), so every emitted fact
  is auditable and replayable, satisfying the blueprint's evidence requirement.

Serializable via :class:`~src.export.contracts.ExportContractModel` so the lake
and read API can publish it directly.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from src.export.contracts import ExportContractModel
from src.graph.entity_resolution.canonical import CanonicalEntity

EnrichmentStatus = Literal["pending", "ready"]
EntityType = Literal["person", "org"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class ContractSourceAnchor(ExportContractModel):
    """One merged source's provenance, flattened for publication."""

    source_system: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    content_sha256: str
    content_address: str
    known_at: datetime
    valid_from: date
    valid_to: date | None = None

    @field_validator("known_at")
    @classmethod
    def _aware_known_at(cls, value: datetime) -> datetime:
        return _require_aware(value, field_name="known_at")

    @field_validator("content_sha256")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("content_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        sha = self.content_sha256
        expected = f"sha256/{sha[:2]}/{sha[2:4]}/{sha}"
        if self.content_address != expected:
            raise ValueError("content_address must be the sharded path of content_sha256")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be strictly after valid_from")
        return self


class EntityResolutionOutput(ExportContractModel):
    """One canonical entity, emitted continuously to Track B."""

    canonical_id: str = Field(min_length=1)
    entity_type: EntityType
    display_name: str = Field(min_length=1)
    external_ids: list[str] = Field(default_factory=list)
    known_at: datetime
    source_anchors: list[ContractSourceAnchor]
    # Filled by the later dossier/embedding slices; ``pending`` until then.
    dossier_json: dict[str, Any] | None = None
    dossier_embedding: list[float] | None = None
    structural_embedding: list[float] | None = None
    enrichment_status: EnrichmentStatus = "pending"

    @field_validator("known_at")
    @classmethod
    def _aware_known_at(cls, value: datetime) -> datetime:
        return _require_aware(value, field_name="known_at")

    @property
    def canonical_person_id(self) -> str | None:
        """The canonical ID when this row is a person, else ``None``."""
        return self.canonical_id if self.entity_type == "person" else None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.source_anchors:
            raise ValueError("source_anchors must not be empty")
        earliest = min(anchor.known_at for anchor in self.source_anchors)
        if self.known_at != earliest:
            raise ValueError("known_at must equal the earliest source anchor's known_at")
        if self.external_ids != sorted(set(self.external_ids)):
            raise ValueError("external_ids must be sorted and unique")
        if self.enrichment_status == "ready" and (
            self.dossier_json is None
            or self.dossier_embedding is None
            or self.structural_embedding is None
        ):
            raise ValueError("enrichment_status 'ready' requires dossier_json and both embeddings")
        return self


def build_entity_resolution_output(
    entity: CanonicalEntity,
    *,
    canonical_id: str | None = None,
) -> EntityResolutionOutput:
    """Project a resolved canonical entity into the published output row.

    Pass ``canonical_id`` to publish under a *persistent* stable ID (from
    :func:`~src.graph.entity_resolution.assignment.assign_canonical_ids`) instead
    of the entity's content-addressed ID — which is what downstream consumers and
    the CDC feed key on.
    """
    anchors = [
        ContractSourceAnchor(
            source_system=anchor.source_system,
            record_id=anchor.record_id,
            source_url=anchor.provenance.source_url,
            content_sha256=anchor.provenance.content_sha256,
            content_address=anchor.provenance.content_address(),
            known_at=anchor.provenance.known_at,
            valid_from=anchor.provenance.valid_from,
            valid_to=anchor.provenance.valid_to,
        )
        for anchor in entity.anchors
    ]
    return EntityResolutionOutput(
        canonical_id=canonical_id if canonical_id is not None else entity.canonical_id,
        entity_type=entity.entity_type,
        display_name=entity.display_name,
        external_ids=[ext.canonical_key for ext in entity.external_ids],
        known_at=entity.known_at,
        source_anchors=anchors,
    )
