"""Typed aggregate contract for a fully parsed disclosure document.

Parsers produce a ParseResult.  Downstream callers — parser_dispatch,
runtime parsing, and the transform/load path — consume it.

No parsing is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.parse.disclosures.models import Filing, Holding, OutsidePosition, Transaction


@dataclass(frozen=True)
class ParserMeta:
    """Metadata emitted by the parser alongside extracted line items.

    parser_name identifies which parser produced this result (e.g.
    "senate_text", "house_ocr").  parse_warnings carries non-fatal
    extraction issues that should be logged but do not block loading.
    """

    parser_name: str
    parser_version: str = "1.0"
    parse_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    """Complete output of one parse pass over a single disclosure document.

    Aggregates the filing identity record, all extracted line items, and
    parser metadata into one immutable typed contract.

    Line items are tuples so the aggregate is fully immutable once built.
    Use list(result.holdings) etc. when a mutable sequence is needed downstream.
    """

    filing: Filing
    holdings: tuple[Holding, ...]
    transactions: tuple[Transaction, ...]
    outside_positions: tuple[OutsidePosition, ...]
    meta: ParserMeta


def empty_result(filing: Filing, meta: ParserMeta) -> ParseResult:
    """Return a ParseResult with no line items.

    Useful for stub parsers, filings with no extractable sections, and tests.
    """
    return ParseResult(
        filing=filing,
        holdings=(),
        transactions=(),
        outside_positions=(),
        meta=meta,
    )
