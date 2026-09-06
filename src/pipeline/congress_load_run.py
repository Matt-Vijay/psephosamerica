"""Live Congress load runner: plans → FK resolution → DB writes.

Accepts typed ingest inputs and an open DB connection.
Executes in four FK-safe phases with lookup refreshes at phase boundaries.
Returns a consolidated LoadSummary.

Phase order:
  1  member, committee          — no FK deps
  2  member_term,               — FK → member, committee
     committee_membership
  3  bill, vote_event           — no FK deps
  4  bill_sponsor, vote_cast    — FK → bill/vote_event/member
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db.load_executor import Resolvers, execute_load_plan
from src.db.load_report import LoadSummary, WarnErrorSummary, build_load_summary
from src.db.lookups import LookupBundle
from src.db.repositories import (
    ConnectionLike,
    Row,
    commit_or_rollback,
    ensure_transactional_for_commit,
    fetch_all,
    rollback_if_available,
)
from src.db.runtime_lookups import load_lookup_bundle
from src.ingest.congress.models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
    VoteCastRecord,
    VoteEventRecord,
)
from src.load.congress import (
    CommitteeMembershipSpec,
    MemberTermSpec,
    PrimarySponsorSpec,
    plan_bill_sponsors,
    plan_bills,
    plan_committee_memberships,
    plan_committees,
    plan_member_terms,
    plan_members,
    plan_vote_casts,
    plan_vote_events,
)

# Typed input container


@dataclass(frozen=True)
class CongressIngestInputs:
    """All records required for a full Congress canonical load."""

    members: list[MemberRecord]
    member_terms: list[MemberTermSpec]
    committees: list[CommitteeRecord]
    memberships: list[CommitteeMembershipSpec]
    bills: list[BillRecord]
    primary_sponsors: list[PrimarySponsorSpec]
    cosponsors: list[CosponsorRecord]
    vote_events: list[VoteEventRecord]
    vote_casts: list[VoteCastRecord]


# FK-map queries (bill and vote_event are not in LookupBundle)

_BILL_SQL = "SELECT id, congress, bill_type, bill_number FROM bill"
_VOTE_EVENT_SQL = "SELECT id, chamber, congress, session_number, roll_call_number FROM vote_event"


def _fetch_bill_map(conn: Any) -> dict[tuple[int, str, int], int]:
    """(congress, bill_type, bill_number) → bill.id"""
    rows = fetch_all(conn, _BILL_SQL)
    return {(r["congress"], r["bill_type"], r["bill_number"]): r["id"] for r in rows}


def _fetch_vote_event_map(conn: Any) -> dict[tuple[str, int, int, int], int]:
    """(chamber, congress, session_number, roll_call_number) → vote_event.id"""
    rows = fetch_all(conn, _VOTE_EVENT_SQL)
    return {
        (r["chamber"], r["congress"], r["session_number"], r["roll_call_number"]): r["id"]
        for r in rows
    }


# Resolver builder


def _build_resolvers(
    bundle: LookupBundle,
    bill_map: dict[tuple[int, str, int], int] | None = None,
    vote_event_map: dict[tuple[str, int, int, int], int] | None = None,
) -> Resolvers:
    """Construct executor Resolvers from a LookupBundle and optional FK maps.

    The _bioguide_id resolver falls back to _lis_member_id so Senate vote_cast
    rows — where bioguide_id may be absent before crosswalk — still resolve
    member_id.
    """
    bio_map = bundle.bioguide_map
    lis_map = bundle.lis_member_map
    comm_map = bundle.committee_code_map

    def _resolve_member_id(row: Row) -> int | None:
        bioguide_id = row.get("_bioguide_id")
        if isinstance(bioguide_id, str):
            resolved = bio_map.get(bioguide_id)
            if resolved is not None:
                return resolved
        lis_member_id = row.get("_lis_member_id")
        if isinstance(lis_member_id, str):
            return lis_map.get(lis_member_id)
        return None

    def _resolve_committee_id(row: Row) -> int | None:
        committee_code = row.get("_committee_code")
        congress = row.get("congress")
        if congress is None:
            congress = row.get("_congress")
        if isinstance(committee_code, str) and type(congress) is int:
            return comm_map.get((committee_code, congress))
        return None

    resolvers: Resolvers = {
        "_bioguide_id": (
            "member_id",
            _resolve_member_id,
        ),
        "_committee_code": (
            "committee_id",
            _resolve_committee_id,
        ),
    }
    if bill_map is not None:

        def _resolve_bill_id(row: Row) -> int | None:
            bill_key = row.get("_bill_key")
            if isinstance(bill_key, tuple) and len(bill_key) == 3:
                congress, bill_type, bill_number = bill_key
                if (
                    type(congress) is int
                    and isinstance(bill_type, str)
                    and type(bill_number) is int
                ):
                    return bill_map.get((congress, bill_type, bill_number))
            return None

        resolvers["_bill_key"] = (
            "bill_id",
            _resolve_bill_id,
        )
    if vote_event_map is not None:

        def _resolve_vote_event_id(row: Row) -> int | None:
            vote_event_key = row.get("_vote_event_key")
            if isinstance(vote_event_key, tuple) and len(vote_event_key) == 4:
                chamber, congress, session, roll_call = vote_event_key
                if (
                    isinstance(chamber, str)
                    and type(congress) is int
                    and type(session) is int
                    and type(roll_call) is int
                ):
                    return vote_event_map.get((chamber, congress, session, roll_call))
            return None

        resolvers["_vote_event_key"] = (
            "vote_event_id",
            _resolve_vote_event_id,
        )
    return resolvers


# Summary helpers


def _merge_warn_errors(summaries: list[LoadSummary]) -> WarnErrorSummary:
    merged = WarnErrorSummary()
    for s in summaries:
        for w in s.warn_error.warnings:
            merged.add_warning(w)
        for e in s.warn_error.errors:
            merged.add_error(e)
    return merged


# Public API


def run_congress_load(
    inputs: CongressIngestInputs,
    conn: ConnectionLike,
    *,
    run_id: int | None = None,
    commit: bool = True,
) -> LoadSummary:
    """Execute a full Congress ingest in FK-safe phases with lookup refreshes.

    Phase 1  member, committee          — no FK deps
    Phase 2  member_term,               — FK → member, committee
             committee_membership
    Phase 3  bill, vote_event           — no FK deps
    Phase 4  bill_sponsor, vote_cast    — FK → bill/vote_event/member
    """
    phase_summaries: list[LoadSummary] = []
    ensure_transactional_for_commit(conn, commit=commit)

    try:
        # Phase 1: root entities — member, committee
        # committee self-reference is resolved by the write layer.
        phase1_ops = [
            plan_members(inputs.members),
            plan_committees(inputs.committees),
        ]
        phase_summaries.append(execute_load_plan(conn, phase1_ops, run_id=run_id, commit=False))

        # Lookup refresh: member and committee PKs are now stable.
        bundle = load_lookup_bundle(conn)
        resolvers_phase2 = _build_resolvers(bundle)

        # Phase 2: member-scoped rows — member_term, committee_membership
        phase2_ops = [
            plan_member_terms(inputs.member_terms),
            plan_committee_memberships(inputs.memberships),
        ]
        phase_summaries.append(
            execute_load_plan(
                conn,
                phase2_ops,
                resolvers=resolvers_phase2,
                run_id=run_id,
                commit=False,
            )
        )

        # Phase 3: independent legislative entities — bill, vote_event
        phase3_ops = [
            plan_bills(inputs.bills),
            plan_vote_events(inputs.vote_events),
        ]
        phase_summaries.append(execute_load_plan(conn, phase3_ops, run_id=run_id, commit=False))

        # Lookup refresh: bill and vote_event PKs are now stable.
        bundle = load_lookup_bundle(conn)
        bill_map = _fetch_bill_map(conn)
        vote_event_map = _fetch_vote_event_map(conn)
        resolvers_phase4 = _build_resolvers(
            bundle, bill_map=bill_map, vote_event_map=vote_event_map
        )

        # Phase 4: cross-reference rows — bill_sponsor, vote_cast
        phase4_ops = [
            plan_bill_sponsors(inputs.primary_sponsors, inputs.cosponsors),
            plan_vote_casts(inputs.vote_casts),
        ]
        phase_summaries.append(
            execute_load_plan(
                conn,
                phase4_ops,
                resolvers=resolvers_phase4,
                run_id=run_id,
                commit=False,
            )
        )
    except Exception:
        if commit:
            rollback_if_available(conn)
        raise

    if commit:
        commit_or_rollback(conn)

    all_table_results = [tr for s in phase_summaries for tr in s.table_results]
    merged_warn_error = _merge_warn_errors(phase_summaries)
    return build_load_summary(all_table_results, warn_error=merged_warn_error, run_id=run_id)
