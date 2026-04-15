"""Reusable temp Congress archive fixture support.

Creates a temporary archive directory tree from inline JSON/XML data so
tests can exercise archive-reading code without static binary fixtures or
network calls.

Public API
----------
CongressArchiveBuilder
    Fluent builder that writes JSON/XML files into a tempfile.TemporaryDirectory.

make_archive(tmp_path, **kwargs) -> CongressArchive
    One-call convenience wrapper.  Returns a CongressArchive rooted at
    *tmp_path*.

All data is generated from inline Python dicts/strings; no binary fixtures
are created.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from src.ingest.congress.archive import CongressArchive


# ---------------------------------------------------------------------------
# Minimal inline fixtures (representative, not exhaustive)
# ---------------------------------------------------------------------------

_DEFAULT_MEMBER: dict[str, Any] = {
    "bioguideId": "P000197",
    "firstName": "Nancy",
    "lastName": "Pelosi",
    "directOrderName": "Nancy Pelosi",
    "partyName": "Democratic",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023-01-03"}]},
}

_DEFAULT_COMMITTEE: dict[str, Any] = {
    "systemCode": "hswm00",
    "chamber": "House",
    "committeeTypeCode": "Standing",
    "name": "Committee on Ways and Means",
}

_DEFAULT_BILL: dict[str, Any] = {
    "congress": 119,
    "type": "HR",
    "number": 1,
    "title": "Test Bill One",
    "introducedDate": "2025-01-09",
    "latestAction": {"actionDate": "2025-03-01", "text": "Passed"},
}

_DEFAULT_MEMBER_DETAIL: dict[str, Any] = {
    "member": {
        "bioguideId": "P000197",
        "firstName": "Nancy",
        "lastName": "Pelosi",
        "directOrderName": "Nancy Pelosi",
        "partyName": "Democratic",
        "state": "CA",
        "currentMember": True,
        "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2023-01-03"}]},
    }
}

_DEFAULT_BILL_DETAIL: dict[str, Any] = {
    "bill": {
        "congress": 119,
        "type": "HR",
        "number": 1,
        "title": "Test Bill One",
        "introducedDate": "2025-01-09",
        "latestAction": {"actionDate": "2025-03-01", "text": "Passed"},
    }
}

_DEFAULT_COSPONSORS: dict[str, Any] = {
    "cosponsors": [
        {
            "bioguideId": "S000148",
            "isOriginalCosponsor": False,
            "sponsorshipDate": "2025-01-15",
        }
    ]
}

_HOUSE_VOTE_XML_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0"?>
    <rollcall-vote>
      <vote-metadata>
        <congress>{congress}</congress>
        <session>{session}</session>
        <rollcall-num>{roll_call_number}</rollcall-num>
        <vote-question>On Passage</vote-question>
        <vote-result>Passed</vote-result>
        <action-date date="{iso_date}">{display_date}</action-date>
      </vote-metadata>
      <vote-data>
        <recorded-vote>
          <legislator name-id="P000197" party="D" state="CA">Pelosi</legislator>
          <vote>Yea</vote>
        </recorded-vote>
      </vote-data>
    </rollcall-vote>
""")

_SENATE_VOTE_XML_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0"?>
    <roll_call_vote>
      <congress>{congress}</congress>
      <session>{session}</session>
      <vote_number>{roll_call_number}</vote_number>
      <vote_question_text>On the Motion</vote_question_text>
      <vote_result_text>Agreed To</vote_result_text>
      <vote_date>{display_date}</vote_date>
      <members>
        <member>
          <lis_member_id>S270</lis_member_id>
          <member_full>Schumer (D-NY)</member_full>
          <vote_cast>Yea</vote_cast>
        </member>
      </members>
    </roll_call_vote>
""")


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class CongressArchiveBuilder:
    """Write an in-memory Congress archive to a real temp directory.

    Usage::

        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.with_members([...]).with_committees([...]).build()
        archive = builder.archive()
    """

    def __init__(self, root: Path, congress: int = 119) -> None:
        self._root = root
        self._congress = congress
        self._members: list[dict[str, Any]] = [_DEFAULT_MEMBER]
        self._committees: list[dict[str, Any]] = [_DEFAULT_COMMITTEE]
        self._bills: list[dict[str, Any]] = [_DEFAULT_BILL]
        self._member_details: dict[str, dict[str, Any]] = {
            "P000197": _DEFAULT_MEMBER_DETAIL
        }
        self._bill_details: dict[tuple[int, str, int], dict[str, Any]] = {
            (119, "hr", 1): _DEFAULT_BILL_DETAIL
        }
        self._cosponsors: dict[tuple[int, str, int], dict[str, Any]] = {
            (119, "hr", 1): _DEFAULT_COSPONSORS
        }
        # vote tuples: (year, roll_call_number, congress, session)
        self._house_votes: list[tuple[int, int, int, int]] = []
        # vote tuples: (congress, session, roll_call_number)
        self._senate_votes: list[tuple[int, int, int]] = []

    # ------------------------------------------------------------------
    # Fluent setters
    # ------------------------------------------------------------------

    def with_members(self, members: list[dict[str, Any]]) -> "CongressArchiveBuilder":
        self._members = members
        return self

    def with_committees(
        self, committees: list[dict[str, Any]]
    ) -> "CongressArchiveBuilder":
        self._committees = committees
        return self

    def with_bills(self, bills: list[dict[str, Any]]) -> "CongressArchiveBuilder":
        self._bills = bills
        return self

    def with_member_detail(
        self, bioguide_id: str, detail: dict[str, Any]
    ) -> "CongressArchiveBuilder":
        self._member_details[bioguide_id] = detail
        return self

    def with_bill_detail(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
        detail: dict[str, Any],
    ) -> "CongressArchiveBuilder":
        self._bill_details[(congress, bill_type, bill_number)] = detail
        return self

    def with_cosponsors(
        self,
        congress: int,
        bill_type: str,
        bill_number: int,
        payload: dict[str, Any],
    ) -> "CongressArchiveBuilder":
        self._cosponsors[(congress, bill_type, bill_number)] = payload
        return self

    def add_house_vote(
        self,
        year: int,
        roll_call_number: int,
        congress: int = 119,
        session: int = 1,
    ) -> "CongressArchiveBuilder":
        self._house_votes.append((year, roll_call_number, congress, session))
        return self

    def add_senate_vote(
        self,
        congress: int,
        session: int,
        roll_call_number: int,
    ) -> "CongressArchiveBuilder":
        self._senate_votes.append((congress, session, roll_call_number))
        return self

    def no_member_details(self) -> "CongressArchiveBuilder":
        self._member_details = {}
        return self

    def no_bill_details(self) -> "CongressArchiveBuilder":
        self._bill_details = {}
        return self

    def no_cosponsors(self) -> "CongressArchiveBuilder":
        self._cosponsors = {}
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> "CongressArchiveBuilder":
        """Write all files to disk; returns self for chaining."""
        root = self._root
        root.mkdir(parents=True, exist_ok=True)

        _write_json(root / "members.json", {"members": self._members})
        _write_json(root / "committees.json", {"committees": self._committees})
        _write_json(root / "bills.json", {"bills": self._bills})

        # member_details/
        if self._member_details:
            md_dir = root / "member_details"
            md_dir.mkdir(exist_ok=True)
            for bioguide_id, detail in self._member_details.items():
                _write_json(md_dir / f"{bioguide_id}.json", detail)

        # bill_details/
        if self._bill_details:
            bd_dir = root / "bill_details"
            bd_dir.mkdir(exist_ok=True)
            for (c, bt, bn), detail in self._bill_details.items():
                _write_json(bd_dir / f"{c}_{bt}_{bn}.json", detail)

        # cosponsors/
        if self._cosponsors:
            cs_dir = root / "cosponsors"
            cs_dir.mkdir(exist_ok=True)
            for (c, bt, bn), payload in self._cosponsors.items():
                _write_json(cs_dir / f"{c}_{bt}_{bn}.json", payload)

        # house_votes/
        if self._house_votes:
            hv_dir = root / "house_votes"
            hv_dir.mkdir(exist_ok=True)
            for year, rc, congress, session in self._house_votes:
                filename = f"{year}_{rc:04d}.xml"
                xml = _HOUSE_VOTE_XML_TEMPLATE.format(
                    congress=congress,
                    session=session,
                    roll_call_number=rc,
                    iso_date=f"{year}-01-15",
                    display_date=f"15-Jan-{year}",
                )
                (hv_dir / filename).write_text(xml, encoding="utf-8")

        # senate_votes/
        if self._senate_votes:
            sv_dir = root / "senate_votes"
            sv_dir.mkdir(exist_ok=True)
            for congress, session, rc in self._senate_votes:
                filename = f"{congress}_{session}_{rc:05d}.xml"
                xml = _SENATE_VOTE_XML_TEMPLATE.format(
                    congress=congress,
                    session=session,
                    roll_call_number=rc,
                    display_date="January 20, 2025",
                )
                (sv_dir / filename).write_text(xml, encoding="utf-8")

        return self

    def archive(self) -> CongressArchive:
        """Return a CongressArchive rooted at the builder's directory."""
        return CongressArchive(root=self._root, congress=self._congress)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def make_archive(
    tmp_path: Path,
    *,
    congress: int = 119,
    members: list[dict[str, Any]] | None = None,
    committees: list[dict[str, Any]] | None = None,
    bills: list[dict[str, Any]] | None = None,
    member_details: dict[str, dict[str, Any]] | None = None,
    bill_details: dict[tuple[int, str, int], dict[str, Any]] | None = None,
    cosponsors: dict[tuple[int, str, int], dict[str, Any]] | None = None,
    house_vote_keys: list[tuple[int, int]] | None = None,
    senate_vote_keys: list[tuple[int, int, int]] | None = None,
) -> CongressArchive:
    """Create a populated temp archive and return a CongressArchive.

    Args:
        tmp_path:         Root directory (typically pytest's tmp_path fixture).
        congress:         Congress number stored in the archive and manifest.
        members:          Override the members list payload.
        committees:       Override the committees list payload.
        bills:            Override the bills list payload.
        member_details:   Map of bioguide_id -> full detail dict (with "member" key).
        bill_details:     Map of (congress, bill_type, bill_number) -> detail dict.
        cosponsors:       Map of (congress, bill_type, bill_number) -> cosponsor payload.
        house_vote_keys:  List of (year, roll_call_number) to generate XML for.
        senate_vote_keys: List of (congress, session, roll_call_number) to generate XML for.

    Returns:
        A CongressArchive pointing at *tmp_path* with all requested files written.
    """
    builder = CongressArchiveBuilder(tmp_path, congress=congress)

    if members is not None:
        builder.with_members(members)
    if committees is not None:
        builder.with_committees(committees)
    if bills is not None:
        builder.with_bills(bills)

    if member_details is not None:
        builder._member_details = member_details
    if bill_details is not None:
        builder._bill_details = bill_details
    if cosponsors is not None:
        builder._cosponsors = cosponsors

    for (year, rc) in (house_vote_keys or []):
        builder.add_house_vote(year, rc, congress=congress)
    for (c, sess, rc) in (senate_vote_keys or []):
        builder.add_senate_vote(c, sess, rc)

    builder.build()
    return builder.archive()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
