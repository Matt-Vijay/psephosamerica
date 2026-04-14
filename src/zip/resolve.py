"""ZIP-to-federal-representatives resolution layer.

Pure helpers only.  No I/O, no network calls, no geocoder.

Callers supply precomputed row-shaped data; this module selects the
plurality House district, emits an ambiguity note when a ZIP crosses
district boundaries, and assembles the canonical 3-member federal bundle
(1 House member + 2 senators).

Congress only.  No state or local officials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# ---------------------------------------------------------------------------
# Input row shapes  (caller-supplied; no I/O performed here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZipDistrictRow:
    """One row from the Census ZIP/ZCTA-to-congressional-district crosswalk.

    ``population_share`` is the fraction of the ZIP's population that falls
    inside this district (0.0 < share <= 1.0).  Rows for the same ZIP sum
    to approximately 1.0.
    """

    zip5: str
    state: str          # two-letter postal abbreviation, upper-case
    district: int       # 0 for at-large
    population_share: float


@dataclass(frozen=True)
class DistrictMemberRow:
    """Current House member for one state/district pair."""

    state: str
    district: int
    bioguide_id: str
    full_name: str
    party: str
    slug: str


@dataclass(frozen=True)
class SenatorRow:
    """One of the two current senators for a state."""

    state: str
    bioguide_id: str
    full_name: str
    party: str
    slug: str
    seat: int           # 1 or 2, distinguishes the two seats


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRef:
    """Lightweight member reference used inside the federal bundle."""

    bioguide_id: str
    full_name: str
    party: str
    slug: str
    chamber: str        # "house" or "senate"


@dataclass(frozen=True)
class PluralityDistrict:
    """Result of selecting the plurality district for a ZIP."""

    state: str
    district: int
    population_share: float
    is_ambiguous: bool
    ambiguity_note: str | None


@dataclass(frozen=True)
class FederalBundle:
    """The 3-member federal bundle for one ZIP lookup.

    ``house_member`` is None only when the plurality district has no
    matching member row (data gap, not a logic error).
    ``senators`` may have 0-2 entries for the same reason.
    """

    zip5: str
    plurality_district: PluralityDistrict
    house_member: MemberRef | None
    senators: tuple[MemberRef, ...]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def select_plurality_district(
    zip5: str,
    zip_district_rows: Sequence[ZipDistrictRow],
) -> PluralityDistrict | None:
    """Return the district with the largest population share for *zip5*.

    Returns ``None`` when no rows match the given ZIP.

    When a ZIP maps to exactly one district, ``is_ambiguous`` is False and
    ``ambiguity_note`` is None.  When it maps to multiple districts,
    ``is_ambiguous`` is True and ``ambiguity_note`` describes the situation.
    """
    rows = [r for r in zip_district_rows if r.zip5 == zip5]
    if not rows:
        return None

    rows_sorted = sorted(rows, key=lambda r: r.population_share, reverse=True)
    best = rows_sorted[0]
    is_ambiguous = len(rows_sorted) > 1

    note: str | None = None
    if is_ambiguous:
        others = rows_sorted[1:]
        other_desc = ", ".join(
            f"{r.state}-{r.district:02d} ({r.population_share:.0%})"
            for r in others
        )
        note = (
            f"ZIP {zip5} spans multiple congressional districts. "
            f"Showing the plurality district "
            f"{best.state}-{best.district:02d} "
            f"({best.population_share:.0%} of ZIP population). "
            f"Other district(s): {other_desc}."
        )

    return PluralityDistrict(
        state=best.state,
        district=best.district,
        population_share=best.population_share,
        is_ambiguous=is_ambiguous,
        ambiguity_note=note,
    )


def find_house_member(
    state: str,
    district: int,
    district_member_rows: Sequence[DistrictMemberRow],
) -> MemberRef | None:
    """Return the House member for a given state/district, or None if absent."""
    for row in district_member_rows:
        if row.state == state and row.district == district:
            return MemberRef(
                bioguide_id=row.bioguide_id,
                full_name=row.full_name,
                party=row.party,
                slug=row.slug,
                chamber="house",
            )
    return None


def find_senators(
    state: str,
    senator_rows: Sequence[SenatorRow],
) -> tuple[MemberRef, ...]:
    """Return the (up to two) current senators for *state*, ordered by seat."""
    matched = sorted(
        (r for r in senator_rows if r.state == state),
        key=lambda r: r.seat,
    )
    return tuple(
        MemberRef(
            bioguide_id=r.bioguide_id,
            full_name=r.full_name,
            party=r.party,
            slug=r.slug,
            chamber="senate",
        )
        for r in matched
    )


def assemble_federal_bundle(
    zip5: str,
    zip_district_rows: Sequence[ZipDistrictRow],
    district_member_rows: Sequence[DistrictMemberRow],
    senator_rows: Sequence[SenatorRow],
) -> FederalBundle | None:
    """Build the complete 3-member federal bundle for *zip5*.

    Returns ``None`` when *zip5* has no district mapping rows at all.
    """
    plurality = select_plurality_district(zip5, zip_district_rows)
    if plurality is None:
        return None

    house_member = find_house_member(
        plurality.state, plurality.district, district_member_rows
    )
    senators = find_senators(plurality.state, senator_rows)

    return FederalBundle(
        zip5=zip5,
        plurality_district=plurality,
        house_member=house_member,
        senators=senators,
    )
