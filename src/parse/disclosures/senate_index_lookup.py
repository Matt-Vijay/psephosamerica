"""Senate EFD disclosure index lookup helpers keyed by doc_id.

Exposes:
  index_senate_rows_by_doc_id — build a doc_id → SenateIndexRow mapping from
                                 an already-fetched row list; pure, no I/O
  fetch_senate_rows_by_doc_id — fetch all rows for a year and return the same
                                 mapping; delegates network work to senate_index

Chamber-specific; do not merge with House logic.
"""

from __future__ import annotations

import httpx

from src.parse.disclosures.senate_index import SenateIndexRow, fetch_senate_index


def index_senate_rows_by_doc_id(
    rows: list[SenateIndexRow],
) -> dict[str, SenateIndexRow]:
    """Return a mapping from doc_id to SenateIndexRow.

    doc_ids are UUIDs assigned by EFD and are unique per filing.  When
    duplicates appear in *rows* the last occurrence wins, matching the
    source order from the paginated API.
    """
    return {row.doc_id: row for row in rows}


def fetch_senate_rows_by_doc_id(
    year: int,
    *,
    client: httpx.Client | None = None,
) -> dict[str, SenateIndexRow]:
    """Fetch all Senate EFD rows for *year* and index them by doc_id.

    *client* is forwarded to fetch_senate_index unchanged; see that function
    for the session/CSRF protocol and close-on-return semantics.

    No DB writes; returns pure data.
    """
    rows = fetch_senate_index(year, client=client)
    return index_senate_rows_by_doc_id(rows)
