"""Ingest-boundary records for FEC bulk data.

Parsed rows from FEC bulk files; before any DB writes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CommitteeRecord:
    """One row from the FEC committee-master bulk file (cm.txt)."""

    fec_committee_id: str
    committee_name: str
    treasurer_name: str | None
    city: str | None
    state: str | None
    committee_type: str | None
    designation_code: str | None
    # FEC fields we carry but don't use in v1 scoring
    party: str | None = None
    filing_frequency: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateCommitteeLinkage:
    """One row from the FEC candidate-committee linkage file (ccl.txt)."""

    fec_candidate_id: str
    fec_committee_id: str
    linkage_type: str  # e.g. 'P' (principal), 'A' (authorized)
    election_year: int | None = None


@dataclass(frozen=True, slots=True)
class ContributionRecord:
    """One itemized individual-contribution row from indiv.txt."""

    fec_committee_id: str  # recipient committee
    donor_name: str
    city: str | None
    state: str | None
    zip_code: str | None
    employer: str | None
    occupation: str | None
    contribution_date: datetime.date | None
    amount: Decimal
    transaction_type: str | None
    memo_text: str | None = None
    sub_id: str | None = None  # FEC unique row key
    entity_type: str | None = None  # IND, COM, etc.
