"""Tests for disclosure classification and normalization."""

from __future__ import annotations

from decimal import Decimal

from src.parse.disclosures.classify import (
    PdfHeuristicInput,
    PdfKind,
    ReviewTrigger,
    classify_pdf,
)
from src.parse.disclosures.models import (
    Chamber,
    OwnerType,
    TransactionType,
)
from src.parse.disclosures.normalize import (
    clean_asset_name,
    normalize_amount_range,
    normalize_owner_label,
    normalize_tx_type,
)

# --- classify_pdf ---


class TestClassifyPdfKind:
    def test_text_senate_filing(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=10,
            avg_chars_per_text_page=500.0,
            detected_headers=frozenset({"Schedule A", "Part II"}),
            chamber=Chamber.SENATE,
        )
        result = classify_pdf(inp)
        assert result.pdf_kind == PdfKind.TEXT
        assert not result.needs_ocr

    def test_image_filing(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=5,
            text_pages=0,
            avg_chars_per_text_page=0.0,
            detected_headers=frozenset(),
            chamber=Chamber.HOUSE,
        )
        result = classify_pdf(inp)
        assert result.pdf_kind == PdfKind.IMAGE
        assert result.needs_ocr

    def test_mixed_filing(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=5,
            avg_chars_per_text_page=300.0,
            detected_headers=frozenset({"Schedule A"}),
            chamber=Chamber.SENATE,
        )
        result = classify_pdf(inp)
        assert result.pdf_kind == PdfKind.MIXED
        assert result.needs_ocr

    def test_house_stricter_threshold(self) -> None:
        """House uses 90% text threshold vs Senate's 80%."""
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=9,
            avg_chars_per_text_page=300.0,
            detected_headers=frozenset({"Part I"}),
            chamber=Chamber.HOUSE,
        )
        # 9/10 = 0.9 is at threshold for house
        result = classify_pdf(inp)
        assert result.pdf_kind == PdfKind.TEXT

        # 8/10 = 0.8 is below house threshold
        inp2 = PdfHeuristicInput(
            total_pages=10,
            text_pages=8,
            avg_chars_per_text_page=300.0,
            detected_headers=frozenset({"Part I"}),
            chamber=Chamber.HOUSE,
        )
        result2 = classify_pdf(inp2)
        assert result2.pdf_kind == PdfKind.MIXED


class TestClassifyReviewTriggers:
    def test_amendment_triggers_review(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=10,
            avg_chars_per_text_page=500.0,
            detected_headers=frozenset({"Schedule A"}),
            chamber=Chamber.SENATE,
            is_amendment=True,
        )
        result = classify_pdf(inp)
        assert ReviewTrigger.AMENDMENT_FILING in result.review_triggers
        assert result.needs_review

    def test_member_identity_mismatch(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=10,
            avg_chars_per_text_page=500.0,
            detected_headers=frozenset({"Schedule A"}),
            chamber=Chamber.SENATE,
            member_name_match_score=0.5,
        )
        result = classify_pdf(inp)
        assert ReviewTrigger.MEMBER_IDENTITY_MISMATCH in result.review_triggers

    def test_clean_filing_no_triggers(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=10,
            avg_chars_per_text_page=500.0,
            detected_headers=frozenset({"Schedule A", "Part II"}),
            chamber=Chamber.SENATE,
        )
        result = classify_pdf(inp)
        assert result.review_triggers == ()
        assert not result.needs_review

    def test_missing_headers_triggers_review(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=10,
            text_pages=10,
            avg_chars_per_text_page=500.0,
            detected_headers=frozenset({"Something Else"}),
            chamber=Chamber.SENATE,
        )
        result = classify_pdf(inp)
        assert ReviewTrigger.MISSING_SECTION_HEADERS in result.review_triggers

    def test_ptr_header_check(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=2,
            text_pages=2,
            avg_chars_per_text_page=400.0,
            detected_headers=frozenset({"Transaction", "Amount"}),
            chamber=Chamber.SENATE,
            is_ptr=True,
        )
        result = classify_pdf(inp)
        assert ReviewTrigger.MISSING_SECTION_HEADERS not in result.review_triggers

    def test_multiple_triggers(self) -> None:
        inp = PdfHeuristicInput(
            total_pages=5,
            text_pages=0,
            avg_chars_per_text_page=0.0,
            detected_headers=frozenset(),
            chamber=Chamber.HOUSE,
            is_amendment=True,
            has_non_standard_amounts=True,
            has_impossible_dates=True,
        )
        result = classify_pdf(inp)
        assert ReviewTrigger.LOW_TEXT_RATIO in result.review_triggers
        assert ReviewTrigger.AMENDMENT_FILING in result.review_triggers
        assert ReviewTrigger.NON_STANDARD_AMOUNTS in result.review_triggers
        assert ReviewTrigger.IMPOSSIBLE_DATE in result.review_triggers
        assert ReviewTrigger.MISSING_SECTION_HEADERS in result.review_triggers


# --- normalize_amount_range ---


class TestNormalizeAmountRange:
    def test_standard_range(self) -> None:
        result = normalize_amount_range("$1,001 - $15,000")
        assert result == (Decimal("1001"), Decimal("15000"))

    def test_whitespace_insensitive(self) -> None:
        result = normalize_amount_range("  $1,001  -  $15,000  ")
        assert result == (Decimal("1001"), Decimal("15000"))

    def test_case_insensitive(self) -> None:
        result = normalize_amount_range("over $50,000,000")
        assert result == (Decimal("50000001"), Decimal("50000001"))

    def test_unrecognized_returns_none(self) -> None:
        assert normalize_amount_range("unknown range") is None


# --- normalize_tx_type ---


class TestNormalizeTxType:
    def test_purchase_variants(self) -> None:
        assert normalize_tx_type("P") == TransactionType.PURCHASE
        assert normalize_tx_type("purchase") == TransactionType.PURCHASE
        assert normalize_tx_type("Buy") == TransactionType.PURCHASE

    def test_sale_variants(self) -> None:
        assert normalize_tx_type("S") == TransactionType.SALE
        assert normalize_tx_type("Sale (Full)") == TransactionType.SALE
        assert normalize_tx_type("Sale (Partial)") == TransactionType.SALE

    def test_unknown_falls_back(self) -> None:
        assert normalize_tx_type("something weird") == TransactionType.OTHER


# --- normalize_owner_label ---


class TestNormalizeOwnerLabel:
    def test_spouse_variants(self) -> None:
        assert normalize_owner_label("SP") == OwnerType.SPOUSE
        assert normalize_owner_label("Spouse") == OwnerType.SPOUSE

    def test_dependent_variants(self) -> None:
        assert normalize_owner_label("DC") == OwnerType.DEPENDENT
        assert normalize_owner_label("Dep. Child") == OwnerType.DEPENDENT

    def test_unknown_falls_back(self) -> None:
        assert normalize_owner_label("mystery") == OwnerType.OTHER


# --- clean_asset_name ---


class TestCleanAssetName:
    def test_strips_filing_id(self) -> None:
        result = clean_asset_name("Apple Inc (Filing ID: 12345)")
        assert result == "Apple Inc"

    def test_strips_brackets(self) -> None:
        result = clean_asset_name("Microsoft Corp [Common Stock]")
        assert result == "Microsoft Corp"

    def test_collapses_whitespace(self) -> None:
        result = clean_asset_name("  Google   LLC  ")
        assert result == "Google LLC"

    def test_empty_fallback(self) -> None:
        # When cleanup produces empty string, fallback returns stripped original.
        result = clean_asset_name("  [removed]  ")
        assert result == "[removed]"
