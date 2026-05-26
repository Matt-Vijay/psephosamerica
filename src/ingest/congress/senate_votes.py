"""URL builders and XML parser for Senate roll-call votes.

Source: https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{number}.xml

Senate XML identifies members by lis_member_id, not bioguide_id.
Downstream crosswalk resolution (unitedstates/congress-legislators) fills bioguide_id.
"""

from __future__ import annotations

import datetime
from typing import Literal

from defusedxml.ElementTree import fromstring

from .models import VoteCastRecord, VoteEventRecord

SENATE_VOTE_BASE = "https://www.senate.gov/legislative/LIS/roll_call_votes"
VoteOption = Literal["yea", "nay", "present", "not_voting", "paired", "abstain"]


def roll_call_url(congress: int, session: int, vote_number: int) -> str:
    prefix = f"vote{congress}{session}"
    filename = f"vote_{congress}_{session}_{vote_number:05d}.xml"
    return f"{SENATE_VOTE_BASE}/{prefix}/{filename}"


def roll_call_list_url(congress: int, session: int) -> str:
    prefix = f"vote{congress}{session}"
    return f"{SENATE_VOTE_BASE}/{prefix}/vote_summary.xml"


def _vote_option(raw: str) -> VoteOption:
    mapping: dict[str, VoteOption] = {
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


def parse_senate_vote_date(raw: str | None) -> datetime.date:
    value = (raw or "").strip()
    if not value:
        raise ValueError("vote_date is required")

    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        pass

    parts = value.split(",")
    if len(parts) >= 2:
        candidate = f"{parts[0].strip()}, {parts[1].strip()}"
        try:
            return datetime.datetime.strptime(candidate, "%B %d, %Y").date()
        except ValueError:
            pass

    raise ValueError(f"unparseable vote_date: {raw!r}")


def parse_senate_vote_xml(xml_text: str) -> tuple[VoteEventRecord, list[VoteCastRecord]]:
    """VoteCastRecord objects carry lis_member_id; bioguide_id is None until crosswalk resolution."""
    root = fromstring(xml_text)

    congress = int(root.findtext("congress", "0"))
    session = int(root.findtext("session", "0"))
    vote_number = int(root.findtext("vote_number", "0"))
    question = root.findtext("vote_question_text", "") or root.findtext("question", "")
    result = root.findtext("vote_result_text") or root.findtext("vote_result")

    vote_date = parse_senate_vote_date(root.findtext("vote_date"))

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
            casts.append(
                VoteCastRecord(
                    chamber="senate",
                    congress=congress,
                    session_number=session,
                    roll_call_number=vote_number,
                    vote_option=option,
                    bioguide_id=None,
                    lis_member_id=lis_id.strip(),
                )
            )

    return event, casts


def extract_lis_member_ids(xml_text: str) -> list[str]:
    root = fromstring(xml_text)
    ids: set[str] = set()
    for member in root.iter("member"):
        lis_id = member.findtext("lis_member_id")
        if lis_id:
            ids.add(lis_id.strip())
    return sorted(ids)
