"""Disclosure classification runtime layer.

Wires text extraction into the heuristic classifier.

Public API
----------
build_heuristic_input(metrics, chamber, **flags) -> PdfHeuristicInput
    Construct the classifier's input struct from a TextMetrics snapshot.

classify_artifact_bytes(data, chamber, *, extractor=extract_text_metrics, **flags)
    Extract text metrics from raw PDF bytes, then classify.

classify_from_pages(pages, chamber, *, known_headers, **flags) -> ClassifyResult
    Higher-level entry point: takes a sequence of PageText objects from
    text_document, derives DocumentProfile observations, and routes into the
    heuristic classifier — wiring repeated-header and noise-page detection
    without requiring the caller to assemble TextMetrics manually.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable, Optional

from src.parse.disclosures.classify import (
    ClassifyResult,
    PdfHeuristicInput,
    classify_pdf,
    _ANNUAL_SECTION_HEADERS,
    _PTR_SECTION_HEADERS,
)
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.text_document import (
    PageText,
    build_document_profile,
    flatten_pages,
)
from src.parse.disclosures.text_extract import TextMetrics, extract_text_metrics


def build_heuristic_input(
    metrics: TextMetrics,
    chamber: Chamber,
    *,
    is_ptr: bool = False,
    is_amendment: bool = False,
    has_non_standard_amounts: bool = False,
    has_impossible_dates: bool = False,
    has_duplicate_header_rows: bool = False,
    has_unresolved_options_or_trusts: bool = False,
    member_name_match_score: Optional[float] = None,
) -> PdfHeuristicInput:
    """Build a PdfHeuristicInput from extracted text metrics and caller-supplied flags."""
    return PdfHeuristicInput(
        total_pages=metrics.total_pages,
        text_pages=metrics.text_pages,
        avg_chars_per_text_page=metrics.avg_chars_per_text_page,
        detected_headers=metrics.detected_headers,
        chamber=chamber,
        is_ptr=is_ptr,
        is_amendment=is_amendment,
        has_non_standard_amounts=has_non_standard_amounts,
        has_impossible_dates=has_impossible_dates,
        has_duplicate_header_rows=has_duplicate_header_rows,
        has_unresolved_options_or_trusts=has_unresolved_options_or_trusts,
        member_name_match_score=member_name_match_score,
    )


def classify_artifact_bytes(
    data: bytes,
    chamber: Chamber,
    *,
    extractor: Callable[[bytes], TextMetrics] = extract_text_metrics,
    is_ptr: bool = False,
    is_amendment: bool = False,
    has_non_standard_amounts: bool = False,
    has_impossible_dates: bool = False,
    has_duplicate_header_rows: bool = False,
    has_unresolved_options_or_trusts: bool = False,
    member_name_match_score: Optional[float] = None,
) -> ClassifyResult:
    """Extract text metrics from raw PDF bytes and return a ClassifyResult.

    The default extractor requires pypdf; inject an alternative to avoid that
    dependency in tests or alternative PDF toolchains.
    """
    metrics = extractor(data)
    heuristic_input = build_heuristic_input(
        metrics,
        chamber,
        is_ptr=is_ptr,
        is_amendment=is_amendment,
        has_non_standard_amounts=has_non_standard_amounts,
        has_impossible_dates=has_impossible_dates,
        has_duplicate_header_rows=has_duplicate_header_rows,
        has_unresolved_options_or_trusts=has_unresolved_options_or_trusts,
        member_name_match_score=member_name_match_score,
    )
    return classify_pdf(heuristic_input)


def classify_from_pages(
    pages: Sequence[PageText],
    chamber: Chamber,
    *,
    known_headers: Optional[frozenset[str]] = None,
    is_ptr: bool = False,
    is_amendment: bool = False,
    has_non_standard_amounts: bool = False,
    has_impossible_dates: bool = False,
    has_unresolved_options_or_trusts: bool = False,
    member_name_match_score: Optional[float] = None,
    repeated_header_fraction: float = 0.5,
) -> ClassifyResult:
    """Classify a disclosure document from its extracted PageText objects.

    Derives DocumentProfile observations (repeated headers, noise/OCR page
    counts) and wires them into the heuristic classifier, automatically setting
    has_duplicate_header_rows when repeated_headers is non-empty.

    This is the preferred entry point when the caller already holds PageText
    objects (e.g. after text extraction) and wants structural observations to
    flow through automatically.

    Parameters
    ----------
    pages:
        All pages of the document in physical order.
    chamber:
        The filing chamber (HOUSE or SENATE).
    known_headers:
        Header tokens to check for repetition.  Defaults to the union of annual
        and PTR section headers from classify.py.
    is_ptr:
        True when the document is a Periodic Transaction Report.
    is_amendment:
        True when the document is an amendment filing.
    has_non_standard_amounts:
        Caller-detected non-standard amount values.
    has_impossible_dates:
        Caller-detected impossible dates.
    has_unresolved_options_or_trusts:
        Caller-detected unresolved options or trust disclosures.
    member_name_match_score:
        Fuzzy match score [0.0, 1.0] for the member name on the filing cover.
    repeated_header_fraction:
        Fraction of pages a header token must appear on to be flagged as
        repeated.  Passed through to detect_repeated_headers.
    """
    effective_known_headers = (
        known_headers
        if known_headers is not None
        else (_PTR_SECTION_HEADERS if is_ptr else _ANNUAL_SECTION_HEADERS)
    )

    profile = build_document_profile(
        pages,
        effective_known_headers,
        repeated_header_fraction=repeated_header_fraction,
    )

    # Derive detected_headers by scanning the full flat text.
    flat = flatten_pages(pages)
    detected: set[str] = set()
    for line in flat.text.splitlines():
        token = line.strip().lower()
        if token in effective_known_headers:
            detected.add(token)

    metrics = profile.metrics
    text_metrics = TextMetrics(
        total_pages=metrics.page_count,
        text_pages=metrics.text_page_count,
        avg_chars_per_text_page=metrics.avg_chars_per_text_page,
        detected_headers=frozenset(detected),
    )

    has_duplicate_header_rows = len(profile.repeated_headers) > 0

    heuristic_input = build_heuristic_input(
        text_metrics,
        chamber,
        is_ptr=is_ptr,
        is_amendment=is_amendment,
        has_non_standard_amounts=has_non_standard_amounts,
        has_impossible_dates=has_impossible_dates,
        has_duplicate_header_rows=has_duplicate_header_rows,
        has_unresolved_options_or_trusts=has_unresolved_options_or_trusts,
        member_name_match_score=member_name_match_score,
    )
    return classify_pdf(heuristic_input)
