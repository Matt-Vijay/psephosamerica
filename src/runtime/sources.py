from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["official", "supporting", "artifact", "internal"]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    slug: str
    name: str
    source_kind: SourceKind
    base_url: str | None


CONGRESS_CORE = SourceSpec(
    slug="congress-gov-api",
    name="Congress.gov API",
    source_kind="official",
    base_url="https://api.congress.gov/v3",
)

HOUSE_DISCLOSURES = SourceSpec(
    slug="house-disclosures",
    name="House Financial Disclosures",
    source_kind="official",
    base_url="https://disclosures.house.gov",
)

SENATE_DISCLOSURES = SourceSpec(
    slug="senate-disclosures",
    name="Senate Financial Disclosures",
    source_kind="official",
    base_url="https://efdsearch.senate.gov",
)

FEC_BULK = SourceSpec(
    slug="fec-bulk",
    name="Federal Election Commission Bulk Data",
    source_kind="official",
    base_url="https://www.fec.gov",
)

MEMBER_FEC_CROSSWALK = SourceSpec(
    slug="member-fec-crosswalk",
    name="Member FEC Candidate Crosswalk",
    source_kind="supporting",
    base_url="https://github.com/unitedstates/congress-legislators",
)

DISCLOSURE_LOAD = SourceSpec(
    slug="financial-disclosures",
    name="Disclosure Canonical Load",
    source_kind="internal",
    base_url=None,
)

CONFLICT_RECOMPUTE = SourceSpec(
    slug="conflict-recompute",
    name="Conflict-of-Interest Recompute",
    source_kind="internal",
    base_url=None,
)

SNAPSHOT_PUBLISH = SourceSpec(
    slug="snapshot-publish",
    name="Published Score Snapshot",
    source_kind="artifact",
    base_url=None,
)

_ALL: tuple[SourceSpec, ...] = (
    CONGRESS_CORE,
    HOUSE_DISCLOSURES,
    SENATE_DISCLOSURES,
    FEC_BULK,
    MEMBER_FEC_CROSSWALK,
    DISCLOSURE_LOAD,
    CONFLICT_RECOMPUTE,
    SNAPSHOT_PUBLISH,
)

_BY_SLUG: dict[str, SourceSpec] = {s.slug: s for s in _ALL}


DISCLOSURE_SOURCE_SLUG_BY_CHAMBER: dict[str, str] = {
    "house": HOUSE_DISCLOSURES.slug,
    "senate": SENATE_DISCLOSURES.slug,
}

_LEGACY_DISCLOSURE_SOURCE_SLUGS: dict[str, str] = {
    "house_disclosures": HOUSE_DISCLOSURES.slug,
    "senate_disclosures": SENATE_DISCLOSURES.slug,
}


def all_sources() -> tuple[SourceSpec, ...]:
    return _ALL


def canonical_source_slug(slug: str) -> str:
    """Return the canonical data_source slug for known legacy aliases."""
    return _LEGACY_DISCLOSURE_SOURCE_SLUGS.get(slug, slug)


def source_by_slug(slug: str) -> SourceSpec:
    slug = canonical_source_slug(slug)
    try:
        return _BY_SLUG[slug]
    except KeyError:
        raise KeyError(f"unknown source slug: {slug!r}") from None
