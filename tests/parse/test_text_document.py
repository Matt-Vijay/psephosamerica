"""Tests for text_document typed contracts.

All tests are pure (no network, no filesystem).
Covers:
  - PageText / FlatText / DocumentMetrics immutability and field storage
  - flatten_pages and document_metrics core behaviour
  - PageKind classification: TEXT, SPARSE, OCR_CANDIDATE, NOISE
  - classify_page_kind across realistic multi-page documents
  - is_likely_noise_page for blank, whitespace, and digit-only pages
  - detect_repeated_headers with inline multi-page text fixtures
  - build_document_profile end-to-end
"""

from __future__ import annotations

import pytest

from src.parse.disclosures.text_document import (
    MIN_TEXT_PAGE_CHARS,
    DocumentMetrics,
    DocumentProfile,
    FlatText,
    PageKind,
    PageText,
    build_document_profile,
    classify_page_kind,
    detect_repeated_headers,
    document_metrics,
    flatten_pages,
    is_likely_noise_page,
)

# ---------------------------------------------------------------------------
# Shared constants and fixtures
# ---------------------------------------------------------------------------

_ANNUAL_HEADERS: frozenset[str] = frozenset(
    {
        "schedule a",
        "schedule b",
        "schedule c",
        "part i",
        "part ii",
        "part iii",
    }
)

_RICH_TEXT = "a" * MIN_TEXT_PAGE_CHARS  # exactly at the text threshold

# Realistic holding-table excerpt that passes as a text page.
_HOLDING_PAGE = (
    "Schedule A - Public Investments\n"
    "Asset: Apple Inc. (AAPL)\n"
    "Owner: Self\n"
    "Value: $1,000,001 - $5,000,000\n"
    "Type: Stock\n"
    "Year-End Value: Over $1,000,001\n"
    "Comment: Held in brokerage account\n"
    "Filing Year: 2023\n"
    "Member: Jane Smith\n"
    "District: CA-12\n"
)

# Realistic PTR transaction excerpt.
_TRANSACTION_PAGE = (
    "Transaction Report\n"
    "Date: 03/15/2023\n"
    "Owner: Self\n"
    "Asset: Microsoft Corp (MSFT)\n"
    "Transaction Type: Purchase\n"
    "Amount: $15,001 - $50,000\n"
    "Filing Status: New\n"
    "Notification Date: 04/01/2023\n"
    "Member: John Doe\n"
    "State: TX\n"
)

# Cover page / noise page: only headers, numbers, whitespace.
_COVER_PAGE_NOISE = "   \nUnited States House of Representatives\n   \n2023\nPage 1\n"


# ---------------------------------------------------------------------------
# PageText
# ---------------------------------------------------------------------------


class TestPageText:
    def test_stores_page_number_and_text(self) -> None:
        p = PageText(page_number=1, text="hello")
        assert p.page_number == 1
        assert p.text == "hello"

    def test_empty_text_allowed(self) -> None:
        p = PageText(page_number=3, text="")
        assert p.text == ""

    def test_is_immutable(self) -> None:
        p = PageText(page_number=1, text="x")
        with pytest.raises((AttributeError, TypeError)):
            p.text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FlatText
# ---------------------------------------------------------------------------


class TestFlatText:
    def test_stores_text(self) -> None:
        f = FlatText(text="section a\nsection b")
        assert f.text == "section a\nsection b"

    def test_empty_text_allowed(self) -> None:
        f = FlatText(text="")
        assert f.text == ""

    def test_is_immutable(self) -> None:
        f = FlatText(text="abc")
        with pytest.raises((AttributeError, TypeError)):
            f.text = "xyz"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DocumentMetrics
# ---------------------------------------------------------------------------


class TestDocumentMetrics:
    def test_stores_all_three_fields(self) -> None:
        m = DocumentMetrics(page_count=5, text_page_count=3, avg_chars_per_text_page=450.0)
        assert m.page_count == 5
        assert m.text_page_count == 3
        assert m.avg_chars_per_text_page == 450.0

    def test_is_immutable(self) -> None:
        m = DocumentMetrics(page_count=1, text_page_count=1, avg_chars_per_text_page=300.0)
        with pytest.raises((AttributeError, TypeError)):
            m.page_count = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# flatten_pages
# ---------------------------------------------------------------------------


class TestFlattenPages:
    def test_single_page(self) -> None:
        pages = [PageText(page_number=1, text="schedule a")]
        result = flatten_pages(pages)
        assert isinstance(result, FlatText)
        assert result.text == "schedule a"

    def test_multiple_pages_joined_with_newlines(self) -> None:
        pages = [
            PageText(page_number=1, text="part i"),
            PageText(page_number=2, text="part ii"),
            PageText(page_number=3, text="part iii"),
        ]
        result = flatten_pages(pages)
        assert result.text == "part i\npart ii\npart iii"

    def test_empty_sequence_returns_empty_flat_text(self) -> None:
        result = flatten_pages([])
        assert isinstance(result, FlatText)
        assert result.text == ""

    def test_preserves_page_order(self) -> None:
        pages = [PageText(page_number=i, text=str(i)) for i in range(1, 6)]
        result = flatten_pages(pages)
        assert result.text == "1\n2\n3\n4\n5"

    def test_empty_page_text_included_as_blank(self) -> None:
        pages = [
            PageText(page_number=1, text="text"),
            PageText(page_number=2, text=""),
            PageText(page_number=3, text="more text"),
        ]
        result = flatten_pages(pages)
        assert result.text == "text\n\nmore text"


# ---------------------------------------------------------------------------
# document_metrics
# ---------------------------------------------------------------------------


class TestDocumentMetrics_Function:
    def test_empty_sequence(self) -> None:
        m = document_metrics([])
        assert m.page_count == 0
        assert m.text_page_count == 0
        assert m.avg_chars_per_text_page == 0.0

    def test_all_pages_below_threshold(self) -> None:
        pages = [PageText(page_number=i, text="x" * (MIN_TEXT_PAGE_CHARS - 1)) for i in range(1, 4)]
        m = document_metrics(pages)
        assert m.page_count == 3
        assert m.text_page_count == 0
        assert m.avg_chars_per_text_page == 0.0

    def test_all_pages_at_threshold(self) -> None:
        text = "a" * MIN_TEXT_PAGE_CHARS
        pages = [PageText(page_number=i, text=text) for i in range(1, 4)]
        m = document_metrics(pages)
        assert m.page_count == 3
        assert m.text_page_count == 3
        assert m.avg_chars_per_text_page == float(MIN_TEXT_PAGE_CHARS)

    def test_all_pages_above_threshold(self) -> None:
        pages = [
            PageText(page_number=1, text="a" * 500),
            PageText(page_number=2, text="b" * 300),
        ]
        m = document_metrics(pages)
        assert m.page_count == 2
        assert m.text_page_count == 2
        assert m.avg_chars_per_text_page == 400.0

    def test_mixed_pages(self) -> None:
        # page 1: 600 chars (text page), page 2: 50 chars (image page), page 3: 400 chars (text page)
        pages = [
            PageText(page_number=1, text="x" * 600),
            PageText(page_number=2, text="x" * 50),
            PageText(page_number=3, text="x" * 400),
        ]
        m = document_metrics(pages)
        assert m.page_count == 3
        assert m.text_page_count == 2
        assert m.avg_chars_per_text_page == 500.0

    def test_exactly_one_text_page(self) -> None:
        pages = [
            PageText(page_number=1, text=""),
            PageText(page_number=2, text="z" * 800),
        ]
        m = document_metrics(pages)
        assert m.page_count == 2
        assert m.text_page_count == 1
        assert m.avg_chars_per_text_page == 800.0

    def test_returns_document_metrics_instance(self) -> None:
        m = document_metrics([PageText(page_number=1, text="a" * 300)])
        assert isinstance(m, DocumentMetrics)

    def test_avg_chars_is_float(self) -> None:
        pages = [
            PageText(page_number=1, text="a" * 300),
            PageText(page_number=2, text="a" * 301),
            PageText(page_number=3, text="a" * 302),
        ]
        m = document_metrics(pages)
        assert isinstance(m.avg_chars_per_text_page, float)
        assert m.avg_chars_per_text_page == pytest.approx(301.0)

    def test_threshold_boundary_exclusive(self) -> None:
        # One below threshold must not count; one above must count.
        below = PageText(page_number=1, text="x" * (MIN_TEXT_PAGE_CHARS - 1))
        at_threshold = PageText(page_number=2, text="x" * MIN_TEXT_PAGE_CHARS)
        m_below = document_metrics([below])
        m_at = document_metrics([at_threshold])
        assert m_below.text_page_count == 0
        assert m_at.text_page_count == 1

    def test_metrics_fields_map_to_pdf_heuristic_input_names(self) -> None:
        # Smoke-check that the field names match what classify.PdfHeuristicInput expects.
        pages = [PageText(page_number=1, text="a" * 500)]
        m = document_metrics(pages)
        # total_pages, text_pages, avg_chars_per_text_page
        _ = m.page_count  # → total_pages
        _ = m.text_page_count  # → text_pages
        _ = m.avg_chars_per_text_page


# ---------------------------------------------------------------------------
# classify_page_kind
# ---------------------------------------------------------------------------


class TestClassifyPageKind:
    def test_rich_text_page_is_text(self) -> None:
        page = PageText(page_number=1, text=_HOLDING_PAGE)
        assert classify_page_kind(page) == PageKind.TEXT

    def test_exactly_at_threshold_is_text(self) -> None:
        page = PageText(page_number=1, text="a" * MIN_TEXT_PAGE_CHARS)
        assert classify_page_kind(page) == PageKind.TEXT

    def test_empty_page_is_noise(self) -> None:
        page = PageText(page_number=1, text="")
        assert classify_page_kind(page) == PageKind.NOISE

    def test_whitespace_only_page_is_noise(self) -> None:
        page = PageText(page_number=2, text="   \n   \n\t\t\n")
        assert classify_page_kind(page) == PageKind.NOISE

    def test_digit_only_page_is_noise(self) -> None:
        # Pages containing only numbers (e.g. page footers stamped as separate pages)
        page = PageText(page_number=3, text="123\n456\n789")
        assert classify_page_kind(page) == PageKind.NOISE

    def test_sparse_page_is_sparse(self) -> None:
        # 80 chars — above the OCR_CANDIDATE threshold, below MIN_TEXT_PAGE_CHARS.
        page = PageText(page_number=4, text="Schedule A\nNo reportable assets\n" + "x" * 40)
        assert classify_page_kind(page) == PageKind.SPARSE

    def test_very_short_text_page_is_ocr_candidate(self) -> None:
        # 10 chars — likely a near-blank image page.
        page = PageText(page_number=5, text="Page 1 of")
        assert classify_page_kind(page) == PageKind.OCR_CANDIDATE

    def test_realistic_holding_page_is_text(self) -> None:
        page = PageText(page_number=1, text=_HOLDING_PAGE)
        assert classify_page_kind(page) == PageKind.TEXT

    def test_realistic_transaction_page_is_text(self) -> None:
        page = PageText(page_number=1, text=_TRANSACTION_PAGE)
        assert classify_page_kind(page) == PageKind.TEXT

    def test_mixed_document_page_kinds(self) -> None:
        """A realistic 10-page filing: 6 rich, 2 sparse/OCR, 2 noise."""
        pages = [
            PageText(page_number=1, text=""),  # NOISE
            PageText(page_number=2, text=_HOLDING_PAGE),  # TEXT
            PageText(page_number=3, text=_HOLDING_PAGE),  # TEXT
            PageText(page_number=4, text="x" * 30),  # OCR_CANDIDATE
            PageText(page_number=5, text=_TRANSACTION_PAGE),  # TEXT
            PageText(page_number=6, text=_TRANSACTION_PAGE),  # TEXT
            PageText(page_number=7, text="Schedule B\nNone reported\n" + "y" * 60),  # SPARSE
            PageText(page_number=8, text=_HOLDING_PAGE),  # TEXT
            PageText(page_number=9, text="   \n1\n2\n   "),  # NOISE
            PageText(page_number=10, text=_HOLDING_PAGE),  # TEXT
        ]
        kinds = [classify_page_kind(p) for p in pages]
        assert kinds[0] == PageKind.NOISE
        assert kinds[1] == PageKind.TEXT
        assert kinds[2] == PageKind.TEXT
        assert kinds[3] == PageKind.OCR_CANDIDATE
        assert kinds[4] == PageKind.TEXT
        assert kinds[5] == PageKind.TEXT
        assert kinds[6] == PageKind.SPARSE
        assert kinds[7] == PageKind.TEXT
        assert kinds[8] == PageKind.NOISE
        assert kinds[9] == PageKind.TEXT


# ---------------------------------------------------------------------------
# is_likely_noise_page
# ---------------------------------------------------------------------------


class TestIsLikelyNoisePage:
    def test_empty_page_is_noise(self) -> None:
        assert is_likely_noise_page(PageText(page_number=1, text="")) is True

    def test_whitespace_only_is_noise(self) -> None:
        assert is_likely_noise_page(PageText(page_number=1, text="   \n\t  \n")) is True

    def test_digit_only_lines_are_noise(self) -> None:
        # A page composed entirely of numeric lines (e.g. scanned page stamps).
        assert is_likely_noise_page(PageText(page_number=1, text="1\n2\n3\n")) is True

    def test_punctuation_only_is_noise(self) -> None:
        assert is_likely_noise_page(PageText(page_number=1, text="---\n===\n___")) is True

    def test_rich_text_is_not_noise(self) -> None:
        assert is_likely_noise_page(PageText(page_number=1, text=_HOLDING_PAGE)) is False

    def test_sparse_but_alphabetic_text_is_not_noise(self) -> None:
        # A short page with alphabetic content is not noise even if sparse.
        assert is_likely_noise_page(PageText(page_number=1, text="None reported")) is False

    def test_cover_page_digits_and_whitespace_is_noise(self) -> None:
        # All lines are either whitespace or pure numbers.
        page = PageText(page_number=1, text="   \n2023\n   \n1\n   ")
        assert is_likely_noise_page(page) is True


# ---------------------------------------------------------------------------
# detect_repeated_headers
# ---------------------------------------------------------------------------


class TestDetectRepeatedHeaders:
    def test_empty_pages_returns_empty(self) -> None:
        result = detect_repeated_headers([], _ANNUAL_HEADERS)
        assert result == frozenset()

    def test_empty_known_headers_returns_empty(self) -> None:
        pages = [PageText(page_number=1, text="schedule a\nsome content")]
        result = detect_repeated_headers(pages, frozenset())
        assert result == frozenset()

    def test_header_on_every_page_is_flagged(self) -> None:
        """10-page doc: 'schedule a' appears on every page → flagged as repeated."""
        header_line = "schedule a\n"
        pages = [PageText(page_number=i, text=header_line + "x" * 100) for i in range(1, 11)]
        result = detect_repeated_headers(pages, _ANNUAL_HEADERS)
        assert "schedule a" in result

    def test_header_on_minority_of_pages_not_flagged(self) -> None:
        """10-page doc: 'schedule a' appears on 2/10 pages → below 50% threshold."""
        pages = [
            PageText(page_number=1, text="schedule a\nholdings data here"),
            PageText(page_number=2, text="part i\nlegislative activity"),
        ] + [PageText(page_number=i, text="x" * 100) for i in range(3, 11)]
        result = detect_repeated_headers(pages, _ANNUAL_HEADERS, min_page_fraction=0.5)
        # 2/10 = 20 % < 50 % threshold
        assert "schedule a" not in result

    def test_custom_threshold_controls_flagging(self) -> None:
        """Same data, lower threshold flags the header."""
        pages = [
            PageText(page_number=1, text="schedule a\ndata"),
            PageText(page_number=2, text="schedule a\nmore data"),
            PageText(page_number=3, text="x" * 100),
            PageText(page_number=4, text="x" * 100),
        ]
        # 2/4 = 50 %, threshold 0.4 → should flag
        flagged = detect_repeated_headers(pages, _ANNUAL_HEADERS, min_page_fraction=0.4)
        assert "schedule a" in flagged
        # threshold 0.6 → should not flag
        not_flagged = detect_repeated_headers(pages, _ANNUAL_HEADERS, min_page_fraction=0.6)
        assert "schedule a" not in not_flagged

    def test_only_counted_once_per_page(self) -> None:
        """Header appearing twice on the same page counts as one occurrence."""
        # 4 pages, 'schedule a' appears twice on page 1, once on page 2.
        pages = [
            PageText(page_number=1, text="schedule a\ndata\nschedule a\nmore"),
            PageText(page_number=2, text="schedule a\npart ii data"),
            PageText(page_number=3, text="x" * 100),
            PageText(page_number=4, text="x" * 100),
        ]
        # 2/4 = 50 %, with min_page_fraction=0.5 → threshold = max(1, round(2)) = 2 → flagged
        result = detect_repeated_headers(pages, _ANNUAL_HEADERS, min_page_fraction=0.5)
        assert "schedule a" in result

    def test_multiple_headers_independently_evaluated(self) -> None:
        """'schedule a' may be flagged while 'part i' is not."""
        pages = [
            PageText(page_number=1, text="schedule a\npart i\ncontent"),
            PageText(page_number=2, text="schedule a\ncontent"),
            PageText(page_number=3, text="schedule a\ncontent"),
            PageText(page_number=4, text="x" * 100),
        ]
        # schedule a: 3/4 = 75 % → flagged at 50 % threshold
        # part i: 1/4 = 25 % → not flagged
        result = detect_repeated_headers(pages, _ANNUAL_HEADERS, min_page_fraction=0.5)
        assert "schedule a" in result
        assert "part i" not in result

    def test_returns_frozenset(self) -> None:
        result = detect_repeated_headers([], _ANNUAL_HEADERS)
        assert isinstance(result, frozenset)

    def test_case_insensitive_matching(self) -> None:
        """Headers in the page text are lower-cased before matching."""
        pages = [PageText(page_number=i, text="Schedule A\nAsset info\n") for i in range(1, 5)]
        result = detect_repeated_headers(pages, _ANNUAL_HEADERS)
        assert "schedule a" in result


# ---------------------------------------------------------------------------
# build_document_profile
# ---------------------------------------------------------------------------


class TestBuildDocumentProfile:
    def test_returns_document_profile_instance(self) -> None:
        pages = [PageText(page_number=1, text=_HOLDING_PAGE)]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert isinstance(profile, DocumentProfile)

    def test_metrics_match_document_metrics(self) -> None:
        pages = [
            PageText(page_number=1, text=_HOLDING_PAGE),
            PageText(page_number=2, text=""),
        ]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        expected = document_metrics(pages)
        assert profile.metrics == expected

    def test_page_kinds_tuple_length_matches_page_count(self) -> None:
        pages = [PageText(page_number=i, text=_HOLDING_PAGE) for i in range(1, 6)]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert len(profile.page_kinds) == 5

    def test_noise_page_count_matches_noise_classifications(self) -> None:
        pages = [
            PageText(page_number=1, text=""),  # NOISE
            PageText(page_number=2, text=_HOLDING_PAGE),  # TEXT
            PageText(page_number=3, text="   \n"),  # NOISE
        ]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert profile.noise_page_count == 2

    def test_ocr_candidate_count(self) -> None:
        pages = [
            PageText(page_number=1, text=_HOLDING_PAGE),  # TEXT
            PageText(page_number=2, text="x" * 20),  # OCR_CANDIDATE
            PageText(page_number=3, text="y" * 15),  # OCR_CANDIDATE
        ]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert profile.ocr_candidate_count == 2

    def test_repeated_headers_detected(self) -> None:
        """A header on 8/10 pages is flagged in the profile."""
        pages = [PageText(page_number=i, text="schedule a\n" + "x" * 50) for i in range(1, 11)]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert "schedule a" in profile.repeated_headers

    def test_no_repeated_headers_when_each_appears_once(self) -> None:
        pages = [
            PageText(page_number=1, text="schedule a\n" + _HOLDING_PAGE),
            PageText(page_number=2, text="schedule b\n" + _HOLDING_PAGE),
            PageText(page_number=3, text="part i\n" + _HOLDING_PAGE),
            PageText(page_number=4, text="part ii\n" + _HOLDING_PAGE),
        ]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        # Each header appears on 1/4 pages (25 %) — below 50 % default threshold.
        assert len(profile.repeated_headers) == 0

    def test_empty_pages_gives_zero_counts(self) -> None:
        profile = build_document_profile([], _ANNUAL_HEADERS)
        assert profile.metrics.page_count == 0
        assert profile.noise_page_count == 0
        assert profile.ocr_candidate_count == 0
        assert profile.repeated_headers == frozenset()
        assert profile.page_kinds == ()

    def test_realistic_mixed_ocr_and_text_document(self) -> None:
        """Senate annual disclosure: 8 text pages, 2 image-based → profile summary."""
        good_page = _HOLDING_PAGE  # ~TEXT
        image_page = "x" * 10  # OCR_CANDIDATE
        pages = [
            PageText(page_number=1, text=""),  # NOISE (cover)
            PageText(page_number=2, text=good_page),  # TEXT
            PageText(page_number=3, text=good_page),  # TEXT
            PageText(page_number=4, text=image_page),  # OCR_CANDIDATE
            PageText(page_number=5, text=good_page),  # TEXT
            PageText(page_number=6, text=good_page),  # TEXT
            PageText(page_number=7, text=good_page),  # TEXT
            PageText(page_number=8, text=image_page),  # OCR_CANDIDATE
            PageText(page_number=9, text=good_page),  # TEXT
            PageText(page_number=10, text=good_page),  # TEXT
        ]
        profile = build_document_profile(pages, _ANNUAL_HEADERS)
        assert profile.metrics.page_count == 10
        assert profile.metrics.text_page_count == 7
        assert profile.noise_page_count == 1
        assert profile.ocr_candidate_count == 2
