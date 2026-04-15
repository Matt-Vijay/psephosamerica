"""Tests for classify_runtime.  No network calls; extractor is always injected.

Covers:
  - build_heuristic_input field pass-through
  - classify_artifact_bytes via injected extractor
  - classify_from_pages with inline multi-page fixtures covering:
      * mixed OCR/text documents
      * repeated header detection → DUPLICATE_HEADER_ROWS trigger
      * sparse-text pages flowing into the classifier
      * non-disclosure noise pages ignored in text count
      * PTR vs annual header routing
      * amendment, mismatch, and other flag forwarding
"""

from __future__ import annotations

from src.parse.disclosures.classify import PdfKind, ReviewTrigger
from src.parse.disclosures.classify_runtime import (
    build_heuristic_input,
    classify_artifact_bytes,
    classify_from_pages,
)
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.text_document import PageText
from src.parse.disclosures.text_extract import TextMetrics


# ---------------------------------------------------------------------------
# Inline multi-page text fixtures
# ---------------------------------------------------------------------------

# Rich text — clearly above the MIN_TEXT_PAGE_CHARS threshold.
_HOLDING_PAGE = (
    "Schedule A - Public Investments\n"
    "Asset: Apple Inc. (AAPL)\n"
    "Owner: Self\n"
    "Value: $1,000,001 - $5,000,000\n"
    "Type: Stock\n"
    "Year-End Value: Over $1,000,001\n"
    "Comment: Held in managed account\n"
    "Filing Year: 2023\n"
    "Member: Jane Smith\n"
)

_TRANSACTION_PAGE = (
    "Transaction\n"
    "Date: 03/15/2023\n"
    "Owner: Self\n"
    "Asset: Microsoft Corp (MSFT)\n"
    "Transaction Type: Purchase\n"
    "Amount: $15,001 - $50,000\n"
    "Filing Status: New\n"
)

# Image-like page: barely any text, well below OCR_CANDIDATE boundary.
_IMAGE_PAGE = "x" * 10

# Sparse page: alphabetic but below MIN_TEXT_PAGE_CHARS.
_SPARSE_PAGE = "Schedule B\nNone reported\n" + "y" * 40

# Pure noise: whitespace and digits only.
_NOISE_PAGE = "   \n2023\n1\n   "

# Annual headers repeated on every page (triggers duplicate-header detection).
_REPEATED_SCHEDULE_A = "schedule a\n" + _HOLDING_PAGE


def _make_good_pages(n: int, start: int = 1) -> list[PageText]:
    """Return n rich text pages starting at page_number=start."""
    return [PageText(page_number=start + i, text=_HOLDING_PAGE) for i in range(n)]


# ---------------------------------------------------------------------------
# Shared TextMetrics helpers (for classify_artifact_bytes tests)
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


# ---------------------------------------------------------------------------
# classify_from_pages
# ---------------------------------------------------------------------------


class TestClassifyFromPages:
    # --- Basic classification ---

    def test_all_text_senate_filing_is_text(self) -> None:
        """10 rich text pages → TEXT, no OCR needed, no review (no flags)."""
        pages = _make_good_pages(10)
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.TEXT
        assert not result.needs_ocr

    def test_all_image_pages_is_image(self) -> None:
        """10 near-blank pages → IMAGE, OCR needed."""
        pages = [PageText(page_number=i, text=_IMAGE_PAGE) for i in range(1, 11)]
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.IMAGE
        assert result.needs_ocr

    def test_empty_page_list_is_image(self) -> None:
        """No pages → IMAGE (no text ratio)."""
        result = classify_from_pages([], Chamber.SENATE)
        assert result.pdf_kind == PdfKind.IMAGE

    # --- Mixed OCR / text documents ---

    def test_mixed_ocr_text_senate_80pct_is_text(self) -> None:
        """8 text pages + 2 image-like pages = 80 % → passes Senate threshold."""
        pages = _make_good_pages(8) + [
            PageText(page_number=9, text=_IMAGE_PAGE),
            PageText(page_number=10, text=_IMAGE_PAGE),
        ]
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.TEXT

    def test_mixed_ocr_text_house_80pct_is_mixed(self) -> None:
        """8 text pages + 2 image-like pages = 80 % → fails House 90 % threshold → MIXED."""
        pages = _make_good_pages(8) + [
            PageText(page_number=9, text=_IMAGE_PAGE),
            PageText(page_number=10, text=_IMAGE_PAGE),
        ]
        result = classify_from_pages(pages, Chamber.HOUSE)
        assert result.pdf_kind == PdfKind.MIXED
        assert result.needs_ocr
        assert ReviewTrigger.LOW_TEXT_RATIO in result.review_triggers

    def test_half_image_half_text_is_mixed(self) -> None:
        """5 text + 5 image → MIXED regardless of chamber."""
        pages = _make_good_pages(5) + [
            PageText(page_number=5 + i, text=_IMAGE_PAGE) for i in range(1, 6)
        ]
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.MIXED

    def test_noise_pages_do_not_count_as_text_pages(self) -> None:
        """Noise pages (blank/digits) do not inflate the text page count."""
        pages = _make_good_pages(7) + [
            PageText(page_number=8, text=_NOISE_PAGE),
            PageText(page_number=9, text=_NOISE_PAGE),
            PageText(page_number=10, text=_NOISE_PAGE),
        ]
        # 7/10 text pages = 70 % → below Senate 80 % threshold → MIXED
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.MIXED

    def test_sparse_pages_do_not_count_as_text_pages(self) -> None:
        """Sparse pages (< MIN_TEXT_PAGE_CHARS) don't satisfy the text count."""
        pages = _make_good_pages(6) + [
            PageText(page_number=7 + i, text=_SPARSE_PAGE) for i in range(4)
        ]
        # Sparse pages have chars < MIN_TEXT_PAGE_CHARS, so text_page_count = 6/10 = 60 %
        result = classify_from_pages(pages, Chamber.SENATE)
        assert result.pdf_kind == PdfKind.MIXED

    # --- Repeated header detection → DUPLICATE_HEADER_ROWS ---

    def test_header_repeated_on_every_page_triggers_duplicate_header_rows(self) -> None:
        """'schedule a' on all 10 pages → has_duplicate_header_rows → DUPLICATE_HEADER_ROWS trigger."""
        pages = [
            PageText(page_number=i, text=_REPEATED_SCHEDULE_A)
            for i in range(1, 11)
        ]
        result = classify_from_pages(pages, Chamber.SENATE)
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS in result.review_triggers
        assert result.needs_review

    def test_header_on_minority_of_pages_no_duplicate_trigger(self) -> None:
        """'schedule a' on 2/10 pages → below 50 % fraction → no DUPLICATE_HEADER_ROWS."""
        pages = (
            [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)]
            + _make_good_pages(8, start=2)
            + [PageText(page_number=10, text="schedule a\n" + _HOLDING_PAGE)]
        )
        result = classify_from_pages(pages, Chamber.SENATE)
        # 2/10 = 20 % < 50 % default → should not trigger
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS not in result.review_triggers

    def test_custom_repeated_header_fraction(self) -> None:
        """Lowering the fraction threshold flags repeated headers sooner."""
        # 3/10 = 30 % — below default 50 %, above custom 20 %
        pages = [
            PageText(page_number=i, text="schedule a\n" + _HOLDING_PAGE)
            for i in range(1, 4)
        ] + _make_good_pages(7, start=4)
        without_custom = classify_from_pages(pages, Chamber.SENATE, repeated_header_fraction=0.5)
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS not in without_custom.review_triggers

        with_custom = classify_from_pages(pages, Chamber.SENATE, repeated_header_fraction=0.2)
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS in with_custom.review_triggers

    # --- PTR vs annual header routing ---

    def test_ptr_pages_with_ptr_headers_no_missing_header_trigger(self) -> None:
        """PTR filing with 'transaction'/'owner'/'amount' headers → no MISSING_SECTION_HEADERS."""
        ptr_page = (
            "Transaction\n"
            "Date: 01/05/2023\n"
            "Owner: Self\n"
            "Asset: XYZ Corp\n"
            "Amount: $1,001 - $15,000\n"
            "Member: Alice Example\n"
        )
        pages = [PageText(page_number=i, text=ptr_page) for i in range(1, 6)]
        result = classify_from_pages(pages, Chamber.SENATE, is_ptr=True)
        assert ReviewTrigger.MISSING_SECTION_HEADERS not in result.review_triggers

    def test_annual_pages_with_schedule_headers_no_missing_header_trigger(self) -> None:
        """Annual filing with 'schedule a' → no MISSING_SECTION_HEADERS."""
        annual_page = "schedule a\n" + _HOLDING_PAGE
        pages = [PageText(page_number=i, text=annual_page) for i in range(1, 6)]
        result = classify_from_pages(pages, Chamber.SENATE, is_ptr=False)
        assert ReviewTrigger.MISSING_SECTION_HEADERS not in result.review_triggers

    def test_doc_with_no_known_headers_triggers_missing_headers(self) -> None:
        """Pages with no known section headers → MISSING_SECTION_HEADERS."""
        bare_pages = [
            PageText(page_number=i, text="Apple Inc. AAPL 1000001 Self Stock")
            for i in range(1, 6)
        ]
        result = classify_from_pages(bare_pages, Chamber.SENATE)
        assert ReviewTrigger.MISSING_SECTION_HEADERS in result.review_triggers

    # --- Flag forwarding ---

    def test_amendment_flag_triggers_review(self) -> None:
        pages = _make_good_pages(5)
        pages[0] = PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)
        result = classify_from_pages(pages, Chamber.SENATE, is_amendment=True)
        assert ReviewTrigger.AMENDMENT_FILING in result.review_triggers
        assert result.needs_review

    def test_member_name_mismatch_triggers_review(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)] + _make_good_pages(4, start=2)
        result = classify_from_pages(pages, Chamber.SENATE, member_name_match_score=0.5)
        assert ReviewTrigger.MEMBER_IDENTITY_MISMATCH in result.review_triggers

    def test_high_member_name_score_no_mismatch(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)] + _make_good_pages(4, start=2)
        result = classify_from_pages(pages, Chamber.SENATE, member_name_match_score=0.95)
        assert ReviewTrigger.MEMBER_IDENTITY_MISMATCH not in result.review_triggers

    def test_unresolved_trusts_flag_forwarded(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)] + _make_good_pages(4, start=2)
        result = classify_from_pages(
            pages, Chamber.SENATE, has_unresolved_options_or_trusts=True
        )
        assert ReviewTrigger.UNRESOLVED_OPTIONS_OR_TRUSTS in result.review_triggers

    def test_impossible_dates_flag_forwarded(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)] + _make_good_pages(4, start=2)
        result = classify_from_pages(pages, Chamber.SENATE, has_impossible_dates=True)
        assert ReviewTrigger.IMPOSSIBLE_DATE in result.review_triggers

    def test_non_standard_amounts_flag_forwarded(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE)] + _make_good_pages(4, start=2)
        result = classify_from_pages(pages, Chamber.SENATE, has_non_standard_amounts=True)
        assert ReviewTrigger.NON_STANDARD_AMOUNTS in result.review_triggers

    # --- Custom known_headers override ---

    def test_custom_known_headers_controls_repeated_header_detection(self) -> None:
        """Caller-supplied known_headers controls which tokens are checked for repetition.

        Note: classify_pdf's MISSING_SECTION_HEADERS check always compares against
        its own internal annual/PTR header sets — custom known_headers only affects
        repeated-header detection and the token vocabulary used to scan pages.

        Fixture: 4 pages with "holdings table" on all 4 pages but "schedule a" on
        only 1 page.  With default known_headers, "holdings table" is invisible so
        nothing repeats.  Adding "holdings table" to the custom set reveals the
        repetition and fires DUPLICATE_HEADER_ROWS.
        """
        custom_with_extra = frozenset({"schedule a", "holdings table"})
        pages = [
            PageText(page_number=1, text="schedule a\nholdings table\n" + "x" * 200),
            PageText(page_number=2, text="holdings table\n" + "x" * 200),
            PageText(page_number=3, text="holdings table\n" + "x" * 200),
            PageText(page_number=4, text="holdings table\n" + "x" * 200),
        ]

        # Default annual known_headers: "schedule a" appears on 1/4 pages (25 %) —
        # below the 50 % threshold → no DUPLICATE_HEADER_ROWS.
        # "holdings table" is not in the default set so is never counted.
        result_default = classify_from_pages(pages, Chamber.SENATE)
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS not in result_default.review_triggers

        # Custom set includes "holdings table": it appears on 4/4 pages (100 %) →
        # ceil(0.5 * 4) = 2 ≤ 4 → triggers DUPLICATE_HEADER_ROWS.
        result_custom = classify_from_pages(
            pages, Chamber.SENATE, known_headers=custom_with_extra
        )
        assert ReviewTrigger.DUPLICATE_HEADER_ROWS in result_custom.review_triggers
