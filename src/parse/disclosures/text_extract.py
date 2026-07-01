"""PDF text extraction for disclosure artifacts.

Importable before pypdf is installed; functions raise ImportError when called without it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from src.parse.disclosures.text_tokens import DETECTION_HEADERS as _KNOWN_HEADERS

_pypdf: Any | None
try:
    import pypdf as _pypdf
except ImportError:
    _pypdf = None

# A text page must have at least this many characters to count as text-bearing.
_MIN_CHARS_TEXT_PAGE = 200


@dataclass(frozen=True)
class TextMetrics:
    """Page-level statistics consumed by the classifier layer.

    Field names match the corresponding ``PdfHeuristicInput`` fields so callers
    can spread this struct directly into that dataclass.
    """

    total_pages: int
    text_pages: int
    avg_chars_per_text_page: float
    detected_headers: frozenset[str]


def extract_text_pages(data: bytes) -> list[str]:
    """Return one string per page extracted from *data*.

    Pages whose ``extract_text`` yields ``None`` are normalised to ``""``.
    Order matches the physical page order in the PDF.

    Raises ``ImportError`` if pypdf is not installed.
    """
    if _pypdf is None:
        raise ImportError(
            "pypdf is required for PDF text extraction; install with: pip install 'psephosamerica[pdf]'"
        )
    reader = _pypdf.PdfReader(io.BytesIO(data))
    return [page.extract_text() or "" for page in reader.pages]


def extract_text_metrics(data: bytes) -> TextMetrics:
    """Compute page-level metrics from raw PDF bytes.

    Metrics:
    - ``total_pages``: physical page count.
    - ``text_pages``: pages with at least ``_MIN_CHARS_TEXT_PAGE`` characters.
    - ``avg_chars_per_text_page``: mean character count across text-bearing pages
      (0.0 when there are none).
    - ``detected_headers``: lower-cased header tokens found on any page.

    Raises ``ImportError`` if pypdf is not installed.
    """
    pages = extract_text_pages(data)
    total_pages = len(pages)

    text_bearing = [p for p in pages if len(p) >= _MIN_CHARS_TEXT_PAGE]
    n_text_pages = len(text_bearing)

    avg_chars = sum(len(p) for p in text_bearing) / n_text_pages if n_text_pages > 0 else 0.0

    detected: set[str] = set()
    for page_text in pages:
        for line in page_text.splitlines():
            token = line.strip().lower()
            if token in _KNOWN_HEADERS:
                detected.add(token)

    return TextMetrics(
        total_pages=total_pages,
        text_pages=n_text_pages,
        avg_chars_per_text_page=avg_chars,
        detected_headers=frozenset(detected),
    )
