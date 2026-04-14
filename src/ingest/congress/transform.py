"""Convert Congress ingest-boundary records into canonical row payloads.

All functions here are pure and deterministic.  They accept typed records
from ``models.py`` and return plain dicts shaped for INSERT into the
corresponding canonical table (db/schema.sql).

FK columns that require a DB-assigned ``id`` (e.g. ``member_id``,
``committee_id``, ``vote_event_id``) are left as ``None`` in the returned
dict.  Callers that resolve FKs before writing should fill those keys in.

The one exception is fields where the lookup key is carried alongside:
  - ``_bioguide_id`` is included in ``member_term``, ``committee_membership``,
    ``bill_sponsor``, and ``vote_cast`` rows so callers can resolve
    ``member_id`` without re-reading the record.
  - ``_committee_code`` is included in ``committee_membership`` and
    ``committee`` rows to allow parent/membership resolution.
  - ``_vote_event_key`` (tuple) is included in ``vote_cast`` rows.

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

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _to_slug(bioguide_id: str) -> str:
    """Deterministic URL slug: lower-cased bioguide_id with no whitespace."""
    normalized = unicodedata.normalize("NFKD", bioguide_id)
    lower = normalized.lower().strip()
    return _SLUG_NON_ALNUM.sub("-", lower).strip("-")


# ---------------------------------------------------------------------------
# member
# ---------------------------------------------------------------------------

def member_row(record: MemberRecord) -> dict[str, Any]:
    """Map a ``MemberRecord`` to a ``member`` table payload.

    ``source_artifact_id`` is left ``None``; the caller supplies it after
    storing the raw artifact.
    """
    return {
        "bioguide_id": record.bioguide_id,
        "lis_member_id": record.lis_member_id,
        "fec_candidate_id": None,          # resolved via FEC crosswalk, not here
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


# ---------------------------------------------------------------------------
# member_term
# ---------------------------------------------------------------------------

def member_term_row(
    record: MemberRecord,
    *,
    congress: int,
    start_date: Any,               # datetime.date
    end_date: Any = None,          # datetime.date | None
    district: int | None = None,
    is_current: bool = False,
) -> dict[str, Any]:
    """Map a ``MemberRecord`` + term metadata to a ``member_term`` payload.

    ``district`` must be ``None`` for senators; it is required (>0) only for
    House members.  Callers are responsible for passing the correct value.

    ``member_id`` is left ``None``; callers resolve it via ``bioguide_id``.
    The ``_bioguide_id`` hint key is included for that resolution.
    """
    # Senators must not carry a district value.
    effective_district = None if record.chamber == "senate" else district
    return {
        "_bioguide_id": record.bioguide_id,   # FK resolution hint
        "member_id": None,
        "congress": congress,
        "chamber": record.chamber,
        "state": record.state,
        "district": effective_district,
        "start_date": start_date,
        "end_date": end_date,
        "is_current": is_current,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


# ---------------------------------------------------------------------------
# committee
# ---------------------------------------------------------------------------

def committee_row(record: CommitteeRecord) -> dict[str, Any]:
    """Map a ``CommitteeRecord`` to a ``committee`` table payload.

    ``parent_committee_id`` is always ``None`` here; callers resolve it from
    ``_parent_committee_code``.  ``jurisdiction_basis`` and ``review_tier``
    are mapping-layer concerns populated after sector assignment; defaults are
    provided so the row is insertable.
    """
    return {
        "_parent_committee_code": record.parent_committee_code,   # FK hint
        "committee_code": record.committee_code,
        "congress": record.congress,
        "chamber": record.chamber,
        "committee_type": record.committee_type,
        "name": record.name,
        "parent_committee_id": None,
        "jurisdiction_basis": None,
        "review_tier": "review_required",   # conservative default; updated by mapping layer
        "is_active": True,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


# ---------------------------------------------------------------------------
# committee_membership
# ---------------------------------------------------------------------------

def committee_membership_row(
    bioguide_id: str,
    committee_code: str,
    congress: int,
    *,
    role: str,
    start_date: Any,               # datetime.date
    end_date: Any = None,
    is_current: bool = False,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Build a ``committee_membership`` payload.

    Both ``member_id`` and ``committee_id`` require DB-level resolution.
    Hint keys ``_bioguide_id``, ``_committee_code``, and ``_congress`` are
    included to support that lookup.
    """
    return {
        "_bioguide_id": bioguide_id,         # FK resolution hint → member_id
        "_committee_code": committee_code,   # FK resolution hint → committee_id
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


# ---------------------------------------------------------------------------
# bill
# ---------------------------------------------------------------------------

def bill_row(record: BillRecord) -> dict[str, Any]:
    """Map a ``BillRecord`` to a ``bill`` table payload."""
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


# ---------------------------------------------------------------------------
# bill_sponsor
# ---------------------------------------------------------------------------

def bill_sponsor_row(record: BillRecord, bioguide_id: str) -> dict[str, Any]:
    """Build a primary-sponsor ``bill_sponsor`` payload.

    ``bill_id`` and ``member_id`` require DB resolution.
    Hint keys ``_bill_key`` (congress, type, number) and ``_bioguide_id``
    are included for that lookup.
    """
    return {
        "_bill_key": (record.congress, record.bill_type, record.bill_number),
        "_bioguide_id": bioguide_id,
        "bill_id": None,
        "member_id": None,
        "sponsor_role": "primary",
        "is_primary": True,
        "sponsor_date": record.introduced_date,
        "source_artifact_id": None,
        "source_record_id": record.source_url,
    }


def cosponsor_row(record: CosponsorRecord) -> dict[str, Any]:
    """Map a ``CosponsorRecord`` to a ``bill_sponsor`` payload.

    ``sponsor_role`` is ``'original_cosponsor'`` when ``is_original`` is
    True, otherwise ``'cosponsor'``.
    """
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


# ---------------------------------------------------------------------------
# vote_event
# ---------------------------------------------------------------------------

def vote_event_row(record: VoteEventRecord) -> dict[str, Any]:
    """Map a ``VoteEventRecord`` to a ``vote_event`` table payload.

    The natural uniqueness key is (chamber, congress, session_number,
    roll_call_number), which matches the UNIQUE constraint in schema.sql.
    """
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


# ---------------------------------------------------------------------------
# vote_cast
# ---------------------------------------------------------------------------

def vote_cast_row(record: VoteCastRecord) -> dict[str, Any]:
    """Map a ``VoteCastRecord`` to a ``vote_cast`` table payload.

    The uniqueness key is (vote_event_id, member_id).  Because neither FK
    is known at transform time, both are ``None``.

    Hint keys:
    - ``_vote_event_key``: (chamber, congress, session_number, roll_call_number)
      to look up ``vote_event_id``.
    - ``_bioguide_id``: set when known (House votes); ``None`` for raw Senate
      records where only ``_lis_member_id`` is available.
    - ``_lis_member_id``: set for Senate votes before crosswalk resolution.

    After crosswalk resolution the caller fills ``_bioguide_id`` and
    ultimately ``member_id``.
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
