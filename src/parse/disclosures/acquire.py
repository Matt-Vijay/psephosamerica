"""Source and artifact metadata helpers for House and Senate disclosure PDFs.

This module does NOT perform network calls. It defines metadata structures
and helpers that the ingestion layer uses to locate, name, and store
disclosure artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src.parse.disclosures.models import Chamber


class ArtifactKind(str, Enum):
    PDF = "pdf"
    HTML = "html"


# --- Data-source slugs (match data_source.slug in the DB) ---

HOUSE_DISCLOSURE_SOURCE = "house-disclosures"
SENATE_DISCLOSURE_SOURCE = "senate-disclosures"


# --- Portal URL templates (no fetching, just string building) ---

_HOUSE_BASE = "https://disclosures.house.gov"
_SENATE_BASE = "https://efdsearch.senate.gov"


@dataclass(frozen=True)
class ArtifactMeta:
    """Metadata envelope for a disclosure artifact before storage."""

    source_slug: str
    chamber: Chamber
    artifact_kind: ArtifactKind
    source_url: str
    storage_key: str
    member_bioguide_id: str
    filing_year: int
    fetched_at: Optional[datetime] = None
    sha256: Optional[str] = None
    source_record_id: Optional[str] = None


def _storage_key(chamber: Chamber, bioguide_id: str, year: int, record_id: str) -> str:
    return f"disclosures/{chamber.value}/{year}/{bioguide_id}/{record_id}.pdf"


def house_artifact_meta(
    bioguide_id: str,
    filing_year: int,
    doc_id: str,
) -> ArtifactMeta:
    """Build artifact metadata for a House disclosure PDF."""
    source_url = f"{_HOUSE_BASE}/public_disc/ptr-pdfs/{filing_year}/{doc_id}.pdf"
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
    """Build artifact metadata for a Senate disclosure PDF."""
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
