"""Canonical row payloads from FEC ingest records.

Pure, no I/O. Accepts typed records from models.py, applies normalize.py
helpers, returns dicts whose keys match db/schema.sql columns.

FK columns that require a DB identity are returned as raw FEC string
variants (e.g. recipient_fec_committee_id_raw) so the write layer can
resolve them without coupling this layer to DB state.
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

# FEC transaction-type codes → canonical contribution_type.
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
_CONTRIBUTION_CODES: frozenset[str] = frozenset(
    {"10", "11", "12", "13", "20", "22", "30", "31", "32", "40", "41", "42"}
)

_ENTITY_TYPE_MAP: dict[str, str] = {
    "IND": "individual",
    "COM": "committee",
    "ORG": "organization",
    "CCM": "committee",
    "PAC": "committee",
}


def _map_contribution_type(transaction_type: str | None) -> str:
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
    if not entity_type:
        return "other"
    return _ENTITY_TYPE_MAP.get(entity_type.strip().upper(), "other")


def _clean_state(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper()
    return s if len(s) == 2 else None


def committee_to_row(record: CommitteeRecord) -> dict[str, Any]:
    """Raises ValueError for invalid fec_committee_id.
    Provenance fields (source_artifact_id, source_record_id) are injected by the write layer.
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
    """Raises ValueError if contribution_date is None or fec_committee_id is invalid.

    recipient_fec_committee_id_raw carries the normalized FEC string;
    the write layer resolves it to the bigint FK.

    Underscore-prefixed keys (_donor_city, _donor_state, _donor_zip,
    _donor_employer_raw, _donor_occupation_raw) are passed for downstream
    employer/occupation clustering; stripped by the write layer if unused.
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
        "recipient_fec_committee_id_raw": raw_cid,
        "source_record_id": record.sub_id,
        "donor_name": normalize_donor_name(record.donor_name),
        "donor_type": _map_donor_type(record.entity_type),
        "contribution_type": _map_contribution_type(record.transaction_type),
        "contribution_date": record.contribution_date,
        "amount": record.amount,
        "memo": record.memo_text,
        "_donor_city": record.city,
        "_donor_state": _clean_state(record.state),
        "_donor_zip": record.zip_code,
        "_donor_employer_raw": normalize_employer(record.employer),
        "_donor_occupation_raw": normalize_occupation(record.occupation),
    }


def linkage_to_row(record: CandidateCommitteeLinkage) -> dict[str, Any]:
    """Member resolution (bioguide_id lookup) is NOT performed here — that is the entity-resolution layer."""
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
