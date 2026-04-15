"""Unified disclosure index lookup by chamber, year, and doc_id.

Delegates to house_index and senate_index; does not duplicate their type
definitions or fetch protocols.  House and Senate branching is kept explicit —
the two sources differ in fetch protocol, field sets, and filing-kind
granularity.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from src.parse.disclosures.house_index import (
    HouseFilingKind,
    HouseIndexRow,
    fetch_house_index,
)
from src.parse.disclosures.senate_index import (
    SenateIndexRow,
    fetch_senate_index,
)

Chamber = Literal["house", "senate"]


def fetch_disclosure_rows_by_doc_id(
    chamber: Chamber,
    year: int,
    *,
    filing_kind: Optional[str] = None,
    client=None,
) -> Union[dict[str, HouseIndexRow], dict[str, SenateIndexRow]]:
    """Return a doc_id-keyed dict of index rows for *chamber* and *year*.

    House
    -----
    *filing_kind* accepts "ptr" or "annual" (case-insensitive values of
    HouseFilingKind).  When None, both kinds are fetched and merged into a
    single dict.  If the same doc_id appears in both indices (which the source
    data does not produce in practice), the annual entry wins because it is
    inserted last.

    Senate
    ------
    The Senate EFD endpoint returns all report types in one paginated request;
    *filing_kind* is ignored for this chamber.

    Parameters
    ----------
    chamber:      "house" or "senate"
    year:         filing year
    filing_kind:  optional kind filter (House only)
    client:       optional httpx.Client for session reuse or test injection

    Raises
    ------
    ValueError    if *chamber* is not "house" or "senate"
    ValueError    if *filing_kind* is supplied for House and is not a valid
                  HouseFilingKind value
    """
    if chamber == "house":
        return _house_lookup(year, filing_kind=filing_kind, client=client)
    if chamber == "senate":
        return _senate_lookup(year, client=client)
    raise ValueError(f"Unknown chamber: {chamber!r}; expected 'house' or 'senate'")


# ---------------------------------------------------------------------------
# Chamber-specific helpers
# ---------------------------------------------------------------------------


def _house_lookup(
    year: int,
    *,
    filing_kind: Optional[str],
    client,
) -> dict[str, HouseIndexRow]:
    if filing_kind is not None:
        # Delegates validation to HouseFilingKind; raises ValueError on bad input.
        kinds = [HouseFilingKind(filing_kind)]
    else:
        kinds = list(HouseFilingKind)

    result: dict[str, HouseIndexRow] = {}
    for kind in kinds:
        for row in fetch_house_index(year, kind, client=client):
            result[row.doc_id] = row
    return result


def _senate_lookup(
    year: int,
    *,
    client,
) -> dict[str, SenateIndexRow]:
    rows = fetch_senate_index(year, client=client)
    return {row.doc_id: row for row in rows}
