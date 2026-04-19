"""Pure builders: dict rows → validated Pydantic models.  No I/O."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
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
from .manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256


# ── Internal helpers ───────────────────────────────────────────────


def _req(row: dict[str, Any], key: str, context: str) -> Any:
    """Return ``row[key]``, raising a readable ValueError when the key is absent."""
    if key not in row:
        raise ValueError(f"{context} row missing required field {key!r}")
    return row[key]


# ── Evidence Card ──────────────────────────────────────────────────


def build_evidence_card(
    rule_fire: dict[str, Any],
    member: dict[str, Any],
    source_rows: list[dict[str, Any]],
    snapshot_date: date,
) -> EvidenceCardPayload:
    raw_blocks = _req(rule_fire, "blocks", "rule_fire")
    blocks = [
        EvidenceBlock(
            section=EvidenceSection(_req(b, "section", "rule_fire.blocks[]")),
            text=_req(b, "text", "rule_fire.blocks[]"),
        )
        for b in raw_blocks
    ]
    anchors = [
        SourceAnchor(
            source_type=_req(s, "source_type", "source"),
            source_id=_req(s, "source_id", "source"),
            url=s.get("url"),
            label=_req(s, "label", "source"),
        )
        for s in source_rows
    ]
    return EvidenceCardPayload(
        evidence_card_id=_req(rule_fire, "evidence_card_id", "rule_fire"),
        member_bioguide_id=_req(member, "bioguide_id", "member"),
        member_name=_req(member, "name", "member"),
        member_slug=_req(member, "slug", "member"),
        dimension=_req(rule_fire, "dimension", "rule_fire"),
        rule_id=_req(rule_fire, "rule_id", "rule_fire"),
        rule_version=_req(rule_fire, "rule_version", "rule_fire"),
        score_delta=_req(rule_fire, "score_delta", "rule_fire"),
        short_explanation=_req(rule_fire, "short_explanation", "rule_fire"),
        blocks=blocks,
        source_anchors=anchors,
        confidence=ConfidenceLabel(_req(rule_fire, "confidence", "rule_fire")),
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
            dimension=_req(s, "dimension", "score"),
            current_score=_req(s, "current_score", "score"),
            rule_fire_count=_req(s, "rule_fire_count", "score"),
        )
        for s in score_rows
    ]
    fires = [
        RecentRuleFire(
            rule_id=_req(f, "rule_id", "recent_fire"),
            evidence_card_id=_req(f, "evidence_card_id", "recent_fire"),
            short_explanation=_req(f, "short_explanation", "recent_fire"),
            score_delta=_req(f, "score_delta", "recent_fire"),
            snapshot_date=_req(f, "snapshot_date", "recent_fire"),
        )
        for f in recent_fires
    ]
    committees = [
        CommitteeMembership(
            committee_name=_req(c, "committee_name", "committee"),
            role=c.get("role"),
        )
        for c in committee_rows
    ]
    return MemberProfilePayload(
        bioguide_id=_req(member, "bioguide_id", "member"),
        name=_req(member, "name", "member"),
        slug=_req(member, "slug", "member"),
        state=_req(member, "state", "member"),
        district=member.get("district"),
        chamber=_req(member, "chamber", "member"),
        party=_req(member, "party", "member"),
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
            bioguide_id=_req(m, "bioguide_id", "member"),
            name=_req(m, "name", "member"),
            slug=_req(m, "slug", "member"),
            chamber=_req(m, "chamber", "member"),
            party=_req(m, "party", "member"),
            scores=[
                ScoreSummary(
                    dimension=_req(s, "dimension", "score"),
                    current_score=_req(s, "current_score", "score"),
                    rule_fire_count=_req(s, "rule_fire_count", "score"),
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


def _is_confined(path: str) -> bool:
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def _validate_manifest_entries(entries: list[ManifestEntry]) -> None:
    seen_paths: set[str] = set()
    duplicate_paths: set[str] = set()

    for entry in entries:
        if not _is_confined(entry.path):
            raise ValueError(f"manifest entry path must stay confined to publish root: {entry.path!r}")
        if entry.path in seen_paths:
            duplicate_paths.add(entry.path)
        seen_paths.add(entry.path)

    if duplicate_paths:
        duplicates = ", ".join(sorted(duplicate_paths))
        raise ValueError(f"duplicate manifest entry paths are not allowed: {duplicates}")


def build_manifest(
    snapshot_id: str,
    file_entries: list[dict[str, Any]],  # each must have: path, sha256, size_bytes
) -> SnapshotManifest:
    entries = [
        ManifestEntry(
            path=_req(f, "path", "file_entry"),
            sha256=_req(f, "sha256", "file_entry"),
            size_bytes=_req(f, "size_bytes", "file_entry"),
        )
        for f in file_entries
    ]
    _validate_manifest_entries(entries)
    ordered_entries = sorted(entries, key=lambda entry: entry.path)
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime.now(UTC),
        entries=ordered_entries,
        total_files=len(ordered_entries),
        total_bytes=sum(e.size_bytes for e in ordered_entries),
        root_sha256=manifest_root_sha256(ordered_entries),
    )
