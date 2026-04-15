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


def assemble_committee_sector_trade_bundle(row: dict[str, Any]) -> ConflictBundle:
    """Required row keys: member_bioguide_id, financial_disclosure_id,
    committee_membership_id, plus keys consumed by build_committee_sector_trade_context.
    """
    context = build_committee_sector_trade_context(row)
    anchors: list[SourceAnchor] = [
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
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
        build_source_anchor(
            source_type="financial_disclosure",
            source_id=str(row["financial_disclosure_id"]),
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
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
            label="Financial disclosure",
        ),
        build_source_anchor(
            source_type="committee_membership",
            source_id=str(row["committee_membership_id"]),
            label=f"Committee membership: {row.get('committee_name', '')}",
        ),
    ]
    return ConflictBundle(
        member_bioguide_id=row["member_bioguide_id"],
        context=context,
        source_anchors=anchors,
        superseded_filing_id=None,
    )
