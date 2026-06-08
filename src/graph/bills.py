"""Canonical bill identity.

Unlike people, bills carry authoritative identifiers — a jurisdiction, a
session, and a bill number — so they resolve *deterministically* rather than
probabilistically. :class:`BillRef` captures that triple and mints a stable
``canonical_bill_id`` (the ``canonical_bill_id`` side of the Track A output
contract). The identifier space is jurisdiction-general (``hr-1``, ``sb-50``,
``ordinance-2024-12``), with congress-specific helpers layered on top.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

from src.graph.entity_resolution.ids import stable_id

CONGRESS_BILL_TYPES = frozenset({"hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"})

_CONGRESS_BILL_RE = re.compile(r"^([a-z.\s]+?)\.?\s*(\d+)$")


def normalize_bill_identifier(raw: str) -> str:
    """Lowercase, drop abbreviation dots, and hyphenate other separators.

    Periods are removed without a gap ("H.R. 1" -> "hr-1"); every other run of
    non-alphanumerics collapses to a single hyphen ("Ordinance 2024/12" ->
    "ordinance-2024-12").
    """
    lowered = raw.strip().lower().replace(".", "")
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if not collapsed:
        raise ValueError("bill identifier must not be blank")
    return collapsed


def congress_bill_identifier(bill_type: str, number: int) -> str:
    """Build a normalized congress identifier, validating the type and number."""
    normalized_type = re.sub(r"[^a-z]", "", bill_type.strip().lower())
    if normalized_type not in CONGRESS_BILL_TYPES:
        raise ValueError(f"unknown congress bill type: {bill_type!r}")
    if number <= 0:
        raise ValueError("bill number must be a positive integer")
    return f"{normalized_type}-{number}"


def parse_congress_bill_identifier(text: str) -> str:
    """Parse free-text like 'H.R. 1' or 'S.Con.Res. 12' into 'hr-1' / 'sconres-12'."""
    match = _CONGRESS_BILL_RE.match(text.strip().lower())
    if match is None:
        raise ValueError(f"cannot parse congress bill reference: {text!r}")
    bill_type = re.sub(r"[^a-z]", "", match.group(1))
    return congress_bill_identifier(bill_type, int(match.group(2)))


class BillRef(BaseModel):
    """A jurisdiction/session/identifier triple that names exactly one bill."""

    model_config = ConfigDict(frozen=True)

    jurisdiction_id: str
    session_id: str
    identifier: str

    @field_validator("jurisdiction_id", "session_id", mode="before")
    @classmethod
    def _normalize_context(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("identifier", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_bill_identifier(value)
        return value

    @field_validator("jurisdiction_id", "session_id")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value:
            raise ValueError("jurisdiction_id and session_id must be non-blank")
        return value

    @classmethod
    def for_congress(cls, congress: int, bill_reference: str) -> Self:
        """Build a federal-congress BillRef from a congress number and a citation."""
        return cls(
            jurisdiction_id="us-congress",
            session_id=str(congress),
            identifier=parse_congress_bill_identifier(bill_reference),
        )

    @property
    def canonical_id(self) -> str:
        """Stable ``cb-<digest(jurisdiction, session, identifier)>``."""
        return stable_id([self.jurisdiction_id, self.session_id, self.identifier], "cb")
