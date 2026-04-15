"""Tests for src/query/evidence_card.py.

Pure unit tests — no DB, no network, no I/O.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceCardPayload,
    EvidenceSection,
)
from src.query.evidence_card import (
    _blocks_from_columns,
    _created_at,
    _parse_anchors,
    _section_blocks,
    _snapshot_date,
    assemble_evidence_card,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RENDERED_AT = dt.datetime(2024, 3, 15, 12, 0, tzinfo=dt.timezone.utc)
_CREATED_AT = dt.datetime(2024, 3, 14, 9, 0, tzinfo=dt.timezone.utc)

# Minimal complete row; tests override individual fields as needed.
BASE_ROW: dict = {
    "public_id": "ec_abc123",
    "member_bioguide_id": "A000001",
    "member_full_name": "Jane Doe",
    "member_slug": "jane-doe",
    "dimension": "conflict_of_interest_risk",
    "rule_id": "committee_sector_trade.v1",
    "rule_version": "1",
    "score_delta": -10.0,
    "short_explanation": "Sold tech stock while on tech committee.",
    "facts": {"overlap": "Served on Science Committee during trade window."},
    "inferences": {"risk": "Trade timing suggests awareness of committee activity."},
    "normative_judgments": {"concern": "Late disclosure reduces transparency."},
    "source_anchors": [
        {
            "source_type": "financial_disclosure",
            "source_id": "fd_001",
            "url": "https://example.gov/fd/001",
            "label": "PTR filed 2024-01-15",
        }
    ],
    "confidence_label": "HIGH",
    "rendered_at": _RENDERED_AT,
    "created_at": _CREATED_AT,
}


# ---------------------------------------------------------------------------
# _section_blocks
# ---------------------------------------------------------------------------


class TestSectionBlocks:
    def test_dict_produces_one_block_per_entry(self):
        data = {"a": "text a", "b": "text b"}
        result = _section_blocks(data, EvidenceSection.FACT)
        assert len(result) == 2

    def test_dict_keys_sorted_for_stability(self):
        data = {"zzz": "last", "aaa": "first"}
        result = _section_blocks(data, EvidenceSection.FACT)
        assert result[0].text == "first"
        assert result[1].text == "last"

    def test_dict_section_set_on_all_blocks(self):
        data = {"k": "text"}
        result = _section_blocks(data, EvidenceSection.INFERENCE)
        assert all(b.section == EvidenceSection.INFERENCE for b in result)

    def test_list_preserves_order(self):
        data = ["first", "second", "third"]
        result = _section_blocks(data, EvidenceSection.FACT)
        assert [b.text for b in result] == ["first", "second", "third"]

    def test_list_section_set_on_all_blocks(self):
        result = _section_blocks(["t"], EvidenceSection.NORMATIVE_JUDGMENT)
        assert result[0].section == EvidenceSection.NORMATIVE_JUDGMENT

    def test_empty_dict_returns_empty(self):
        assert _section_blocks({}, EvidenceSection.FACT) == []

    def test_empty_list_returns_empty(self):
        assert _section_blocks([], EvidenceSection.FACT) == []

    def test_none_returns_empty(self):
        assert _section_blocks(None, EvidenceSection.FACT) == []

    def test_empty_string_values_skipped_in_dict(self):
        data = {"a": "real text", "b": ""}
        result = _section_blocks(data, EvidenceSection.FACT)
        assert len(result) == 1
        assert result[0].text == "real text"

    def test_empty_string_values_skipped_in_list(self):
        result = _section_blocks(["ok", "", "also ok"], EvidenceSection.FACT)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _blocks_from_columns
# ---------------------------------------------------------------------------


class TestBlocksFromColumns:
    def test_section_order_is_fact_inference_normative(self):
        result = _blocks_from_columns(
            {"f": "fact text"},
            {"i": "inference text"},
            {"n": "normative text"},
        )
        sections = [b.section for b in result]
        assert sections == [
            EvidenceSection.FACT,
            EvidenceSection.INFERENCE,
            EvidenceSection.NORMATIVE_JUDGMENT,
        ]

    def test_empty_sections_skipped(self):
        result = _blocks_from_columns({"f": "fact text"}, None, None)
        assert len(result) == 1
        assert result[0].section == EvidenceSection.FACT

    def test_all_empty_returns_empty(self):
        assert _blocks_from_columns(None, None, None) == []

    def test_multiple_entries_per_section(self):
        result = _blocks_from_columns(
            {"a": "fact 1", "b": "fact 2"},
            {"c": "inference"},
            {},
        )
        assert len(result) == 3
        assert result[0].section == EvidenceSection.FACT
        assert result[1].section == EvidenceSection.FACT
        assert result[2].section == EvidenceSection.INFERENCE

    def test_list_format_for_facts(self):
        result = _blocks_from_columns(["fact a", "fact b"], None, None)
        texts = [b.text for b in result]
        assert texts == ["fact a", "fact b"]

    def test_normative_only(self):
        result = _blocks_from_columns(None, None, {"j": "judgment"})
        assert len(result) == 1
        assert result[0].section == EvidenceSection.NORMATIVE_JUDGMENT


# ---------------------------------------------------------------------------
# _parse_anchors
# ---------------------------------------------------------------------------


class TestParseAnchors:
    def test_parses_all_fields(self):
        raw = [
            {
                "source_type": "financial_disclosure",
                "source_id": "fd_001",
                "url": "https://example.gov/fd/001",
                "label": "PTR 2024-01-15",
            }
        ]
        result = _parse_anchors(raw)
        assert len(result) == 1
        assert result[0].source_type == "financial_disclosure"
        assert result[0].source_id == "fd_001"
        assert result[0].url == "https://example.gov/fd/001"
        assert result[0].label == "PTR 2024-01-15"

    def test_url_optional(self):
        raw = [{"source_type": "fec_contribution", "source_id": "c1", "label": "FEC filing"}]
        result = _parse_anchors(raw)
        assert result[0].url is None

    def test_none_returns_empty(self):
        assert _parse_anchors(None) == []

    def test_empty_list_returns_empty(self):
        assert _parse_anchors([]) == []

    def test_multiple_anchors_preserved_in_order(self):
        raw = [
            {"source_type": "a", "source_id": "1", "label": "first"},
            {"source_type": "b", "source_id": "2", "label": "second"},
        ]
        result = _parse_anchors(raw)
        assert [a.label for a in result] == ["first", "second"]


# ---------------------------------------------------------------------------
# _snapshot_date
# ---------------------------------------------------------------------------


class TestSnapshotDate:
    def test_datetime_coerced_to_date(self):
        row = {"rendered_at": dt.datetime(2024, 5, 10, 8, 30)}
        assert _snapshot_date(row) == dt.date(2024, 5, 10)

    def test_date_returned_unchanged(self):
        row = {"rendered_at": dt.date(2024, 5, 10)}
        assert _snapshot_date(row) == dt.date(2024, 5, 10)

    def test_missing_rendered_at_returns_today(self):
        result = _snapshot_date({})
        assert result == dt.date.today()

    def test_none_rendered_at_returns_today(self):
        result = _snapshot_date({"rendered_at": None})
        assert result == dt.date.today()

    def test_timezone_aware_datetime(self):
        row = {"rendered_at": dt.datetime(2024, 5, 10, 8, 30, tzinfo=dt.timezone.utc)}
        assert _snapshot_date(row) == dt.date(2024, 5, 10)


# ---------------------------------------------------------------------------
# _created_at
# ---------------------------------------------------------------------------


class TestCreatedAt:
    def test_prefers_created_at_over_rendered_at(self):
        row = {
            "created_at": dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            "rendered_at": dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        }
        assert _created_at(row) == dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)

    def test_falls_back_to_rendered_at(self):
        row = {"rendered_at": dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc)}
        assert _created_at(row) == dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc)

    def test_falls_back_to_now_when_both_missing(self):
        before = dt.datetime.now(dt.timezone.utc)
        result = _created_at({})
        after = dt.datetime.now(dt.timezone.utc)
        assert before <= result <= after

    def test_none_created_at_falls_back_to_rendered_at(self):
        row = {
            "created_at": None,
            "rendered_at": dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc),
        }
        assert _created_at(row) == dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# assemble_evidence_card — integration
# ---------------------------------------------------------------------------


class TestAssembleEvidenceCard:
    def test_returns_evidence_card_payload(self):
        result = assemble_evidence_card(BASE_ROW)
        assert isinstance(result, EvidenceCardPayload)

    def test_evidence_card_id_from_public_id(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.evidence_card_id == "ec_abc123"

    def test_member_bioguide_id(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.member_bioguide_id == "A000001"

    def test_member_name_from_member_full_name(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.member_name == "Jane Doe"

    def test_member_name_falls_back_to_full_name_key(self):
        row = {**BASE_ROW, "member_full_name": None, "full_name": "John Smith"}
        result = assemble_evidence_card(row)
        assert result.member_name == "John Smith"

    def test_member_slug(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.member_slug == "jane-doe"

    def test_dimension(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.dimension == "conflict_of_interest_risk"

    def test_rule_id(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.rule_id == "committee_sector_trade.v1"

    def test_rule_version_coerced_to_int(self):
        row = {**BASE_ROW, "rule_version": "3"}
        result = assemble_evidence_card(row)
        assert result.rule_version == 3
        assert isinstance(result.rule_version, int)

    def test_rule_version_int_passthrough(self):
        row = {**BASE_ROW, "rule_version": 2}
        result = assemble_evidence_card(row)
        assert result.rule_version == 2

    def test_score_delta_is_float(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.score_delta == -10.0
        assert isinstance(result.score_delta, float)

    def test_short_explanation(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.short_explanation == "Sold tech stock while on tech committee."

    def test_blocks_section_order(self):
        result = assemble_evidence_card(BASE_ROW)
        sections = [b.section for b in result.blocks]
        assert sections == [
            EvidenceSection.FACT,
            EvidenceSection.INFERENCE,
            EvidenceSection.NORMATIVE_JUDGMENT,
        ]

    def test_blocks_content(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.blocks[0].text == "Served on Science Committee during trade window."
        assert result.blocks[1].text == "Trade timing suggests awareness of committee activity."
        assert result.blocks[2].text == "Late disclosure reduces transparency."

    def test_empty_json_columns_produce_no_blocks(self):
        row = {**BASE_ROW, "facts": {}, "inferences": {}, "normative_judgments": {}}
        result = assemble_evidence_card(row)
        assert result.blocks == []

    def test_none_json_columns_produce_no_blocks(self):
        row = {**BASE_ROW, "facts": None, "inferences": None, "normative_judgments": None}
        result = assemble_evidence_card(row)
        assert result.blocks == []

    def test_source_anchors_parsed(self):
        result = assemble_evidence_card(BASE_ROW)
        assert len(result.source_anchors) == 1
        assert result.source_anchors[0].source_id == "fd_001"

    def test_empty_source_anchors(self):
        row = {**BASE_ROW, "source_anchors": []}
        result = assemble_evidence_card(row)
        assert result.source_anchors == []

    def test_confidence_high(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.confidence == ConfidenceLabel.HIGH

    def test_confidence_medium(self):
        row = {**BASE_ROW, "confidence_label": "MEDIUM"}
        result = assemble_evidence_card(row)
        assert result.confidence == ConfidenceLabel.MEDIUM

    def test_confidence_low(self):
        row = {**BASE_ROW, "confidence_label": "LOW"}
        result = assemble_evidence_card(row)
        assert result.confidence == ConfidenceLabel.LOW

    def test_snapshot_date_from_rendered_at(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.snapshot_date == dt.date(2024, 3, 15)

    def test_created_at_from_created_at_column(self):
        result = assemble_evidence_card(BASE_ROW)
        assert result.created_at == _CREATED_AT

    def test_blocks_dict_sorted_within_section(self):
        row = {
            **BASE_ROW,
            "facts": {"z_label": "z text", "a_label": "a text"},
            "inferences": {},
            "normative_judgments": {},
        }
        result = assemble_evidence_card(row)
        fact_texts = [b.text for b in result.blocks if b.section == EvidenceSection.FACT]
        assert fact_texts == ["a text", "z text"]

    def test_missing_public_id_raises(self):
        row = {k: v for k, v in BASE_ROW.items() if k != "public_id"}
        with pytest.raises(KeyError):
            assemble_evidence_card(row)

    def test_missing_member_bioguide_id_raises(self):
        row = {k: v for k, v in BASE_ROW.items() if k != "member_bioguide_id"}
        with pytest.raises(KeyError):
            assemble_evidence_card(row)
