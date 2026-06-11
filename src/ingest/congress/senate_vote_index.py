"""URL builder, parser, and fetcher for the Senate vote summary index.

Source: https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml

The index lists every roll-call vote for a congress/session with enough
metadata to drive individual vote fetching.  No DB writes occur here.

The live menu XML carries ``congress_year`` at the document root and per-vote
dates as day-month only (``18-Dec``); results live in ``result``. The parser
also accepts full dates and the ``vote_result`` element name, so older locally
archived index files keep loading.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring

import httpx

from .official_fetch import fetch_official_congress_text
from .senate_votes import parse_senate_vote_date, roll_call_list_url, roll_call_url


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
    return roll_call_list_url(congress, session)


def _session_year(congress: int, session: int) -> int:
    """The calendar year of a congress's session (119/1 -> 2025, 118/2 -> 2024)."""
    return 1789 + (congress - 1) * 2 + (session - 1)


def _index_vote_date(raw: str, *, year: int) -> datetime.date:
    """A menu date: full forms via parse_senate_vote_date, else ``18-Dec`` + year."""
    try:
        return parse_senate_vote_date(raw)
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(f"{raw.strip()}-{year}", "%d-%b-%Y").date()
    except ValueError:
        raise ValueError(f"unparseable vote_date: {raw!r}") from None


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

    year_text = (root.findtext("congress_year") or "").strip()
    year = int(year_text) if year_text.isdigit() else _session_year(congress, session)

    rows: list[SenateVoteIndexRow] = []
    for vote_el in votes_el.iter("vote"):
        number_text = (vote_el.findtext("vote_number") or "").strip()
        if not number_text:
            continue
        vote_number = int(number_text)

        date_text = (vote_el.findtext("vote_date") or "").strip()
        vote_date = _index_vote_date(date_text, year=year)

        question = (vote_el.findtext("question") or "").strip()
        result_text = (vote_el.findtext("result") or vote_el.findtext("vote_result") or "").strip()
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
