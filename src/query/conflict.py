"""Bridge from pre-joined row dicts to rule-engine-ready ConflictBundles.

Pure helpers — no SQL, no DB calls, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.evidence.builder import build_source_anchor
from src.export.contracts import SourceAnchor
from src.rules.contexts import (
    build_committee_sector_trade_context,
    build_late_or_amended_disclosure_context,
    build_repeated_committee_linked_trading_context,
    build_sector_holdings_overlap_context,
    row_reference_date,
    transaction_matches_sector_window,
)


@dataclass(frozen=True)
class ConflictBundle:
    """Rule engine inputs assembled from one pre-joined row.

    superseded_filing_id is set when the row represents an amendment filing.
    """

    member_bioguide_id: str
    context: dict[str, Any]
    source_anchors: list[SourceAnchor]
    superseded_filing_id: str | None


def _source_anchor_if_present(
    *,
    source_type: str,
    source_id: Any,
    label: str,
    url: str | None = None,
) -> SourceAnchor | None:
    if source_id is None:
        return None
    return build_source_anchor(
        source_type=source_type,
        source_id=str(source_id),
        url=url,
        label=label,
    )


def _source_url(row: dict[str, Any]) -> str | None:
    value = row.get("source_url") or row.get("financial_disclosure_source_url")
    return value if isinstance(value, str) and value else None


def _committee_membership_source_url(row: dict[str, Any]) -> str | None:
    value = row.get("committee_membership_source_url") or row.get("committee_source_url")
    return value if isinstance(value, str) and value else None


def _repeated_trading_transaction_contributes(
    row: dict[str, Any],
    transaction: dict[str, Any],
) -> bool:
    committee_sector = row.get("committee_sector")
    if committee_sector is None:
        return False
    return transaction_matches_sector_window(
        transaction,
        committee_sector,
        row["committee_start_date"],
        row.get("committee_end_date"),
        reference_date=row_reference_date(row),
    )


def _repeated_trading_disclosure_anchors(row: dict[str, Any]) -> list[SourceAnchor]:
    """Anchor every distinct filing that contributed a matched transaction."""
    anchors: list[SourceAnchor] = []
    seen: set[str] = set()
    has_contributing_transaction = False
    for transaction in row.get("transactions", []):
        if not _repeated_trading_transaction_contributes(row, transaction):
            continue
        has_contributing_transaction = True
        source_id = transaction.get("financial_disclosure_id")
        if source_id is None:
            continue
        normalized = str(source_id)
        if normalized in seen:
            continue
        seen.add(normalized)
        anchors.append(
            build_source_anchor(
                source_type="financial_disclosure",
                source_id=normalized,
                url=_source_url(transaction),
                label="Financial disclosure",
            )
        )

    fallback = row.get("financial_disclosure_id")
    if has_contributing_transaction and fallback is not None and str(fallback) not in seen:
        anchors.append(
            build_source_anchor(
                source_type="financial_disclosure",
                source_id=str(fallback),
                url=_source_url(row),
                label="Financial disclosure",
            )
        )
    return anchors


def assemble_committee_sector_trade_bundle(row: dict[str, Any]) -> ConflictBundle:
    """Required row keys: member_bioguide_id, financial_disclosure_id,
    committee_membership_id, plus keys consumed by build_committee_sector_trade_context.
    """
    context = build_committee_sector_trade_context(row)
    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            url=_source_url(row),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            url=_committee_membership_source_url(row),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]
    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )


def assemble_repeated_committee_linked_trading_bundle(
    row: dict[str, Any],
) -> ConflictBundle:
    """Required row keys: member_bioguide_id, financial_disclosure_id,
    committee_membership_id, plus keys consumed by build_repeated_committee_linked_trading_context.
    """
    context = build_repeated_committee_linked_trading_context(row)
    anchors: list[SourceAnchor] = [
        *_repeated_trading_disclosure_anchors(row),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            url=_committee_membership_source_url(row),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]
    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )


def assemble_late_or_amended_disclosure_bundle(
    row: dict[str, Any],
) -> ConflictBundle:
    """Required row keys: member_bioguide_id, financial_disclosure_id,
    plus keys consumed by build_late_or_amended_disclosure_context.

    When filing_is_amendment is truthy, superseded_filing_id is forwarded
    onto the bundle so the rule engine can link the fire to the replaced filing.
    """
    context = build_late_or_amended_disclosure_context(row)
    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            url=_source_url(row),
            label=f"Financial disclosure ({row.get('filing_kind', 'filing')})",
        ),
    ]
    superseded: str | None = None
    if row.get("filing_is_amendment") and row.get("superseded_filing_id") is not None:
        superseded = str(row["superseded_filing_id"])
    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=superseded,
    )


def assemble_sector_holdings_overlap_bundle(row: dict[str, Any]) -> ConflictBundle:
    """Required row keys: member_bioguide_id, financial_disclosure_id,
    committee_membership_id, plus keys consumed by build_sector_holdings_overlap_context.
    """
    context = build_sector_holdings_overlap_context(row)
    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            url=_source_url(row),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            url=_committee_membership_source_url(row),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]
    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )
