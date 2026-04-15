"""Canonical token sets for disclosure text parsing.

Single source of truth for the header vocabularies used across
text_extract (detection) and text_lines (section slicing).
"""

from __future__ import annotations

# Structural section headers that bound disclosure sections (lower-cased).
# Used by text_lines for section slicing and as the base for DETECTION_HEADERS.
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

# Full detection set used by text_extract: section headers plus PTR column-header
# tokens that appear as standalone lines in Periodic Transaction Reports.
DETECTION_HEADERS: frozenset[str] = SECTION_HEADERS | frozenset(
    {
        "transaction",
        "owner",
        "asset",
        "amount",
        "date",
    }
)
