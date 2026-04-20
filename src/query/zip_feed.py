"""Assemble a ZipFeedPayload from a FederalBundle and supplementary row data.

Pure helpers — no SQL, no DB, no I/O.

The FederalBundle's plurality_district.ambiguity_note is passed through
unchanged when a ZIP spans multiple congressional districts (§10 spec).
Street-level resolution is deferred.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Mapping, Sequence, cast

from src.export.contracts import (
    ScoreSummary,
    ZipFeedPayload,
    ZipMemberSummary,
)
from src.zip.resolve import FederalBundle, MemberRef


def _format_district(state: str, district: int) -> str:
    # e.g. "CA-33", "AK-00" (at-large)
    return f"{state}-{district:02d}"


def _member_chamber(chamber: str) -> Literal["house", "senate"]:
    return cast(Literal["house", "senate"], chamber)


def _normalized_score_rows(score_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(score_rows, key=lambda row: (row["dimension"],))


def _normalized_evidence_card_ids(evidence_card_ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for card_id in evidence_card_ids:
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        normalized.append(card_id)
    return normalized[:3]


def _build_member_summary(
    ref: MemberRef,
    score_rows: Sequence[dict[str, Any]],
    evidence_card_ids: Sequence[str],
) -> ZipMemberSummary:
    scores = [
        ScoreSummary(
            dimension=row["dimension"],
            current_score=row["current_score"],
            rule_fire_count=row["rule_fire_count"],
        )
        for row in _normalized_score_rows(score_rows)
    ]
    return ZipMemberSummary(
        bioguide_id=ref.bioguide_id,
        name=ref.full_name,
        slug=ref.slug,
        chamber=_member_chamber(ref.chamber),
        party=ref.party,
        scores=scores,
        top_evidence_card_ids=_normalized_evidence_card_ids(evidence_card_ids),
    )


def assemble_zip_feed(
    bundle: FederalBundle,
    member_summary_rows: Sequence[dict[str, Any]],
    recent_evidence_ids: Mapping[str, Sequence[str]],
    snapshot_date: date,
) -> ZipFeedPayload:
    """Build ZipFeedPayload for a resolved FederalBundle.

    member_summary_rows: one row per (bioguide_id, dimension) — needs
        bioguide_id, dimension, current_score, rule_fire_count.
    recent_evidence_ids: bioguide_id → card IDs most-recent-first; up to 3 surfaced.
    """
    pd = bundle.plurality_district

    scores_by_member: dict[str, list[dict[str, Any]]] = {}
    for row in member_summary_rows:
        scores_by_member.setdefault(row["bioguide_id"], []).append(row)

    # House member first, then senators in seat order (as resolved by FederalBundle).
    refs: list[MemberRef] = []
    if bundle.house_member is not None:
        refs.append(bundle.house_member)
    refs.extend(bundle.senators)

    return ZipFeedPayload(
        zip_code=bundle.zip5,
        congressional_district=_format_district(pd.state, pd.district),
        ambiguity_note=pd.ambiguity_note,
        members=[
            _build_member_summary(
                ref=ref,
                score_rows=scores_by_member.get(ref.bioguide_id, []),
                evidence_card_ids=recent_evidence_ids.get(ref.bioguide_id, []),
            )
            for ref in refs
        ],
        snapshot_date=snapshot_date,
    )
