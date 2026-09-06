"""High-level Congress harvest helpers.

Wraps CongressAPIClient into explicit list-oriented fetch functions.
No DB writes; callers own persistence.

Fetch ordering contract
-----------------------
Every helper drains the client iterator into a ``list`` before returning, so
callers receive a fully-materialized, stable sequence.  ``fetch_cosponsors_for_bills``
iterates bills in the exact order supplied and appends cosponsors in that same
order; bills that return zero cosponsors contribute nothing to the output list
but do not interrupt the iteration.
"""

from __future__ import annotations

from collections.abc import Iterable

from .congress_api import CongressAPIClient
from .models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord


def fetch_members(
    client: CongressAPIClient,
    congress: int | None = None,
) -> list[MemberRecord]:
    """Return all members for *congress*, or all current members if omitted."""
    return list(client.iter_members(congress))


def fetch_committees(
    client: CongressAPIClient,
    congress: int,
    chamber: str | None = None,
) -> list[CommitteeRecord]:
    """Return all committees for *congress*, optionally filtered by *chamber*."""
    if chamber is None:
        return list(client.iter_committees(congress))
    return list(client.iter_committees(congress, chamber))


def fetch_bills(
    client: CongressAPIClient,
    congress: int,
    bill_type: str | None = None,
) -> list[BillRecord]:
    """Return all bills for *congress*, optionally filtered by *bill_type*."""
    if bill_type is None:
        return list(client.iter_bills(congress))
    return list(client.iter_bills(congress, bill_type))


def fetch_cosponsors_for_bills(
    client: CongressAPIClient,
    bills: Iterable[BillRecord],
) -> list[CosponsorRecord]:
    """Return cosponsors for every bill in *bills*.

    Iterates each bill independently; results are concatenated in input order.
    Bills with zero cosponsors contribute nothing to the output list.
    All pages for each bill are consumed before moving to the next bill,
    so the result is deterministic regardless of pagination depth.
    """
    results: list[CosponsorRecord] = []
    for bill in bills:
        results.extend(client.iter_cosponsors(bill.congress, bill.bill_type, bill.bill_number))
    return results
