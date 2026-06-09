"""Extract text from text-layer municipal-minutes PDFs (pure-Python).

Many municipal agendas/minutes are published only as PDFs. :func:`extract_pdf_text`
pulls the embedded text layer via pdfplumber (pure-Python; no OCR binary). Paired
with the :class:`~src.graph.enrichment.entity_linker.EntityLinker`,
:func:`link_minutes_to_officials` turns a minutes PDF into ``(mention,
canonical_person_id)`` links — who was present / who spoke.

**Image-only PDF gap:** a scanned (image-only) PDF has no text layer;
:func:`extract_pdf_text` returns ``""`` and :func:`is_text_pdf` returns ``False``.
Those need a raster OCR engine (Tesseract) — out of scope for the pure-Python
path and documented as a gap rather than silently dropped.
"""

from __future__ import annotations

import io

import pdfplumber

from src.graph.enrichment.entity_linker import EntityLinker


def extract_pdf_text(data: bytes) -> str:
    """Extract the embedded text layer from a PDF (``""`` if image-only/empty)."""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages).strip()


def is_text_pdf(data: bytes, *, min_chars: int = 20) -> bool:
    """Whether the PDF carries an extractable text layer (vs. image-only/scanned)."""
    return len(extract_pdf_text(data)) >= min_chars


def link_minutes_to_officials(data: bytes, linker: EntityLinker) -> list[tuple[str, str]]:
    """Extract minutes text and link named officials to canonical Person IDs."""
    return linker.link_text(extract_pdf_text(data))
