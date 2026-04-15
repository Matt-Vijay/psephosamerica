"""Tests for src/parse/disclosures/parse_result.py.

All tests are pure — no DB, no network, no file I/O.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.parse.disclosures.models import (
    Chamber,
    Filing,
    FilingType,
    Holding,
    OutsidePosition,
    OwnerType,
    Transaction,
    TransactionType,
)
from src.parse.disclosures.parse_result import ParserMeta, ParseResult, empty_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

META = ParserMeta(parser_name="senate_text", parser_version="1.0")


def _filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="A000001",
        chamber=Chamber.SENATE,
        filing_year=2023,
        filing_type=FilingType.ANNUAL,
        filed_at=date(2024, 5, 15),
        filing_period_start=date(2023, 1, 1),
        filing_period_end=date(2023, 12, 31),
        amendment_number=0,
        is_amended=False,
        source_record_id="FILING-001",
    )
    defaults.update(kwargs)
    return Filing(**defaults)


def _holding(line_number: int = 1) -> Holding:
    return Holding(
        line_number=line_number,
        owner_type=OwnerType.SELF,
        issuer_name="Acme Corp",
        issuer_ticker="ACME",
        value_min=Decimal("15001"),
        value_max=Decimal("50000"),
    )


def _transaction(line_number: int = 1) -> Transaction:
    return Transaction(
        line_number=line_number,
        owner_type=OwnerType.SELF,
        issuer_name="Acme Corp",
        transaction_type=TransactionType.PURCHASE,
        transaction_date=date(2023, 6, 1),
        amount_min=Decimal("1001"),
        amount_max=Decimal("15000"),
    )


def _outside_position(line_number: int = 1) -> OutsidePosition:
    return OutsidePosition(
        line_number=line_number,
        owner_type=OwnerType.SELF,
        entity_name="Acme Foundation",
        position_title="Board Member",
        from_date=date(2020, 1, 1),
    )


# ---------------------------------------------------------------------------
# ParserMeta
# ---------------------------------------------------------------------------


def test_parser_meta_required_field():
    meta = ParserMeta(parser_name="house_ocr")
    assert meta.parser_name == "house_ocr"


def test_parser_meta_defaults():
    meta = ParserMeta(parser_name="senate_text")
    assert meta.parser_version == "1.0"
    assert meta.parse_warnings == ()


def test_parser_meta_with_warnings():
    meta = ParserMeta(
        parser_name="house_ocr",
        parse_warnings=("low confidence on page 3", "missing section header"),
    )
    assert len(meta.parse_warnings) == 2
    assert "low confidence on page 3" in meta.parse_warnings


def test_parser_meta_is_immutable():
    meta = ParserMeta(parser_name="senate_text")
    with pytest.raises((AttributeError, TypeError)):
        meta.parser_name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ParseResult construction
# ---------------------------------------------------------------------------


def test_parse_result_stores_all_fields():
    filing = _filing()
    holdings = (_holding(1), _holding(2))
    transactions = (_transaction(1),)
    outside_positions = (_outside_position(1),)

    result = ParseResult(
        filing=filing,
        holdings=holdings,
        transactions=transactions,
        outside_positions=outside_positions,
        meta=META,
    )

    assert result.filing is filing
    assert result.holdings == holdings
    assert result.transactions == transactions
    assert result.outside_positions == outside_positions
    assert result.meta is META


def test_parse_result_is_immutable():
    result = ParseResult(
        filing=_filing(),
        holdings=(_holding(),),
        transactions=(),
        outside_positions=(),
        meta=META,
    )
    with pytest.raises((AttributeError, TypeError)):
        result.holdings = ()  # type: ignore[misc]


def test_parse_result_line_items_are_tuples():
    result = ParseResult(
        filing=_filing(),
        holdings=(_holding(),),
        transactions=(_transaction(),),
        outside_positions=(_outside_position(),),
        meta=META,
    )
    assert isinstance(result.holdings, tuple)
    assert isinstance(result.transactions, tuple)
    assert isinstance(result.outside_positions, tuple)


def test_parse_result_empty_line_items():
    result = ParseResult(
        filing=_filing(),
        holdings=(),
        transactions=(),
        outside_positions=(),
        meta=META,
    )
    assert len(result.holdings) == 0
    assert len(result.transactions) == 0
    assert len(result.outside_positions) == 0


# ---------------------------------------------------------------------------
# empty_result helper
# ---------------------------------------------------------------------------


def test_empty_result_has_no_line_items():
    filing = _filing()
    result = empty_result(filing, META)

    assert result.filing is filing
    assert result.holdings == ()
    assert result.transactions == ()
    assert result.outside_positions == ()
    assert result.meta is META


def test_empty_result_returns_parse_result_instance():
    result = empty_result(_filing(), META)
    assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# Compatibility with transform.transform_filing inputs
# ---------------------------------------------------------------------------


def test_parse_result_unpacks_to_transform_args():
    """Verify ParseResult fields map cleanly to transform_filing's positional args."""
    from src.parse.disclosures.transform import ParseContext, transform_filing

    filing = _filing()
    holdings = [_holding(1), _holding(2)]
    transactions = [_transaction(1)]
    outside_positions = [_outside_position(1)]

    result = ParseResult(
        filing=filing,
        holdings=tuple(holdings),
        transactions=tuple(transactions),
        outside_positions=tuple(outside_positions),
        meta=META,
    )

    ctx = ParseContext(parse_run_id=1, source_artifact_id=2, ingestion_run_id=3)
    transform_result = transform_filing(
        result.filing,
        list(result.holdings),
        list(result.transactions),
        list(result.outside_positions),
        ctx,
    )

    assert transform_result.disclosure.member_bioguide_id == "A000001"
    assert len(transform_result.holdings) == 2
    assert len(transform_result.transactions) == 1
    assert len(transform_result.outside_positions) == 1
