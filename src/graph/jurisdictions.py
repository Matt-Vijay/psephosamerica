"""Standardized, hierarchical jurisdiction identity.

The blueprint's central bet is that federal, state, county, city, and
special-district government are *one homogeneous graph*, not special cases. That
only works if every layer keys on a consistent ``jurisdiction_id`` vocabulary.
:class:`Jurisdiction` mints those codes — human-readable, stable, and
hierarchical (``us`` -> ``us-ca`` -> ``us-ca-city-los_angeles``) — and records
each jurisdiction's parent so the graph can roll signals up and down the
hierarchy.

Codes are the canonical IDs (no hashing): they are already stable and legible.
A level discriminator (``county`` / ``city`` / ``sd``) keeps same-named
county/city/district from colliding.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator

JurisdictionLevel = Literal["federal", "state", "county", "city", "special_district"]

_USPS_RE = re.compile(r"^[a-z]{2}$")


def slugify_place(name: str) -> str:
    """Fold a place name to a code slug: no accents, lowercase, underscores."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"[^a-z0-9]+", "_", without_accents.lower()).strip("_")
    if not collapsed:
        raise ValueError("place name must not be blank")
    return collapsed


def _usps(code: str) -> str:
    normalized = code.strip().lower()
    if not _USPS_RE.match(normalized):
        raise ValueError(f"{code!r} is not a 2-letter USPS state code")
    return normalized


class Jurisdiction(BaseModel):
    """A government at one level of the federal -> local hierarchy."""

    model_config = ConfigDict(frozen=True)

    level: JurisdictionLevel
    code: str
    name: str
    parent_id: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _normalize_code(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("code", "name")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("code and name must be non-blank")
        return value

    # ── constructors ───────────────────────────────────────────────

    @classmethod
    def federal(cls) -> Self:
        return cls(level="federal", code="us", name="United States", parent_id=None)

    @classmethod
    def state(cls, usps: str) -> Self:
        code = _usps(usps)
        return cls(level="state", code=f"us-{code}", name=code.upper(), parent_id="us")

    @classmethod
    def county(cls, usps: str, name: str) -> Self:
        code = _usps(usps)
        return cls(
            level="county",
            code=f"us-{code}-county-{slugify_place(name)}",
            name=name,
            parent_id=f"us-{code}",
        )

    @classmethod
    def city(cls, usps: str, name: str) -> Self:
        code = _usps(usps)
        return cls(
            level="city",
            code=f"us-{code}-city-{slugify_place(name)}",
            name=name,
            parent_id=f"us-{code}",
        )

    @classmethod
    def special_district(cls, usps: str, name: str) -> Self:
        code = _usps(usps)
        return cls(
            level="special_district",
            code=f"us-{code}-sd-{slugify_place(name)}",
            name=name,
            parent_id=f"us-{code}",
        )

    # ── hierarchy ──────────────────────────────────────────────────

    def is_descendant_of(self, other: Jurisdiction) -> bool:
        """True when ``other`` is this jurisdiction's direct parent."""
        return self.parent_id == other.code
