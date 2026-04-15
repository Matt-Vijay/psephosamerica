"""Ingest-boundary records for FEC bulk data.

Parsed rows from FEC bulk files; before any DB writes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True, slots=True)
class CommitteeRecord:
    """One row from the FEC committee-master bulk file (cm.txt)."""

    fec_committee_id: str
    committee_name: str
    treasurer_name: Optional[str]
    city: Optional[str]
    state: Optional[str]
    committee_type: Optional[str]
    designation_code: Optional[str]
    # FEC fields we carry but don't use in v1 scoring
    party: Optional[str] = None
    filing_frequency: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CandidateCommitteeLinkage:
    """One row from the FEC candidate-committee linkage file (ccl.txt)."""

    fec_candidate_id: str
    fec_committee_id: str
    linkage_type: str  # e.g. 'P' (principal), 'A' (authorized)
    election_year: Optional[int] = None


@dataclass(frozen=True, slots=True)
class ContributionRecord:
    """One itemized individual-contribution row from indiv.txt."""

    fec_committee_id: str  # recipient committee
    donor_name: str
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    employer: Optional[str]
    occupation: Optional[str]
    contribution_date: Optional[datetime.date]
    amount: Decimal
    transaction_type: Optional[str]
    memo_text: Optional[str] = None
    sub_id: Optional[str] = None  # FEC unique row key
    entity_type: Optional[str] = None  # IND, COM, etc.
