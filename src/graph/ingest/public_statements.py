"""Adapt members' public statements into member -> sector alignment edges.

A public statement (press release, floor remark, RSS-published news) that an
official makes on a topic is a stance signal — especially for the thin-record
problem. This adapter records a ``public_statement`` edge from the member to a
sector/issue the statement aligns with (via keyword or model match), source-
anchored to the statement and stamped at the statement date (public when
published).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope


def statement_provenance(
    *,
    source_url: str,
    content_sha256: str,
    statement_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a public statement; ``known_at`` defaults to the statement day."""
    default_known = datetime(
        statement_date.year, statement_date.month, statement_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=statement_date,
        known_at=known_at if known_at is not None else default_known,
    )


def statement_alignment_edge(
    *,
    member_canonical_id: str,
    sector_id: str,
    match_method: str,
    statement_id: str,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the member -> sector public-statement alignment edge."""
    if not match_method.strip():
        raise ValueError("match_method must be non-blank")
    return GraphEdge(
        edge_type="public_statement",
        src_id=member_canonical_id,
        dst_id=sector_id,
        attributes={"match_method": match_method.strip().lower()},
        external_key=statement_id,
        provenance=provenance,
    )
