"""Typed, provenance-carrying edges of the heterogeneous knowledge graph.

A :class:`GraphEdge` is one atomic, observed relationship between two canonical
entities — a vote, a sponsorship, a donation, an endorsement — sourced from a
single artifact (a roll-call record, an FEC filing) and therefore carrying a
single :class:`~src.graph.provenance.ProvenanceEnvelope`. (Canonical *nodes* are
multi-source and already modelled by the entity-resolution layer; edges are the
atomic facts.)

Every edge inherits the bitemporal/leakage discipline from its envelope:
``known_as_of`` is the strict-cutoff gate that keeps relational features from
leaking future facts, and :func:`edges_known_as_of` projects an edge set to what
was knowable at time ``t`` — the relational half of "replayable to any
time-``t`` snapshot".

The edge-type taxonomy is open (novel relations are allowed) with a
``RECOMMENDED_EDGE_TYPES`` set for the common ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.graph.entity_resolution.ids import stable_id
from src.graph.provenance import ProvenanceEnvelope

RECOMMENDED_EDGE_TYPES = frozenset(
    {
        "vote",
        "sponsorship",
        "cosponsorship",
        "committee_membership",
        "donation",
        "endorsement",
        "employment",
        "lobbying_contact",
        "public_statement",
        "membership",
        "independent_expenditure",
    }
)


def normalize_edge_type(raw: str) -> str:
    """Lowercase, trim, and collapse spaces/hyphens to underscores."""
    collapsed = re.sub(r"[\s-]+", "_", raw.strip().lower())
    return collapsed.strip("_")


def is_recommended_edge_type(edge_type: str) -> bool:
    """True when ``edge_type`` (after normalization) is in the recommended set."""
    return normalize_edge_type(edge_type) in RECOMMENDED_EDGE_TYPES


class GraphEdge(BaseModel):
    """One provenance-carrying relationship between two canonical entities."""

    model_config = ConfigDict(frozen=True)

    edge_type: str = Field(description="Relation kind, e.g. 'vote', 'donation'.")
    src_id: str = Field(min_length=1, description="Canonical ID of the source node.")
    dst_id: str = Field(min_length=1, description="Canonical ID of the destination node.")
    attributes: dict[str, str] = Field(default_factory=dict)
    external_key: str | None = Field(
        default=None,
        description="Source-specific discriminator (transaction / roll-call ID) "
        "for relations that recur on the same day between the same pair.",
    )
    provenance: ProvenanceEnvelope

    @field_validator("edge_type", mode="before")
    @classmethod
    def _normalize_edge_type(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_edge_type(value)
        return value

    @field_validator("src_id", "dst_id", mode="before")
    @classmethod
    def _strip_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("external_key", mode="before")
    @classmethod
    def _blank_external_key_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("edge_type")
    @classmethod
    def _require_edge_type(cls, value: str) -> str:
        if not value:
            raise ValueError("edge_type must be non-blank")
        return value

    @model_validator(mode="after")
    def _no_self_loop(self) -> Self:
        if self.src_id == self.dst_id:
            raise ValueError("edge must not be a self-loop (src_id == dst_id)")
        return self

    @property
    def edge_id(self) -> str:
        """Stable identity over (type, src, dst, valid_from, external_key).

        The attribute payload never affects identity; ``external_key`` does, so
        same-day recurring relations (multiple donations between a pair) stay
        distinct when the source provides a transaction discriminator.
        """
        return stable_id(
            [
                self.edge_type,
                self.src_id,
                self.dst_id,
                self.provenance.valid_from.isoformat(),
                self.external_key or "",
            ],
            "ge",
        )

    @property
    def known_at(self) -> datetime:
        return self.provenance.known_at

    def known_as_of(self, cutoff: datetime) -> bool:
        """The strict-cutoff leakage gate, delegated to provenance."""
        return self.provenance.known_as_of(cutoff)

    def covers(self, as_of: date) -> bool:
        """Whether the relationship's valid-time window covers ``as_of``."""
        return self.provenance.covers(as_of)


def edges_known_as_of(edges: Iterable[GraphEdge], cutoff: datetime) -> list[GraphEdge]:
    """Return only the edges knowable by ``cutoff`` (leakage-safe projection)."""
    return [edge for edge in edges if edge.known_as_of(cutoff)]
