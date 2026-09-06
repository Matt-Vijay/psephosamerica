"""Typed contracts for PDF text extraction results.

Consumed by extraction, classification (classify.py), and later parsers.
Three immutable types cover what every consumer needs:
  - PageText   — text from one page
  - FlatText   — all pages joined for pattern scanning
  - DocumentMetrics — page/character counts that drive classification

Additional typed helpers cover realistic document quality patterns:
  - PageKind              — categorical quality label for a single page
  - classify_page_kind    — assign a quality label to a PageText
  - detect_repeated_headers — find header tokens that appear across many pages
  - is_likely_noise_page  — identify pages that carry no disclosure content
  - DocumentProfile       — richer per-document summary used by classify_runtime
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# Matches the threshold in classify.py (_MIN_CHARS_PER_TEXT_PAGE).
# A page must meet this to count as a text page.
MIN_TEXT_PAGE_CHARS: int = 200

# Pages with more than zero but at most this many characters are "sparse":
# they have extractable text but are too thin to be meaningful disclosure pages.
_SPARSE_PAGE_MAX_CHARS: int = 50

# Pattern for common non-disclosure noise: page-number lines, purely numeric
# lines, or lines consisting solely of whitespace/punctuation.
_NOISE_ONLY_PATTERN: re.Pattern[str] = re.compile(
    r"^[\s\W\d_]*$",
    re.MULTILINE,
)

# Default fraction of pages a header token must appear on to be flagged as
# "repeated" (likely a duplicate header row issue rather than genuine structure).
_DEFAULT_REPEATED_HEADER_FRACTION: float = 0.5


class PageKind(str, Enum):
    """Categorical text-quality label for a single PDF page.

    TEXT          : >= MIN_TEXT_PAGE_CHARS characters — rich, parseable text.
    SPARSE        : (_SPARSE_PAGE_MAX_CHARS, MIN_TEXT_PAGE_CHARS) characters —
                    some text, but below the useful-disclosure threshold.
    OCR_CANDIDATE : <= _SPARSE_PAGE_MAX_CHARS characters — almost certainly
                    an image-based page that requires OCR.
    NOISE         : Has text, but its content is clearly non-disclosure
                    boilerplate (cover pages, blank pages, page-number lines).
    """

    TEXT = "text"
    SPARSE = "sparse"
    OCR_CANDIDATE = "ocr_candidate"
    NOISE = "noise"


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


@dataclass(frozen=True)
class DocumentProfile:
    """Richer per-document summary that extends DocumentMetrics.

    Intended for use by classify_runtime as a higher-level input that bundles
    structural observations — repeated headers, noise pages, OCR-candidate
    pages — alongside the raw metric counts.

    Attributes
    ----------
    metrics:
        Core page/character counts (the existing DocumentMetrics).
    page_kinds:
        Per-page kind classifications in original page order.
    repeated_headers:
        Header tokens that appear on an unusually high fraction of pages,
        suggesting duplicate header rows rather than genuine section structure.
    noise_page_count:
        Number of pages classified as NOISE (non-disclosure content).
    ocr_candidate_count:
        Number of pages classified as OCR_CANDIDATE.
    """

    metrics: DocumentMetrics
    page_kinds: tuple[PageKind, ...]
    repeated_headers: frozenset[str]
    noise_page_count: int
    ocr_candidate_count: int


# Core helpers (existing public surface)


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


# Page-level quality helpers


def classify_page_kind(page: PageText) -> PageKind:
    """Assign a PageKind to a single page based on its extracted text.

    Classification is purely character-count-based for OCR_CANDIDATE and
    SPARSE; NOISE requires inspecting the actual content.

    Decision tree
    -------------
    1. If the page is *effectively empty* after noise-pattern matching  →  NOISE.
    2. If len(text) >= MIN_TEXT_PAGE_CHARS                             →  TEXT.
    3. If _SPARSE_PAGE_MAX_CHARS < len(text) < MIN_TEXT_PAGE_CHARS    →  SPARSE.
    4. Otherwise (len <= _SPARSE_PAGE_MAX_CHARS)                       →  OCR_CANDIDATE.
    """
    text = page.text
    char_count = len(text)

    # Empty or purely noise content (whitespace, digits, punctuation only).
    if char_count == 0 or _is_noise_content(text):
        return PageKind.NOISE

    if char_count >= MIN_TEXT_PAGE_CHARS:
        return PageKind.TEXT

    if char_count > _SPARSE_PAGE_MAX_CHARS:
        return PageKind.SPARSE

    return PageKind.OCR_CANDIDATE


def is_likely_noise_page(page: PageText) -> bool:
    """Return True if the page carries no useful disclosure content.

    Noise pages include: completely blank pages, pages with only page-number
    lines, cover pages with only whitespace / punctuation, and pages whose
    entire text matches the noise-only pattern.
    """
    text = page.text
    if len(text) == 0:
        return True
    return _is_noise_content(text)


def detect_repeated_headers(
    pages: Sequence[PageText],
    known_headers: frozenset[str],
    *,
    min_page_fraction: float = _DEFAULT_REPEATED_HEADER_FRACTION,
) -> frozenset[str]:
    """Return header tokens that appear on an unusually high fraction of pages.

    A header token that shows up on >= min_page_fraction of all pages is likely
    being repeated due to duplicate header rows (e.g. the parser mis-reads table
    column headers as data rows on every page), rather than reflecting genuine
    document structure.

    Parameters
    ----------
    pages:
        All pages of the document.
    known_headers:
        The set of canonical lower-cased header tokens to look for.
    min_page_fraction:
        Fraction of pages [0.0, 1.0] a header must appear on to be flagged.

    Returns
    -------
    frozenset[str] of lower-cased header tokens that exceeded the threshold.
    """
    if not pages or not known_headers:
        return frozenset()

    total = len(pages)
    threshold = max(1, math.ceil(min_page_fraction * total))

    counts: dict[str, int] = {}
    for page in pages:
        seen_on_page: set[str] = set()
        for line in page.text.splitlines():
            token = line.strip().lower()
            if token in known_headers and token not in seen_on_page:
                counts[token] = counts.get(token, 0) + 1
                seen_on_page.add(token)

    return frozenset(token for token, count in counts.items() if count >= threshold)


def build_document_profile(
    pages: Sequence[PageText],
    known_headers: frozenset[str],
    *,
    repeated_header_fraction: float = _DEFAULT_REPEATED_HEADER_FRACTION,
) -> DocumentProfile:
    """Derive a DocumentProfile from a sequence of pages.

    Combines DocumentMetrics with per-page quality labels and structural
    observations (repeated headers, noise/OCR counts).

    Parameters
    ----------
    pages:
        All pages of the document.
    known_headers:
        Canonical lower-cased header tokens used for repeated-header detection.
    repeated_header_fraction:
        Threshold passed to detect_repeated_headers.
    """
    metrics = document_metrics(pages)
    kinds = tuple(classify_page_kind(p) for p in pages)
    repeated = detect_repeated_headers(
        pages,
        known_headers,
        min_page_fraction=repeated_header_fraction,
    )
    noise_count = sum(1 for k in kinds if k == PageKind.NOISE)
    ocr_count = sum(1 for k in kinds if k == PageKind.OCR_CANDIDATE)
    return DocumentProfile(
        metrics=metrics,
        page_kinds=kinds,
        repeated_headers=repeated,
        noise_page_count=noise_count,
        ocr_candidate_count=ocr_count,
    )


# Internal helpers


def _is_noise_content(text: str) -> bool:
    """True when the entire text block contains only whitespace, digits, and
    punctuation — i.e. no alphabetic disclosure content at all."""
    # Split into non-empty lines; if every line matches the noise pattern,
    # the page carries no alphabetic disclosure content.
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    return all(_NOISE_ONLY_PATTERN.fullmatch(line) for line in lines)
