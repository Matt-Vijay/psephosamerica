"""URL builder, parser, and fetcher for the House roll-call vote index.

Source: https://clerk.house.gov/evs/{year}/index.xml

The index XML lists every roll-call vote for a given calendar year with
enough metadata to drive individual vote fetches via house_votes.roll_call_url.

Public interface
----------------
house_vote_index_url(year)          -> str
parse_house_vote_index(xml_text)    -> list[HouseVoteIndexRow]
fetch_house_vote_index(year, ...)   -> list[HouseVoteIndexRow]
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from xml.etree.ElementTree import fromstring

import httpx

from .house_votes import roll_call_url

HOUSE_VOTE_INDEX_BASE = "https://clerk.house.gov/evs"


@dataclass(frozen=True, slots=True)
class HouseVoteIndexRow:
    """One entry from the House vote summary index.

    Carries enough information to call roll_call_url(year, roll_call_number)
    and to identify the vote in the canonical data model.
    """

    congress: int
    session: int
    roll_call_number: int
    vote_date: datetime.date
    question: str
    result: str | None
    source_url: str   # points to the individual roll-call XML


def house_vote_index_url(year: int) -> str:
    """Return the canonical index URL for *year*."""
    return f"{HOUSE_VOTE_INDEX_BASE}/{year}/index.xml"


def parse_house_vote_index(xml_text: str) -> list[HouseVoteIndexRow]:
    """Parse the House vote index XML and return one row per roll-call entry.

    Expects a <vote-summary> root with top-level <congress> and <session>
    elements and any number of <vote-total> children, each containing:
      <vote-number>, <vote-question>, <vote-result>, <action-date>.

    Raises ValueError on missing required fields or unparseable dates.
    """
    root = fromstring(xml_text.strip())

    congress_text = root.findtext("congress")
    session_text = root.findtext("session")
    if congress_text is None:
        raise ValueError("Missing <congress> in vote-summary root")
    if session_text is None:
        raise ValueError("Missing <session> in vote-summary root")

    congress = int(congress_text.strip())
    session = int(session_text.strip())

    rows: list[HouseVoteIndexRow] = []
    for entry in root.iter("vote-total"):
        roll_call_number = _required_int(entry, "vote-number")
        question = _required_text(entry, "vote-question")
        result = _optional_text(entry, "vote-result")
        date_str = _required_text(entry, "action-date")
        vote_date = _parse_action_date(date_str)

        # Derive the calendar year from the parsed date; the index URL year
        # and the vote date year agree for all normal congressional sessions.
        url = roll_call_url(vote_date.year, roll_call_number)

        rows.append(HouseVoteIndexRow(
            congress=congress,
            session=session,
            roll_call_number=roll_call_number,
            vote_date=vote_date,
            question=question,
            result=result,
            source_url=url,
        ))

    return rows


def fetch_house_vote_index(
    year: int,
    *,
    client: httpx.Client | None = None,
) -> list[HouseVoteIndexRow]:
    """Fetch and parse the House vote index for *year*.

    Accepts an optional *client* for session reuse or custom headers.
    Falls back to a one-shot httpx.get when none is provided.

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = house_vote_index_url(year)
    if client is not None:
        response = client.get(url)
        response.raise_for_status()
    else:
        response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
    return parse_house_vote_index(response.text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _required_text(entry, tag: str) -> str:
    el = entry.find(tag)
    if el is None or el.text is None:
        raise ValueError(f"Missing required field <{tag}> in <{entry.tag}>")
    value = el.text.strip()
    if not value:
        raise ValueError(f"Empty required field <{tag}> in <{entry.tag}>")
    return value


def _required_int(entry, tag: str) -> int:
    return int(_required_text(entry, tag))


def _optional_text(entry, tag: str) -> str | None:
    el = entry.find(tag)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value if value else None


def _parse_action_date(value: str) -> datetime.date:
    # Clerk format: DD-Mon-YYYY (e.g. "01-Feb-2023") or ISO YYYY-MM-DD.
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(value, "%d-%b-%Y").date()
    except ValueError:
        raise ValueError(f"Unparseable action-date in House vote index: {value!r}")
