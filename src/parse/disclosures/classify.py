"""Heuristic PDF classification.  No OCR or network calls; callers supply pre-extracted inputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.parse.disclosures.models import Chamber


class PdfKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    MIXED = "mixed"


class ReviewTrigger(str, Enum):
    NONE = "none"
    LOW_TEXT_RATIO = "low_text_ratio"
    MISSING_SECTION_HEADERS = "missing_section_headers"
    NON_STANDARD_AMOUNTS = "non_standard_amounts"
    IMPOSSIBLE_DATE = "impossible_date"
    MEMBER_IDENTITY_MISMATCH = "member_identity_mismatch"
    AMENDMENT_FILING = "amendment_filing"
    UNRESOLVED_OPTIONS_OR_TRUSTS = "unresolved_options_or_trusts"
    DUPLICATE_HEADER_ROWS = "duplicate_header_rows"


# --- Thresholds ---

_TEXT_PAGE_RATIO_THRESHOLD = 0.80
_MIN_CHARS_PER_TEXT_PAGE = 200
# House filings use a stricter text ratio because they are more often scanned.
_HOUSE_TEXT_PAGE_RATIO_THRESHOLD = 0.90

_ANNUAL_SECTION_HEADERS = frozenset(
    {
        "schedule a",
        "schedule b",
        "schedule c",
        "schedule d",
        "part i",
        "part ii",
        "part iii",
        "part iv",
        "part v",
        "part vi",
        "part vii",
        "part viii",
        "part ix",
    }
)

_PTR_SECTION_HEADERS = frozenset(
    {
        "transaction",
        "owner",
        "asset",
        "amount",
        "date",
    }
)


@dataclass(frozen=True)
class PdfHeuristicInput:
    total_pages: int
    text_pages: int
    avg_chars_per_text_page: float
    detected_headers: frozenset[str]
    chamber: Chamber
    is_amendment: bool = False
    has_non_standard_amounts: bool = False
    has_impossible_dates: bool = False
    has_duplicate_header_rows: bool = False
    has_unresolved_options_or_trusts: bool = False
    member_name_match_score: float | None = None
    is_ptr: bool = False


@dataclass(frozen=True)
class ClassifyResult:
    pdf_kind: PdfKind
    review_triggers: tuple[ReviewTrigger, ...]
    needs_ocr: bool
    needs_review: bool


def classify_pdf(inp: PdfHeuristicInput) -> ClassifyResult:
    triggers: list[ReviewTrigger] = []

    # --- PDF kind ---
    if inp.total_pages == 0:
        pdf_kind = PdfKind.IMAGE
    else:
        text_ratio = inp.text_pages / inp.total_pages if inp.total_pages > 0 else 0.0
        threshold = (
            _HOUSE_TEXT_PAGE_RATIO_THRESHOLD
            if inp.chamber == Chamber.HOUSE
            else _TEXT_PAGE_RATIO_THRESHOLD
        )
        chars_ok = inp.avg_chars_per_text_page >= _MIN_CHARS_PER_TEXT_PAGE

        if text_ratio >= threshold and chars_ok:
            pdf_kind = PdfKind.TEXT
        elif text_ratio > 0:
            pdf_kind = PdfKind.MIXED
        else:
            pdf_kind = PdfKind.IMAGE

    # --- Low text ratio ---
    if pdf_kind in (PdfKind.IMAGE, PdfKind.MIXED):
        triggers.append(ReviewTrigger.LOW_TEXT_RATIO)

    # --- Missing section headers ---
    detected_lower = frozenset(h.lower() for h in inp.detected_headers)
    expected = _PTR_SECTION_HEADERS if inp.is_ptr else _ANNUAL_SECTION_HEADERS
    if not expected & detected_lower:
        triggers.append(ReviewTrigger.MISSING_SECTION_HEADERS)

    # --- Direct flags ---
    if inp.has_non_standard_amounts:
        triggers.append(ReviewTrigger.NON_STANDARD_AMOUNTS)
    if inp.has_impossible_dates:
        triggers.append(ReviewTrigger.IMPOSSIBLE_DATE)
    if inp.has_duplicate_header_rows:
        triggers.append(ReviewTrigger.DUPLICATE_HEADER_ROWS)
    if inp.has_unresolved_options_or_trusts:
        triggers.append(ReviewTrigger.UNRESOLVED_OPTIONS_OR_TRUSTS)
    if inp.is_amendment:
        triggers.append(ReviewTrigger.AMENDMENT_FILING)

    # --- Member identity mismatch ---
    if inp.member_name_match_score is not None and inp.member_name_match_score < 0.8:
        triggers.append(ReviewTrigger.MEMBER_IDENTITY_MISMATCH)

    needs_ocr = pdf_kind != PdfKind.TEXT
    needs_review = len(triggers) > 0

    return ClassifyResult(
        pdf_kind=pdf_kind,
        review_triggers=tuple(triggers),
        needs_ocr=needs_ocr,
        needs_review=needs_review,
    )
