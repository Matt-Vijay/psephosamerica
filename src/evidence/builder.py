"""Pure helpers for constructing EvidenceCardPayload objects from rule fires.

No I/O, no network calls, no database access.  All functions take plain
Python values or typed models and return typed models.

Fact / Inference / Normative separation is explicit: callers pass text for
each section independently.  Mixing sections inside one list is not
supported by this API.
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


# ---------------------------------------------------------------------------
# Source anchor helpers
# ---------------------------------------------------------------------------


def build_source_anchor(
    source_type: str,
    source_id: str,
    label: str,
    url: str | None = None,
) -> SourceAnchor:
    """Build a single SourceAnchor from its constituent fields.

    Args:
        source_type: Domain category, e.g. ``'financial_disclosure'``,
            ``'fec_contribution'``.
        source_id: Primary key or artifact ID in the canonical store.
        label: Human-readable label for display.
        url: Optional public URL to the source record.
    """
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        url=url,
        label=label,
    )


# ---------------------------------------------------------------------------
# Evidence block helpers
# ---------------------------------------------------------------------------


def build_evidence_block(section: EvidenceSection, text: str) -> EvidenceBlock:
    """Build an evidence block tagged with *section*."""
    return EvidenceBlock(section=section, text=text)


def build_fact_block(text: str) -> EvidenceBlock:
    """Build a FACT-section evidence block."""
    return EvidenceBlock(section=EvidenceSection.FACT, text=text)


def build_inference_block(text: str) -> EvidenceBlock:
    """Build an INFERENCE-section evidence block."""
    return EvidenceBlock(section=EvidenceSection.INFERENCE, text=text)


def build_normative_block(text: str) -> EvidenceBlock:
    """Build a NORMATIVE_JUDGMENT-section evidence block."""
    return EvidenceBlock(section=EvidenceSection.NORMATIVE_JUDGMENT, text=text)


def assemble_blocks(
    fact_texts: list[str],
    inference_texts: list[str],
    normative_texts: list[str],
) -> list[EvidenceBlock]:
    """Assemble blocks from three parallel text lists in canonical order.

    Section order is always: FACT → INFERENCE → NORMATIVE_JUDGMENT.
    Empty text strings are skipped so callers may pass ``[]`` for unused
    sections without producing empty blocks.
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


# ---------------------------------------------------------------------------
# Full payload assembly
# ---------------------------------------------------------------------------


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

    Args:
        rule_fire: The RuleFire that triggered this evidence card.
        member: Dict with at least ``bioguide_id``, ``full_name`` (or
            ``name``), and ``slug``.  Mirrors the ``member`` DB row shape.
        source_anchors: Pre-built SourceAnchor objects.  Use
            :func:`build_source_anchor` to construct them.
        fact_texts: Texts for the FACT section (what is in the record).
        inference_texts: Texts for the INFERENCE section (what the facts
            imply, short of a normative claim).
        normative_texts: Texts for the NORMATIVE_JUDGMENT section (value
            judgments about the pattern, kept strictly separate from fact).
        evidence_card_id: Stable public identifier for this card.
        score_delta: Score change caused by this rule fire.
        confidence: Confidence label for entity resolution supporting this card.
        snapshot_date: Date of the recompute snapshot this card belongs to.
        created_at: Override card creation timestamp (defaults to now UTC).
    """
    blocks = assemble_blocks(fact_texts, inference_texts, normative_texts)

    member_name = member.get("full_name") or member.get("name", "")
    member_slug = member["slug"]
    member_bioguide_id = member["bioguide_id"]

    return EvidenceCardPayload(
        evidence_card_id=evidence_card_id,
        member_bioguide_id=member_bioguide_id,
        member_name=member_name,
        member_slug=member_slug,
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
