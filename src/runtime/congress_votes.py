from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.ingest.congress.house_vote_index import fetch_house_vote_index
from src.ingest.congress.live_votes import fetch_house_votes, fetch_senate_votes
from src.ingest.congress.models import VoteCastRecord, VoteEventRecord
from src.ingest.congress.senate_vote_index import fetch_senate_vote_index


@dataclass(frozen=True)
class VoteFetchResult:
    vote_events: list[VoteEventRecord]
    vote_casts: list[VoteCastRecord]


def fetch_house_vote_records(
    year: int,
    *,
    roll_call_numbers: list[int] | None = None,
    client: httpx.Client | None = None,
) -> VoteFetchResult:
    """Return House vote records for *year*, filtered to *roll_call_numbers* when given.

    When roll_call_numbers is None, fetches the full year index first.
    """
    if roll_call_numbers is None:
        index_rows = fetch_house_vote_index(year, client=client)
        roll_call_numbers = [row.roll_call_number for row in index_rows]

    pairs = fetch_house_votes(year, roll_call_numbers, client=client)

    events = [event for event, _ in pairs]
    casts = [cast for _, cast_list in pairs for cast in cast_list]

    return VoteFetchResult(vote_events=events, vote_casts=casts)


def fetch_senate_vote_records(
    congress: int,
    session: int,
    *,
    vote_numbers: list[int] | None = None,
    client: httpx.Client | None = None,
) -> VoteFetchResult:
    """Return Senate vote records for *congress*/*session*, filtered to *vote_numbers* when given.

    When vote_numbers is None, fetches the full index for the congress/session first.
    """
    if vote_numbers is None:
        index_rows = fetch_senate_vote_index(congress, session, client=client)
        vote_numbers = [row.vote_number for row in index_rows]

    pairs = fetch_senate_votes(congress, session, vote_numbers, client=client)

    events = [event for event, _ in pairs]
    casts = [cast for _, cast_list in pairs for cast in cast_list]

    return VoteFetchResult(vote_events=events, vote_casts=casts)


def fetch_congress_vote_records(
    congress: int,
    *,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
    client: httpx.Client | None = None,
) -> VoteFetchResult:
    vote_events: list[VoteEventRecord] = []
    vote_casts: list[VoteCastRecord] = []

    if house_vote_year is not None:
        house = fetch_house_vote_records(house_vote_year, client=client)
        vote_events.extend(house.vote_events)
        vote_casts.extend(house.vote_casts)

    if senate_session is not None:
        senate = fetch_senate_vote_records(congress, senate_session, client=client)
        vote_events.extend(senate.vote_events)
        vote_casts.extend(senate.vote_casts)

    return VoteFetchResult(vote_events=vote_events, vote_casts=vote_casts)
