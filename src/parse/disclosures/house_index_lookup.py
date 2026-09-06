"""House disclosure index lookup helpers keyed by doc_id.

Thin layer over house_index.py.  No DB writes.
"""

from __future__ import annotations

import httpx

from src.parse.disclosures.house_index import (
    HouseFilingKind,
    HouseIndexRow,
    fetch_house_index,
)


def index_house_rows_by_doc_id(
    rows: list[HouseIndexRow],
) -> dict[str, HouseIndexRow]:
    """Return a mapping of doc_id → HouseIndexRow for *rows*.

    Raises ValueError if any two rows share a doc_id; duplicate doc_ids
    indicate corrupted or concatenated index data and must not be silently
    dropped.
    """
    index: dict[str, HouseIndexRow] = {}
    for row in rows:
        if row.doc_id in index:
            raise ValueError(f"Duplicate doc_id {row.doc_id!r} in House index rows")
        index[row.doc_id] = row
    return index


def fetch_house_rows_by_doc_id(
    year: int,
    filing_kind: HouseFilingKind,
    *,
    client: httpx.Client | None = None,
) -> dict[str, HouseIndexRow]:
    """Fetch the House index for *year* / *filing_kind* and key rows by doc_id.

    Delegates network I/O to fetch_house_index; accepts the same optional
    *client* for session reuse or custom headers.

    Raises httpx.HTTPStatusError on non-2xx responses.
    Raises ValueError on duplicate doc_ids in the fetched index.
    """
    rows = fetch_house_index(year, filing_kind, client=client)
    return index_house_rows_by_doc_id(rows)
