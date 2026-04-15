"""Pure builders: dict rows → validated Pydantic models.  No I/O."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from .contracts import (
    CommitteeMembership,
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    MemberProfilePayload,
    RecentRuleFire,
    ScoreSummary,
    SourceAnchor,
    ZipFeedPayload,
    ZipMemberSummary,
)
from .manifest import ManifestEntry, SnapshotManifest


# ── Evidence Card ──────────────────────────────────────────────────


def build_evidence_card(
    rule_fire: dict[str, Any],
    member: dict[str, Any],
    source_rows: list[dict[str, Any]],
    snapshot_date: date,
) -> EvidenceCardPayload:
    blocks = [
        EvidenceBlock(section=EvidenceSection(b["section"]), text=b["text"])
        for b in rule_fire["blocks"]
    ]
    anchors = [
        SourceAnchor(
            source_type=s["source_type"],
            source_id=s["source_id"],
            url=s.get("url"),
            label=s["label"],
        )
        for s in source_rows
    ]
    return EvidenceCardPayload(
        evidence_card_id=rule_fire["evidence_card_id"],
        member_bioguide_id=member["bioguide_id"],
        member_name=member["name"],
        member_slug=member["slug"],
        dimension=rule_fire["dimension"],
        rule_id=rule_fire["rule_id"],
        rule_version=rule_fire["rule_version"],
        score_delta=rule_fire["score_delta"],
        short_explanation=rule_fire["short_explanation"],
        blocks=blocks,
        source_anchors=anchors,
        confidence=ConfidenceLabel(rule_fire["confidence"]),
        snapshot_date=snapshot_date,
        created_at=rule_fire.get("created_at", datetime.now(UTC)),
    )


# ── Member Profile ─────────────────────────────────────────────────


def build_member_profile(
    member: dict[str, Any],
    score_rows: list[dict[str, Any]],
    recent_fires: list[dict[str, Any]],
    committee_rows: list[dict[str, Any]],
    total_evidence_cards: int,
    snapshot_date: date,
) -> MemberProfilePayload:
    scores = [
        ScoreSummary(
            dimension=s["dimension"],
            current_score=s["current_score"],
            rule_fire_count=s["rule_fire_count"],
        )
        for s in score_rows
    ]
    fires = [
        RecentRuleFire(
            rule_id=f["rule_id"],
            evidence_card_id=f["evidence_card_id"],
            short_explanation=f["short_explanation"],
            score_delta=f["score_delta"],
            snapshot_date=f["snapshot_date"],
        )
        for f in recent_fires
    ]
    committees = [
        CommitteeMembership(
            committee_name=c["committee_name"],
            role=c.get("role"),
        )
        for c in committee_rows
    ]
    return MemberProfilePayload(
        bioguide_id=member["bioguide_id"],
        name=member["name"],
        slug=member["slug"],
        state=member["state"],
        district=member.get("district"),
        chamber=member["chamber"],
        party=member["party"],
        scores=scores,
        recent_rule_fires=fires,
        committees=committees,
        total_evidence_cards=total_evidence_cards,
        snapshot_date=snapshot_date,
    )


# ── ZIP Feed ───────────────────────────────────────────────────────


def build_zip_feed(
    zip_code: str,
    district: str | None,
    ambiguity_note: str | None,
    member_rows: list[dict[str, Any]],
    snapshot_date: date,
) -> ZipFeedPayload:
    members = [
        ZipMemberSummary(
            bioguide_id=m["bioguide_id"],
            name=m["name"],
            slug=m["slug"],
            chamber=m["chamber"],
            party=m["party"],
            scores=[
                ScoreSummary(
                    dimension=s["dimension"],
                    current_score=s["current_score"],
                    rule_fire_count=s["rule_fire_count"],
                )
                for s in m.get("scores", [])
            ],
            top_evidence_card_ids=m.get("top_evidence_card_ids", []),
        )
        for m in member_rows
    ]
    return ZipFeedPayload(
        zip_code=zip_code,
        congressional_district=district,
        ambiguity_note=ambiguity_note,
        members=members,
        snapshot_date=snapshot_date,
    )


# ── Snapshot Manifest ──────────────────────────────────────────────


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(
    snapshot_id: str,
    file_entries: list[dict[str, Any]],  # each must have: path, sha256, size_bytes
) -> SnapshotManifest:
    entries = [
        ManifestEntry(
            path=f["path"],
            sha256=f["sha256"],
            size_bytes=f["size_bytes"],
        )
        for f in file_entries
    ]
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime.now(UTC),
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(e.size_bytes for e in entries),
    )
