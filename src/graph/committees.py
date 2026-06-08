"""Canonical committee identity.

Like bills, committees carry authoritative codes (the House/Senate Thomas codes
such as ``HSAG``; a city council's standing-committee slug), so they resolve
deterministically. :class:`CommitteeRef` captures the
``(jurisdiction, chamber, code)`` triple and mints a stable ``canonical_id``
(``cc-…``) that ``committee_membership`` edges point at.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator

from src.graph.entity_resolution.ids import stable_id

Chamber = Literal["house", "senate", "joint"]


class CommitteeRef(BaseModel):
    """A jurisdiction/chamber/code triple naming one committee."""

    model_config = ConfigDict(frozen=True)

    jurisdiction_id: str
    code: str
    chamber: Chamber | None = None

    @field_validator("jurisdiction_id", "code", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("chamber", mode="before")
    @classmethod
    def _normalize_chamber(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("jurisdiction_id", "code")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value:
            raise ValueError("jurisdiction_id and code must be non-blank")
        return value

    @classmethod
    def for_congress(cls, chamber: Chamber, code: str) -> Self:
        return cls(jurisdiction_id="us-congress", code=code, chamber=chamber)

    @property
    def canonical_id(self) -> str:
        """Stable ``cc-<digest(jurisdiction, chamber, code)>``."""
        return stable_id([self.jurisdiction_id, self.chamber or "", self.code], "cc")
