"""Tests for src/evidence/builder.py.

All tests are deterministic and make no network calls.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.evidence.builder import (
    assemble_blocks,
    build_evidence_block,
    build_evidence_card_payload,
    build_fact_block,
    build_inference_block,
    build_normative_block,
    build_source_anchor,
)
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    SourceAnchor,
)
from src.rules.models import RuleFire, Severity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SNAPSHOT_DATE = dt.date(2025, 1, 15)
CREATED_AT = dt.datetime(2025, 1, 15, 12, 0, 0, tzinfo=dt.UTC)

MEMBER = {
    "bioguide_id": "A000001",
    "full_name": "Jane Smith",
    "slug": "jane-smith-a000001",
}

MEMBER_NAME_ONLY = {
    "bioguide_id": "B000002",
    "name": "John Doe",
    "slug": "john-doe-b000002",
}


def _make_rule_fire(**overrides: object) -> RuleFire:
    defaults: dict = {
        "fire_id": "fire-abc-123",
        "rule_id": "conflict_of_interest_risk.committee_sector_trade.v1",
        "rule_version": 1,
        "member_bioguide_id": "A000001",
        "dimension": "conflict_of_interest_risk",
        "severity": Severity.high,
        "sourced_facts": {"sector": "finance", "trade_date": "2024-03-01"},
        "derived_values": {"days_after_hearing": 5},
        "parameters_used": {"window_days": 30},
        "recompute_run_id": "run-99",
        "explanation": "Member traded finance-sector stock within 30 days of a committee hearing.",
    }
    defaults.update(overrides)
    return RuleFire(**defaults)


def _make_anchor(n: int = 1) -> SourceAnchor:
    return build_source_anchor(
        source_type="financial_disclosure",
        source_id=f"fd-{n}",
        label=f"Financial Disclosure #{n}",
        url=f"https://example.gov/fd/{n}",
    )


# ---------------------------------------------------------------------------
# build_source_anchor
# ---------------------------------------------------------------------------

class TestBuildSourceAnchor:
    def test_returns_source_anchor(self):
        anchor = build_source_anchor("financial_disclosure", "fd-1", "Disclosure 2024")
        assert isinstance(anchor, SourceAnchor)

    def test_fields_set_correctly(self):
        anchor = build_source_anchor(
            source_type="fec_contribution",
            source_id="contrib-42",
            label="FEC Filing",
            url="https://fec.gov/c/42",
        )
        assert anchor.source_type == "fec_contribution"
        assert anchor.source_id == "contrib-42"
        assert anchor.label == "FEC Filing"
        assert anchor.url == "https://fec.gov/c/42"

    def test_url_defaults_to_none(self):
        anchor = build_source_anchor("vote_event", "ve-7", "Vote #7")
        assert anchor.url is None


# ---------------------------------------------------------------------------
# build_evidence_block / section-specific helpers
# ---------------------------------------------------------------------------

class TestBuildEvidenceBlock:
    def test_returns_evidence_block(self):
        block = build_evidence_block(EvidenceSection.FACT, "Senator held shares.")
        assert isinstance(block, EvidenceBlock)

    def test_section_and_text_set(self):
        block = build_evidence_block(EvidenceSection.INFERENCE, "This suggests overlap.")
        assert block.section == EvidenceSection.INFERENCE
        assert block.text == "This suggests overlap."

    def test_fact_block_helper(self):
        block = build_fact_block("Disclosed holding: ACME Corp, $15k–$50k.")
        assert block.section == EvidenceSection.FACT
        assert "ACME" in block.text

    def test_inference_block_helper(self):
        block = build_inference_block("Timing suggests awareness of committee action.")
        assert block.section == EvidenceSection.INFERENCE

    def test_normative_block_helper(self):
        block = build_normative_block("Pattern raises conflict-of-interest concern.")
        assert block.section == EvidenceSection.NORMATIVE_JUDGMENT


# ---------------------------------------------------------------------------
# assemble_blocks
# ---------------------------------------------------------------------------

class TestAssembleBlocks:
    def test_empty_all_sections(self):
        assert assemble_blocks([], [], []) == []

    def test_ordering_fact_then_inference_then_normative(self):
        blocks = assemble_blocks(
            ["Fact text."],
            ["Inference text."],
            ["Normative text."],
        )
        assert len(blocks) == 3
        assert blocks[0].section == EvidenceSection.FACT
        assert blocks[1].section == EvidenceSection.INFERENCE
        assert blocks[2].section == EvidenceSection.NORMATIVE_JUDGMENT

    def test_multiple_texts_per_section(self):
        blocks = assemble_blocks(
            ["Fact A.", "Fact B."],
            ["Inference A."],
            [],
        )
        assert len(blocks) == 3
        assert blocks[0].text == "Fact A."
        assert blocks[1].text == "Fact B."
        assert blocks[2].text == "Inference A."

    def test_empty_strings_are_skipped(self):
        blocks = assemble_blocks(["", "Real fact."], [""], [])
        assert len(blocks) == 1
        assert blocks[0].text == "Real fact."

    def test_only_normative(self):
        blocks = assemble_blocks([], [], ["This is a normative judgment."])
        assert len(blocks) == 1
        assert blocks[0].section == EvidenceSection.NORMATIVE_JUDGMENT

    def test_section_ordering_preserved_across_multiple(self):
        blocks = assemble_blocks(
            ["F1", "F2"],
            ["I1"],
            ["N1", "N2"],
        )
        sections = [b.section for b in blocks]
        assert sections == [
            EvidenceSection.FACT,
            EvidenceSection.FACT,
            EvidenceSection.INFERENCE,
            EvidenceSection.NORMATIVE_JUDGMENT,
            EvidenceSection.NORMATIVE_JUDGMENT,
        ]


# ---------------------------------------------------------------------------
# build_evidence_card_payload
# ---------------------------------------------------------------------------

class TestBuildEvidenceCardPayload:
    def _build(self, **overrides: object) -> EvidenceCardPayload:
        defaults: dict = {
            "rule_fire": _make_rule_fire(),
            "member": MEMBER,
            "source_anchors": [_make_anchor(1)],
            "fact_texts": ["Senator held $15k–$50k in ACME Corp."],
            "inference_texts": ["Trade occurred 5 days after committee hearing."],
            "normative_texts": ["Pattern is consistent with committee-linked trading risk."],
            "evidence_card_id": "ec-001",
            "score_delta": -2.5,
            "confidence": ConfidenceLabel.HIGH,
            "snapshot_date": SNAPSHOT_DATE,
            "created_at": CREATED_AT,
        }
        defaults.update(overrides)
        return build_evidence_card_payload(**defaults)

    def test_returns_evidence_card_payload(self):
        payload = self._build()
        assert isinstance(payload, EvidenceCardPayload)

    def test_member_fields_mapped_from_full_name(self):
        payload = self._build()
        assert payload.member_bioguide_id == "A000001"
        assert payload.member_name == "Jane Smith"
        assert payload.member_slug == "jane-smith-a000001"

    def test_member_name_falls_back_to_name_key(self):
        payload = self._build(member=MEMBER_NAME_ONLY)
        assert payload.member_name == "John Doe"
        assert payload.member_bioguide_id == "B000002"

    def test_rule_fire_fields_propagated(self):
        payload = self._build()
        assert payload.rule_id == "conflict_of_interest_risk.committee_sector_trade.v1"
        assert payload.rule_version == 1
        assert payload.dimension == "conflict_of_interest_risk"
        assert "30 days" in payload.short_explanation

    def test_score_delta_preserved(self):
        payload = self._build(score_delta=-5.0)
        assert payload.score_delta == -5.0

    def test_positive_score_delta_allowed(self):
        payload = self._build(score_delta=1.0)
        assert payload.score_delta == 1.0

    def test_confidence_label_set(self):
        payload = self._build(confidence=ConfidenceLabel.MEDIUM)
        assert payload.confidence == ConfidenceLabel.MEDIUM

    def test_snapshot_date_preserved(self):
        payload = self._build()
        assert payload.snapshot_date == SNAPSHOT_DATE

    def test_created_at_preserved(self):
        payload = self._build()
        assert payload.created_at == CREATED_AT

    def test_created_at_defaults_to_now_when_omitted(self):
        before = dt.datetime.now(dt.UTC)
        payload = build_evidence_card_payload(
            rule_fire=_make_rule_fire(),
            member=MEMBER,
            source_anchors=[],
            fact_texts=["A fact."],
            inference_texts=[],
            normative_texts=[],
            evidence_card_id="ec-default-ts",
            score_delta=0.0,
            confidence=ConfidenceLabel.LOW,
            snapshot_date=SNAPSHOT_DATE,
            # created_at intentionally omitted
        )
        after = dt.datetime.now(dt.UTC)
        assert before <= payload.created_at <= after

    def test_blocks_assembled_in_order(self):
        payload = self._build(
            fact_texts=["Fact one.", "Fact two."],
            inference_texts=["Inference one."],
            normative_texts=["Normative one."],
        )
        sections = [b.section for b in payload.blocks]
        assert sections[0] == EvidenceSection.FACT
        assert sections[1] == EvidenceSection.FACT
        assert sections[2] == EvidenceSection.INFERENCE
        assert sections[3] == EvidenceSection.NORMATIVE_JUDGMENT

    def test_source_anchors_preserved(self):
        anchors = [_make_anchor(1), _make_anchor(2)]
        payload = self._build(source_anchors=anchors)
        assert len(payload.source_anchors) == 2
        assert payload.source_anchors[0].source_id == "fd-1"
        assert payload.source_anchors[1].source_id == "fd-2"

    def test_empty_source_anchors_allowed(self):
        payload = self._build(source_anchors=[])
        assert payload.source_anchors == []

    def test_no_blocks_when_all_sections_empty(self):
        payload = self._build(
            fact_texts=[],
            inference_texts=[],
            normative_texts=[],
            score_delta=0.0,
        )
        assert payload.blocks == []

    def test_nonzero_score_delta_requires_fact_block(self):
        with pytest.raises(ValueError, match="fact block"):
            self._build(
                fact_texts=[],
                inference_texts=["Inference only."],
                normative_texts=["Normative only."],
            )

    def test_zero_score_delta_allows_no_fact_block(self):
        payload = self._build(
            fact_texts=[],
            inference_texts=["Inference only."],
            normative_texts=["Normative only."],
            score_delta=0.0,
        )
        assert [block.section for block in payload.blocks] == [
            EvidenceSection.INFERENCE,
            EvidenceSection.NORMATIVE_JUDGMENT,
        ]

    def test_evidence_card_id_set(self):
        payload = self._build(evidence_card_id="ec-unique-99")
        assert payload.evidence_card_id == "ec-unique-99"

    def test_different_rule_fires_produce_different_rule_ids(self):
        fire_a = _make_rule_fire(rule_id="coi.committee_sector_trade.v1")
        fire_b = _make_rule_fire(rule_id="coi.late_or_amended_disclosure.v1")
        payload_a = self._build(rule_fire=fire_a)
        payload_b = self._build(rule_fire=fire_b)
        assert payload_a.rule_id != payload_b.rule_id

    def test_superseded_filing_in_rule_fire_not_surfaced_on_payload(self):
        # EvidenceCardPayload has no superseded_filing field; confirm no error.
        fire = _make_rule_fire(superseded_filing_id="fd-old-42")
        payload = self._build(rule_fire=fire)
        assert not hasattr(payload, "superseded_filing_id")
