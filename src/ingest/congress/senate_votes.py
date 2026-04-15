"""URL builders and XML parser for Senate roll-call votes.

Source: https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{number}.xml

Senate XML identifies members by lis_member_id, not bioguide_id.
Downstream crosswalk resolution (unitedstates/congress-legislators) fills bioguide_id.
"""

from __future__ import annotations

import datetime
from xml.etree.ElementTree import fromstring

from .models import VoteCastRecord, VoteEventRecord

SENATE_VOTE_BASE = "https://www.senate.gov/legislative/LIS/roll_call_votes"


def roll_call_url(congress: int, session: int, vote_number: int) -> str:
    prefix = f"vote{congress}{session}"
    filename = f"vote_{congress}_{session}_{vote_number:05d}.xml"
    return f"{SENATE_VOTE_BASE}/{prefix}/{filename}"


def roll_call_list_url(congress: int, session: int) -> str:
    prefix = f"vote{congress}{session}"
    return f"{SENATE_VOTE_BASE}/{prefix}/vote_summary.xml"


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
    """VoteCastRecord objects carry lis_member_id; bioguide_id is None until crosswalk resolution."""
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
    root = fromstring(xml_text)
    ids: set[str] = set()
    for member in root.iter("member"):
        lis_id = member.findtext("lis_member_id")
        if lis_id:
            ids.add(lis_id.strip())
    return sorted(ids)
