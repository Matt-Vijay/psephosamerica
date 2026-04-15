from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Shared primitives ──────────────────────────────────────────────


class ConfidenceLabel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceAnchor(BaseModel):
    """Pointer to the official record that backs a fact."""

    source_type: str = Field(description="e.g. 'financial_disclosure', 'fec_contribution'")
    source_id: str = Field(description="Primary key or artifact ID in the canonical store")
    url: str | None = Field(default=None, description="Public URL to the source, if available")
    label: str = Field(description="Human-readable label for display")


class EvidenceSection(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    NORMATIVE_JUDGMENT = "normative_judgment"


class EvidenceBlock(BaseModel):
    section: EvidenceSection
    text: str


# ── Evidence Card ──────────────────────────────────────────────────


class EvidenceCardPayload(BaseModel):
    """Public JSON contract for ``/evidence/:id``."""

    evidence_card_id: str
    member_bioguide_id: str
    member_name: str
    member_slug: str
    dimension: str = Field(description="Score dimension, e.g. 'conflict_of_interest_risk'")
    rule_id: str
    rule_version: int
    score_delta: float
    short_explanation: str
    blocks: list[EvidenceBlock]
    source_anchors: list[SourceAnchor]
    confidence: ConfidenceLabel
    snapshot_date: date
    created_at: datetime


# ── Member Profile ─────────────────────────────────────────────────


class ScoreSummary(BaseModel):
    dimension: str
    current_score: float
    rule_fire_count: int


class RecentRuleFire(BaseModel):
    rule_id: str
    evidence_card_id: str
    short_explanation: str
    score_delta: float
    snapshot_date: date


class CommitteeMembership(BaseModel):
    committee_name: str
    role: str | None = None


class MemberProfilePayload(BaseModel):
    """Public JSON contract for ``/member/:slug``."""

    bioguide_id: str
    name: str
    slug: str
    state: str
    district: str | None = None
    chamber: Literal["house", "senate"]
    party: str
    scores: list[ScoreSummary]
    recent_rule_fires: list[RecentRuleFire]
    committees: list[CommitteeMembership]
    total_evidence_cards: int
    snapshot_date: date


# ── ZIP Feed ───────────────────────────────────────────────────────


class ZipMemberSummary(BaseModel):
    bioguide_id: str
    name: str
    slug: str
    chamber: Literal["house", "senate"]
    party: str
    scores: list[ScoreSummary]
    top_evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="Up to 3 most recent evidence card IDs for this member",
    )


class ZipFeedPayload(BaseModel):
    """Public JSON contract for ``/zip/:zip``."""

    zip_code: str = Field(min_length=5, max_length=5)
    congressional_district: str | None = Field(
        default=None, description="Plurality district when ZIP maps to multiple"
    )
    ambiguity_note: str | None = Field(
        default=None,
        description="Shown when a ZIP spans multiple districts",
    )
    members: list[ZipMemberSummary]
    snapshot_date: date
