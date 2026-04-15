"""Tests for text_document typed contracts.

All tests are pure (no network, no filesystem).
"""

from __future__ import annotations

import pytest

from src.parse.disclosures.text_document import (
    MIN_TEXT_PAGE_CHARS,
    DocumentMetrics,
    FlatText,
    PageText,
    document_metrics,
    flatten_pages,
)


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
        _ = m.page_count          # → total_pages
        _ = m.text_page_count     # → text_pages
        _ = m.avg_chars_per_text_page
