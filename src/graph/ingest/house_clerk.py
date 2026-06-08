"""Parse House Clerk roll-call vote XML into real member->bill vote data.

The House Clerk publishes every recorded vote as public XML
(``clerk.house.gov/evs/<year>/roll<NNN>.xml``) — no auth, within public-record
bounds. Each ``recorded-vote`` carries the member's bioguide ID (``name-id``)
and their choice, and the metadata carries the bill (``legis-num``) and date.
:func:`parse_house_rollcall_xml` turns that into a :class:`HouseRollCall`; a
runner maps bioguides to canonical Person IDs and the bill to its canonical ID
(:func:`bill_canonical_id_for`) and builds leakage-gated ``vote`` edges via
:func:`~src.graph.ingest.votes.vote_edge`.

Procedural votes (quorum calls, Speaker elections) have no parseable bill, so
``bill_canonical_id_for`` returns ``None`` and only the procedural fact remains.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from defusedxml import ElementTree as ET

from src.graph.bills import BillRef


@dataclass(frozen=True)
class HouseRollCall:
    """One parsed House roll-call vote."""

    congress: int
    session: str
    rollcall_num: int
    legis_num: str | None
    vote_question: str
    vote_result: str
    action_date: date
    recorded_votes: tuple[tuple[str, str], ...]  # (bioguide_id, choice)


def _parse_action_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%d-%b-%Y").date()


def parse_house_rollcall_xml(xml: str) -> HouseRollCall:
    """Parse House Clerk roll-call XML into a :class:`HouseRollCall`."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid roll-call XML: {exc}") from exc
    if root.tag != "rollcall-vote":
        raise ValueError(f"not a House roll-call document (root <{root.tag}>)")
    meta = root.find("vote-metadata")
    if meta is None:
        raise ValueError("roll-call XML is missing <vote-metadata>")

    legis_num = (meta.findtext("legis-num") or "").strip() or None
    recorded: list[tuple[str, str]] = []
    for element in root.iter("recorded-vote"):
        legislator = element.find("legislator")
        choice = (element.findtext("vote") or "").strip()
        if legislator is None:
            continue
        bioguide = (legislator.get("name-id") or "").strip()
        if bioguide and choice:
            recorded.append((bioguide, choice))

    return HouseRollCall(
        congress=int((meta.findtext("congress") or "0").strip()),
        session=(meta.findtext("session") or "").strip(),
        rollcall_num=int((meta.findtext("rollcall-num") or "0").strip()),
        legis_num=legis_num,
        vote_question=(meta.findtext("vote-question") or "").strip(),
        vote_result=(meta.findtext("vote-result") or "").strip(),
        action_date=_parse_action_date(meta.findtext("action-date") or ""),
        recorded_votes=tuple(recorded),
    )


def bill_canonical_id_for(rollcall: HouseRollCall) -> str | None:
    """The canonical bill ID for a substantive vote, or ``None`` if procedural."""
    if rollcall.legis_num is None:
        return None
    try:
        return BillRef.for_congress(rollcall.congress, rollcall.legis_num).canonical_id
    except ValueError:
        return None
