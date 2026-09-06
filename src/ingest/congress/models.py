"""Ingest-boundary records for congressional source data.

After fetching and light parsing; before any DB writes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Literal


def _validate_identity_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


@dataclass(frozen=True, slots=True)
class MemberRecord:
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


@dataclass(frozen=True, slots=True)
class CommitteeRecord:
    committee_code: str
    congress: int
    chamber: Literal["house", "senate", "joint"]
    committee_type: Literal["standing", "select", "joint", "subcommittee", "other"]
    name: str
    parent_committee_code: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_int(self.congress, "congress")


@dataclass(frozen=True, slots=True)
class BillRecord:
    congress: int
    bill_type: Literal["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]
    bill_number: int
    title: str
    short_title: str | None = None
    introduced_date: datetime.date | None = None
    latest_action_date: datetime.date | None = None
    current_status: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_int(self.congress, "congress")
        _validate_identity_int(self.bill_number, "bill_number")


@dataclass(frozen=True, slots=True)
class CosponsorRecord:
    congress: int
    bill_type: str
    bill_number: int
    bioguide_id: str
    is_original: bool = False
    sponsor_date: datetime.date | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_int(self.congress, "congress")
        _validate_identity_int(self.bill_number, "bill_number")


@dataclass(frozen=True, slots=True)
class VoteEventRecord:
    chamber: Literal["house", "senate"]
    congress: int
    session_number: int
    roll_call_number: int
    vote_date: datetime.date
    question: str
    result: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_int(self.congress, "congress")
        _validate_identity_int(self.session_number, "session_number")
        _validate_identity_int(self.roll_call_number, "roll_call_number")


@dataclass(frozen=True, slots=True)
class VoteCastRecord:
    """House votes use bioguide_id; Senate votes arrive with lis_member_id only.
    Downstream crosswalk resolution fills bioguide_id for Senate records.
    """

    chamber: Literal["house", "senate"]
    congress: int
    session_number: int
    roll_call_number: int
    vote_option: Literal["yea", "nay", "present", "not_voting", "paired", "abstain"]
    bioguide_id: str | None = None
    lis_member_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identity_int(self.congress, "congress")
        _validate_identity_int(self.session_number, "session_number")
        _validate_identity_int(self.roll_call_number, "roll_call_number")
        if self.bioguide_id is None and self.lis_member_id is None:
            raise ValueError("VoteCastRecord requires at least one of bioguide_id or lis_member_id")
