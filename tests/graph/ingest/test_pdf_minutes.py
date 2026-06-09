from __future__ import annotations

from fpdf import FPDF  # type: ignore[import-untyped]

from src.graph.enrichment.entity_linker import EntityLinker, roster_from_names
from src.graph.ingest.pdf_minutes import (
    extract_pdf_text,
    is_text_pdf,
    link_minutes_to_officials,
)


def _text_pdf(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _blank_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()  # no text -> no text layer (stands in for an image-only scan)
    return bytes(pdf.output())


def test_extract_text_from_text_pdf() -> None:
    data = _text_pdf(["City Council Regular Meeting Minutes", "Present: Jane Smith and Bob Jones."])
    text = extract_pdf_text(data)
    assert "City Council" in text
    assert "Jane Smith" in text
    assert "Bob Jones" in text


def test_is_text_pdf_true_for_text() -> None:
    assert is_text_pdf(_text_pdf(["Council minutes with substantial text content here."])) is True


def test_is_text_pdf_false_for_imageonly_gap() -> None:
    # A PDF with no text layer (the image-only-scan gap) extracts to empty.
    assert extract_pdf_text(_blank_pdf()) == ""
    assert is_text_pdf(_blank_pdf()) is False


def test_link_minutes_to_officials() -> None:
    roster = roster_from_names({"ce-smith": "Jane Smith", "ce-jones": "Bob Jones"})
    linker = EntityLinker(roster)
    data = _text_pdf(
        ["Minutes of the meeting.", "Councilmember Jane Smith moved; Bob Jones seconded."]
    )
    links = dict(link_minutes_to_officials(data, linker))
    assert links == {"Jane Smith": "ce-smith", "Bob Jones": "ce-jones"}
