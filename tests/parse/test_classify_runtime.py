"""Tests for classify_runtime.  No network calls; extractor is always injected."""
from __future__ import annotations

from src.parse.disclosures.classify import PdfKind, ReviewTrigger
from src.parse.disclosures.classify_runtime import (
    build_heuristic_input,
    classify_artifact_bytes,
)
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.text_extract import TextMetrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _metrics(
    *,
    total_pages: int = 10,
    text_pages: int = 10,
    avg_chars: float = 500.0,
    headers: frozenset[str] = frozenset({"schedule a"}),
) -> TextMetrics:
    return TextMetrics(
        total_pages=total_pages,
        text_pages=text_pages,
        avg_chars_per_text_page=avg_chars,
        detected_headers=headers,
    )


def _extractor_for(metrics: TextMetrics):
    """Return a fake extractor that ignores bytes and yields fixed metrics."""

    def extractor(data: bytes) -> TextMetrics:
        return metrics

    return extractor


# ---------------------------------------------------------------------------
# build_heuristic_input
# ---------------------------------------------------------------------------


class TestBuildHeuristicInput:
    def test_page_counts_pass_through(self) -> None:
        m = _metrics(total_pages=8, text_pages=6, avg_chars=300.0)
        inp = build_heuristic_input(m, Chamber.SENATE)
        assert inp.total_pages == 8
        assert inp.text_pages == 6
        assert inp.avg_chars_per_text_page == 300.0

    def test_detected_headers_pass_through(self) -> None:
        m = _metrics(headers=frozenset({"schedule a", "schedule b"}))
        inp = build_heuristic_input(m, Chamber.SENATE)
        assert "schedule a" in inp.detected_headers
        assert "schedule b" in inp.detected_headers

    def test_chamber_pass_through(self) -> None:
        inp = build_heuristic_input(_metrics(), Chamber.HOUSE)
        assert inp.chamber == Chamber.HOUSE

    def test_default_flags_are_false(self) -> None:
        inp = build_heuristic_input(_metrics(), Chamber.SENATE)
        assert inp.is_ptr is False
        assert inp.is_amendment is False
        assert inp.has_non_standard_amounts is False
        assert inp.has_impossible_dates is False
        assert inp.has_duplicate_header_rows is False
        assert inp.has_unresolved_options_or_trusts is False
        assert inp.member_name_match_score is None

    def test_flags_forwarded(self) -> None:
        inp = build_heuristic_input(
            _metrics(),
            Chamber.HOUSE,
            is_ptr=True,
            is_amendment=True,
            has_non_standard_amounts=True,
            has_impossible_dates=True,
            has_duplicate_header_rows=True,
            has_unresolved_options_or_trusts=True,
            member_name_match_score=0.6,
        )
        assert inp.is_ptr is True
        assert inp.is_amendment is True
        assert inp.has_non_standard_amounts is True
        assert inp.has_impossible_dates is True
        assert inp.has_duplicate_header_rows is True
        assert inp.has_unresolved_options_or_trusts is True
        assert inp.member_name_match_score == 0.6

    def test_zero_page_metrics(self) -> None:
        m = _metrics(total_pages=0, text_pages=0, avg_chars=0.0, headers=frozenset())
        inp = build_heuristic_input(m, Chamber.SENATE)
        assert inp.total_pages == 0
        assert inp.text_pages == 0
        assert inp.avg_chars_per_text_page == 0.0


# ---------------------------------------------------------------------------
# classify_artifact_bytes
# ---------------------------------------------------------------------------


class TestClassifyArtifactBytes:
    def test_text_senate_filing_classifies_as_text(self) -> None:
        m = _metrics(
            total_pages=10,
            text_pages=10,
            avg_chars=500.0,
            headers=frozenset({"schedule a", "part ii"}),
        )
        result = classify_artifact_bytes(b"fake", Chamber.SENATE, extractor=_extractor_for(m))
        assert result.pdf_kind == PdfKind.TEXT
        assert not result.needs_ocr
        assert not result.needs_review

    def test_image_filing_classifies_as_image(self) -> None:
        m = _metrics(
            total_pages=5,
            text_pages=0,
            avg_chars=0.0,
            headers=frozenset(),
        )
        result = classify_artifact_bytes(b"fake", Chamber.HOUSE, extractor=_extractor_for(m))
        assert result.pdf_kind == PdfKind.IMAGE
        assert result.needs_ocr

    def test_amendment_flag_triggers_review(self) -> None:
        m = _metrics(headers=frozenset({"schedule a"}))
        result = classify_artifact_bytes(
            b"fake", Chamber.SENATE, extractor=_extractor_for(m), is_amendment=True
        )
        assert ReviewTrigger.AMENDMENT_FILING in result.review_triggers
        assert result.needs_review

    def test_member_identity_mismatch_triggers_review(self) -> None:
        m = _metrics(headers=frozenset({"schedule a"}))
        result = classify_artifact_bytes(
            b"fake",
            Chamber.SENATE,
            extractor=_extractor_for(m),
            member_name_match_score=0.4,
        )
        assert ReviewTrigger.MEMBER_IDENTITY_MISMATCH in result.review_triggers

    def test_house_stricter_threshold(self) -> None:
        # 8 out of 10 text pages = 80 %, which passes Senate (80 %) but fails House (90 %).
        m_80 = _metrics(
            total_pages=10,
            text_pages=8,
            avg_chars=300.0,
            headers=frozenset({"part i"}),
        )
        senate_result = classify_artifact_bytes(b"fake", Chamber.SENATE, extractor=_extractor_for(m_80))
        assert senate_result.pdf_kind == PdfKind.TEXT

        house_result = classify_artifact_bytes(b"fake", Chamber.HOUSE, extractor=_extractor_for(m_80))
        assert house_result.pdf_kind == PdfKind.MIXED

    def test_extractor_receives_the_original_bytes(self) -> None:
        captured: list[bytes] = []

        def capturing_extractor(data: bytes) -> TextMetrics:
            captured.append(data)
            return _metrics()

        classify_artifact_bytes(b"sentinel", Chamber.SENATE, extractor=capturing_extractor)
        assert captured == [b"sentinel"]

    def test_ptr_header_check(self) -> None:
        m = _metrics(
            total_pages=5,
            text_pages=5,
            avg_chars=400.0,
            headers=frozenset({"transaction", "owner", "amount"}),
        )
        result = classify_artifact_bytes(
            b"fake", Chamber.SENATE, extractor=_extractor_for(m), is_ptr=True
        )
        assert ReviewTrigger.MISSING_SECTION_HEADERS not in result.review_triggers
