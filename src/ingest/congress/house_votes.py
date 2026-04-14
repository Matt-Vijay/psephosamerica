"""URL builders and XML parsing helpers for House roll-call votes.

Source: https://clerk.house.gov/evs/{year}/roll{number}.xml

Each XML file contains a single roll-call vote with member-level results.
Members are identified by ``bioguide_id`` in the XML attribute ``bioguideid``.
"""

from __future__ import annotations

import datetime
from xml.etree.ElementTree import fromstring

from .models import VoteCastRecord, VoteEventRecord

HOUSE_VOTE_BASE = "https://clerk.house.gov/evs"


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def roll_call_url(year: int, roll_call_number: int) -> str:
    """Build the canonical URL for a House roll-call XML file."""
    return f"{HOUSE_VOTE_BASE}/{year}/roll{roll_call_number:03d}.xml"


def roll_call_index_url(year: int) -> str:
    """URL for the House roll-call index page for a given year."""
    return f"{HOUSE_VOTE_BASE}/{year}/index.asp"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _vote_option(raw: str) -> str:
    mapping = {
        "yea": "yea",
        "aye": "yea",
        "nay": "nay",
        "no": "nay",
        "present": "present",
        "not voting": "not_voting",
    }
    return mapping.get(raw.lower().strip(), "not_voting")


def parse_house_vote_xml(xml_text: str) -> tuple[VoteEventRecord, list[VoteCastRecord]]:
    """Parse a House roll-call XML document into typed records.

    Returns a ``(VoteEventRecord, [VoteCastRecord, ...])`` tuple.
    """
    root = fromstring(xml_text)

    # -- vote metadata -------------------------------------------------------
    vote_meta = root.find("vote-metadata")
    if vote_meta is None:
        raise ValueError("Missing <vote-metadata> element")

    congress = int(vote_meta.findtext("congress", "0"))
    session = int(vote_meta.findtext("session", "0"))
    roll_call = int(vote_meta.findtext("rollcall-num", "0"))
    question = vote_meta.findtext("vote-question", "")
    result = vote_meta.findtext("vote-result")

    action_date_el = vote_meta.find("action-date")
    date_str = action_date_el.get("date", "") if action_date_el is not None else ""
    if date_str:
        # Format: "02-Jan-2025" or ISO
        try:
            vote_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            vote_date = datetime.datetime.strptime(date_str, "%d-%b-%Y").date()
    else:
        vote_date = datetime.date.today()

    source_url = roll_call_url(vote_date.year, roll_call)

    event = VoteEventRecord(
        chamber="house",
        congress=congress,
        session_number=session,
        roll_call_number=roll_call,
        vote_date=vote_date,
        question=question or "",
        result=result,
        source_url=source_url,
    )

    # -- individual votes ----------------------------------------------------
    casts: list[VoteCastRecord] = []
    vote_data = root.find("vote-data")
    if vote_data is not None:
        for voter in vote_data.iter("recorded-vote"):
            legislator = voter.find("legislator")
            if legislator is None:
                continue
            bioguide = legislator.get("name-id") or legislator.get("bioguideid")
            if not bioguide:
                continue
            vote_el = voter.find("vote")
            option = _vote_option(vote_el.text if vote_el is not None and vote_el.text else "not voting")
            casts.append(VoteCastRecord(
                chamber="house",
                congress=congress,
                session_number=session,
                roll_call_number=roll_call,
                vote_option=option,
                bioguide_id=bioguide,
                lis_member_id=None,
            ))

    return event, casts


def extract_bioguide_ids(xml_text: str) -> list[str]:
    """Extract all unique bioguide IDs from a House roll-call XML."""
    root = fromstring(xml_text)
    ids: set[str] = set()
    for legislator in root.iter("legislator"):
        bid = legislator.get("name-id") or legislator.get("bioguideid")
        if bid:
            ids.add(bid)
    return sorted(ids)
