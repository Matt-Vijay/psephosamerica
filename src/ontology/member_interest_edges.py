from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from src.export.contracts import SourceAnchor
from src.evidence.source_anchor_policy import is_official_source_url
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef


def build_member_interest_edges(
    *,
    membership_rows: list[dict[str, Any]],
    holding_rows: list[dict[str, Any]],
    transaction_rows: list[dict[str, Any]],
    contribution_rows: list[dict[str, Any]] | None = None,
    statement_rows: list[dict[str, Any]] | None = None,
) -> list[OntologyEdgePayload]:
    """Build source-backed member-interest ontology edges from canonical rows."""
    edges: list[OntologyEdgePayload] = []
    seen: set[str] = set()

    for row in membership_rows:
        _append_deduped(edges, seen, _member_committee_assignment_edge(row))
        _append_deduped(edges, seen, _committee_sector_jurisdiction_edge(row))
    for row in holding_rows:
        _append_deduped(edges, seen, _member_sector_holding_exposure_edge(row))
    for row in transaction_rows:
        _append_deduped(edges, seen, _member_sector_transaction_exposure_edge(row))
    for row in contribution_rows or []:
        _append_deduped(edges, seen, _member_sector_contribution_exposure_edge(row))
    for row in statement_rows or []:
        _append_deduped(edges, seen, _member_sector_public_statement_alignment_edge(row))

    return edges


def _append_deduped(
    edges: list[OntologyEdgePayload],
    seen: set[str],
    edge: OntologyEdgePayload | None,
) -> None:
    if edge is None or edge.edge_id in seen:
        return
    seen.add(edge.edge_id)
    edges.append(edge)


def _stable_edge_id(
    edge_type: str,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
    source_id: str,
) -> str:
    raw = "|".join([edge_type, subject_type, subject_id, object_type, object_id, source_id])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"ont-edge-{digest}"


def _str_value(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _date_value(value: object) -> str | None:
    if isinstance(value, dt.date):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _source_url(row: dict[str, Any]) -> str | None:
    value = (
        row.get("source_url")
        or row.get("committee_membership_source_url")
        or row.get("statement_source_url")
    )
    return value if isinstance(value, str) and value else None


def _committee_anchor(row: dict[str, Any]) -> SourceAnchor | None:
    source_id = _str_value(row, "committee_membership_id")
    url = _source_url(row)
    if source_id is None or not is_official_source_url("committee_membership", url):
        return None
    return SourceAnchor(
        source_type="committee_membership",
        source_id=source_id,
        url=url,
        label=f"Committee membership: {row.get('committee_name', '')}",
    )


def _financial_anchor(row: dict[str, Any]) -> SourceAnchor | None:
    source_id = _str_value(row, "financial_disclosure_id")
    url = _source_url(row)
    if source_id is None or not is_official_source_url("financial_disclosure", url):
        return None
    return SourceAnchor(
        source_type="financial_disclosure",
        source_id=source_id,
        url=url,
        label="Financial disclosure",
    )


def _fec_contribution_anchor(row: dict[str, Any]) -> SourceAnchor | None:
    source_id = _str_value(row, "contribution_id") or _str_value(row, "source_record_id")
    url = _source_url(row)
    if source_id is None or not is_official_source_url("fec_contribution", url):
        return None
    donor_name = _str_value(row, "donor_name")
    return SourceAnchor(
        source_type="fec_contribution",
        source_id=source_id,
        url=url,
        label=f"FEC contribution: {donor_name}" if donor_name else "FEC contribution",
    )


def _public_statement_anchor(row: dict[str, Any]) -> SourceAnchor | None:
    source_id = (
        _str_value(row, "statement_id")
        or _str_value(row, "source_record_id")
        or _str_value(row, "source_id")
    )
    url = _source_url(row)
    if source_id is None or not is_official_source_url("public_statement", url):
        return None
    title = _str_value(row, "statement_title")
    return SourceAnchor(
        source_type="public_statement",
        source_id=source_id,
        url=url,
        label=f"Public statement: {title}" if title else "Public statement",
    )


def _member_node(row: dict[str, Any]) -> OntologyNodeRef | None:
    bioguide_id = _str_value(row, "member_bioguide_id")
    if bioguide_id is None:
        return None
    return OntologyNodeRef(
        node_type="member",
        node_id=bioguide_id,
        label=_str_value(row, "member_name"),
    )


def _committee_node(row: dict[str, Any]) -> OntologyNodeRef | None:
    committee_id = _str_value(row, "committee_code") or _str_value(row, "committee_id")
    if committee_id is None:
        return None
    return OntologyNodeRef(
        node_type="committee",
        node_id=committee_id,
        label=_str_value(row, "committee_name"),
    )


def _sector_node(row: dict[str, Any], key: str) -> OntologyNodeRef | None:
    sector_id = _str_value(row, key)
    if sector_id is None:
        return None
    return OntologyNodeRef(
        node_type="sector",
        node_id=sector_id,
        label=_str_value(row, "sector_name") or sector_id,
    )


def _edge(
    edge_type: str,
    subject: OntologyNodeRef,
    object_: OntologyNodeRef,
    source_anchor: SourceAnchor,
    attributes: dict[str, Any] | None = None,
) -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id=_stable_edge_id(
            edge_type,
            subject.node_type,
            subject.node_id,
            object_.node_type,
            object_.node_id,
            source_anchor.source_id,
        ),
        edge_type=edge_type,  # type: ignore[arg-type]
        subject=subject,
        object=object_,
        source_anchors=[source_anchor],
        attributes=attributes or {},
    )


def _member_committee_assignment_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _member_node(row)
    object_ = _committee_node(row)
    anchor = _committee_anchor(row)
    if subject is None or object_ is None or anchor is None:
        return None
    return _edge(
        "member_committee_assignment",
        subject,
        object_,
        anchor,
        {
            "role": row.get("role"),
            "start_date": _date_value(row.get("committee_start_date")),
            "end_date": _date_value(row.get("committee_end_date")),
        },
    )


def _committee_sector_jurisdiction_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _committee_node(row)
    object_ = _sector_node(row, "committee_sector")
    anchor = _committee_anchor(row)
    if subject is None or object_ is None or anchor is None:
        return None
    return _edge(
        "committee_sector_jurisdiction",
        subject,
        object_,
        anchor,
        {
            "mapping_tier": row.get("committee_mapping_tier"),
            "subcommittee_name": row.get("committee_subcommittee_name"),
            "start_date": _date_value(row.get("committee_start_date")),
        },
    )


def _member_sector_holding_exposure_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _member_node(row)
    object_ = _sector_node(row, "holding_sector")
    anchor = _financial_anchor(row)
    if subject is None or object_ is None or anchor is None:
        return None
    return _edge(
        "member_sector_holding_exposure",
        subject,
        object_,
        anchor,
        {
            "holding_id": row.get("holding_id"),
            "issuer_name": row.get("issuer_name"),
            "issuer_ticker": row.get("issuer_ticker"),
            "filing_date": _date_value(row.get("filed_at") or row.get("filing_date")),
            "value_min": row.get("holding_value_min"),
            "value_max": row.get("holding_value_max"),
        },
    )


def _member_sector_transaction_exposure_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _member_node(row)
    object_ = _sector_node(row, "sector")
    anchor = _financial_anchor(row)
    if subject is None or object_ is None or anchor is None:
        return None
    return _edge(
        "member_sector_transaction_exposure",
        subject,
        object_,
        anchor,
        {
            "transaction_id": row.get("transaction_id"),
            "issuer_name": row.get("issuer_name"),
            "issuer_ticker": row.get("issuer_ticker"),
            "transaction_type": row.get("transaction_type"),
            "transaction_date": _date_value(row.get("transaction_date")),
            "amount_min": row.get("amount_min"),
            "amount_max": row.get("amount_max"),
        },
    )


def _member_sector_contribution_exposure_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _member_node(row)
    object_ = _sector_node(row, "sector")
    anchor = _fec_contribution_anchor(row)
    contribution_date = _date_value(row.get("contribution_date"))
    if subject is None or object_ is None or anchor is None or contribution_date is None:
        return None
    return _edge(
        "member_sector_contribution_exposure",
        subject,
        object_,
        anchor,
        {
            "contribution_id": row.get("contribution_id"),
            "source_record_id": row.get("source_record_id"),
            "donor_name": row.get("donor_name"),
            "donor_type": row.get("donor_type"),
            "contribution_type": row.get("contribution_type"),
            "contribution_date": contribution_date,
            "amount": row.get("amount"),
            "memo": row.get("memo"),
            "recipient_fec_committee_id": row.get("recipient_fec_committee_id"),
            "fec_committee_name": row.get("fec_committee_name"),
            "alignment_score": row.get("alignment_score", 1.0),
        },
    )


def _member_sector_public_statement_alignment_edge(
    row: dict[str, Any],
) -> OntologyEdgePayload | None:
    subject = _member_node(row)
    object_ = _sector_node(row, "sector")
    anchor = _public_statement_anchor(row)
    statement_date = _date_value(row.get("statement_date"))
    if subject is None or object_ is None or anchor is None or statement_date is None:
        return None
    return _edge(
        "member_sector_public_statement_alignment",
        subject,
        object_,
        anchor,
        {
            "statement_id": row.get("statement_id"),
            "source_record_id": row.get("source_record_id"),
            "statement_date": statement_date,
            "statement_title": row.get("statement_title"),
            "statement_excerpt": row.get("statement_excerpt"),
            "alignment_score": row.get("alignment_score", 1.0),
        },
    )
