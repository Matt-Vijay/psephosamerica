"""Disclosure identity extraction from live index rows.

Bridges the parse layer (HouseIndexRow, SenateIndexRow) to the resolution
layer (DisclosureHeaderIdentity).  Each chamber has an explicit extractor;
chamber-specific field names stay visible rather than being collapsed into a
shared path.

Delegates all parsing to:
  src.parse.disclosures.house_index  — HouseIndexRow
  src.parse.disclosures.senate_index — SenateIndexRow
  src.runtime.disclosures_member_resolution — house_identity / senate_identity
"""

from __future__ import annotations

from src.parse.disclosures.house_index import HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_member_resolution import (
    DisclosureHeaderIdentity,
    house_identity,
    senate_identity,
)


def house_identity_from_index_row(row: HouseIndexRow) -> DisclosureHeaderIdentity:
    """Extract a DisclosureHeaderIdentity from a HouseIndexRow.

    Delegates state_dst parsing to house_identity(); raises ValueError on
    malformed state_dst (propagated unchanged).
    """
    return house_identity(row.last_name, row.first_name, row.state_dst)


def senate_identity_from_index_row(row: SenateIndexRow) -> DisclosureHeaderIdentity:
    """Extract a DisclosureHeaderIdentity from a SenateIndexRow.

    Delegates office parsing to senate_identity(); raises ValueError on
    malformed office field (propagated unchanged).
    """
    return senate_identity(row.last_name, row.first_name, row.office)


def disclosure_identity_from_index_row(
    chamber: str,
    row: HouseIndexRow | SenateIndexRow,
) -> DisclosureHeaderIdentity:
    """Dispatch to the correct chamber extractor based on *chamber*.

    chamber must be 'house' or 'senate' (case-insensitive).
    Raises ValueError for any other value.
    """
    key = chamber.lower()
    if key == "house":
        if not isinstance(row, HouseIndexRow):
            raise TypeError(f"chamber='house' requires a HouseIndexRow, got {type(row).__name__}")
        return house_identity_from_index_row(row)
    if key == "senate":
        if not isinstance(row, SenateIndexRow):
            raise TypeError(f"chamber='senate' requires a SenateIndexRow, got {type(row).__name__}")
        return senate_identity_from_index_row(row)
    raise ValueError(f"Unknown chamber: {chamber!r}. Expected 'house' or 'senate'.")
