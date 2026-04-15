"""Dispatch disclosure page text to the chamber-specific parser."""

from __future__ import annotations

from src.parse.disclosures import house_annual_text, house_ptr_text, senate_text
from src.parse.disclosures.models import Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult


def parse_disclosure_pages(
    page_texts: list[str],
    filing: Filing,
) -> ParseResult:
    if filing.filing_type is FilingType.AMENDMENT:
        raise ValueError(
            "filing_type=AMENDMENT cannot be dispatched directly. "
            "Resolve the underlying type (PTR or ANNUAL) before calling parse_disclosure_pages."
        )

    if filing.chamber.value == "house" and filing.filing_type is FilingType.PTR:
        return house_ptr_text.parse_house_ptr(
            page_texts,
            member_bioguide_id=filing.member_bioguide_id,
        )

    if filing.chamber.value == "house" and filing.filing_type is FilingType.ANNUAL:
        return house_annual_text.parse_house_annual(page_texts, filing)

    if filing.chamber.value == "senate" and filing.filing_type is FilingType.PTR:
        return senate_text.parse_senate_text(page_texts, filing)

    if filing.chamber.value == "senate" and filing.filing_type is FilingType.ANNUAL:
        return senate_text.parse_senate_text(page_texts, filing)

    raise ValueError(
        "No parser registered for "
        f"chamber={filing.chamber.value!r} filing_type={filing.filing_type!r}"
    )
