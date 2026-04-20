"""Assemble an EvidenceCardPayload from one persisted evidence_card row.

Pure helpers — no SQL, no DB, no I/O.

Expected row shape (caller merges evidence_card + member + rule_fire columns):
  public_id            — evidence_card.public_id
  member_bioguide_id   — member.bioguide_id
  member_full_name     — member.full_name  (fallback: "full_name")
  member_slug          — member.slug
  dimension            — evidence_card.dimension
  rule_id              — rule_fire.rule_id
  rule_version         — rule_fire.rule_version  (text or int, coerced to int)
  score_delta          — evidence_card.score_delta
  short_explanation    — evidence_card.short_explanation
  facts                — evidence_card.facts        (jsonb dict or list)
  inferences           — evidence_card.inferences   (jsonb dict or list)
  normative_judgments  — evidence_card.normative_judgments (jsonb dict or list)
  source_anchors       — evidence_card.source_anchors (jsonb list)
  confidence_label     — evidence_card.confidence_label ("HIGH"/"MEDIUM"/"LOW")
  rendered_at          — evidence_card.rendered_at  (used for snapshot_date)
  created_at           — evidence_card.created_at   (optional; falls back to rendered_at)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    SourceAnchor,
)

_SECTION_ORDER = (
    EvidenceSection.FACT,
    EvidenceSection.INFERENCE,
    EvidenceSection.NORMATIVE_JUDGMENT,
)


def _section_blocks(
    data: dict[str, str] | list[str] | None,
    section: EvidenceSection,
) -> list[EvidenceBlock]:
    """Turn one JSON column value into EvidenceBlocks for that section.

    Dicts are iterated in sorted-key order for stable replay.
    Lists are taken in their stored order.
    Empty strings are dropped.
    """
    if not data:
        return []
    if isinstance(data, dict):
        return [
            EvidenceBlock(section=section, text=text)
            for _, text in sorted(data.items())
            if text
        ]
    return [EvidenceBlock(section=section, text=text) for text in data if text]


def _blocks_from_columns(
    facts: dict[str, str] | list[str] | None,
    inferences: dict[str, str] | list[str] | None,
    normative_judgments: dict[str, str] | list[str] | None,
) -> list[EvidenceBlock]:
    """Reconstruct EvidenceBlocks from stored JSON columns.

    Section order is always FACT → INFERENCE → NORMATIVE_JUDGMENT.
    """
    blocks: list[EvidenceBlock] = []
    for section, data in zip(_SECTION_ORDER, (facts, inferences, normative_judgments)):
        blocks.extend(_section_blocks(data, section))
    return blocks


def _parse_anchors(raw: list[dict[str, Any]] | None) -> list[SourceAnchor]:
    if not raw:
        return []
    best_by_identity: dict[tuple[str, str], SourceAnchor] = {}
    for anchor_row in raw:
        anchor = SourceAnchor(
            source_type=anchor_row["source_type"],
            source_id=anchor_row["source_id"],
            url=anchor_row.get("url"),
            label=anchor_row["label"],
        )
        identity = (anchor.source_type, anchor.source_id)
        existing = best_by_identity.get(identity)
        if existing is None:
            best_by_identity[identity] = anchor
            continue

        existing_key = (existing.url is None, -len(existing.label), existing.label, existing.url or "")
        candidate_key = (anchor.url is None, -len(anchor.label), anchor.label, anchor.url or "")
        if candidate_key < existing_key:
            best_by_identity[identity] = anchor

    return sorted(
        best_by_identity.values(),
        key=lambda anchor: (anchor.source_type, anchor.source_id, anchor.label, anchor.url or ""),
    )


def _snapshot_date(row: dict[str, Any]) -> date:
    rendered = row.get("rendered_at")
    if isinstance(rendered, datetime):
        return rendered.date()
    if isinstance(rendered, date):
        return rendered
    return date.today()


def _created_at(row: dict[str, Any]) -> datetime:
    # Prefer the explicit created_at column; fall back to rendered_at.
    ts = row.get("created_at") or row.get("rendered_at")
    if isinstance(ts, datetime):
        return ts
    return datetime.now(UTC)


def assemble_evidence_card(row: dict[str, Any]) -> EvidenceCardPayload:
    """Turn one persisted evidence_card row into an EvidenceCardPayload.

    The caller is responsible for merging in rule_fire fields (rule_id,
    rule_version) and member.bioguide_id before passing the row here.
    """
    blocks = _blocks_from_columns(
        row.get("facts"),
        row.get("inferences"),
        row.get("normative_judgments"),
    )
    anchors = _parse_anchors(row.get("source_anchors"))
    member_name = row.get("member_full_name") or row.get("full_name", "")

    return EvidenceCardPayload(
        evidence_card_id=row["public_id"],
        member_bioguide_id=row["member_bioguide_id"],
        member_name=member_name,
        member_slug=row["member_slug"],
        dimension=row["dimension"],
        rule_id=row["rule_id"],
        rule_version=int(row["rule_version"]),
        score_delta=float(row["score_delta"]),
        short_explanation=row["short_explanation"],
        blocks=blocks,
        source_anchors=anchors,
        confidence=ConfidenceLabel(row["confidence_label"].lower()),
        snapshot_date=_snapshot_date(row),
        created_at=_created_at(row),
    )
