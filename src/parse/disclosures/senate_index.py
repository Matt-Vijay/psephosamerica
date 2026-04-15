"""Senate EFD (efdsearch.senate.gov) disclosure index layer.

Exposes:
  senate_index_url  — canonical source URL for a given year's listing
  parse_senate_index — extract typed rows from a DataTables JSON payload or
                       raw HTML; pure function, no I/O
  fetch_senate_index — live fetch via the EFD search API; no DB writes

The Senate EFD search requires a two-step CSRF + terms-agreement protocol
followed by a paginated DataTables JSON endpoint.  fetch_senate_index owns
that protocol; parse_senate_index is kept pure so tests stay offline.

Chamber-specific behaviour lives here; do not unify with House logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]


_SENATE_EFD_BASE = "https://efdsearch.senate.gov"
_SEARCH_HOME = f"{_SENATE_EFD_BASE}/search/"
_DATA_ENDPOINT = f"{_SENATE_EFD_BASE}/search/report/data/"
_PAGE_SIZE = 100

# The EFD link cell contains an href like /search/view/paper/{doc_id}/
_DOC_HREF_RE = re.compile(r"/search/view/paper/([^/\"']+)/")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SenateIndexRow:
    """One entry from the Senate EFD annual/PTR filing index.

    Contains enough information to call senate_artifact_meta(bioguide_id,
    filing_year, doc_id) once the bioguide_id is resolved via name lookup.
    """

    first_name: str
    last_name: str
    office: str          # e.g. "Senator, TX" — used as a resolution hint
    report_type: str     # plain-text label, e.g. "Annual Report for CY2023"
    date_filed: str      # raw date string from the index, e.g. "01/15/2024"
    doc_id: str          # UUID used as the source_record_id in ArtifactMeta
    filing_year: int     # caller-supplied; not present in the raw row


def senate_index_url(year: int) -> str:
    """Canonical URL identifying the Senate EFD index for *year*.

    The EFD uses a POST-based protocol, so this URL is not directly GETtable,
    but it serves as the provenance source_url for the index request.
    """
    return f"{_SEARCH_HOME}?report_type=annual&search_year={year}"


def _extract_doc_id(cell: str) -> Optional[str]:
    m = _DOC_HREF_RE.search(cell)
    return m.group(1) if m else None


def _row_from_cells(cells: Sequence[str], year: int) -> Optional[SenateIndexRow]:
    """Build a SenateIndexRow from a 5-element cell list, or return None.

    Invariant: the link column (index 3) must contain a /search/view/paper/{id}/
    href; rows missing this are silently dropped because they cannot produce a
    valid ArtifactMeta.
    """
    if len(cells) < 5:
        return None
    first, last, office, link_cell, date_filed = (cells[i].strip() for i in range(5))
    doc_id = _extract_doc_id(link_cell)
    if not doc_id:
        return None
    report_type = _TAG_RE.sub("", link_cell).strip()
    return SenateIndexRow(
        first_name=first,
        last_name=last,
        office=office,
        report_type=report_type,
        date_filed=date_filed,
        doc_id=doc_id,
        filing_year=year,
    )


def parse_senate_index(
    payload: Union[dict[str, Any], str],
    *,
    year: int,
) -> list[SenateIndexRow]:
    """Return typed rows from a Senate EFD payload or HTML fragment.

    *payload* is either:
      - dict  — decoded JSON from the EFD DataTables endpoint
      - str   — raw HTML containing a <table> with <tr>/<td> rows

    Rows whose link cell does not contain a recognisable doc_id are dropped.
    The caller can detect truncation by comparing len(result) to the
    recordsTotal field in the JSON payload.
    """
    if isinstance(payload, dict):
        return [
            row
            for cells in payload.get("data", [])
            if (row := _row_from_cells(cells, year)) is not None
        ]

    # HTML path: prefer rows inside <tbody>, fall back to the whole fragment.
    tbody = re.search(r"<tbody[^>]*>(.*?)</tbody>", payload, re.DOTALL | re.IGNORECASE)
    body = tbody.group(1) if tbody else payload
    rows: list[SenateIndexRow] = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr.group(1), re.DOTALL | re.IGNORECASE)
        row = _row_from_cells(cells, year)
        if row is not None:
            rows.append(row)
    return rows


def fetch_senate_index(year: int, *, client=None) -> list[SenateIndexRow]:
    """Fetch all Senate EFD disclosure index rows for *year*.

    Protocol:
      1. GET _SEARCH_HOME to establish a session and capture the CSRF token.
      2. POST _SEARCH_HOME to accept the terms-of-use agreement.
      3. Paginate through _DATA_ENDPOINT with DataTables parameters until all
         records are collected.

    *client* is an optional httpx.Client.  When None, a client is created and
    closed on return.  A caller-supplied client is not closed here.

    No DB writes; returns pure data.
    """
    if _httpx is None:  # pragma: no cover
        raise ImportError("httpx is required for fetch_senate_index")

    own_client = client is None
    http: Any = client if client is not None else _httpx.Client(
        follow_redirects=True, timeout=30.0
    )
    try:
        resp = http.get(_SEARCH_HOME)
        resp.raise_for_status()
        csrftoken = http.cookies.get("csrftoken", "")

        http.post(
            _SEARCH_HOME,
            data={"csrfmiddlewaretoken": csrftoken, "action": "agree",
                  "agree_statement": "I agree"},
            headers={"Referer": _SEARCH_HOME},
        )

        all_rows: list[SenateIndexRow] = []
        start = 0
        while True:
            data_resp = http.post(
                _DATA_ENDPOINT,
                data={
                    "csrfmiddlewaretoken": csrftoken,
                    "start": start,
                    "length": _PAGE_SIZE,
                    "report_type[]": ["annual", "ptr"],
                    "submitted_start_date": f"01/01/{year} 00:00:00",
                    "submitted_end_date": f"12/31/{year} 23:59:59",
                },
                headers={
                    "Referer": _SEARCH_HOME,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            data_resp.raise_for_status()
            payload = data_resp.json()
            batch = parse_senate_index(payload, year=year)
            all_rows.extend(batch)
            records_total: int = payload.get("recordsTotal", 0)
            start += _PAGE_SIZE
            if not batch or start >= records_total:
                break
        return all_rows
    finally:
        if own_client:
            http.close()
