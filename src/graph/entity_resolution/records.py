"""Source records — the unit of entity resolution.

A :class:`SourceRecord` is one observation of a real-world Person or Org as it
appears in *one* source (an FEC filing, a Congress.gov member page, an
OpenStates legislator row, a county business registration). Entity resolution's
job is to decide which source records denote the same real-world entity and
collapse them under a canonical ID — without ever losing the per-source audit
trail. This module is the typed, provenance-carrying input to that process.

Two linkage signals live here:

* **External IDs** (:class:`ExternalId`) — exact agreement on a strong key
  (matching FEC candidate ID, bioguide ID) is the single most decisive signal a
  linker has; modelled as a first-class, case-insensitively-comparable value.
* **Names** — parsed lazily via :mod:`src.graph.entity_resolution.names`.

Every record carries a :class:`~src.graph.provenance.ProvenanceEnvelope`, so the
leakage gate (``known_as_of``) and replayability hold at the record level.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.graph.entity_resolution.names import PersonName
from src.graph.provenance import ProvenanceEnvelope

EntityType = Literal["person", "org"]

_DIGEST_SIZE = 10  # 80 bits — ample for non-adversarial collision resistance


def _digest(parts: list[str], prefix: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    raw = hashlib.blake2b(payload, digest_size=_DIGEST_SIZE).digest()
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"{prefix}-{encoded}"


class ExternalId(BaseModel):
    """A strong external identifier for an entity (FEC ID, bioguide, etc.)."""

    model_config = ConfigDict(frozen=True)

    system: str = Field(description="Identifier namespace, e.g. 'bioguide', 'fec_candidate'.")
    value: str = Field(description="The identifier within that namespace.")

    @field_validator("system", mode="before")
    @classmethod
    def _normalize_system(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _strip_value(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("system", "value")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value:
            raise ValueError("ExternalId system and value must be non-blank")
        return value

    @property
    def canonical_key(self) -> str:
        """``system:value`` with a casefolded value for case-insensitive joins."""
        return f"{self.system}:{self.value.casefold()}"

    def agrees_with(self, other: ExternalId) -> bool:
        """True when both refer to the same namespace and identifier."""
        return self.canonical_key == other.canonical_key


class SourceRecord(BaseModel):
    """One source's observation of a Person or Org, with full provenance."""

    model_config = ConfigDict(frozen=True)

    source_system: str = Field(description="The source dataset, e.g. 'fec', 'openstates'.")
    source_record_id: str = Field(description="Natural key of this row within its source.")
    entity_type: EntityType
    display_name: str = Field(description="Name as it appears in the source.")
    external_ids: tuple[ExternalId, ...] = ()
    jurisdiction: str | None = None
    party: str | None = None
    region: str | None = Field(default=None, description="State / locale context, if any.")
    provenance: ProvenanceEnvelope

    @field_validator("source_system", "source_record_id", "display_name", mode="before")
    @classmethod
    def _strip_required(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("jurisdiction", "party", "region", mode="before")
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("source_system", "source_record_id", "display_name")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value:
            raise ValueError("source_system, source_record_id, and display_name must be non-blank")
        return value

    @field_validator("external_ids", mode="before")
    @classmethod
    def _coerce_external_ids(cls, value: object) -> object:
        # Accept dict payloads (pydantic builds the models) but keep tuples/lists as-is.
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _dedupe_external_ids(self) -> Self:
        seen: dict[str, ExternalId] = {}
        for ext in self.external_ids:
            seen.setdefault(ext.canonical_key, ext)
        ordered = tuple(sorted(seen.values(), key=lambda e: (e.system, e.value)))
        object.__setattr__(self, "external_ids", ordered)
        return self

    @property
    def record_id(self) -> str:
        """Stable, source-scoped ID: ``sr-<digest(type, system, source_id)>``."""
        return _digest([self.entity_type, self.source_system, self.source_record_id], "sr")

    @property
    def external_id_keys(self) -> frozenset[str]:
        """The set of casefolded ``system:value`` keys for fast agreement tests."""
        return frozenset(ext.canonical_key for ext in self.external_ids)

    @property
    def known_at(self) -> datetime:
        """Leakage stamp from the provenance envelope."""
        return self.provenance.known_at

    def known_as_of(self, cutoff: datetime) -> bool:
        """Was this record knowable by ``cutoff``? Delegates to provenance."""
        return self.provenance.known_as_of(cutoff)

    def shares_external_id(self, other: SourceRecord) -> bool:
        """True when the two records agree on at least one external identifier."""
        return bool(self.external_id_keys & other.external_id_keys)

    def person_name(self) -> PersonName | None:
        """Parsed name for ``person`` records; ``None`` for orgs."""
        if self.entity_type != "person":
            return None
        return PersonName.parse(self.display_name)
