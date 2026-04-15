"""Network-facing helpers for fetching House and Senate roll-call votes.

Delegates URL construction to house_votes and senate_votes modules.
Returns parsed VoteEventRecord / VoteCastRecord tuples from their parsers.
"""

from __future__ import annotations

import httpx

from .house_votes import (
    parse_house_vote_xml,
    roll_call_url as _house_vote_url,
)
from .models import VoteCastRecord, VoteEventRecord
from .senate_votes import (
    parse_senate_vote_xml,
    roll_call_url as _senate_vote_url,
)


def fetch_house_vote(
    year: int,
    roll_call_number: int,
    *,
    client: httpx.Client | None = None,
) -> tuple[VoteEventRecord, list[VoteCastRecord]]:
    url = _house_vote_url(year, roll_call_number)
    xml = _get(url, client)
    return parse_house_vote_xml(xml)


def fetch_senate_vote(
    congress: int,
    session: int,
    vote_number: int,
    *,
    client: httpx.Client | None = None,
) -> tuple[VoteEventRecord, list[VoteCastRecord]]:
    url = _senate_vote_url(congress, session, vote_number)
    xml = _get(url, client)
    return parse_senate_vote_xml(xml)


def fetch_house_votes(
    year: int,
    roll_call_numbers: list[int],
    *,
    client: httpx.Client | None = None,
) -> list[tuple[VoteEventRecord, list[VoteCastRecord]]]:
    own_client = client is None
    if own_client:
        client = httpx.Client()
    try:
        return [fetch_house_vote(year, n, client=client) for n in roll_call_numbers]
    finally:
        if own_client:
            client.close()


def fetch_senate_votes(
    congress: int,
    session: int,
    vote_numbers: list[int],
    *,
    client: httpx.Client | None = None,
) -> list[tuple[VoteEventRecord, list[VoteCastRecord]]]:
    own_client = client is None
    if own_client:
        client = httpx.Client()
    try:
        return [fetch_senate_vote(congress, session, n, client=client) for n in vote_numbers]
    finally:
        if own_client:
            client.close()


def _get(url: str, client: httpx.Client | None) -> str:
    if client is not None:
        return client.get(url).raise_for_status().text
    with httpx.Client() as c:
        return c.get(url).raise_for_status().text
