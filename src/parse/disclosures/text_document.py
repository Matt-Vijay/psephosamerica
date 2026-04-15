"""Typed contracts for PDF text extraction results.

Consumed by extraction, classification (classify.py), and later parsers.
Three immutable types cover what every consumer needs:
  - PageText   — text from one page
  - FlatText   — all pages joined for pattern scanning
  - DocumentMetrics — page/character counts that drive classification
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Matches the threshold in classify.py (_MIN_CHARS_PER_TEXT_PAGE).
# A page must meet this to count as a text page.
MIN_TEXT_PAGE_CHARS: int = 200


@dataclass(frozen=True)
class PageText:
    """Text extracted from one PDF page.

    page_number is 1-based to match PDF conventions.
    text is the raw extracted string; empty string means no extraction.
    """

    page_number: int
    text: str


@dataclass(frozen=True)
class FlatText:
    """All page text joined into one string for header and pattern scanning."""

    text: str


@dataclass(frozen=True)
class DocumentMetrics:
    """Page and character counts derived from a sequence of PageText objects.

    Maps directly to the fields that PdfHeuristicInput expects:
      page_count           → total_pages
      text_page_count      → text_pages
      avg_chars_per_text_page → avg_chars_per_text_page
    """

    page_count: int
    text_page_count: int
    avg_chars_per_text_page: float


def flatten_pages(pages: Sequence[PageText]) -> FlatText:
    """Join page texts with newlines, preserving page order."""
    return FlatText(text="\n".join(p.text for p in pages))


def document_metrics(pages: Sequence[PageText]) -> DocumentMetrics:
    """Compute page counts and average chars from extracted pages.

    Only pages with at least MIN_TEXT_PAGE_CHARS characters count as text pages.
    avg_chars_per_text_page is 0.0 when there are no text pages.
    """
    page_count = len(pages)
    text_pages = [p for p in pages if len(p.text) >= MIN_TEXT_PAGE_CHARS]
    text_page_count = len(text_pages)
    total_chars = sum(len(p.text) for p in text_pages)
    avg = total_chars / text_page_count if text_page_count else 0.0
    return DocumentMetrics(
        page_count=page_count,
        text_page_count=text_page_count,
        avg_chars_per_text_page=avg,
    )
