"""URL builders and XML parsing helpers for Senate roll-call votes.

Source: https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{number}.xml

Senate vote XML identifies members by ``lis_member_id``, which must be
mapped to ``bioguide_id`` downstream using the crosswalk in
``unitedstates/congress-legislators``.
"""

from __future__ import annotations

import datetime
from xml.etree.ElementTree import fromstring

from .models import VoteCastRecord, VoteEventRecord

SENATE_VOTE_BASE = "https://www.senate.gov/legislative/LIS/roll_call_votes"


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def roll_call_url(congress: int, session: int, vote_number: int) -> str:
    """Build the canonical URL for a Senate roll-call XML file."""
    prefix = f"vote{congress}{session}"
    filename = f"vote_{congress}_{session}_{vote_number:05d}.xml"
    return f"{SENATE_VOTE_BASE}/{prefix}/{filename}"


def roll_call_list_url(congress: int, session: int) -> str:
    """URL for the Senate roll-call listing for a congress/session."""
    prefix = f"vote{congress}{session}"
    return f"{SENATE_VOTE_BASE}/{prefix}/vote_summary.xml"


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
        "guilty": "yea",
        "not guilty": "nay",
    }
    return mapping.get(raw.lower().strip(), "not_voting")


def parse_senate_vote_xml(xml_text: str) -> tuple[VoteEventRecord, list[VoteCastRecord]]:
    """Parse a Senate roll-call XML document into typed records.

    Returns a ``(VoteEventRecord, [VoteCastRecord, ...])`` tuple.

    Senate XML uses ``lis_member_id`` to identify members.  The returned
    ``VoteCastRecord`` objects carry ``lis_member_id`` and leave
    ``bioguide_id`` as ``None``.  Downstream crosswalk resolution fills
    the bioguide after ingest.
    """
    root = fromstring(xml_text)

    congress = int(root.findtext("congress", "0"))
    session = int(root.findtext("session", "0"))
    vote_number = int(root.findtext("vote_number", "0"))
    question = root.findtext("vote_question_text", "") or root.findtext("question", "")
    result = root.findtext("vote_result_text") or root.findtext("vote_result")

    vote_date_str = root.findtext("vote_date", "")
    if vote_date_str:
        try:
            vote_date = datetime.date.fromisoformat(vote_date_str[:10])
        except ValueError:
            vote_date = datetime.datetime.strptime(vote_date_str.split(",")[0].strip(), "%B %d").replace(year=datetime.date.today().year).date()
    else:
        vote_date = datetime.date.today()

    source_url = roll_call_url(congress, session, vote_number)

    event = VoteEventRecord(
        chamber="senate",
        congress=congress,
        session_number=session,
        roll_call_number=vote_number,
        vote_date=vote_date,
        question=question or "",
        result=result,
        source_url=source_url,
    )

    # -- individual votes ----------------------------------------------------
    casts: list[VoteCastRecord] = []
    members_el = root.find("members")
    if members_el is not None:
        for member in members_el.iter("member"):
            lis_id = member.findtext("lis_member_id")
            if not lis_id:
                continue
            vote_text = member.findtext("vote_cast", "Not Voting")
            option = _vote_option(vote_text)
            casts.append(VoteCastRecord(
                chamber="senate",
                congress=congress,
                session_number=session,
                roll_call_number=vote_number,
                vote_option=option,
                bioguide_id=None,
                lis_member_id=lis_id.strip(),
            ))

    return event, casts


def extract_lis_member_ids(xml_text: str) -> list[str]:
    """Extract all unique LIS member IDs from a Senate roll-call XML."""
    root = fromstring(xml_text)
    ids: set[str] = set()
    for member in root.iter("member"):
        lis_id = member.findtext("lis_member_id")
        if lis_id:
            ids.add(lis_id.strip())
    return sorted(ids)
