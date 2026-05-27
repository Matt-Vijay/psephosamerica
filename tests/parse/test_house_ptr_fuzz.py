"""Fuzz test: the House PTR text parser must never crash on arbitrary input.

Disclosure PDFs are OCR'd, so the parser receives noisy, malformed, and
adversarial text. It must always return a ParseResult (degrading to empty
sections) rather than raising. Seeded stdlib randomness (no hypothesis offline).
"""

from __future__ import annotations

import random
import string

from src.parse.disclosures.house_ptr_text import parse_house_ptr
from src.parse.disclosures.parse_result import ParseResult

# Tokens that resemble real PTR table structure, mixed with noise, so the fuzz
# exercises section/table-detection paths rather than only trivial rejection.
_TOKENS = [
    "Asset",
    "Transaction",
    "Date",
    "Amount",
    "$1,001 - $15,000",
    "$15,001 - $50,000",
    "P",
    "S",
    "01/15/2024",
    "2024-01-15",
    "AAPL",
    "Honeywell International Inc.",
    "Filing ID",
    "Notification Date",
    "Owner",
    "SP",
    "JT",
    "Description",
    "(partial)",
    "Stock",
    "Bond",
    "",
    "ID#",
]


def _rand_line(rng: random.Random) -> str:
    parts = [rng.choice(_TOKENS) for _ in range(rng.randint(0, 7))]
    if rng.random() < 0.3:
        noise_len = rng.randint(0, 40)
        parts.append("".join(rng.choice(string.printable) for _ in range(noise_len)))
    return "  ".join(parts)


def _rand_page(rng: random.Random) -> str:
    return "\n".join(_rand_line(rng) for _ in range(rng.randint(0, 50)))


def test_parse_house_ptr_returns_result_on_fuzzed_text() -> None:
    rng = random.Random(2024)
    for _ in range(400):
        pages = [_rand_page(rng) for _ in range(rng.randint(0, 5))]
        result = parse_house_ptr(pages, member_bioguide_id="A000001")
        assert isinstance(result, ParseResult)
        assert isinstance(result.holdings, tuple)
        assert isinstance(result.transactions, tuple)
        assert isinstance(result.outside_positions, tuple)


def test_parse_house_ptr_handles_degenerate_inputs() -> None:
    for pages in ([], [""], ["\n\n\n"], ["   "], ["\x00\x01\x02"], ["a" * 10000]):
        result = parse_house_ptr(pages, member_bioguide_id="")
        assert isinstance(result, ParseResult)
