"""Fuzz test: the Senate EFD text parser must never crash on arbitrary input."""

from __future__ import annotations

import datetime as dt
import random
import string

from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult
from src.parse.disclosures.senate_text import parse_senate_text

# Senate parsing keys on 2+ spaces between columns; mix structured tokens
# (joined with double spaces) with random noise.
_TOKENS = [
    "Part 4. Asset",
    "Part 4b. Transactions",
    "Honeywell International Inc.",
    "Stock",
    "$1,001 - $15,000",
    "Purchase",
    "Sale",
    "01/15/2024",
    "Spouse",
    "Self",
    "Joint",
    "Owner",
    "Asset Type",
    "Amount",
    "Comment",
    "None disclosed",
    "",
]


def _filing(filing_type: FilingType) -> Filing:
    return Filing(
        member_bioguide_id="S000001",
        chamber=Chamber.SENATE,
        filing_year=2023,
        filing_type=filing_type,
        filed_at=dt.date(2024, 5, 15),
        source_record_id="DOC-FUZZ-1",
    )


def _rand_line(rng: random.Random) -> str:
    parts = [rng.choice(_TOKENS) for _ in range(rng.randint(0, 6))]
    if rng.random() < 0.3:
        parts.append("".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40))))
    return "  ".join(parts)  # double space = column separator


def _rand_page(rng: random.Random) -> str:
    return "\n".join(_rand_line(rng) for _ in range(rng.randint(0, 50)))


def test_parse_senate_text_returns_result_on_fuzzed_text() -> None:
    rng = random.Random(7)
    for _ in range(400):
        pages = [_rand_page(rng) for _ in range(rng.randint(0, 5))]
        filing = _filing(rng.choice([FilingType.ANNUAL, FilingType.PTR]))
        result = parse_senate_text(pages, filing)
        assert isinstance(result, ParseResult)
        assert isinstance(result.holdings, tuple)
        assert isinstance(result.transactions, tuple)
        assert isinstance(result.outside_positions, tuple)


def test_parse_senate_text_handles_degenerate_inputs() -> None:
    for pages in ([], [""], ["\n\n\n"], ["   "], ["\x00\x01\x02"], ["a" * 10000]):
        result = parse_senate_text(pages, _filing(FilingType.ANNUAL))
        assert isinstance(result, ParseResult)
