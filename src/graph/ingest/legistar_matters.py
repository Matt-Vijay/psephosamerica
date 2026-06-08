"""Adapt Legistar matters (municipal legislation) into canonical bill IDs.

A Legistar "matter" is a piece of municipal legislation — an ordinance,
resolution, council bill, or appointment. :func:`parse_legistar_matter` parses
one record; :func:`matter_bill_canonical_id` mints its canonical Bill ID via
:class:`~src.graph.bills.BillRef`, keyed by (city jurisdiction, intro year, the
matter file number). This extends bill identity to the municipal tier, the
target that municipal vote edges would reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.graph.bills import BillRef


@dataclass(frozen=True)
class LegistarMatter:
    """One parsed Legistar matter (municipal legislation)."""

    matter_id: int
    file: str
    name: str
    matter_type: str
    status: str
    intro_date: date | None


def _parse_intro_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_legistar_matter(record: dict[str, Any]) -> LegistarMatter:
    """Parse one Legistar matter record."""
    matter_id = record.get("MatterId")
    if matter_id is None:
        raise ValueError("Legistar matter is missing a MatterId")
    return LegistarMatter(
        matter_id=int(matter_id),
        file=(record.get("MatterFile") or "").strip(),
        name=(record.get("MatterName") or "").strip(),
        matter_type=(record.get("MatterTypeName") or "").strip(),
        status=(record.get("MatterStatusName") or "").strip(),
        intro_date=_parse_intro_date(record.get("MatterIntroDate")),
    )


def matter_bill_canonical_id(matter: LegistarMatter, *, jurisdiction_code: str) -> str:
    """The canonical municipal Bill ID for a matter (needs a file number)."""
    if not matter.file:
        raise ValueError("Legistar matter has no file number; cannot mint a bill id")
    session = str(matter.intro_date.year) if matter.intro_date is not None else "unknown"
    return BillRef(
        jurisdiction_id=jurisdiction_code,
        session_id=session,
        identifier=matter.file,
    ).canonical_id
