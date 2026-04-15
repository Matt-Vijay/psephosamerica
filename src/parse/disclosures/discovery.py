"""Unified disclosure artifact discovery layer.

Translates raw index rows from the chamber-specific listing modules into
typed ArtifactMeta objects.  No DB writes; no direct network calls.

Bioguide resolution is a downstream normalization step — both house_index
and senate_index explicitly state this.  Artifacts returned here carry
member_bioguide_id="" until the normalization pipeline resolves the
name+district/office match.

Public API
----------
fetch_house_artifacts(year, filing_kind, *, client=None)   -> list[ArtifactMeta]
fetch_senate_artifacts(year, *, client=None)               -> list[ArtifactMeta]
fetch_disclosure_artifacts(chamber, year, *, filing_kind=None, client=None)
                                                           -> list[ArtifactMeta]
"""

from __future__ import annotations

from typing import Optional

from src.parse.disclosures.acquire import (
    HOUSE_DISCLOSURE_SOURCE,
    SENATE_DISCLOSURE_SOURCE,
    ArtifactKind,
    ArtifactMeta,
)
from src.parse.disclosures.house_index import (
    HouseFilingKind,
    HouseIndexRow,
    fetch_house_index,
)
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.senate_index import SenateIndexRow, fetch_senate_index

_HOUSE_BASE = "https://disclosures.house.gov"
_SENATE_EFD_BASE = "https://efdsearch.senate.gov"

_HOUSE_KIND_PATH = {
    HouseFilingKind.PTR: "public_disc/ptr-pdfs",
    HouseFilingKind.ANNUAL: "public_disc/financial-pdfs",
}


def _house_row_to_meta(row: HouseIndexRow) -> ArtifactMeta:
    kind_path = _HOUSE_KIND_PATH[row.filing_kind]
    source_url = f"{_HOUSE_BASE}/{kind_path}/{row.year}/{row.doc_id}.pdf"
    # storage_key omits the bioguide subfolder until normalization resolves it
    storage_key = f"disclosures/house/{row.year}/{row.doc_id}.pdf"
    return ArtifactMeta(
        source_slug=HOUSE_DISCLOSURE_SOURCE,
        chamber=Chamber.HOUSE,
        artifact_kind=ArtifactKind.PDF,
        source_url=source_url,
        storage_key=storage_key,
        member_bioguide_id="",  # resolved in normalization step via name+state_dst match
        filing_year=row.year,
        source_record_id=row.doc_id,
    )


def _senate_row_to_meta(row: SenateIndexRow) -> ArtifactMeta:
    source_url = f"{_SENATE_EFD_BASE}/search/view/paper/{row.doc_id}/"
    storage_key = f"disclosures/senate/{row.filing_year}/{row.doc_id}.pdf"
    return ArtifactMeta(
        source_slug=SENATE_DISCLOSURE_SOURCE,
        chamber=Chamber.SENATE,
        artifact_kind=ArtifactKind.PDF,
        source_url=source_url,
        storage_key=storage_key,
        member_bioguide_id="",  # resolved in normalization step via name+office match
        filing_year=row.filing_year,
        source_record_id=row.doc_id,
    )


def fetch_house_artifacts(
    year: int,
    filing_kind: str,
    *,
    client: Optional[object] = None,
) -> list[ArtifactMeta]:
    """Return provisional ArtifactMeta for every House filing of *filing_kind* in *year*.

    *filing_kind* must be a valid HouseFilingKind value (``"ptr"`` or ``"annual"``).
    The returned objects have member_bioguide_id="" — bioguide resolution happens
    downstream in the normalization pipeline via name+state_dst matching.
    """
    kind = HouseFilingKind(filing_kind)
    rows = fetch_house_index(year, kind, client=client)
    return [_house_row_to_meta(row) for row in rows]


def fetch_senate_artifacts(
    year: int,
    *,
    client: Optional[object] = None,
) -> list[ArtifactMeta]:
    """Return provisional ArtifactMeta for every Senate EFD filing in *year*.

    The returned objects have member_bioguide_id="" — bioguide resolution happens
    downstream in the normalization pipeline via name+office matching.
    """
    rows = fetch_senate_index(year, client=client)
    return [_senate_row_to_meta(row) for row in rows]


def fetch_disclosure_artifacts(
    chamber: str,
    year: int,
    *,
    filing_kind: Optional[str] = None,
    client: Optional[object] = None,
) -> list[ArtifactMeta]:
    """Return provisional ArtifactMeta for all filings in *chamber* and *year*.

    Args:
        chamber:     ``"house"`` or ``"senate"``.  ``"both"`` is not supported;
                     call this function twice if both chambers are needed.
        year:        Calendar year of the filing period.
        filing_kind: Required when *chamber* is ``"house"`` (e.g. ``"ptr"``,
                     ``"annual"``).  Ignored for ``"senate"``.
        client:      Optional HTTP client forwarded to the index layer.

    Raises:
        ValueError: If *chamber* is not ``"house"`` or ``"senate"``.
        ValueError: If *chamber* is ``"house"`` and *filing_kind* is omitted.
    """
    if chamber == "house":
        if filing_kind is None:
            raise ValueError("filing_kind is required for the house chamber")
        return fetch_house_artifacts(year, filing_kind, client=client)
    if chamber == "senate":
        return fetch_senate_artifacts(year, client=client)
    raise ValueError(f"chamber must be 'house' or 'senate', got {chamber!r}")
