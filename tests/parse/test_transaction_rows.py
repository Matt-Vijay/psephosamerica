"""Tests for transaction row parsing helpers.

All tests are pure: no DB, no network, no file I/O.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.parse.disclosures.models import OwnerType, Transaction, TransactionType
from src.parse.disclosures.transaction_rows import (
    transaction_from_cells,
    transaction_rows_from_table,
)


# ---------------------------------------------------------------------------
# Minimal valid row helper
# ---------------------------------------------------------------------------


def _cells(**overrides) -> dict:
    defaults = dict(
        line_number=1,
        owner_raw="self",
        issuer_name_raw="Apple Inc",
        tx_type_raw="P",
        tx_date_raw="06/15/2023",
        amount_raw="$1,001 - $15,000",
    )
    defaults.update(overrides)
    return defaults


def _row(**overrides) -> dict[str, str]:
    defaults = {
        "owner": "self",
        "issuer_name": "Apple Inc",
        "tx_type": "P",
        "tx_date": "06/15/2023",
        "amount": "$1,001 - $15,000",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# transaction_from_cells — field mapping
# ---------------------------------------------------------------------------


class TestTransactionFromCells:
    def test_returns_transaction_instance(self) -> None:
        t = transaction_from_cells(**_cells())
        assert isinstance(t, Transaction)

    def test_line_number_preserved(self) -> None:
        t = transaction_from_cells(**_cells(line_number=7))
        assert t.line_number == 7

    def test_issuer_name_cleaned(self) -> None:
        t = transaction_from_cells(**_cells(issuer_name_raw="  Apple Inc  "))
        assert t.issuer_name == "Apple Inc"

    def test_issuer_name_noise_stripped(self) -> None:
        t = transaction_from_cells(**_cells(issuer_name_raw="Apple Inc (filing id: 123)"))
        assert "filing id" not in t.issuer_name

    def test_transaction_date_slash_format(self) -> None:
        t = transaction_from_cells(**_cells(tx_date_raw="01/31/2023"))
        assert t.transaction_date == date(2023, 1, 31)

    def test_transaction_date_iso_format(self) -> None:
        t = transaction_from_cells(**_cells(tx_date_raw="2023-01-31"))
        assert t.transaction_date == date(2023, 1, 31)

    def test_transaction_date_two_digit_year(self) -> None:
        t = transaction_from_cells(**_cells(tx_date_raw="06/15/23"))
        assert t.transaction_date == date(2023, 6, 15)

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized transaction date"):
            transaction_from_cells(**_cells(tx_date_raw="not-a-date"))

    def test_amount_label_recognized(self) -> None:
        t = transaction_from_cells(**_cells(amount_raw="$1,001 - $15,000"))
        assert t.amount_min == Decimal("1001")
        assert t.amount_max == Decimal("15000")
        assert t.amount_label == "$1,001 - $15,000"

    def test_amount_label_unrecognized_preserves_label_and_leaves_decimals_none(self) -> None:
        t = transaction_from_cells(**_cells(amount_raw="Unknown range"))
        assert t.amount_label == "Unknown range"
        assert t.amount_min is None
        assert t.amount_max is None

    def test_empty_amount_raw_yields_none_label(self) -> None:
        t = transaction_from_cells(**_cells(amount_raw=""))
        assert t.amount_label is None
        assert t.amount_min is None
        assert t.amount_max is None

    def test_amount_raw_whitespace_only_yields_none_label(self) -> None:
        t = transaction_from_cells(**_cells(amount_raw="   "))
        assert t.amount_label is None

    def test_ticker_set_when_provided(self) -> None:
        t = transaction_from_cells(**_cells(issuer_ticker_raw="AAPL"))
        assert t.issuer_ticker == "AAPL"

    def test_ticker_none_when_empty(self) -> None:
        t = transaction_from_cells(**_cells(issuer_ticker_raw=""))
        assert t.issuer_ticker is None

    def test_ticker_none_when_whitespace(self) -> None:
        t = transaction_from_cells(**_cells(issuer_ticker_raw="  "))
        assert t.issuer_ticker is None

    def test_description_set_when_provided(self) -> None:
        t = transaction_from_cells(**_cells(asset_description_raw="Common Stock"))
        assert t.asset_description == "Common Stock"

    def test_description_none_when_empty(self) -> None:
        t = transaction_from_cells(**_cells(asset_description_raw=""))
        assert t.asset_description is None

    def test_source_record_id_preserved(self) -> None:
        t = transaction_from_cells(**_cells(source_record_id="TX-042"))
        assert t.source_record_id == "TX-042"

    def test_source_record_id_none_by_default(self) -> None:
        t = transaction_from_cells(**_cells())
        assert t.source_record_id is None

    def test_transaction_is_frozen(self) -> None:
        t = transaction_from_cells(**_cells())
        with pytest.raises((AttributeError, TypeError)):
            t.line_number = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# transaction_from_cells — owner normalization
# ---------------------------------------------------------------------------


class TestOwnerNormalization:
    def test_self(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="self"))
        assert t.owner_type == OwnerType.SELF

    def test_spouse_abbreviation(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="SP"))
        assert t.owner_type == OwnerType.SPOUSE

    def test_spouse_full(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="spouse"))
        assert t.owner_type == OwnerType.SPOUSE

    def test_joint_abbreviation(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="JT"))
        assert t.owner_type == OwnerType.JOINT

    def test_dependent_abbreviation(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="DC"))
        assert t.owner_type == OwnerType.DEPENDENT

    def test_dependent_full(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="Dependent Child"))
        assert t.owner_type == OwnerType.DEPENDENT

    def test_unknown_owner_maps_to_other(self) -> None:
        t = transaction_from_cells(**_cells(owner_raw="???"))
        assert t.owner_type == OwnerType.OTHER


# ---------------------------------------------------------------------------
# transaction_from_cells — transaction type normalization
# ---------------------------------------------------------------------------


class TestTransactionTypeNormalization:
    def test_purchase_abbreviation(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="P"))
        assert t.transaction_type == TransactionType.PURCHASE

    def test_purchase_full(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="purchase"))
        assert t.transaction_type == TransactionType.PURCHASE

    def test_sale_abbreviation(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="S"))
        assert t.transaction_type == TransactionType.SALE

    def test_sale_full(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="sale"))
        assert t.transaction_type == TransactionType.SALE

    def test_sale_full_variant(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="Sale (full)"))
        assert t.transaction_type == TransactionType.SALE

    def test_exchange(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="E"))
        assert t.transaction_type == TransactionType.EXCHANGE

    def test_gift(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="Gift"))
        assert t.transaction_type == TransactionType.GIFT

    def test_income(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="Income"))
        assert t.transaction_type == TransactionType.INCOME

    def test_dividend_maps_to_income(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="Dividend"))
        assert t.transaction_type == TransactionType.INCOME

    def test_unknown_type_maps_to_other(self) -> None:
        t = transaction_from_cells(**_cells(tx_type_raw="???"))
        assert t.transaction_type == TransactionType.OTHER


# ---------------------------------------------------------------------------
# transaction_from_cells — amount range coverage
# ---------------------------------------------------------------------------


class TestAmountRangeCoverage:
    @pytest.mark.parametrize(
        "label,expected_min,expected_max",
        [
            ("$1 - $1,000", Decimal("1"), Decimal("1000")),
            ("$1,001 - $15,000", Decimal("1001"), Decimal("15000")),
            ("$15,001 - $50,000", Decimal("15001"), Decimal("50000")),
            ("$50,001 - $100,000", Decimal("50001"), Decimal("100000")),
            ("$100,001 - $250,000", Decimal("100001"), Decimal("250000")),
            ("$250,001 - $500,000", Decimal("250001"), Decimal("500000")),
            ("$500,001 - $1,000,000", Decimal("500001"), Decimal("1000000")),
            ("$1,000,001 - $5,000,000", Decimal("1000001"), Decimal("5000000")),
            ("Over $50,000,000", Decimal("50000001"), Decimal("50000001")),
        ],
    )
    def test_recognized_labels(self, label: str, expected_min: Decimal, expected_max: Decimal) -> None:
        t = transaction_from_cells(**_cells(amount_raw=label))
        assert t.amount_min == expected_min
        assert t.amount_max == expected_max


# ---------------------------------------------------------------------------
# transaction_rows_from_table
# ---------------------------------------------------------------------------


class TestTransactionRowsFromTable:
    def test_empty_table_returns_empty_list(self) -> None:
        assert transaction_rows_from_table([]) == []

    def test_single_row(self) -> None:
        result = transaction_rows_from_table([_row()])
        assert len(result) == 1
        assert isinstance(result[0], Transaction)

    def test_line_numbers_assigned_sequentially_from_one(self) -> None:
        rows = [_row(issuer_name=f"Corp{i}") for i in range(3)]
        result = transaction_rows_from_table(rows)
        assert [t.line_number for t in result] == [1, 2, 3]

    def test_custom_line_number_start(self) -> None:
        rows = [_row(), _row()]
        result = transaction_rows_from_table(rows, line_number_start=5)
        assert [t.line_number for t in result] == [5, 6]

    def test_row_fields_passed_through(self) -> None:
        rows = [_row(
            owner="SP",
            issuer_name="Tesla Inc",
            tx_type="S",
            tx_date="09/30/2023",
            amount="$50,001 - $100,000",
            ticker="TSLA",
            description="Common Stock",
            source_record_id="TX-007",
        )]
        t = transaction_rows_from_table(rows)[0]
        assert t.owner_type == OwnerType.SPOUSE
        assert t.issuer_name == "Tesla Inc"
        assert t.transaction_type == TransactionType.SALE
        assert t.transaction_date == date(2023, 9, 30)
        assert t.amount_min == Decimal("50001")
        assert t.amount_max == Decimal("100000")
        assert t.issuer_ticker == "TSLA"
        assert t.asset_description == "Common Stock"
        assert t.source_record_id == "TX-007"

    def test_optional_keys_default_gracefully(self) -> None:
        rows = [{"owner": "self", "issuer_name": "Acme", "tx_type": "P", "tx_date": "01/01/2023", "amount": ""}]
        t = transaction_rows_from_table(rows)[0]
        assert t.issuer_ticker is None
        assert t.asset_description is None
        assert t.source_record_id is None

    def test_preserves_order(self) -> None:
        names = ["Alpha Corp", "Beta Inc", "Gamma LLC"]
        rows = [_row(issuer_name=n) for n in names]
        result = transaction_rows_from_table(rows)
        assert [t.issuer_name for t in result] == names

    def test_multiple_rows_independent(self) -> None:
        rows = [
            _row(tx_type="P", issuer_name="Microsoft"),
            _row(tx_type="S", issuer_name="Google"),
        ]
        result = transaction_rows_from_table(rows)
        assert result[0].transaction_type == TransactionType.PURCHASE
        assert result[1].transaction_type == TransactionType.SALE

    def test_invalid_date_propagates(self) -> None:
        rows = [_row(tx_date="not-a-date")]
        with pytest.raises(ValueError, match="Unrecognized transaction date"):
            transaction_rows_from_table(rows)
