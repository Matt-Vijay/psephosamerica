"""Deterministic member resolution for parsed disclosure header identities.

Pure over row data — no DB access, no parsing.  Callers fetch member rows
and supply them alongside typed identity objects.

Resolution is deterministic:
  1. Filter by chamber + state + district (district=None for Senate).
  2. Filter by normalized last_name.
  3. If still ambiguous, break ties by first-name first-token.
  4. Return Resolved, NoMatch, or Ambiguous — never a silent guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# Identity — parsed header fields from an index row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosureHeaderIdentity:
    """Canonical identity extracted from a disclosure index row.

    Chamber-specific:
      house  — state (2-char abbrev), district (int, 1-based)
      senate — state (2-char abbrev), district=None
    """

    last_name: str
    first_name: str
    chamber: str  # 'house' | 'senate'
    state: str  # 2-char abbreviation (e.g. 'CA')
    district: Optional[int] = None  # House only; None for Senate


# ---------------------------------------------------------------------------
# Identity constructors from raw index fields
# ---------------------------------------------------------------------------


def house_identity(
    last_name: str,
    first_name: str,
    state_dst: str,
) -> DisclosureHeaderIdentity:
    """Build a DisclosureHeaderIdentity from House index fields.

    state_dst format: '{state_abbrev}{zero_padded_district}' e.g. 'CA08', 'TX05'.
    The district is stripped of leading zeros; 'TX05' → 5.

    Raises ValueError if state_dst is shorter than 3 chars or district is
    not numeric — these represent unexpected index data that must not be
    silently coerced.
    """
    if len(state_dst) < 3:
        raise ValueError(f"state_dst too short to parse: {state_dst!r}")
    state = state_dst[:2].upper()
    district_str = state_dst[2:]
    if not district_str.isdigit():
        raise ValueError(f"Non-numeric district in state_dst: {state_dst!r}")
    return DisclosureHeaderIdentity(
        last_name=last_name,
        first_name=first_name,
        chamber="house",
        state=state,
        district=int(district_str),
    )


def senate_identity(
    last_name: str,
    first_name: str,
    office: str,
) -> DisclosureHeaderIdentity:
    """Build a DisclosureHeaderIdentity from Senate EFD office field.

    office format: 'Senator, {state_abbrev}' e.g. 'Senator, TX'.
    Only the state abbreviation portion (last comma-delimited token) is used.

    Raises ValueError if the state portion cannot be parsed as a 2-char
    abbreviation.
    """
    parts = [p.strip() for p in office.split(",")]
    state_part = parts[-1].strip() if parts else ""
    if len(state_part) != 2 or not state_part.isalpha():
        raise ValueError(f"Cannot extract 2-char state from office: {office!r}")
    return DisclosureHeaderIdentity(
        last_name=last_name,
        first_name=first_name,
        chamber="senate",
        state=state_part.upper(),
        district=None,
    )


# ---------------------------------------------------------------------------
# Resolution outcomes
# ---------------------------------------------------------------------------

#: Exhaustive set of reasons a disclosure header identity could not be matched
#: to a member row.  Kept as a Literal so callers can exhaustively branch.
#:   no_member_for_location  — no member row matches chamber + state + district
#:   no_member_for_last_name — location matches but no row matches the last name
NoMatchReason = Literal["no_member_for_location", "no_member_for_last_name"]


@dataclass(frozen=True)
class Resolved:
    bioguide_id: str
    identity: DisclosureHeaderIdentity


@dataclass(frozen=True)
class NoMatch:
    identity: DisclosureHeaderIdentity
    reason: NoMatchReason


@dataclass(frozen=True)
class Ambiguous:
    identity: DisclosureHeaderIdentity
    candidates: tuple[str, ...]  # bioguide_ids of all matching members


ResolutionResult = Union[Resolved, NoMatch, Ambiguous]


# ---------------------------------------------------------------------------
# Internal normalization helpers
# ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    return name.strip().upper()


def _first_token(name: str) -> str:
    tokens = name.strip().split()
    return tokens[0].upper() if tokens else ""


# ---------------------------------------------------------------------------
# Single-identity resolution
# ---------------------------------------------------------------------------


def resolve_disclosure_member(
    identity: DisclosureHeaderIdentity,
    member_rows: Sequence[dict[str, Any]],
) -> ResolutionResult:
    """Resolve one parsed disclosure header identity to a bioguide_id.

    member_rows must contain at minimum: bioguide_id, chamber, state,
    district (int or None), last_name, first_name.

    The district field must be an integer for House members and None for
    Senate members — matching what the DB member table stores.
    """
    chamber = identity.chamber.lower()
    state = identity.state.upper()
    district = identity.district
    target_last = _norm(identity.last_name)

    # Step 1 — narrow by geographic location + chamber
    location_matches = [
        row
        for row in member_rows
        if _norm(row.get("chamber") or "") == chamber.upper()
        and _norm(row.get("state") or "") == state
        and row.get("district") == district
    ]

    if not location_matches:
        return NoMatch(identity=identity, reason="no_member_for_location")

    # Step 2 — narrow by last_name (case-insensitive)
    last_matches = [
        row for row in location_matches if _norm(row.get("last_name") or "") == target_last
    ]

    if not last_matches:
        return NoMatch(identity=identity, reason="no_member_for_last_name")

    if len(last_matches) == 1:
        return Resolved(bioguide_id=last_matches[0]["bioguide_id"], identity=identity)

    # Step 3 — break last_name ties by first-name first-token
    target_first = _first_token(identity.first_name)
    if target_first:
        first_matches = [
            row for row in last_matches if _first_token(row.get("first_name") or "") == target_first
        ]
        if len(first_matches) == 1:
            return Resolved(bioguide_id=first_matches[0]["bioguide_id"], identity=identity)

    return Ambiguous(
        identity=identity,
        candidates=tuple(row["bioguide_id"] for row in last_matches),
    )


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------


def resolve_disclosure_members(
    identities: Sequence[DisclosureHeaderIdentity],
    member_rows: Sequence[dict[str, Any]],
) -> list[ResolutionResult]:
    """Resolve a sequence of disclosure header identities to bioguide_ids.

    Returns one ResolutionResult per identity in the same order.
    Each identity is resolved independently against the full member_rows set.
    """
    return [resolve_disclosure_member(identity, member_rows) for identity in identities]
