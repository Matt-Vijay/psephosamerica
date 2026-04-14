"""Typed ingest-boundary records for congressional data sources.

These models represent normalized payloads at the ingest boundary — after
fetching and light parsing, before any DB writes.  Field names align with
the canonical schema in db/schema.sql.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MemberRecord:
    """A single member as returned by the Congress.gov /member endpoint."""

    bioguide_id: str
    first_name: str
    last_name: str
    full_name: str
    chamber: Literal["house", "senate"]
    party: str | None = None
    state: str | None = None
    middle_name: str | None = None
    lis_member_id: str | None = None
    current_term_start: datetime.date | None = None
    current_term_end: datetime.date | None = None
    is_current: bool = True
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Committee
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CommitteeRecord:
    """A committee record from the Congress.gov /committee endpoint."""

    committee_code: str
    congress: int
    chamber: Literal["house", "senate", "joint"]
    committee_type: Literal["standing", "select", "joint", "subcommittee", "other"]
    name: str
    parent_committee_code: str | None = None
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Bill
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BillRecord:
    """A bill summary from the Congress.gov /bill endpoint."""

    congress: int
    bill_type: Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]
    bill_number: int
    title: str
    short_title: str | None = None
    introduced_date: datetime.date | None = None
    latest_action_date: datetime.date | None = None
    current_status: str | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class CosponsorRecord:
    """A cosponsor link from the Congress.gov /bill/{}/cosponsors endpoint."""

    congress: int
    bill_type: str
    bill_number: int
    bioguide_id: str
    is_original: bool = False
    sponsor_date: datetime.date | None = None
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Vote event + vote cast
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VoteEventRecord:
    """A single roll-call vote event (House or Senate)."""

    chamber: Literal["house", "senate"]
    congress: int
    session_number: int
    roll_call_number: int
    vote_date: datetime.date
    question: str
    result: str | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class VoteCastRecord:
    """A single member's vote within a roll-call event.

    For House votes the identifier is ``bioguide_id``.
    For Senate votes the raw identifier is ``lis_member_id``; downstream
    resolution maps it to ``bioguide_id``.
    """

    chamber: Literal["house", "senate"]
    congress: int
    session_number: int
    roll_call_number: int
    vote_option: Literal["yea", "nay", "present", "not_voting", "paired", "abstain"]
    bioguide_id: str | None = None
    lis_member_id: str | None = None

    def __post_init__(self) -> None:
        if self.bioguide_id is None and self.lis_member_id is None:
            raise ValueError("VoteCastRecord requires at least one of bioguide_id or lis_member_id")
