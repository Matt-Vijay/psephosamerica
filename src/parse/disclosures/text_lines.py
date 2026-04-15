"""Text-line helpers for disclosure page text.

One mechanical step in the parse pipeline: split page strings into ordered
line lists, flatten across pages, filter noise, and slice by section header.

Does not parse holdings or transactions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Section header tokens that bound disclosure sections (lower-cased).
# Kept in sync with the token set in text_extract._KNOWN_HEADERS.
SECTION_HEADERS: frozenset[str] = frozenset(
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


@dataclass(frozen=True)
class PageLines:
    """Lines split from one PDF page.

    page_number is 1-based to match PDF conventions.
    lines preserves the original order from the page text.
    """

    page_number: int
    lines: tuple[str, ...]


def split_page(page_number: int, text: str) -> PageLines:
    """Split one page's text string into an ordered line list."""
    return PageLines(page_number=page_number, lines=tuple(text.splitlines()))


def pages_to_line_lists(page_texts: list[str]) -> tuple[PageLines, ...]:
    """Convert a list of raw page strings (1-indexed) into PageLines objects."""
    return tuple(split_page(i + 1, text) for i, text in enumerate(page_texts))


def flatten_lines(pages: Sequence[PageLines]) -> tuple[str, ...]:
    """Concatenate all page line lists in page order into one flat sequence."""
    out: list[str] = []
    for page in pages:
        out.extend(page.lines)
    return tuple(out)


def drop_empty(lines: Sequence[str]) -> tuple[str, ...]:
    """Remove lines that are empty or contain only whitespace."""
    return tuple(line for line in lines if line.strip())


def slice_section(
    lines: Sequence[str],
    header: str,
    known_headers: frozenset[str] = SECTION_HEADERS,
) -> tuple[str, ...]:
    """Return lines belonging to the section introduced by *header*.

    Scans for the first line whose stripped lower-case text equals
    *header* (stripped, lower-cased), then collects subsequent lines
    until another token from *known_headers* is encountered or the
    stream ends.  The header line itself is excluded from the result.

    Returns an empty tuple when *header* is not found.

    Hidden invariant: a header may appear mid-stream on a line by itself;
    lines whose stripped form matches *header* exactly trigger the slice.
    """
    needle = header.strip().lower()
    collecting = False
    result: list[str] = []

    for line in lines:
        token = line.strip().lower()
        if not collecting:
            if token == needle:
                collecting = True
        else:
            if token in known_headers:
                break
            result.append(line)

    return tuple(result)
