"""Congress canonical load-plan layer.

Produces ordered table-batch operation plan dicts; no DB writes.

Each plan dict has:
  table:            str           canonical table name
  rows:             list[dict]    transform-output rows for the write layer
  conflict_columns: list[str]     ON CONFLICT identity columns
  mode:             str           "upsert"

FK columns (member_id, committee_id, bill_id, vote_event_id) are None in
the rows; the write layer resolves them via underscore-prefixed hint keys
(_bioguide_id, _committee_code, _bill_key, _vote_event_key).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Iterable

from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
    VoteCastRecord,
    VoteEventRecord,
)
from src.ingest.congress.transform import (
    bill_row,
    bill_sponsor_row,
    committee_membership_row,
    committee_row,
    cosponsor_row,
    member_row,
    member_term_row,
    vote_cast_row,
    vote_event_row,
)


@dataclass(frozen=True, slots=True)
class MemberTermSpec:
    """Pairs a MemberRecord with one term's metadata for member_term_row."""

    record: MemberRecord
    congress: int
    start_date: datetime.date
    end_date: datetime.date | None = None
    district: int | None = None
    is_current: bool = False


@dataclass(frozen=True, slots=True)
class CommitteeMembershipSpec:
    """Carries all inputs required by committee_membership_row."""

    bioguide_id: str
    committee_code: str
    congress: int
    role: str
    start_date: datetime.date
    end_date: datetime.date | None = None
    is_current: bool = False
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class PrimarySponsorSpec:
    """Links a BillRecord to the primary sponsor's bioguide_id."""

    record: BillRecord
    bioguide_id: str


def plan_members(records: Iterable[MemberRecord]) -> dict[str, Any]:
    return {
        "table": "member",
        "rows": [member_row(r) for r in records],
        "conflict_columns": ["bioguide_id"],
        "mode": "upsert",
    }


def plan_member_terms(specs: Iterable[MemberTermSpec]) -> dict[str, Any]:
    """Conflict identity: (member_id, congress, chamber, start_date). member_id resolved via _bioguide_id."""
    rows = [
        member_term_row(
            s.record,
            congress=s.congress,
            start_date=s.start_date,
            end_date=s.end_date,
            district=s.district,
            is_current=s.is_current,
        )
        for s in specs
    ]
    return {
        "table": "member_term",
        "rows": rows,
        "conflict_columns": ["member_id", "congress", "chamber", "start_date"],
        "mode": "upsert",
    }


def plan_committees(records: Iterable[CommitteeRecord]) -> dict[str, Any]:
    """Conflict identity: (congress, committee_code). Parent self-refs resolved by write layer."""
    return {
        "table": "committee",
        "rows": [committee_row(r) for r in records],
        "conflict_columns": ["congress", "committee_code"],
        "mode": "upsert",
    }


def plan_committee_memberships(specs: Iterable[CommitteeMembershipSpec]) -> dict[str, Any]:
    """Conflict identity: (committee_id, member_id, start_date). Both FKs resolved by write layer."""
    rows = [
        committee_membership_row(
            s.bioguide_id,
            s.committee_code,
            s.congress,
            role=s.role,
            start_date=s.start_date,
            end_date=s.end_date,
            is_current=s.is_current,
            source_url=s.source_url,
        )
        for s in specs
    ]
    return {
        "table": "committee_membership",
        "rows": rows,
        "conflict_columns": ["committee_id", "member_id", "start_date"],
        "mode": "upsert",
    }


def plan_bills(records: Iterable[BillRecord]) -> dict[str, Any]:
    return {
        "table": "bill",
        "rows": [bill_row(r) for r in records],
        "conflict_columns": ["congress", "bill_type", "bill_number"],
        "mode": "upsert",
    }


def plan_bill_sponsors(
    primary_specs: Iterable[PrimarySponsorSpec],
    cosponsors: Iterable[CosponsorRecord],
) -> dict[str, Any]:
    """Primary sponsors first so upserts don't downgrade an existing primary row.
    Conflict identity: (bill_id, member_id). Both FKs resolved via _bill_key and _bioguide_id.
    """
    rows = [bill_sponsor_row(s.record, s.bioguide_id) for s in primary_specs]
    rows += [cosponsor_row(c) for c in cosponsors]
    return {
        "table": "bill_sponsor",
        "rows": rows,
        "conflict_columns": ["bill_id", "member_id"],
        "mode": "upsert",
    }


def plan_vote_events(records: Iterable[VoteEventRecord]) -> dict[str, Any]:
    return {
        "table": "vote_event",
        "rows": [vote_event_row(r) for r in records],
        "conflict_columns": ["chamber", "congress", "session_number", "roll_call_number"],
        "mode": "upsert",
    }


def plan_vote_casts(records: Iterable[VoteCastRecord]) -> dict[str, Any]:
    """Conflict identity: (vote_event_id, member_id). Both FKs resolved via _vote_event_key / _bioguide_id / _lis_member_id."""
    return {
        "table": "vote_cast",
        "rows": [vote_cast_row(r) for r in records],
        "conflict_columns": ["vote_event_id", "member_id"],
        "mode": "upsert",
    }


def congress_load_plan(
    members: Iterable[MemberRecord],
    member_terms: Iterable[MemberTermSpec],
    committees: Iterable[CommitteeRecord],
    memberships: Iterable[CommitteeMembershipSpec],
    bills: Iterable[BillRecord],
    primary_sponsors: Iterable[PrimarySponsorSpec],
    cosponsors: Iterable[CosponsorRecord],
    vote_events: Iterable[VoteEventRecord],
    vote_casts: Iterable[VoteCastRecord],
) -> list[dict[str, Any]]:
    """Ordered operation plan for a full Congress load.

    Execute batches in list order to respect FK dependencies:
      1. member               — no core FK deps
      2. committee            — no core FK deps (self-ref resolved by write layer)
      3. member_term          — FK → member
      4. committee_membership — FK → member, committee
      5. bill                 — no core FK deps
      6. bill_sponsor         — FK → bill, member
      7. vote_event           — no core FK deps
      8. vote_cast            — FK → vote_event, member
    """
    return [
        plan_members(members),
        plan_committees(committees),
        plan_member_terms(member_terms),
        plan_committee_memberships(memberships),
        plan_bills(bills),
        plan_bill_sponsors(primary_sponsors, cosponsors),
        plan_vote_events(vote_events),
        plan_vote_casts(vote_casts),
    ]
