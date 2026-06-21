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

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.graph.bills import BillRef
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

# A congress bill citation inside a lobbying-activity description, e.g.
# "H.R. 2471", "S. 4321", "H.J.Res. 7". Captures the type token + number so the
# adapter can mint the BillRef for the bill lobbied on.
_BILL_MENTION_RE = re.compile(
    r"\b(H\.?\s?R\.?|S\.?|H\.?J\.?\s?Res\.?|S\.?J\.?\s?Res\.?|"
    r"H\.?\s?Con\.?\s?Res\.?|S\.?\s?Con\.?\s?Res\.?|H\.?\s?Res\.?|S\.?\s?Res\.?)\s?(\d{1,5})\b",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class LdaActivity:
    """One lobbying-activity line on an LDA filing.

    Carries the general issue area, the descriptive text, and the distinct
    congress bills referenced in that text (mapped to canonical bill IDs under
    the filing year's congress).
    """

    issue_area_code: str
    description: str
    bill_identifiers: tuple[str, ...]  # normalized congress ids, e.g. ("hr-1", "s-50")


def congress_for_year(year: int) -> int:
    """The congress number sitting in ``year`` (the 1st Congress began in 1789).

    Each congress spans two years starting in odd years, so the 118th Congress
    covers 2023-2024. A filing's bill references resolve under the congress of
    its filing year (the year the activity was disclosed).
    """
    if year < 1789:
        raise ValueError("year predates the 1st U.S. Congress")
    return (year - 1789) // 2 + 1


def _bill_ids_in(text: str) -> tuple[str, ...]:
    """Distinct normalized congress bill identifiers cited in ``text`` (order-stable)."""
    seen: list[str] = []
    for match in _BILL_MENTION_RE.finditer(text):
        token = re.sub(r"[.\s]", "", match.group(1)).lower()
        # Re-spell the citation as "<type> <number>" so BillRef's parser handles it.
        citation = f"{token} {match.group(2)}"
        try:
            identifier = BillRef.for_congress(1, citation).identifier
        except ValueError:
            continue
        if identifier not in seen:
            seen.append(identifier)
    return tuple(seen)


def parse_lda_activities(record: dict[str, Any]) -> list[LdaActivity]:
    """Parse a filing's ``lobbying_activities`` into typed activity lines.

    Each activity carries its general-issue-area code and any congress bills its
    description references. Activities with neither an issue code nor a bill
    reference are dropped (nothing to link); malformed entries are skipped, never
    fabricated.
    """
    activities: list[LdaActivity] = []
    for raw in record.get("lobbying_activities") or []:
        if not isinstance(raw, dict):
            continue
        issue_code = (raw.get("general_issue_area_code") or "").strip()
        description = (raw.get("description") or "").strip()
        bill_ids = _bill_ids_in(description)
        if not issue_code and not bill_ids:
            continue
        activities.append(
            LdaActivity(
                issue_area_code=issue_code,
                description=description,
                bill_identifiers=bill_ids,
            )
        )
    return activities


def lda_bill_lobbying_edges(
    *,
    client_canonical_id: str,
    filing: LdaFiling,
    activities: list[LdaActivity],
    provenance: ProvenanceEnvelope,
    registrant_name: str | None = None,
) -> list[GraphEdge]:
    """Build client -> bill ``lobbying_contact`` edges for a filing's activities.

    A distinct edge per (bill, issue area) referenced on the filing, keyed by the
    filing UUID + bill identifier so re-filings stay distinct and the same bill
    cited under two issue areas yields two auditable edges. Bills resolve under the
    filing year's congress. Returns ``[]`` when no activity references a bill.
    """
    from src.graph.ingest.lobbying import lobbying_edge

    congress = congress_for_year(filing.year) if filing.year else None
    edges: list[GraphEdge] = []
    seen: set[str] = set()
    for activity in activities:
        for identifier in activity.bill_identifiers:
            if congress is None:
                continue
            bill_ref = BillRef(
                jurisdiction_id="us-congress", session_id=str(congress), identifier=identifier
            )
            key = f"{filing.filing_uuid}:{identifier}:{activity.issue_area_code}"
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                lobbying_edge(
                    client_canonical_id=client_canonical_id,
                    bill_canonical_id=bill_ref.canonical_id,
                    issue_area=activity.issue_area_code or "UNKNOWN",
                    filing_id=key,
                    provenance=provenance,
                    registrant=registrant_name,
                )
            )
    return edges


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
