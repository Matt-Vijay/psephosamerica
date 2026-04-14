"""FEC ingest-record → canonical row-payload transform.

All functions are pure: no I/O, no DB, no network.  They accept typed
ingest records from models.py, apply only the deterministic helpers in
normalize.py, and return dicts whose keys match the canonical schema
columns in db/schema.sql.

FK columns that require a DB identity (e.g. recipient_fec_committee_id
bigint) are carried as their raw FEC string counterparts
(recipient_fec_committee_id_raw) so that the write layer can resolve them
without coupling the transform layer to DB state.
"""

from __future__ import annotations

from typing import Any

from .models import CandidateCommitteeLinkage, CommitteeRecord, ContributionRecord
from .normalize import (
    normalize_committee_id,
    normalize_donor_name,
    normalize_employer,
    normalize_occupation,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# FEC transaction-type codes that map to each canonical contribution_type.
# Source: FEC bulk data format documentation.
_REFUND_CODES: frozenset[str] = frozenset(
    {"24A", "24N", "28L", "28G", "29"}
)
_TRANSFER_CODES: frozenset[str] = frozenset(
    {"24G", "24K", "24Z", "18G", "18J"}
)
_IN_KIND_CODES: frozenset[str] = frozenset(
    {"19Y", "21Y", "22Y"}
)
# Codes that are unambiguously direct contributions (positive receipts).
_CONTRIBUTION_CODES: frozenset[str] = frozenset(
    {"10", "11", "12", "13", "20", "22", "30", "31", "32", "40", "41", "42"}
)

# FEC entity-type codes → canonical donor_type values.
_ENTITY_TYPE_MAP: dict[str, str] = {
    "IND": "individual",
    "COM": "committee",
    "ORG": "organization",
    "CCM": "committee",  # candidate committee
    "PAC": "committee",  # sometimes used in derived files
}


def _map_contribution_type(transaction_type: str | None) -> str:
    """Map a raw FEC transaction-type code to a canonical contribution_type."""
    if not transaction_type:
        return "other"
    code = transaction_type.strip().upper()
    if code in _REFUND_CODES:
        return "refund"
    if code in _TRANSFER_CODES:
        return "transfer"
    if code in _IN_KIND_CODES:
        return "in_kind"
    if code in _CONTRIBUTION_CODES:
        return "contribution"
    return "other"


def _map_donor_type(entity_type: str | None) -> str:
    """Map a raw FEC entity-type code to a canonical donor_type."""
    if not entity_type:
        return "other"
    return _ENTITY_TYPE_MAP.get(entity_type.strip().upper(), "other")


def _clean_state(raw: str | None) -> str | None:
    """Return a 2-char upper-cased state code or None."""
    if not raw:
        return None
    s = raw.strip().upper()
    return s if len(s) == 2 else None


# ---------------------------------------------------------------------------
# Public transform functions
# ---------------------------------------------------------------------------


def committee_to_row(record: CommitteeRecord) -> dict[str, Any]:
    """Convert a CommitteeRecord into a fec_committee insert payload.

    Keys match fec_committee columns in db/schema.sql.  Provenance fields
    (source_artifact_id, source_record_id) are omitted here; the write
    layer injects them.
    """
    fec_id = normalize_committee_id(record.fec_committee_id)
    if not fec_id:
        raise ValueError(
            f"Invalid fec_committee_id: {record.fec_committee_id!r}"
        )
    return {
        "fec_committee_id": fec_id,
        "committee_name": record.committee_name.strip() or record.committee_name,
        "committee_type": record.committee_type,
        "designation_code": record.designation_code,
        "treasurer_name": record.treasurer_name.strip() if record.treasurer_name else None,
        "city": record.city.strip() if record.city else None,
        "state": _clean_state(record.state),
    }


def contribution_to_row(record: ContributionRecord) -> dict[str, Any]:
    """Convert a ContributionRecord into a contribution insert payload.

    Keys match contribution columns in db/schema.sql except that FK columns
    that require a resolved DB id are returned as their raw string variants:

    - ``recipient_fec_committee_id_raw``  (= normalized fec_committee_id)
      The write layer resolves this to the bigint FK.

    The schema requires contribution_date NOT NULL, so records with a None
    date raise ValueError.  The schema also requires amount > 0, which is
    not enforced here because the write layer enforces the DB constraint;
    negative amounts from refund records are allowed through so the caller
    can decide how to handle them.
    """
    if record.contribution_date is None:
        raise ValueError(
            f"contribution_date is required; got None for sub_id={record.sub_id!r}"
        )

    raw_cid = normalize_committee_id(record.fec_committee_id)
    if not raw_cid:
        raise ValueError(
            f"Invalid recipient fec_committee_id: {record.fec_committee_id!r}"
        )

    return {
        # FK resolved by write layer
        "recipient_fec_committee_id_raw": raw_cid,
        # Provenance
        "source_record_id": record.sub_id,
        # Donor identity
        "donor_name": normalize_donor_name(record.donor_name),
        "donor_type": _map_donor_type(record.entity_type),
        # Contribution details
        "contribution_type": _map_contribution_type(record.transaction_type),
        "contribution_date": record.contribution_date,
        "amount": record.amount,
        "memo": record.memo_text,
        # Supplemental donor fields (not in schema but useful at write time
        # for downstream employer/occupation clustering; stripped if unused)
        "_donor_city": record.city,
        "_donor_state": _clean_state(record.state),
        "_donor_zip": record.zip_code,
        "_donor_employer_raw": normalize_employer(record.employer),
        "_donor_occupation_raw": normalize_occupation(record.occupation),
    }


def linkage_to_row(record: CandidateCommitteeLinkage) -> dict[str, Any]:
    """Convert a CandidateCommitteeLinkage into a structured payload dict.

    This does NOT perform member resolution (bioguide_id lookup) — that is
    the responsibility of the entity-resolution layer.  The payload carries
    only the official FEC identifiers and linkage metadata needed to join
    candidate and committee context at write time.
    """
    fec_cid = normalize_committee_id(record.fec_committee_id)
    if not fec_cid:
        raise ValueError(
            f"Invalid fec_committee_id in linkage: {record.fec_committee_id!r}"
        )
    candidate_id = record.fec_candidate_id.strip().upper() if record.fec_candidate_id else ""
    if not candidate_id:
        raise ValueError(
            f"fec_candidate_id is required; got {record.fec_candidate_id!r}"
        )
    return {
        "fec_candidate_id": candidate_id,
        "fec_committee_id": fec_cid,
        "linkage_type": record.linkage_type.strip().upper() if record.linkage_type else "",
        "election_year": record.election_year,
    }
