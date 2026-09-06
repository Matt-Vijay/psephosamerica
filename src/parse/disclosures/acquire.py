"""Artifact metadata for House and Senate disclosure PDFs.  No network calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from src.parse.disclosures.models import Chamber


class ArtifactKind(str, Enum):
    PDF = "pdf"
    HTML = "html"


# --- Data-source slugs (match data_source.slug in the DB) ---

HOUSE_DISCLOSURE_SOURCE = "house-disclosures"
SENATE_DISCLOSURE_SOURCE = "senate-disclosures"


_HOUSE_BASE = "https://disclosures.house.gov"
_SENATE_BASE = "https://efdsearch.senate.gov"
_HOUSE_FILING_KIND_PATH: dict[str, str] = {
    "ptr": "public_disc/ptr-pdfs",
    "annual": "public_disc/financial-pdfs",
}
HouseArtifactFilingKind = Literal["ptr", "annual"]


@dataclass(frozen=True)
class ArtifactMeta:
    source_slug: str
    chamber: Chamber
    artifact_kind: ArtifactKind
    source_url: str
    storage_key: str
    member_bioguide_id: str
    filing_year: int
    fetched_at: datetime | None = None
    sha256: str | None = None
    source_record_id: str | None = None


def _storage_key(chamber: Chamber, bioguide_id: str, year: int, record_id: str) -> str:
    return f"disclosures/{chamber.value}/{year}/{bioguide_id}/{record_id}.pdf"


def house_artifact_meta(
    bioguide_id: str,
    filing_year: int,
    doc_id: str,
    *,
    filing_kind: HouseArtifactFilingKind = "ptr",
) -> ArtifactMeta:
    kind_path = _HOUSE_FILING_KIND_PATH.get(filing_kind)
    if kind_path is None:
        raise ValueError(f"filing_kind must be 'ptr' or 'annual', got {filing_kind!r}")
    source_url = f"{_HOUSE_BASE}/{kind_path}/{filing_year}/{doc_id}.pdf"
    return ArtifactMeta(
        source_slug=HOUSE_DISCLOSURE_SOURCE,
        chamber=Chamber.HOUSE,
        artifact_kind=ArtifactKind.PDF,
        source_url=source_url,
        storage_key=_storage_key(Chamber.HOUSE, bioguide_id, filing_year, doc_id),
        member_bioguide_id=bioguide_id,
        filing_year=filing_year,
        source_record_id=doc_id,
    )


def senate_artifact_meta(
    bioguide_id: str,
    filing_year: int,
    doc_id: str,
) -> ArtifactMeta:
    source_url = f"{_SENATE_BASE}/search/view/paper/{doc_id}/"
    return ArtifactMeta(
        source_slug=SENATE_DISCLOSURE_SOURCE,
        chamber=Chamber.SENATE,
        artifact_kind=ArtifactKind.PDF,
        source_url=source_url,
        storage_key=_storage_key(Chamber.SENATE, bioguide_id, filing_year, doc_id),
        member_bioguide_id=bioguide_id,
        filing_year=filing_year,
        source_record_id=doc_id,
    )
