"""House financial-disclosure index layer.

Fetches and parses the official XML index files published at disclosures.house.gov.
No DB writes.

Official index sources
----------------------
PTR   : https://disclosures.house.gov/public_disc/ptr-pdfs/{year}PTRindex.xml
Annual: https://disclosures.house.gov/public_disc/financial-pdfs/{year}FDindex.xml

Each XML file contains a <DISCLOSURE> root whose direct children are filing
entries (tag name varies by index type).  Every entry exposes the fields
needed to later call house_artifact_meta() once a bioguide_id is resolved
by name+state_dst matching against the member roster.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

import httpx


_BASE = "https://disclosures.house.gov"

# Invariant: only two filing kinds are in v1 scope.
_INDEX_PATH: dict[str, str] = {
    "ptr":    "public_disc/ptr-pdfs/{year}PTRindex.xml",
    "annual": "public_disc/financial-pdfs/{year}FDindex.xml",
}


class HouseFilingKind(str, Enum):
    PTR = "ptr"
    ANNUAL = "annual"


@dataclass(frozen=True)
class HouseIndexRow:
    """One entry from the House disclosure XML index.

    Carries the fields needed to call house_artifact_meta() once the
    member's bioguide_id is resolved externally by name+state_dst matching.

    raw_filing_type values seen in practice:
      "O" = original annual FD
      "A" = amendment (annual or PTR)
      "T" = termination FD
      "P" = periodic transaction report (PTR)
    """

    last_name: str
    first_name: str
    suffix: str           # empty string when the XML element is absent or blank
    raw_filing_type: str
    state_dst: str        # e.g. "CA08" — state abbreviation + zero-padded district
    year: int
    filing_date: date
    doc_id: str
    filing_kind: HouseFilingKind


def house_index_url(year: int, filing_kind: HouseFilingKind) -> str:
    """Return the canonical index URL for *year* and *filing_kind*."""
    path = _INDEX_PATH[filing_kind.value].format(year=year)
    return f"{_BASE}/{path}"


def parse_house_index(
    html: str,
    *,
    year: int,
    filing_kind: HouseFilingKind,
) -> list[HouseIndexRow]:
    """Parse the House XML index string and return typed rows.

    The parameter is named ``html`` to match the public interface contract;
    the content is XML.  All direct children of <DISCLOSURE> are treated as
    filing entries regardless of their element tag, because the tag name
    differs between PTR and FD index files.

    Raises ValueError on missing required fields or unparseable dates.
    """
    root = ET.fromstring(html.strip())
    rows: list[HouseIndexRow] = []
    for entry in root:
        rows.append(
            HouseIndexRow(
                last_name=_required(entry, "Last"),
                first_name=_required(entry, "First"),
                suffix=_text_or_empty(entry, "Suffix"),
                raw_filing_type=_required(entry, "FilingType"),
                state_dst=_required(entry, "StateDst"),
                year=int(_required(entry, "Year")),
                filing_date=_parse_filing_date(_required(entry, "FilingDate")),
                doc_id=_required(entry, "DocID"),
                filing_kind=filing_kind,
            )
        )
    return rows


def fetch_house_index(
    year: int,
    filing_kind: HouseFilingKind,
    *,
    client: Optional[httpx.Client] = None,
) -> list[HouseIndexRow]:
    """Fetch and parse the House index for *year* and *filing_kind*.

    Accepts an optional *client* for session reuse or custom headers.
    Falls back to a one-shot httpx.get when none is provided.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = house_index_url(year, filing_kind)
    if client is not None:
        response = client.get(url, follow_redirects=True)
    else:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return parse_house_index(response.text, year=year, filing_kind=filing_kind)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _required(entry: ET.Element, tag: str) -> str:
    el = entry.find(tag)
    if el is None or el.text is None:
        raise ValueError(f"Missing required field <{tag}> in <{entry.tag}>")
    value = el.text.strip()
    if not value:
        raise ValueError(f"Empty required field <{tag}> in <{entry.tag}>")
    return value


def _text_or_empty(entry: ET.Element, tag: str) -> str:
    el = entry.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _parse_filing_date(value: str) -> date:
    # House index format: MM/DD/YYYY
    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError(f"Unparseable date in House index: {value!r}")
    try:
        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        return date(year, month, day)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Unparseable date in House index: {value!r}") from exc
