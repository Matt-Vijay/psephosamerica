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

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from defusedxml.ElementTree import fromstring
import httpx


_BASE = "https://disclosures.house.gov"
_HOUSE_INDEX_HOST = "disclosures.house.gov"
_MAX_INDEX_BYTES = 10 * 1024 * 1024

# Invariant: only two filing kinds are in v1 scope.
_INDEX_PATH: dict[str, str] = {
    "ptr": "public_disc/ptr-pdfs/{year}PTRindex.xml",
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
    suffix: str  # empty string when the XML element is absent or blank
    raw_filing_type: str
    state_dst: str  # e.g. "CA08" — state abbreviation + zero-padded district
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
    root = fromstring(html.strip())
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
    html = _fetch_house_index_text(url, client=client, max_bytes=_MAX_INDEX_BYTES)
    return parse_house_index(html, year=year, filing_kind=filing_kind)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _required(entry: Any, tag: str) -> str:
    el = entry.find(tag)
    if el is None or el.text is None:
        raise ValueError(f"Missing required field <{tag}> in <{entry.tag}>")
    raw_text = el.text
    if not isinstance(raw_text, str):
        raise ValueError(f"Field <{tag}> in <{entry.tag}> must be text")
    value = raw_text.strip()
    if not value:
        raise ValueError(f"Empty required field <{tag}> in <{entry.tag}>")
    return value


def _validate_house_index_response(response: Any, url: str) -> None:
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and 300 <= status_code < 400:
        raise ValueError(f"redirect response rejected for House disclosure index URL: {url!r}")

    response_url = getattr(response, "url", None)
    if not isinstance(response_url, (str, httpx.URL)):
        return
    parsed = urlparse(str(response_url))
    if parsed.scheme != "https" or parsed.hostname != _HOUSE_INDEX_HOST:
        raise ValueError(f"off-origin response rejected for House disclosure index URL: {url!r}")


def _fetch_house_index_text(
    url: str,
    *,
    client: httpx.Client | None,
    max_bytes: int,
) -> str:
    if client is None:
        with httpx.stream("GET", url, follow_redirects=False, timeout=30.0) as response:
            return _bounded_response_text(response, url, max_bytes=max_bytes)
    if isinstance(client, httpx.Client):
        with client.stream("GET", url, follow_redirects=False) as response:
            return _bounded_response_text(response, url, max_bytes=max_bytes)

    response = client.get(url, follow_redirects=False)
    return _bounded_response_text(response, url, max_bytes=max_bytes)


def _bounded_response_text(response: Any, url: str, *, max_bytes: int) -> str:
    _validate_house_index_response(response, url)
    response.raise_for_status()
    return _bounded_text(response, max_bytes=max_bytes)


def _content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    get = getattr(headers, "get", None)
    if get is None:
        return None
    raw = get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _bounded_text(response: Any, *, max_bytes: int) -> str:
    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise ValueError(
            f"House disclosure index exceeds maximum size of {max_bytes} bytes: {length}"
        )
    try:
        text = getattr(response, "text", None)
    except httpx.ResponseNotRead:
        text = None
    if not isinstance(text, str):
        iter_bytes = getattr(response, "iter_bytes", None)
        if not callable(iter_bytes):
            raise ValueError("House disclosure index response text must be a string")
        total = 0
        chunks: list[bytes] = []
        for chunk in iter_bytes():
            if not isinstance(chunk, bytes):
                raise ValueError("House disclosure index response yielded non-bytes chunk")
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"House disclosure index exceeds maximum size of {max_bytes} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"House disclosure index exceeds maximum size of {max_bytes} bytes")
    return text


def _text_or_empty(entry: Any, tag: str) -> str:
    el = entry.find(tag)
    if el is None or el.text is None:
        return ""
    raw_text = el.text
    return raw_text.strip() if isinstance(raw_text, str) else ""


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
