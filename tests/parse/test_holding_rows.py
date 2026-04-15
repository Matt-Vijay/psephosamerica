"""Tests for holding row parsing.

All tests are pure: no DB, no network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.parse.disclosures.holding_rows import (
    HoldingColumnMap,
    holding_from_cells,
    holding_rows_from_table,
)
from src.parse.disclosures.models import Holding, OwnerType


# ---------------------------------------------------------------------------
# Shared builder — thin default wrapper around holding_from_cells
# ---------------------------------------------------------------------------


def _h(**kw) -> Holding:
    kw.setdefault("line_number", 1)
    kw.setdefault("owner_raw", "self")
    kw.setdefault("issuer_name_raw", "Corp")
    return holding_from_cells(**kw)


# ---------------------------------------------------------------------------
# holding_from_cells — field mapping
# ---------------------------------------------------------------------------


class TestHoldingFromCells:
    def test_minimal_required_fields(self) -> None:
        h = _h(issuer_name_raw="Apple Inc")
        assert isinstance(h, Holding)
        assert h.line_number == 1
        assert h.owner_type == OwnerType.SELF
        assert h.issuer_name == "Apple Inc"

    def test_all_optional_fields_mapped(self) -> None:
        h = _h(
            line_number=3,
            owner_raw="sp",
            issuer_name_raw="Microsoft Corp",
            ticker_raw="MSFT",
            asset_description_raw="Common Stock",
            asset_category_raw="equity",
            value_label_raw="$1,001 - $15,000",
            income_label_raw="$15,001 - $50,000",
            is_liquid_raw="Y",
            source_record_id="H-003",
        )
        assert h.line_number == 3
        assert h.owner_type == OwnerType.SPOUSE
        assert h.issuer_name == "Microsoft Corp"
        assert h.issuer_ticker == "MSFT"
        assert h.asset_description == "Common Stock"
        assert h.asset_category == "equity"
        assert h.value_min == Decimal("1001")
        assert h.value_max == Decimal("15000")
        assert h.value_label == "$1,001 - $15,000"
        assert h.income_min == Decimal("15001")
        assert h.income_max == Decimal("50000")
        assert h.income_label == "$15,001 - $50,000"
        assert h.is_liquid is True
        assert h.source_record_id == "H-003"

    def test_line_number_preserved(self) -> None:
        for n in (1, 42, 999):
            h = _h(line_number=n, issuer_name_raw="X")
            assert h.line_number == n


# ---------------------------------------------------------------------------
# holding_from_cells — owner type normalization
# ---------------------------------------------------------------------------


class TestOwnerNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("self", OwnerType.SELF),
            ("SELF", OwnerType.SELF),
            ("sp", OwnerType.SPOUSE),
            ("spouse", OwnerType.SPOUSE),
            ("jt", OwnerType.JOINT),
            ("joint", OwnerType.JOINT),
            ("dc", OwnerType.DEPENDENT),
            ("dep. child", OwnerType.DEPENDENT),
            ("dependent", OwnerType.DEPENDENT),
            ("trust", OwnerType.TRUST),
            ("unknown_label", OwnerType.OTHER),
            ("", OwnerType.OTHER),
        ],
    )
    def test_owner_label_variants(self, raw: str, expected: OwnerType) -> None:
        h = _h(owner_raw=raw)
        assert h.owner_type == expected


# ---------------------------------------------------------------------------
# holding_from_cells — issuer name cleanup
# ---------------------------------------------------------------------------


class TestIssuerNameCleanup:
    def test_whitespace_collapsed(self) -> None:
        h = _h(issuer_name_raw="  Apple   Inc  ")
        assert h.issuer_name == "Apple Inc"

    def test_filing_id_noise_stripped(self) -> None:
        h = _h(issuer_name_raw="Apple Inc (filing id: 123)")
        assert "filing id" not in h.issuer_name.lower()

    def test_bracket_noise_stripped(self) -> None:
        h = _h(issuer_name_raw="Tesla [amended]")
        assert "[amended]" not in h.issuer_name


# ---------------------------------------------------------------------------
# holding_from_cells — amount range resolution
# ---------------------------------------------------------------------------


class TestAmountRangeResolution:
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
    def test_recognized_value_labels_resolve(
        self, label: str, expected_min: Decimal, expected_max: Decimal
    ) -> None:
        h = _h(value_label_raw=label)
        assert h.value_min == expected_min
        assert h.value_max == expected_max
        assert h.value_label == label

    def test_unrecognized_value_label_preserves_label_with_none_decimals(self) -> None:
        h = _h(value_label_raw="something weird")
        assert h.value_min is None
        assert h.value_max is None
        assert h.value_label == "something weird"

    def test_recognized_income_label_resolves(self) -> None:
        h = _h(income_label_raw="$1,001 - $15,000")
        assert h.income_min == Decimal("1001")
        assert h.income_max == Decimal("15000")

    def test_unrecognized_income_label_preserves_label_with_none_decimals(self) -> None:
        h = _h(income_label_raw="bogus range")
        assert h.income_min is None
        assert h.income_max is None
        assert h.income_label == "bogus range"

    def test_absent_value_label_gives_none_decimals_and_none_label(self) -> None:
        h = _h()
        assert h.value_min is None
        assert h.value_max is None
        assert h.value_label is None

    def test_whitespace_only_value_label_treated_as_absent(self) -> None:
        h = _h(value_label_raw="   ")
        assert h.value_label is None
        assert h.value_min is None


# ---------------------------------------------------------------------------
# holding_from_cells — is_liquid parsing
# ---------------------------------------------------------------------------


class TestIsLiquidParsing:
    @pytest.mark.parametrize("raw", ["Y", "y", "yes", "YES", "1", "x", "X", "true", "True"])
    def test_truthy_values(self, raw: str) -> None:
        assert _h(is_liquid_raw=raw).is_liquid is True

    @pytest.mark.parametrize("raw", ["N", "n", "no", "NO", "0", "false", "False"])
    def test_falsy_values(self, raw: str) -> None:
        assert _h(is_liquid_raw=raw).is_liquid is False

    @pytest.mark.parametrize("raw", [None, "maybe", "unknown", "  "])
    def test_unrecognized_or_absent_gives_none(self, raw: str | None) -> None:
        assert _h(is_liquid_raw=raw).is_liquid is None


# ---------------------------------------------------------------------------
# holding_from_cells — optional string fields
# ---------------------------------------------------------------------------


class TestOptionalStringFields:
    def test_empty_ticker_becomes_none(self) -> None:
        assert _h(ticker_raw="").issuer_ticker is None

    def test_whitespace_ticker_becomes_none(self) -> None:
        assert _h(ticker_raw="   ").issuer_ticker is None

    def test_valid_ticker_preserved(self) -> None:
        assert _h(ticker_raw="AAPL").issuer_ticker == "AAPL"

    def test_absent_optional_fields_are_none(self) -> None:
        h = _h()
        assert h.issuer_ticker is None
        assert h.asset_description is None
        assert h.asset_category is None
        assert h.source_record_id is None

    def test_source_record_id_passed_through(self) -> None:
        assert _h(source_record_id="ROW-42").source_record_id == "ROW-42"


# ---------------------------------------------------------------------------
# holding_from_cells — return type is frozen Holding
# ---------------------------------------------------------------------------


class TestHoldingIsFrozen:
    def test_returns_holding_instance(self) -> None:
        assert isinstance(_h(), Holding)

    def test_holding_is_immutable(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            _h().line_number = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# holding_rows_from_table — basic operation
# ---------------------------------------------------------------------------


def _default_col_map(**kwargs: int) -> HoldingColumnMap:
    defaults = dict(owner=0, issuer_name=1, ticker=2, value_label=3, income_label=4)
    defaults.update(kwargs)
    return HoldingColumnMap(**defaults)  # type: ignore[arg-type]


class TestHoldingRowsFromTable:
    def test_empty_table_returns_empty_list(self) -> None:
        result = holding_rows_from_table([], _default_col_map())
        assert result == []

    def test_single_data_row_parsed(self) -> None:
        rows = [["self", "Apple Inc", "AAPL", "$1,001 - $15,000", ""]]
        result = holding_rows_from_table(rows, _default_col_map())
        assert len(result) == 1
        h = result[0]
        assert h.issuer_name == "Apple Inc"
        assert h.issuer_ticker == "AAPL"
        assert h.owner_type == OwnerType.SELF
        assert h.value_min == Decimal("1001")
        assert h.value_max == Decimal("15000")

    def test_multiple_rows_in_order(self) -> None:
        rows = [
            ["self", "Apple Inc", "AAPL", "", ""],
            ["sp", "Tesla Inc", "TSLA", "", ""],
            ["jt", "Google LLC", "GOOGL", "", ""],
        ]
        result = holding_rows_from_table(rows, _default_col_map())
        assert len(result) == 3
        assert [h.issuer_name for h in result] == ["Apple Inc", "Tesla Inc", "Google LLC"]

    def test_line_numbers_start_at_one_by_default(self) -> None:
        rows = [
            ["self", "Corp A", "", "", ""],
            ["self", "Corp B", "", "", ""],
        ]
        result = holding_rows_from_table(rows, _default_col_map())
        assert [h.line_number for h in result] == [1, 2]

    def test_start_line_number_respected(self) -> None:
        rows = [
            ["self", "Corp A", "", "", ""],
            ["self", "Corp B", "", "", ""],
        ]
        result = holding_rows_from_table(rows, _default_col_map(), start_line_number=5)
        assert [h.line_number for h in result] == [5, 6]

    def test_blank_issuer_rows_skipped(self) -> None:
        # Callers strip named header rows before calling; this tests blank/empty skip.
        rows = [
            ["", "", "", "", ""],  # blank separator
            ["self", "Apple Inc", "AAPL", "", ""],  # data
        ]
        result = holding_rows_from_table(rows, _default_col_map())
        assert len(result) == 1
        assert result[0].issuer_name == "Apple Inc"

    def test_whitespace_only_issuer_skipped(self) -> None:
        rows = [
            ["self", "   ", "", "", ""],
            ["self", "Real Corp", "", "", ""],
        ]
        result = holding_rows_from_table(rows, _default_col_map())
        assert len(result) == 1
        assert result[0].issuer_name == "Real Corp"

    def test_skipped_rows_do_not_advance_line_number(self) -> None:
        rows = [
            ["self", "Corp A", "", "", ""],  # line 1
            ["", "", "", "", ""],  # blank — skipped, no line number advance
            ["self", "Corp B", "", "", ""],  # line 2
        ]
        result = holding_rows_from_table(rows, _default_col_map())
        assert [h.line_number for h in result] == [1, 2]

    def test_absent_optional_columns_yield_none(self) -> None:
        col_map = HoldingColumnMap(owner=0, issuer_name=1)  # no optional cols
        rows = [["self", "Corp X"]]
        result = holding_rows_from_table(rows, col_map)
        h = result[0]
        assert h.issuer_ticker is None
        assert h.value_min is None
        assert h.income_label is None

    def test_column_index_out_of_row_length_gives_none(self) -> None:
        col_map = HoldingColumnMap(owner=0, issuer_name=1, ticker=99)
        rows = [["self", "Corp X"]]
        result = holding_rows_from_table(rows, col_map)
        assert result[0].issuer_ticker is None

    def test_is_liquid_column_parsed(self) -> None:
        col_map = HoldingColumnMap(owner=0, issuer_name=1, is_liquid=2)
        rows = [
            ["self", "Corp A", "Y"],
            ["self", "Corp B", "N"],
            ["self", "Corp C", ""],
        ]
        result = holding_rows_from_table(rows, col_map)
        assert result[0].is_liquid is True
        assert result[1].is_liquid is False
        assert result[2].is_liquid is None

    def test_asset_description_and_category_mapped(self) -> None:
        col_map = HoldingColumnMap(
            owner=0, issuer_name=1, asset_description=2, asset_category=3
        )
        rows = [["self", "Corp X", "Common Stock", "equity"]]
        result = holding_rows_from_table(rows, col_map)
        h = result[0]
        assert h.asset_description == "Common Stock"
        assert h.asset_category == "equity"

    def test_source_record_id_column_mapped(self) -> None:
        col_map = HoldingColumnMap(owner=0, issuer_name=1, source_record_id=2)
        rows = [["self", "Corp X", "ROW-007"]]
        result = holding_rows_from_table(rows, col_map)
        assert result[0].source_record_id == "ROW-007"

    def test_unrecognized_value_label_preserved_in_table_parse(self) -> None:
        col_map = HoldingColumnMap(owner=0, issuer_name=1, value_label=2)
        rows = [["self", "Corp X", "over nine thousand"]]
        result = holding_rows_from_table(rows, col_map)
        h = result[0]
        assert h.value_label == "over nine thousand"
        assert h.value_min is None
        assert h.value_max is None

    def test_returns_list_of_holding_instances(self) -> None:
        rows = [["self", "Corp A", "", "", ""], ["sp", "Corp B", "", "", ""]]
        result = holding_rows_from_table(rows, _default_col_map())
        assert all(isinstance(h, Holding) for h in result)
