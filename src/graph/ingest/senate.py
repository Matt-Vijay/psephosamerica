"""Parse Senate roll-call vote XML into real senator->bill vote data.

The Senate publishes every roll-call vote as public XML
(``senate.gov/legislative/LIS/roll_call_votes/...``) — no auth, within
public-record bounds. Each ``member`` carries the senator's LIS member ID, name,
state, party, and cast; the metadata carries the measure (``document_name``) and
date. :func:`parse_senate_rollcall_xml` turns that into a :class:`SenateRollCall`
(the House counterpart lives in :mod:`src.graph.ingest.house_clerk`).

Senators are keyed by their LIS member ID; nominations (``PN…``) and other
non-bill measures yield no canonical bill (:func:`bill_canonical_id_for` returns
``None``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from defusedxml import ElementTree as ET

from src.graph.bills import BillRef

_DATE_RE = re.compile(r"([A-Za-z]+ \d{1,2}, \d{4})")


@dataclass(frozen=True)
class SenateMemberVote:
    """One senator's recorded vote."""

    lis_member_id: str
    full_name: str
    state: str
    party: str
    choice: str


@dataclass(frozen=True)
class SenateRollCall:
    """One parsed Senate roll-call vote."""

    congress: int
    session: int
    vote_number: int
    document_name: str | None
    vote_result: str
    vote_date: date
    recorded_votes: tuple[SenateMemberVote, ...]


def _parse_vote_date(raw: str) -> date:
    match = _DATE_RE.search(raw)
    if match is None:
        raise ValueError(f"unparseable Senate vote date: {raw!r}")
    return datetime.strptime(match.group(1), "%B %d, %Y").date()


def parse_senate_rollcall_xml(xml: str) -> SenateRollCall:
    """Parse Senate roll-call XML into a :class:`SenateRollCall`."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid roll-call XML: {exc}") from exc
    if root.tag != "roll_call_vote":
        raise ValueError(f"not a Senate roll-call document (root <{root.tag}>)")

    votes: list[SenateMemberVote] = []
    for member in root.iter("member"):
        lis = (member.findtext("lis_member_id") or "").strip()
        choice = (member.findtext("vote_cast") or "").strip()
        first = (member.findtext("first_name") or "").strip()
        last = (member.findtext("last_name") or "").strip()
        if not lis or not choice:
            continue
        votes.append(
            SenateMemberVote(
                lis_member_id=lis,
                full_name=" ".join(part for part in (first, last) if part),
                state=(member.findtext("state") or "").strip(),
                party=(member.findtext("party") or "").strip(),
                choice=choice,
            )
        )

    document_element = root.find("document")
    document = None
    if document_element is not None:
        document = (document_element.findtext("document_name") or "").strip() or None
    return SenateRollCall(
        congress=int((root.findtext("congress") or "0").strip()),
        session=int((root.findtext("session") or "0").strip()),
        vote_number=int((root.findtext("vote_number") or "0").strip()),
        document_name=document,
        vote_result=(root.findtext("vote_result") or "").strip(),
        vote_date=_parse_vote_date(root.findtext("vote_date") or ""),
        recorded_votes=tuple(votes),
    )


def bill_canonical_id_for(rollcall: SenateRollCall) -> str | None:
    """The canonical bill ID for a measure vote, or ``None`` for nominations etc."""
    if rollcall.document_name is None:
        return None
    try:
        return BillRef.for_congress(rollcall.congress, rollcall.document_name).canonical_id
    except ValueError:
        return None
