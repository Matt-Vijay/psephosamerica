"""URL builder, parser, and fetcher for the Senate vote summary index.

Source: https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_summary.xml

The index lists every roll-call vote for a congress/session with enough
metadata to drive individual vote fetching.  No DB writes occur here.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring

import httpx

from .official_fetch import fetch_official_congress_text
from .senate_votes import SENATE_VOTE_BASE, parse_senate_vote_date, roll_call_url


@dataclass(frozen=True, slots=True)
class SenateVoteIndexRow:
    congress: int
    session: int
    vote_number: int
    vote_date: datetime.date
    question: str
    result: str | None
    source_url: str  # individual roll-call XML URL


def senate_vote_index_url(congress: int, session: int) -> str:
    prefix = f"vote{congress}{session}"
    return f"{SENATE_VOTE_BASE}/{prefix}/vote_summary.xml"


def parse_senate_vote_index(
    xml_text: str,
    *,
    congress: int,
    session: int,
) -> list[SenateVoteIndexRow]:
    """Return one SenateVoteIndexRow per vote element in the summary XML."""
    root = fromstring(xml_text)
    votes_el = root.find("votes")
    if votes_el is None:
        return []

    rows: list[SenateVoteIndexRow] = []
    for vote_el in votes_el.iter("vote"):
        number_text = (vote_el.findtext("vote_number") or "").strip()
        if not number_text:
            continue
        vote_number = int(number_text)

        date_text = (vote_el.findtext("vote_date") or "").strip()
        vote_date = parse_senate_vote_date(date_text)

        question = (vote_el.findtext("question") or "").strip()
        result_text = (vote_el.findtext("vote_result") or "").strip()
        result = result_text or None

        rows.append(
            SenateVoteIndexRow(
                congress=congress,
                session=session,
                vote_number=vote_number,
                vote_date=vote_date,
                question=question,
                result=result,
                source_url=roll_call_url(congress, session, vote_number),
            )
        )

    return rows


def fetch_senate_vote_index(
    congress: int,
    session: int,
    *,
    client: httpx.Client | None = None,
) -> list[SenateVoteIndexRow]:
    url = senate_vote_index_url(congress, session)
    xml = _get(url, client)
    return parse_senate_vote_index(xml, congress=congress, session=session)


def _get(url: str, client: httpx.Client | None) -> str:
    return fetch_official_congress_text(url, client=client)
