"""ZIP feed assembly from canonical row-shaped inputs.

Pure helpers only — no SQL, no DB, no I/O.

Callers supply:
  - a FederalBundle (from src.zip.resolve)
  - per-member score summary rows (list of dicts)
  - per-member recent evidence card IDs (mapping bioguide_id -> list[str])
  - a snapshot date

Returns a ZipFeedPayload ready for serialisation.

Plurality-district ambiguity note: ZIP codes may span multiple congressional
districts.  When they do, the FederalBundle carries a non-None ambiguity_note
on its PluralityDistrict, which this module passes through unchanged to the
ZipFeedPayload.  No attempt is made to resolve the ambiguity here; street-level
resolution is deferred to a later product iteration (§10 ENGINEERING_SPEC_V1).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from src.export.contracts import (
    ScoreSummary,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.zip.resolve import FederalBundle, MemberRef


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_district(state: str, district: int) -> str:
    """Return a canonical district string like 'CA-33' or 'AK-00' (at-large)."""
    return f"{state}-{district:02d}"


def _build_member_summary(
    ref: MemberRef,
    score_rows: Sequence[dict[str, Any]],
    evidence_card_ids: Sequence[str],
) -> ZipMemberSummary:
    """Build one ZipMemberSummary from a MemberRef and pre-filtered data.

    Args:
        ref: Lightweight member reference from the federal bundle.
        score_rows: Score summary rows already filtered for this member.
            Each dict must contain ``dimension``, ``current_score``,
            ``rule_fire_count``.
        evidence_card_ids: Recent evidence card IDs for this member,
            most recent first.  At most 3 are surfaced.
    """
    scores = [
        ScoreSummary(
            dimension=row["dimension"],
            current_score=row["current_score"],
            rule_fire_count=row["rule_fire_count"],
        )
        for row in score_rows
    ]
    return ZipMemberSummary(
        bioguide_id=ref.bioguide_id,
        name=ref.full_name,
        slug=ref.slug,
        chamber=ref.chamber,
        party=ref.party,
        scores=scores,
        top_evidence_card_ids=list(evidence_card_ids)[:3],
    )


# ---------------------------------------------------------------------------
# Public assembly entry point
# ---------------------------------------------------------------------------


def assemble_zip_feed(
    bundle: FederalBundle,
    member_summary_rows: Sequence[dict[str, Any]],
    recent_evidence_ids: Mapping[str, Sequence[str]],
    snapshot_date: date,
) -> ZipFeedPayload:
    """Assemble a ZipFeedPayload from a FederalBundle and supplementary data.

    Args:
        bundle: Federal bundle resolved for the ZIP code via
            ``src.zip.resolve.assemble_federal_bundle``.  Must be non-None
            (callers should guard against unknown ZIPs before calling here).
        member_summary_rows: Per-member score rows for all members in the
            bundle.  Each dict must have:
            ``bioguide_id``, ``dimension``, ``current_score``,
            ``rule_fire_count``.  Multiple rows per member are expected —
            one per scored dimension.
        recent_evidence_ids: Mapping from ``bioguide_id`` to a list of
            recent evidence card IDs, most recent first.  Up to 3 are
            surfaced per member; extras are silently dropped.
        snapshot_date: Recompute snapshot date stamped on the payload.

    Returns:
        ZipFeedPayload suitable for JSON serialisation and CDN publishing.

    Note — plurality-district ambiguity:
        When a ZIP spans multiple congressional districts the bundle's
        ``plurality_district.ambiguity_note`` is non-None.  This function
        passes it through verbatim so the front end can render the
        clarifying note described in §10 of ENGINEERING_SPEC_V1.
    """
    pd = bundle.plurality_district
    district_str = _format_district(pd.state, pd.district)

    # Index score rows by bioguide_id for O(1) lookup per member.
    scores_by_member: dict[str, list[dict[str, Any]]] = {}
    for row in member_summary_rows:
        scores_by_member.setdefault(row["bioguide_id"], []).append(row)

    # House member first, then senators ordered by seat (as resolved upstream).
    refs: list[MemberRef] = []
    if bundle.house_member is not None:
        refs.append(bundle.house_member)
    refs.extend(bundle.senators)

    members = [
        _build_member_summary(
            ref=ref,
            score_rows=scores_by_member.get(ref.bioguide_id, []),
            evidence_card_ids=recent_evidence_ids.get(ref.bioguide_id, []),
        )
        for ref in refs
    ]

    return ZipFeedPayload(
        zip_code=bundle.zip5,
        congressional_district=district_str,
        ambiguity_note=pd.ambiguity_note,
        members=members,
        snapshot_date=snapshot_date,
    )
