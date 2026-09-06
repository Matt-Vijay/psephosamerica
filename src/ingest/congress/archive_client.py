"""Congress archive client adapter.

Exposes the same iter_*/get_* interface as CongressAPIClient but reads from a
local archive bundle via CongressArchive path helpers and archive_loader
functions.  No network calls are made.

Design decisions
----------------
- List iterators (iter_members, iter_committees, iter_bills) delegate to the
  archive_loader bulk-load functions, which each read one file.
- Sub-resource/detail readers (iter_cosponsors, get_member_detail,
  get_bill_detail) resolve the specific file via CongressArchive path helpers
  and read only that file, avoiding full directory scans.
- Congress/chamber/bill_type filters are applied in-memory after loading;
  the archive is a per-congress snapshot so filters are best-effort but kept
  explicit for interface parity with CongressAPIClient.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from .archive import CongressArchive
from .archive_loader import (
    load_bills_payload,
    load_committees_payload,
    load_members_payload,
)
from .congress_api import (
    normalize_bill,
    normalize_committee,
    normalize_cosponsor,
    normalize_member,
)
from .models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord


class CongressArchiveClient:
    """Drop-in local replacement for CongressAPIClient.

    Reads all data from a local CongressArchive bundle; never opens a network
    connection.  Accepts the same call signatures as CongressAPIClient so that
    live_api.py helpers can use either client without modification.
    """

    def __init__(self, archive: CongressArchive) -> None:
        self._archive = archive
        self._congress = archive.congress

    # List iterators

    def iter_members(self, congress: int | None = None) -> Iterator[MemberRecord]:
        """Yield all members from the archive.

        The *congress* parameter is accepted for interface parity; the archive
        bundle is a congress-scoped snapshot so no per-congress filtering is
        applied.
        """
        payload = load_members_payload(self._archive)
        for raw in payload.get("members", []):
            yield normalize_member(raw)

    def iter_committees(
        self,
        congress: int,
        chamber: str | None = None,
    ) -> Iterator[CommitteeRecord]:
        """Yield committees from the archive, optionally filtered by *chamber*."""
        payload = load_committees_payload(self._archive)
        for raw in payload.get("committees", []):
            if chamber is not None and not _committee_matches_chamber(raw, chamber):
                continue
            yield normalize_committee(raw, congress=congress)

    def iter_bills(
        self,
        congress: int,
        bill_type: str | None = None,
    ) -> Iterator[BillRecord]:
        """Yield bills from the archive matching *congress* and *bill_type*."""
        payload = load_bills_payload(self._archive)
        for raw in payload.get("bills", []):
            if raw.get("congress") != congress:
                continue
            if bill_type is not None:
                raw_type = raw.get("type", "").lower().replace(".", "")
                if raw_type != bill_type.lower():
                    continue
            yield normalize_bill(raw)

    def iter_cosponsors(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
    ) -> Iterator[CosponsorRecord]:
        """Yield cosponsors for a specific bill from the archive.

        Returns nothing if the corresponding cosponsors file is absent.
        """
        path = self._archive.cosponsors_path(congress, bill_type, bill_number)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        for raw in data.get("cosponsors", []):
            yield normalize_cosponsor(
                raw,
                congress=congress,
                bill_type=bill_type,
                bill_number=bill_number,
            )

    # Detail accessors

    def get_member_detail(self, bioguide_id: str) -> MemberRecord:
        """Return a fully-normalized MemberRecord from the archive.

        Raises FileNotFoundError if the member detail file is absent.
        """
        path = self._archive.member_detail_path(bioguide_id)
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return normalize_member(data.get("member", data))

    def get_bill_detail(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
    ) -> BillRecord:
        """Return a fully-normalized BillRecord from the archive.

        Raises FileNotFoundError if the bill detail file is absent.
        """
        path = self._archive.bill_detail_path(congress, bill_type, bill_number)
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return normalize_bill(data.get("bill", data))


# Internal helpers


def _committee_matches_chamber(raw: dict[str, object], chamber: str) -> bool:
    """Return True when the raw committee dict matches the requested chamber."""
    raw_chamber = raw.get("chamber", "")
    if isinstance(raw_chamber, dict):
        raw_chamber = raw_chamber.get("name", "")
    raw_chamber_text = raw_chamber if isinstance(raw_chamber, str) else ""
    return chamber.lower() in raw_chamber_text.lower()
