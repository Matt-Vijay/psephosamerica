"""Pure helpers for constructing EvidenceCardPayload objects from rule fires.

No I/O, no network calls, no database access.

Fact / Inference / Normative separation is explicit: callers pass text for
each section independently via assemble_blocks or build_evidence_card_payload.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    SourceAnchor,
)
from src.rules.models import RuleFire


def build_source_anchor(
    source_type: str,
    source_id: str,
    label: str,
    url: str | None = None,
) -> SourceAnchor:
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        url=url,
        label=label,
    )


def build_evidence_block(section: EvidenceSection, text: str) -> EvidenceBlock:
    return EvidenceBlock(section=section, text=text)


def build_fact_block(text: str) -> EvidenceBlock:
    return EvidenceBlock(section=EvidenceSection.FACT, text=text)


def build_inference_block(text: str) -> EvidenceBlock:
    return EvidenceBlock(section=EvidenceSection.INFERENCE, text=text)


def build_normative_block(text: str) -> EvidenceBlock:
    return EvidenceBlock(section=EvidenceSection.NORMATIVE_JUDGMENT, text=text)


def assemble_blocks(
    fact_texts: list[str],
    inference_texts: list[str],
    normative_texts: list[str],
) -> list[EvidenceBlock]:
    """Assemble blocks in canonical order: FACT → INFERENCE → NORMATIVE_JUDGMENT.

    Empty strings are skipped; pass [] for unused sections.
    """
    blocks: list[EvidenceBlock] = []
    for text in fact_texts:
        if text:
            blocks.append(build_fact_block(text))
    for text in inference_texts:
        if text:
            blocks.append(build_inference_block(text))
    for text in normative_texts:
        if text:
            blocks.append(build_normative_block(text))
    return blocks


def build_evidence_card_payload(
    rule_fire: RuleFire,
    member: dict[str, Any],
    source_anchors: list[SourceAnchor],
    fact_texts: list[str],
    inference_texts: list[str],
    normative_texts: list[str],
    *,
    evidence_card_id: str,
    score_delta: float,
    confidence: ConfidenceLabel,
    snapshot_date: dt.date,
    created_at: dt.datetime | None = None,
) -> EvidenceCardPayload:
    """Assemble an EvidenceCardPayload from a RuleFire and supporting data.

    *member* must contain ``bioguide_id``, ``slug``, and either ``full_name``
    or ``name``.
    """
    blocks = assemble_blocks(fact_texts, inference_texts, normative_texts)
    if score_delta != 0 and not any(block.section is EvidenceSection.FACT for block in blocks):
        raise ValueError("Nonzero public evidence cards require at least one fact block")
    member_name = member.get("full_name") or member.get("name", "")

    return EvidenceCardPayload(
        evidence_card_id=evidence_card_id,
        member_bioguide_id=member["bioguide_id"],
        member_name=member_name,
        member_slug=member["slug"],
        dimension=rule_fire.dimension,
        rule_id=rule_fire.rule_id,
        rule_version=rule_fire.rule_version,
        score_delta=score_delta,
        short_explanation=rule_fire.explanation,
        blocks=blocks,
        source_anchors=source_anchors,
        confidence=confidence,
        snapshot_date=snapshot_date,
        created_at=created_at or dt.datetime.now(dt.UTC),
    )
