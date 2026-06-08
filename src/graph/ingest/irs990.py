"""Adapt IRS 990 / nonprofit records into org source records.

Nonprofits, foundations, and 501(c)(4) advocacy orgs are central political
actors (endorsements, model legislation, dark-money flows). The IRS Employer
Identification Number (EIN) is their strong, stable identifier; ProPublica's
Nonprofit Explorer republishes 990 filings as public data. :func:`parse_\
propublica_org` maps one organization into an ``org`` source record keyed by
EIN, so the same nonprofit observed across 990s, FEC filings, and endorsement
records resolves to one canonical Org ID.
"""

from __future__ import annotations

import re
from typing import Any

from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

_EIN_DIGITS_RE = re.compile(r"^\d{9}$")


def normalize_ein(raw: str | int) -> str:
    """Normalize an EIN to the canonical ``NN-NNNNNNN`` form."""
    digits = re.sub(r"\D", "", str(raw).strip())
    if not _EIN_DIGITS_RE.match(digits):
        raise ValueError(f"not a valid 9-digit EIN: {raw!r}")
    return f"{digits[:2]}-{digits[2:]}"


def parse_propublica_org(
    record: dict[str, Any],
    *,
    provenance: ProvenanceEnvelope,
) -> SourceRecord:
    """Map one ProPublica Nonprofit Explorer organization into an org record."""
    ein_value = record.get("strein") or record.get("ein")
    if ein_value is None:
        raise ValueError("nonprofit record is missing an EIN")
    ein = normalize_ein(ein_value)
    name = (record.get("name") or "").strip()
    if not name:
        raise ValueError("nonprofit record is missing a name")
    state = (record.get("state") or "").strip() or None
    return SourceRecord(
        source_system="irs_990",
        source_record_id=ein,
        entity_type="org",
        display_name=name,
        external_ids=(ExternalId(system="ein", value=ein),),
        jurisdiction="us",
        region=state,
        provenance=provenance,
    )
