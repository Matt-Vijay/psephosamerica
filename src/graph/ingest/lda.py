"""Adapt Senate LDA (federal lobbying) disclosures into orgs + retention edges.

The Senate LDA REST API (``lda.senate.gov/api/v1``) publishes federal lobbying
registrations and quarterly filings without a credential. A filing links a
*client* (the entity that hired lobbyists) to a *registrant* (the lobbying firm)
for a year, with income/expenses. This module parses both as ``org`` source
records (EIN-style strong keys ``senate_lda_client`` / ``senate_lda_registrant``)
and builds a ``lobbying_retention`` edge client -> registrant.

Disclosure-lag: lobbying activity is public when the filing is *posted*
(``dt_posted``), so :func:`lda_provenance` stamps ``known_at`` there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.graph.edges import GraphEdge
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope


def _org_record(
    record: dict[str, Any], *, system: str, provenance: ProvenanceEnvelope
) -> SourceRecord:
    org_id = record.get("id")
    if org_id is None:
        raise ValueError(f"{system} record is missing an id")
    name = (record.get("name") or "").strip()
    if not name:
        raise ValueError(f"{system} record is missing a name")
    state = (record.get("state") or "").strip() or None
    return SourceRecord(
        source_system="senate_lda",
        source_record_id=f"{system}:{org_id}",
        entity_type="org",
        display_name=name,
        external_ids=(ExternalId(system=system, value=str(org_id)),),
        jurisdiction="us",
        region=state,
        provenance=provenance,
    )


def parse_lda_registrant(record: dict[str, Any], *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse a lobbying registrant (firm) into an org source record."""
    return _org_record(record, system="senate_lda_registrant", provenance=provenance)


def parse_lda_client(record: dict[str, Any], *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse a lobbying client into an org source record."""
    return _org_record(record, system="senate_lda_client", provenance=provenance)


@dataclass(frozen=True)
class LdaFiling:
    """One parsed LDA filing linking a client to a registrant."""

    filing_uuid: str
    year: int
    filing_type: str
    client_id: int
    registrant_id: int
    income: str | None
    expenses: str | None
    dt_posted: datetime


def parse_lda_filing(record: dict[str, Any]) -> LdaFiling:
    """Parse one LDA filing record."""
    uuid = (record.get("filing_uuid") or "").strip()
    if not uuid:
        raise ValueError("LDA filing is missing a filing_uuid")
    raw_posted = (record.get("dt_posted") or "").strip()
    try:
        posted = datetime.fromisoformat(raw_posted)
    except ValueError as exc:
        raise ValueError(f"unparseable LDA dt_posted: {raw_posted!r}") from exc
    client = record.get("client") or {}
    registrant = record.get("registrant") or {}
    return LdaFiling(
        filing_uuid=uuid,
        year=int(record.get("filing_year") or 0),
        filing_type=(record.get("filing_type_display") or "").strip(),
        client_id=int(client.get("id") or 0),
        registrant_id=int(registrant.get("id") or 0),
        income=(str(record["income"]) if record.get("income") is not None else None),
        expenses=(str(record["expenses"]) if record.get("expenses") is not None else None),
        dt_posted=posted,
    )


def lda_provenance(
    filing: LdaFiling,
    *,
    source_url: str,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for an LDA filing; ``known_at`` defaults to the post time."""
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=date(filing.dt_posted.year, filing.dt_posted.month, filing.dt_posted.day),
        known_at=known_at if known_at is not None else filing.dt_posted,
    )


def lda_retention_edge(
    *,
    client_canonical_id: str,
    registrant_canonical_id: str,
    filing: LdaFiling,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the client -> registrant lobbying-retention edge for a filing."""
    attributes = {"year": str(filing.year)}
    if filing.filing_type:
        attributes["filing_type"] = filing.filing_type
    if filing.income is not None:
        attributes["income"] = filing.income
    if filing.expenses is not None:
        attributes["expenses"] = filing.expenses
    return GraphEdge(
        edge_type="lobbying_retention",
        src_id=client_canonical_id,
        dst_id=registrant_canonical_id,
        attributes=attributes,
        external_key=filing.filing_uuid,
        provenance=provenance,
    )
