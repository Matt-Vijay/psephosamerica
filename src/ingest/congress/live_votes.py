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
    managed_client: httpx.Client | None = None
    active_client = client
    if active_client is None:
        managed_client = httpx.Client()
        active_client = managed_client
    try:
        return [fetch_house_vote(year, n, client=active_client) for n in roll_call_numbers]
    finally:
        if managed_client is not None:
            managed_client.close()


def fetch_senate_votes(
    congress: int,
    session: int,
    vote_numbers: list[int],
    *,
    client: httpx.Client | None = None,
) -> list[tuple[VoteEventRecord, list[VoteCastRecord]]]:
    managed_client: httpx.Client | None = None
    active_client = client
    if active_client is None:
        managed_client = httpx.Client()
        active_client = managed_client
    try:
        return [fetch_senate_vote(congress, session, n, client=active_client) for n in vote_numbers]
    finally:
        if managed_client is not None:
            managed_client.close()


def _get(url: str, client: httpx.Client | None) -> str:
    if client is not None:
        response = client.get(url)
        response.raise_for_status()
        return response.text
    with httpx.Client(timeout=30.0) as c:
        response = c.get(url)
        response.raise_for_status()
        return response.text
