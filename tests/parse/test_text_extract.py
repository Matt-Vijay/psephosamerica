"""Tests for PDF text extraction.

No network calls, no real PDF fixtures.  pypdf is patched at the module attribute
``src.parse.disclosures.text_extract._pypdf`` so tests run whether or not the
library is installed in the current environment.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import src.parse.disclosures.text_extract as _mod
from src.parse.disclosures.text_extract import (
    TextMetrics,
    extract_text_metrics,
    extract_text_pages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_pypdf(page_texts: list[Optional[str]]) -> MagicMock:
    """Return a mock module whose PdfReader yields pages with given texts."""
    pages = []
    for text in page_texts:
        page = MagicMock()
        page.extract_text.return_value = text
        pages.append(page)
    reader = MagicMock()
    reader.pages = pages
    module = MagicMock()
    module.PdfReader.return_value = reader
    return module


# ---------------------------------------------------------------------------
# extract_text_pages
# ---------------------------------------------------------------------------


class TestExtractTextPages:
    def test_returns_one_string_per_page(self) -> None:
        texts = ["First page content.", "Second page content.", "Third page content."]
        with patch.object(_mod, "_pypdf", _fake_pypdf(texts)):
            result = extract_text_pages(b"irrelevant")
        assert result == texts

    def test_empty_pdf_returns_empty_list(self) -> None:
        with patch.object(_mod, "_pypdf", _fake_pypdf([])):
            result = extract_text_pages(b"irrelevant")
        assert result == []

    def test_none_text_normalised_to_empty_string(self) -> None:
        with patch.object(_mod, "_pypdf", _fake_pypdf([None, "Some text"])):
            result = extract_text_pages(b"irrelevant")
        assert result == ["", "Some text"]

    def test_passes_bytes_to_bytesio(self) -> None:
        """PdfReader must be called with a BytesIO wrapping the supplied bytes."""
        import io

        fake = _fake_pypdf(["text"])
        with patch.object(_mod, "_pypdf", fake), patch("io.BytesIO", wraps=io.BytesIO) as bio:
            extract_text_pages(b"pdfdata")
        bio.assert_called_once_with(b"pdfdata")

    def test_raises_when_pypdf_missing(self) -> None:
        with patch.object(_mod, "_pypdf", None):
            with pytest.raises(ImportError, match="pypdf"):
                extract_text_pages(b"irrelevant")


# ---------------------------------------------------------------------------
# extract_text_metrics
# ---------------------------------------------------------------------------


class TestExtractTextMetrics:
    def test_all_text_pages(self) -> None:
        # 3 pages, each well above the 200-char threshold
        long_text = "x" * 400
        with patch.object(_mod, "_pypdf", _fake_pypdf([long_text, long_text, long_text])):
            m = extract_text_metrics(b"irrelevant")
        assert m.total_pages == 3
        assert m.text_pages == 3
        assert m.avg_chars_per_text_page == 400.0

    def test_no_text_pages(self) -> None:
        with patch.object(_mod, "_pypdf", _fake_pypdf(["", ""])):
            m = extract_text_metrics(b"irrelevant")
        assert m.total_pages == 2
        assert m.text_pages == 0
        assert m.avg_chars_per_text_page == 0.0

    def test_mixed_pages(self) -> None:
        short = "x" * 50   # below threshold
        long = "x" * 300   # above threshold
        with patch.object(_mod, "_pypdf", _fake_pypdf([short, long, long])):
            m = extract_text_metrics(b"irrelevant")
        assert m.total_pages == 3
        assert m.text_pages == 2
        assert m.avg_chars_per_text_page == 300.0

    def test_avg_chars_computed_over_text_pages_only(self) -> None:
        # 1 short page (100 chars) + 1 long page (500 chars): avg should be 500, not 300
        with patch.object(_mod, "_pypdf", _fake_pypdf(["a" * 100, "b" * 500])):
            m = extract_text_metrics(b"irrelevant")
        assert m.text_pages == 1
        assert m.avg_chars_per_text_page == 500.0

    def test_empty_pdf(self) -> None:
        with patch.object(_mod, "_pypdf", _fake_pypdf([])):
            m = extract_text_metrics(b"irrelevant")
        assert m == TextMetrics(
            total_pages=0,
            text_pages=0,
            avg_chars_per_text_page=0.0,
            detected_headers=frozenset(),
        )

    def test_detects_annual_section_headers(self) -> None:
        page = "Schedule A\nPart II\nSome disclosure text here"
        with patch.object(_mod, "_pypdf", _fake_pypdf([page])):
            m = extract_text_metrics(b"irrelevant")
        assert "schedule a" in m.detected_headers
        assert "part ii" in m.detected_headers

    def test_detects_ptr_section_headers(self) -> None:
        page = "Transaction\nOwner\nAsset\nAmount\nDate\n" + "data " * 50
        with patch.object(_mod, "_pypdf", _fake_pypdf([page])):
            m = extract_text_metrics(b"irrelevant")
        assert "transaction" in m.detected_headers
        assert "owner" in m.detected_headers
        assert "asset" in m.detected_headers
        assert "amount" in m.detected_headers
        assert "date" in m.detected_headers

    def test_header_detection_case_insensitive(self) -> None:
        page = "SCHEDULE A\nPART IV\n" + "filler " * 40
        with patch.object(_mod, "_pypdf", _fake_pypdf([page])):
            m = extract_text_metrics(b"irrelevant")
        assert "schedule a" in m.detected_headers
        assert "part iv" in m.detected_headers

    def test_unrecognised_lines_not_in_headers(self) -> None:
        page = "Random line\nAnother random line\n" + "x" * 300
        with patch.object(_mod, "_pypdf", _fake_pypdf([page])):
            m = extract_text_metrics(b"irrelevant")
        assert "random line" not in m.detected_headers
        assert len(m.detected_headers) == 0

    def test_headers_detected_across_multiple_pages(self) -> None:
        p1 = "Schedule A\n" + "a" * 300
        p2 = "Part III\n" + "b" * 300
        with patch.object(_mod, "_pypdf", _fake_pypdf([p1, p2])):
            m = extract_text_metrics(b"irrelevant")
        assert "schedule a" in m.detected_headers
        assert "part iii" in m.detected_headers

    def test_returns_frozen_dataclass(self) -> None:
        with patch.object(_mod, "_pypdf", _fake_pypdf([])):
            m = extract_text_metrics(b"irrelevant")
        assert isinstance(m, TextMetrics)
        with pytest.raises((AttributeError, TypeError)):
            m.total_pages = 99  # type: ignore[misc]

    def test_raises_when_pypdf_missing(self) -> None:
        with patch.object(_mod, "_pypdf", None):
            with pytest.raises(ImportError, match="pypdf"):
                extract_text_metrics(b"irrelevant")
