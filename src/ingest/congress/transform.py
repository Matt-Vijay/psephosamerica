"""Canonical row payloads from Congress ingest records.

Pure, deterministic. Accepts typed records from models.py; returns dicts
shaped for INSERT into db/schema.sql tables. No DB writes.

FK columns that require a DB-assigned id (member_id, committee_id, etc.)
are left None. Each row carries underscore-prefixed hint keys so the write
layer can resolve FKs without re-reading the record:
  _bioguide_id        → member_id
  _committee_code     → committee_id (with _congress for uniqueness)
  _bill_key           → bill_id (congress, bill_type, bill_number)
  _vote_event_key     → vote_event_id (chamber, congress, session, roll_call)
  _lis_member_id      → member_id for Senate records before crosswalk resolution

These underscore-prefixed keys are transform hints, not DB columns.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    BillRecord,
    CommitteeRecord,
    CosponsorRecord,
    MemberRecord,
    VoteCastRecord,
    VoteEventRecord,
)

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _to_slug(bioguide_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", bioguide_id)
    lower = normalized.lower().strip()
    return _SLUG_NON_ALNUM.sub("-", lower).strip("-")


def member_row(record: MemberRecord) -> dict[str, Any]:
    """source_artifact_id is left None; caller supplies it after storing the raw artifact."""
    return {
        "bioguide_id": record.bioguide_id,
        "lis_member_id": record.lis_member_id,
        "fec_candidate_id": None,  # resolved via FEC crosswalk, not here
        "slug": _to_slug(record.bioguide_id),
        "first_name": record.first_name,
        "middle_name": record.middle_name,
        "last_name": record.last_name,
        "full_name": record.full_name,
        "party": record.party,
        "state": record.state,
        "chamber": record.chamber,
        "current_term_start": record.current_term_start,
        "current_term_end": record.current_term_end,
        "is_current": record.is_current,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def member_term_row(
    record: MemberRecord,
    *,
    congress: int,
    start_date: Any,
    end_date: Any = None,
    chamber: str | None = None,
    state: str | None = None,
    district: int | None = None,
    is_current: bool = False,
) -> dict[str, Any]:
    effective_chamber = chamber or record.chamber
    effective_state = state if state is not None else record.state
    effective_district = None if effective_chamber == "senate" else district
    return {
        "_bioguide_id": record.bioguide_id,
        "member_id": None,
        "congress": congress,
        "chamber": effective_chamber,
        "state": effective_state,
        "district": effective_district,
        "start_date": start_date,
        "end_date": end_date,
        "is_current": is_current,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def committee_row(record: CommitteeRecord) -> dict[str, Any]:
    """parent_committee_id is always None; write layer resolves via _parent_committee_code.
    jurisdiction_basis and review_tier are populated by the mapping layer after sector assignment.
    """
    return {
        "_parent_committee_code": record.parent_committee_code,
        "committee_code": record.committee_code,
        "congress": record.congress,
        "chamber": record.chamber,
        "committee_type": record.committee_type,
        "name": record.name,
        "parent_committee_id": None,
        "jurisdiction_basis": None,
        "review_tier": "review_required",  # conservative default; updated by mapping layer
        "is_active": True,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def committee_membership_row(
    bioguide_id: str,
    committee_code: str,
    congress: int,
    *,
    role: str,
    start_date: Any,
    end_date: Any = None,
    is_current: bool = False,
    source_url: str | None = None,
) -> dict[str, Any]:
    return {
        "_bioguide_id": bioguide_id,
        "_committee_code": committee_code,
        "_congress": congress,
        "committee_id": None,
        "member_id": None,
        "role": role,
        "start_date": start_date,
        "end_date": end_date,
        "is_current": is_current,
        "source_artifact_id": None,
        "source_record_id": source_url,
    }


def bill_row(record: BillRecord) -> dict[str, Any]:
    return {
        "congress": record.congress,
        "bill_type": record.bill_type,
        "bill_number": record.bill_number,
        "title": record.title,
        "short_title": record.short_title,
        "introduced_date": record.introduced_date,
        "latest_action_date": record.latest_action_date,
        "current_status": record.current_status,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def bill_sponsor_row(
    record: BillRecord,
    bioguide_id: str,
    *,
    sponsor_date: Any = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    return {
        "_bill_key": (record.congress, record.bill_type, record.bill_number),
        "_bioguide_id": bioguide_id,
        "bill_id": None,
        "member_id": None,
        "sponsor_role": "primary",
        "is_primary": True,
        "sponsor_date": sponsor_date if sponsor_date is not None else record.introduced_date,
        "source_artifact_id": None,
        "source_record_id": source_url if source_url is not None else record.source_url,
    }


def cosponsor_row(record: CosponsorRecord) -> dict[str, Any]:
    role = "original_cosponsor" if record.is_original else "cosponsor"
    return {
        "_bill_key": (record.congress, record.bill_type, record.bill_number),
        "_bioguide_id": record.bioguide_id,
        "bill_id": None,
        "member_id": None,
        "sponsor_role": role,
        "is_primary": False,
        "sponsor_date": record.sponsor_date,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def vote_event_row(record: VoteEventRecord) -> dict[str, Any]:
    """Natural uniqueness key: (chamber, congress, session_number, roll_call_number)."""
    return {
        "chamber": record.chamber,
        "congress": record.congress,
        "session_number": record.session_number,
        "roll_call_number": record.roll_call_number,
        "vote_date": record.vote_date,
        "question": record.question,
        "result": record.result,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def vote_cast_row(record: VoteCastRecord) -> dict[str, Any]:
    """_bioguide_id is set for House records; _lis_member_id for Senate records before crosswalk.
    Caller fills _bioguide_id and member_id after crosswalk resolution.
    """
    return {
        "_vote_event_key": (
            record.chamber,
            record.congress,
            record.session_number,
            record.roll_call_number,
        ),
        "_bioguide_id": record.bioguide_id,
        "_lis_member_id": record.lis_member_id,
        "vote_event_id": None,
        "member_id": None,
        "vote_option": record.vote_option,
        "source_artifact_id": None,
        "source_record_id": None,
    }
