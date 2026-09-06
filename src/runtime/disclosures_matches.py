"""Runtime member-match helpers for disclosure artifact rows and index rows.

Bridges raw row-shaped data from the parse pipeline to the member-resolution
layer.  No parsing, no DB access, no network calls.

Public functions:
  resolve_artifact_member   — single artifact + index row pair
  resolve_artifact_members  — positionally-aligned batch

Inputs are row dicts plus a chamber-keyed member lookup.  Resolution results
are Resolved, NoMatch, or Ambiguous — never a silent guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.runtime.disclosures_member_resolution import (
    DisclosureHeaderIdentity,
    ResolutionResult,
    house_identity,
    resolve_disclosure_member,
    senate_identity,
)

# Chamber-keyed pre-fetched member rows.
# Expected keys: "house", "senate".
MemberLookup = dict[str, Sequence[dict[str, Any]]]


# Internal: identity construction from raw index row dict


def _identity_from_index(
    chamber: str,
    index_row: dict[str, Any],
) -> DisclosureHeaderIdentity:
    """Build a DisclosureHeaderIdentity from a raw index row dict.

    House index rows must carry: last_name, first_name, state_dst.
    Senate index rows must carry: last_name, first_name, office.

    Raises KeyError if a required field is absent from index_row.
    Raises ValueError if field values are malformed (propagated from
    house_identity / senate_identity) or if chamber is unknown.
    """
    if chamber == "house":
        return house_identity(
            last_name=index_row["last_name"],
            first_name=index_row["first_name"],
            state_dst=index_row["state_dst"],
        )
    if chamber == "senate":
        return senate_identity(
            last_name=index_row["last_name"],
            first_name=index_row["first_name"],
            office=index_row["office"],
        )
    raise ValueError(f"chamber must be 'house' or 'senate', got {chamber!r}")


# Public API


def resolve_artifact_member(
    artifact_row: dict[str, Any],
    index_row: dict[str, Any],
    member_lookup: MemberLookup,
) -> ResolutionResult:
    """Resolve one artifact + index row pair to a bioguide_id.

    chamber is read from artifact_row['chamber'].  The matching member slice
    is pulled from member_lookup[chamber]; missing chambers yield an empty
    slice, which produces NoMatch rather than an error.

    Returns one of Resolved, NoMatch, or Ambiguous.

    Raises ValueError for an unknown chamber string or malformed index fields.
    Raises KeyError for required fields absent from artifact_row or index_row.
    """
    chamber: str = artifact_row["chamber"]
    rows = list(member_lookup.get(chamber, []))
    identity = _identity_from_index(chamber, index_row)
    return resolve_disclosure_member(identity, rows)


def resolve_artifact_members(
    artifact_rows: Sequence[dict[str, Any]],
    index_rows: Sequence[dict[str, Any]],
    member_lookup: MemberLookup,
) -> list[ResolutionResult]:
    """Resolve a positionally-aligned sequence of artifact + index row pairs.

    artifact_rows and index_rows must have the same length.  Returns one
    ResolutionResult per pair, in the same order.

    Raises ValueError if the sequence lengths differ.
    """
    if len(artifact_rows) != len(index_rows):
        raise ValueError(
            f"artifact_rows and index_rows must be the same length, "
            f"got {len(artifact_rows)} vs {len(index_rows)}"
        )
    return [
        resolve_artifact_member(art, idx, member_lookup)
        for art, idx in zip(artifact_rows, index_rows, strict=False)
    ]
