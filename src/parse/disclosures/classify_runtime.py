"""Disclosure classification runtime layer.

Wires text extraction into the heuristic classifier.

Public API
----------
build_heuristic_input(metrics, chamber, **flags) -> PdfHeuristicInput
    Construct the classifier's input struct from a TextMetrics snapshot.

classify_artifact_bytes(data, chamber, *, extractor=extract_text_metrics, **flags)
    Extract text metrics from raw PDF bytes, then classify.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.parse.disclosures.classify import (
    ClassifyResult,
    PdfHeuristicInput,
    classify_pdf,
)
from src.parse.disclosures.models import Chamber
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
