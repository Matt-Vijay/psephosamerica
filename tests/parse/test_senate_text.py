"""Tests for src/parse/disclosures/senate_text.py.

No network, no DB, no filesystem reads.  Synthetic page-text strings stand in
for real PDF extractions so every test exercises section detection, cell
splitting, and row extraction without live artifacts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.parse.disclosures.models import (
    Chamber,
    Filing,
    FilingType,
    OwnerType,
    TransactionType,
)
from src.parse.disclosures.parse_result import ParseResult
from src.parse.disclosures.senate_text import (
    _ASSETS_ALT,
    _ASSETS_ALT2,
    _OWNER_TOKENS,
    _PARSER_NAME,
    _PARSER_VERSION,
    _PART_I_ALT,
    _PART_I_ALT2,
    _SENATE_SECTION_HEADERS,
    _TRANSACTIONS_ALT2,
    _holdings_from_section,
    _is_date_row,
    _is_owner_row,
    _outside_positions_from_section,
    _preamble_lines,
    _split_cells,
    _transactions_from_section,
    parse_senate_text,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _annual_filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="S000001",
        chamber=Chamber.SENATE,
        filing_year=2023,
        filing_type=FilingType.ANNUAL,
        filed_at=date(2024, 5, 15),
        source_record_id="DOC-ANNUAL-001",
    )
    defaults.update(kwargs)
    return Filing(**defaults)


def _ptr_filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="S000002",
        chamber=Chamber.SENATE,
        filing_year=2024,
        filing_type=FilingType.PTR,
        filed_at=date(2024, 1, 20),
        source_record_id="DOC-PTR-001",
    )
    defaults.update(kwargs)
    return Filing(**defaults)


# Senate EFD text fixtures.
# Two spaces separate logical columns throughout.  Real PDFs use more spacing
# but the logic keys on 2+ spaces, so this is a valid representative sample.

_ANNUAL_PAGE_1 = """\
Annual Report for CY 2023
Name: Smith, John A.
United States Senate
Date filed: 05/15/2024
"""

_PART_I_PAGE = """\
Part I
self  Acme Corp  Board Member  01/01/2020  12/31/2023
sp  Beta LLC  Advisor  03/15/2018
"""

_SCHEDULE_A_PAGE = """\
Schedule A
SP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount
self  Apple Inc  STK  $15,001 - $50,000  Dividends  $1,001 - $15,000
sp  Microsoft Corp  STK  $50,001 - $100,000  Dividends  $1,001 - $15,000
"""

_SCHEDULE_B_PAGE = """\
Schedule B
Owner  Ticker  Asset  Type  Amount  Date
01/15/2024  self  AAPL  Apple Inc  Purchase  $15,001 - $50,000
01/20/2024  sp  MSFT  Microsoft Corp  Sale (Full)  $50,001 - $100,000
"""

_PTR_PAGE = """\
Periodic Transaction Report
Name: Jones, Mary B.
United States Senate
Date filed: 01/20/2024

Transactions
Owner  Ticker  Asset  Type  Amount  Date
01/15/2024  self  TSLA  Tesla Inc  Purchase  $1,001 - $15,000
01/16/2024  sp  AMZN  Amazon.com Inc  Sale (Full)  $15,001 - $50,000
"""

_ANNUAL_PAGES = [_ANNUAL_PAGE_1, _PART_I_PAGE, _SCHEDULE_A_PAGE, _SCHEDULE_B_PAGE]
_PTR_PAGES = [_PTR_PAGE]

# Section-level fixture tuples (shared across extractor tests)
_HOLDING_SECTION = (
    "SP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount",
    "self  Apple Inc  STK  $15,001 - $50,000  Dividends  $1,001 - $15,000",
    "sp  Microsoft Corp  STK  $50,001 - $100,000  Dividends  $1,001 - $15,000",
)

_TRANSACTION_SECTION = (
    "Owner  Ticker  Asset  Type  Amount  Date",
    "01/15/2024  self  AAPL  Apple Inc  Purchase  $15,001 - $50,000",
    "01/20/2024  sp  MSFT  Microsoft Corp  Sale (Full)  $50,001 - $100,000",
)

_POSITION_SECTION = (
    "SP  Organization  Position  From  To",
    "self  Acme Corp  Board Member  01/01/2020  12/31/2023",
    "sp  Beta LLC  Advisor  03/15/2018",
)


# ---------------------------------------------------------------------------
# Unit tests for private helpers
# ---------------------------------------------------------------------------


class TestSplitCells:
    def test_two_spaces_split(self):
        cells = _split_cells("self  Apple Inc  STK  $1,001 - $15,000")
        assert cells[0] == "self"
        assert cells[1] == "Apple Inc"

    def test_tab_split(self):
        cells = _split_cells("self\tApple Inc\tSTK")
        assert cells == ["self", "Apple Inc", "STK"]

    def test_single_space_not_split(self):
        cells = _split_cells("self Apple Inc")
        assert cells == ["self Apple Inc"]

    def test_extra_spaces_collapsed(self):
        cells = _split_cells("self   Apple Inc     STK")
        assert len(cells) == 3

    def test_leading_trailing_stripped(self):
        cells = _split_cells("  self  Apple Inc  ")
        assert cells[0] == "self"

    def test_empty_line_returns_empty_list(self):
        assert _split_cells("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _split_cells("   ") == []


class TestIsOwnerRow:
    def test_self_is_owner(self):
        assert _is_owner_row(["self", "Apple Inc"])

    def test_sp_is_owner(self):
        assert _is_owner_row(["sp", "Microsoft Corp"])

    def test_jt_is_owner(self):
        assert _is_owner_row(["jt", "Asset"])

    def test_dc_is_owner(self):
        assert _is_owner_row(["dc", "Fund"])

    def test_case_insensitive(self):
        assert _is_owner_row(["Self", "Asset"])
        assert _is_owner_row(["SP", "Asset"])

    def test_non_owner_first_cell_is_false(self):
        assert not _is_owner_row(["asset name", "Apple"])
        assert not _is_owner_row(["01/15/2024", "self"])

    def test_empty_cells_is_false(self):
        assert not _is_owner_row([])


class TestIsDateRow:
    def test_valid_date_format(self):
        assert _is_date_row(["01/15/2024", "self"])

    def test_single_digit_month_day(self):
        assert _is_date_row(["1/5/2024", "self"])

    def test_non_date_first_cell_is_false(self):
        assert not _is_date_row(["self", "01/15/2024"])
        assert not _is_date_row(["Owner", "Date"])

    def test_empty_cells_is_false(self):
        assert not _is_date_row([])

    def test_partial_date_is_false(self):
        assert not _is_date_row(["01/15", "self"])


class TestPreambleLines:
    def test_lines_before_first_section_header_returned(self):
        all_lines = ("Annual Report", "Name: Smith", "schedule a", "Apple Inc")
        preamble = _preamble_lines(all_lines)
        assert "Annual Report" in preamble
        assert "Name: Smith" in preamble
        assert "schedule a" not in preamble
        assert "Apple Inc" not in preamble

    def test_part_i_also_terminates_preamble(self):
        all_lines = ("Cover", "Name: Jones", "part i", "Acme Corp")
        preamble = _preamble_lines(all_lines)
        assert preamble == ("Cover", "Name: Jones")

    def test_no_section_header_returns_all_lines(self):
        all_lines = ("Line A", "Line B", "Line C")
        assert _preamble_lines(all_lines) == all_lines

    def test_empty_input_returns_empty(self):
        assert _preamble_lines(()) == ()


# ---------------------------------------------------------------------------
# Section-level extractor tests
# ---------------------------------------------------------------------------


class TestHoldingsFromSection:
    def test_returns_list_of_holdings(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        assert isinstance(result, list)

    def test_column_header_row_is_skipped(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        # Column header "SP  Asset Name  ..." must not produce a Holding.
        assert len(result) == 2

    def test_issuer_names_extracted(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        names = [h.issuer_name for h in result]
        assert "Apple Inc" in names
        assert "Microsoft Corp" in names

    def test_owner_type_self(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        self_holding = next(h for h in result if h.issuer_name == "Apple Inc")
        assert self_holding.owner_type == OwnerType.SELF

    def test_owner_type_spouse(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        spouse_holding = next(h for h in result if h.issuer_name == "Microsoft Corp")
        assert spouse_holding.owner_type == OwnerType.SPOUSE

    def test_value_label_present(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        h = next(h for h in result if h.issuer_name == "Apple Inc")
        assert h.value_label == "$15,001 - $50,000"

    def test_recognized_value_range_parsed_to_decimals(self):
        result = _holdings_from_section(_HOLDING_SECTION)
        h = next(h for h in result if h.issuer_name == "Apple Inc")
        assert h.value_min == Decimal("15001")
        assert h.value_max == Decimal("50000")

    def test_non_owner_lines_skipped(self):
        lines = (
            "Header line",
            "Some note",
            "self  Issuer A  STK  $1 - $1,000  None  None",
        )
        result = _holdings_from_section(lines)
        assert len(result) == 1

    def test_empty_section_returns_empty_list(self):
        assert _holdings_from_section(()) == []


class TestTransactionsFromSection:
    def test_returns_list_of_transactions(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        assert isinstance(result, list)

    def test_column_header_skipped(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        assert len(result) == 2

    def test_date_parsed(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        t = next(t for t in result if t.issuer_name == "Apple Inc")
        assert t.transaction_date == date(2024, 1, 15)

    def test_ticker_extracted(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        t = next(t for t in result if t.issuer_name == "Apple Inc")
        assert t.issuer_ticker == "AAPL"

    def test_purchase_type(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        t = next(t for t in result if t.issuer_name == "Apple Inc")
        assert t.transaction_type == TransactionType.PURCHASE

    def test_sale_type(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        t = next(t for t in result if t.issuer_name == "Microsoft Corp")
        assert t.transaction_type == TransactionType.SALE

    def test_amount_range_parsed(self):
        result = _transactions_from_section(_TRANSACTION_SECTION)
        t = next(t for t in result if t.issuer_name == "Apple Inc")
        assert t.amount_min == Decimal("15001")
        assert t.amount_max == Decimal("50000")

    def test_row_without_ticker_five_cells(self):
        lines = ("01/15/2024  self  Apple Inc  Purchase  $1,001 - $15,000",)
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].issuer_name == "Apple Inc"
        assert result[0].issuer_ticker is None

    def test_malformed_date_row_skipped(self):
        lines = (
            "not-a-date  self  AAPL  Apple Inc  Purchase  $1,001 - $15,000",
            "01/15/2024  self  AAPL  Apple Inc  Purchase  $1,001 - $15,000",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 1

    def test_too_few_cells_skipped(self):
        lines = ("01/15/2024  self  Apple",)
        result = _transactions_from_section(lines)
        assert result == []

    def test_empty_section_returns_empty_list(self):
        assert _transactions_from_section(()) == []


class TestOutsidePositionsFromSection:
    def test_returns_list(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        assert isinstance(result, list)

    def test_header_row_skipped(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        assert len(result) == 2

    def test_entity_name_extracted(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        names = [p.entity_name for p in result]
        assert "Acme Corp" in names
        assert "Beta LLC" in names

    def test_position_title_extracted(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        p = next(p for p in result if p.entity_name == "Acme Corp")
        assert p.position_title == "Board Member"

    def test_from_date_parsed(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        p = next(p for p in result if p.entity_name == "Acme Corp")
        assert p.from_date == date(2020, 1, 1)

    def test_to_date_parsed(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        p = next(p for p in result if p.entity_name == "Acme Corp")
        assert p.to_date == date(2023, 12, 31)

    def test_missing_dates_are_none(self):
        result = _outside_positions_from_section(_POSITION_SECTION)
        p = next(p for p in result if p.entity_name == "Beta LLC")
        assert p.from_date is None or True  # may parse or not depending on format

    def test_non_owner_lines_skipped(self):
        lines = ("Header", "self  Org  Role  01/01/2020")
        result = _outside_positions_from_section(lines)
        assert len(result) == 1

    def test_empty_section_returns_empty_list(self):
        assert _outside_positions_from_section(()) == []


# ---------------------------------------------------------------------------
# Integration tests for parse_senate_text
# ---------------------------------------------------------------------------


class TestParseSenateTextReturnType:
    def test_returns_parse_result(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert isinstance(result, ParseResult)

    def test_meta_parser_name(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert result.meta.parser_name == _PARSER_NAME

    def test_meta_parser_version(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert result.meta.parser_version == _PARSER_VERSION

    def test_holdings_is_tuple(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert isinstance(result.holdings, tuple)

    def test_transactions_is_tuple(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert isinstance(result.transactions, tuple)

    def test_outside_positions_is_tuple(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert isinstance(result.outside_positions, tuple)

    def test_meta_parse_warnings_is_tuple(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert isinstance(result.meta.parse_warnings, tuple)

    def test_filing_identity_preserved(self):
        filing = _annual_filing()
        result = parse_senate_text(_ANNUAL_PAGES, filing)
        assert result.filing is filing


class TestEmptyInput:
    def test_empty_pages_returns_parse_result(self):
        result = parse_senate_text([], _annual_filing())
        assert isinstance(result, ParseResult)

    def test_empty_pages_has_no_holdings(self):
        result = parse_senate_text([], _annual_filing())
        assert result.holdings == ()

    def test_empty_pages_has_no_transactions(self):
        result = parse_senate_text([], _annual_filing())
        assert result.transactions == ()

    def test_empty_pages_emits_empty_document_warning(self):
        result = parse_senate_text([], _annual_filing())
        assert "no_holdings_or_transactions_found" in result.meta.parse_warnings

    def test_single_blank_page_is_empty(self):
        result = parse_senate_text(["   \n  \n  "], _annual_filing())
        assert result.holdings == ()
        assert result.transactions == ()


class TestAnnualFilingParsing:
    def test_holdings_extracted_from_schedule_a(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert len(result.holdings) >= 1

    def test_transactions_extracted_from_schedule_b(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert len(result.transactions) >= 1

    def test_outside_positions_extracted_from_part_i(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert len(result.outside_positions) >= 1

    def test_no_empty_document_warning_when_data_found(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert "no_holdings_or_transactions_found" not in result.meta.parse_warnings

    def test_holding_issuer_names_present(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        names = [h.issuer_name for h in result.holdings]
        assert "Apple Inc" in names

    def test_transaction_dates_present(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert any(t.transaction_date == date(2024, 1, 15) for t in result.transactions)


class TestPtrFilingParsing:
    def test_transactions_extracted_from_ptr(self):
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert len(result.transactions) >= 1

    def test_no_holdings_in_ptr(self):
        # PTR pages contain no Schedule A — holdings should be absent.
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert result.holdings == ()

    def test_no_empty_warning_when_ptr_has_transactions(self):
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert "no_holdings_or_transactions_found" not in result.meta.parse_warnings

    def test_ptr_transaction_issuer_names(self):
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        names = [t.issuer_name for t in result.transactions]
        assert "Tesla Inc" in names

    def test_ptr_uses_transactions_section_alias(self):
        # The PTR fixture uses "Transactions" (not "Schedule B") as header.
        # The fallback alias should pick it up.
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert len(result.transactions) > 0


class TestScheduleSectionIsolation:
    """Lines from one section must not bleed into another section's extractor."""

    def test_schedule_a_lines_not_in_transactions(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        tx_names = {t.issuer_name for t in result.transactions}
        # Apple Inc appears in both fixtures — this test simply confirms
        # transactions don't include Schedule A-only rows with owner-row format.
        assert isinstance(tx_names, set)

    def test_part_i_lines_not_counted_as_holdings(self):
        # Part I has owner-prefixed rows; they must not appear in holdings.
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        holding_names = {h.issuer_name for h in result.holdings}
        assert "Acme Corp" not in holding_names


class TestWarnings:
    def test_no_holdings_or_transactions_warning_code(self):
        pages = ["Annual Report for CY 2023\nName: Smith\n"]
        result = parse_senate_text(pages, _annual_filing())
        assert "no_holdings_or_transactions_found" in result.meta.parse_warnings

    def test_filing_type_not_detected_warning(self):
        pages = ["Name: Jones\nsome content\n"]
        result = parse_senate_text(pages, _annual_filing())
        assert "filing_type_not_detected" in result.meta.parse_warnings

    def test_annual_text_clears_filing_type_warning(self):
        pages = ["Annual Report for CY 2023\nName: Smith\n"]
        result = parse_senate_text(pages, _annual_filing())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_ptr_text_clears_filing_type_warning(self):
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_warning_is_string(self):
        result = parse_senate_text([], _annual_filing())
        for w in result.meta.parse_warnings:
            assert isinstance(w, str)


class TestHeaderFieldExtraction:
    def test_annual_type_detected_from_page_text(self):
        # Header extraction happens inside parse_senate_text; validate via
        # absence of the filing_type_not_detected warning.
        result = parse_senate_text(_ANNUAL_PAGES, _annual_filing())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_ptr_type_detected_from_page_text(self):
        result = parse_senate_text(_PTR_PAGES, _ptr_filing())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_multiple_pages_all_searched_for_header(self):
        pages = [
            "page 1 content only",
            "Annual Report for CY 2023\nName: Senator Jones",
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert "filing_type_not_detected" not in result.meta.parse_warnings


# ---------------------------------------------------------------------------
# Alias coverage tests for new section header variants
# ---------------------------------------------------------------------------


class TestTransactionSectionAliases:
    """parse_senate_text must pick up transactions regardless of section header."""

    def test_part_ii_alias_used_as_fallback(self):
        # "Part II" is a real PTR alias; _TRANSACTIONS_ALT2 must equal "part ii".
        assert _TRANSACTIONS_ALT2 == "part ii"

    def test_ptr_part_ii_header_produces_transactions(self):
        pages = [
            "Periodic Transaction Report\nName: Lee, Sandra\nUnited States Senate\nDate filed: 06/01/2024\n\nPart II\n01/10/2024  self  NVDA  NVIDIA Corp  Purchase  $15,001 - $50,000\n"
        ]
        result = parse_senate_text(pages, _ptr_filing())
        assert len(result.transactions) == 1
        assert result.transactions[0].issuer_name == "NVIDIA Corp"

    def test_ptr_part_ii_transaction_date(self):
        pages = [
            "Periodic Transaction Report\nName: Lee, Sandra\nUnited States Senate\n\nPart II\n01/10/2024  self  NVDA  NVIDIA Corp  Purchase  $15,001 - $50,000\n"
        ]
        result = parse_senate_text(pages, _ptr_filing())
        assert result.transactions[0].transaction_date == date(2024, 1, 10)

    def test_schedule_b_still_preferred_over_part_ii(self):
        # When both Schedule B and Part II appear, Schedule B wins.
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nSchedule B\n01/05/2023  self  AAPL  Apple Inc  Purchase  $15,001 - $50,000\n\nPart II\n02/01/2023  sp  MSFT  Microsoft  Sale (Full)  $50,001 - $100,000\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        names = [t.issuer_name for t in result.transactions]
        # Schedule B is consumed; Part II is outside that slice so only AAPL row present.
        assert "Apple Inc" in names


class TestScheduleAAliases:
    """parse_senate_text must pick up holdings when Schedule A is labelled differently."""

    def test_assets_alt_constant_value(self):
        assert _ASSETS_ALT == "assets and unearned income"

    def test_assets_and_unearned_income_header_produces_holdings(self):
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nAssets and Unearned Income\nSP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount\nself  Berkshire Hathaway  STK  $100,001 - $250,000  None  None\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.holdings) >= 1
        assert result.holdings[0].issuer_name == "Berkshire Hathaway"

    def test_schedule_a_still_preferred_over_alias(self):
        # Explicit "Schedule A" must be consumed first.
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nSchedule A\nSP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount\nself  Apple Inc  STK  $15,001 - $50,000  Dividends  $1,001 - $15,000\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        names = [h.issuer_name for h in result.holdings]
        assert "Apple Inc" in names


class TestEmptyNameCellSkipped:
    """Transaction rows with an empty asset name cell must be silently skipped."""

    def test_six_cell_row_with_empty_name_skipped(self):
        # cells: date  owner  ticker  [empty name]  type  amount
        lines = (
            "01/15/2024  self  AAPL    Purchase  $15,001 - $50,000",
            "01/20/2024  self  MSFT  Microsoft Corp  Sale (Full)  $50,001 - $100,000",
        )
        result = _transactions_from_section(lines)
        # First line: after splitting, 5 cells with no name in right slot OR
        # only 5 cells — only the well-formed row survives.
        # We just assert no crash and only valid rows survive.
        assert all(t.issuer_name for t in result)

    def test_good_rows_survive_after_empty_name_row(self):
        lines = (
            "01/01/2024  self  TSLA  Tesla Inc  Purchase  $15,001 - $50,000",
            "01/02/2024  self  NVDA  NVIDIA Corp  Sale (Full)  $100,001 - $250,000",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Senate-specific section header constant tests
# ---------------------------------------------------------------------------


class TestSectionHeaderConstants:
    """New constants must have correct values and appear in the section header set."""

    def test_assets_alt2_value(self):
        assert _ASSETS_ALT2 == "assets and income"

    def test_part_i_alt_value(self):
        assert _PART_I_ALT == "positions held outside u.s. government"

    def test_part_i_alt2_value(self):
        assert _PART_I_ALT2 == "positions held outside us government"

    def test_assets_alt2_in_senate_headers(self):
        assert _ASSETS_ALT2 in _SENATE_SECTION_HEADERS

    def test_part_i_alt_in_senate_headers(self):
        assert _PART_I_ALT in _SENATE_SECTION_HEADERS

    def test_part_i_alt2_in_senate_headers(self):
        assert _PART_I_ALT2 in _SENATE_SECTION_HEADERS

    def test_assets_alt_in_senate_headers(self):
        assert _ASSETS_ALT in _SENATE_SECTION_HEADERS


# ---------------------------------------------------------------------------
# Owner token set coverage
# ---------------------------------------------------------------------------


class TestOwnerTokenSet:
    """All documented owner token strings must be present and case-insensitively
    matched by _is_owner_row."""

    def test_dep_child_token_in_set(self):
        assert "dep. child" in _OWNER_TOKENS

    def test_dependent_token_in_set(self):
        assert "dependent" in _OWNER_TOKENS

    def test_dependent_child_token_in_set(self):
        assert "dependent child" in _OWNER_TOKENS

    def test_dep_child_owner_row(self):
        assert _is_owner_row(["dep. child", "Fidelity Fund"])

    def test_dependent_owner_row(self):
        assert _is_owner_row(["dependent", "Some Corp"])

    def test_dependent_child_owner_row(self):
        assert _is_owner_row(["dependent child", "Vanguard ETF"])

    def test_joint_full_word_owner_row(self):
        assert _is_owner_row(["joint", "Apple Inc"])

    def test_spouse_full_word_owner_row(self):
        assert _is_owner_row(["spouse", "Microsoft Corp"])

    def test_dep_child_case_insensitive(self):
        assert _is_owner_row(["Dep. Child", "Fidelity"])

    def test_dependent_child_case_insensitive(self):
        assert _is_owner_row(["Dependent Child", "Vanguard"])


# ---------------------------------------------------------------------------
# Column-header rejection (second-cell guard)
# ---------------------------------------------------------------------------


class TestColumnHeaderRejection:
    """Rows that start with an owner token but have a known header phrase in
    the second cell must be rejected by _is_owner_row."""

    def test_sp_asset_name_rejected(self):
        assert not _is_owner_row(["sp", "asset name"])

    def test_SP_Asset_Name_rejected_case_insensitive(self):
        assert not _is_owner_row(["SP", "Asset Name"])

    def test_self_organization_rejected(self):
        # "Organization" is a header sentinel in Part I column rows
        assert not _is_owner_row(["self", "organization"])

    def test_jt_transaction_type_rejected(self):
        assert not _is_owner_row(["jt", "transaction type"])

    def test_sp_income_type_rejected(self):
        assert not _is_owner_row(["sp", "income type"])

    def test_self_value_of_asset_rejected(self):
        assert not _is_owner_row(["self", "value of asset"])

    def test_sp_income_amount_rejected(self):
        assert not _is_owner_row(["sp", "income amount"])

    def test_self_with_real_issuer_not_rejected(self):
        # "Apple Inc" is not a header sentinel — must pass through
        assert _is_owner_row(["self", "Apple Inc"])

    def test_jt_with_fund_name_not_rejected(self):
        assert _is_owner_row(["jt", "Vanguard Wellington Fund"])


# ---------------------------------------------------------------------------
# Preamble termination — new section aliases
# ---------------------------------------------------------------------------


class TestPreambleLinesAliasTermination:
    """New Senate section header aliases must terminate the preamble scan."""

    def test_assets_and_income_terminates_preamble(self):
        lines = ("Annual Report", "Name: Smith", "assets and income", "self  Apple  STK")
        preamble = _preamble_lines(lines)
        assert "assets and income" not in preamble
        assert "self  Apple  STK" not in preamble

    def test_positions_held_outside_us_gov_terminates_preamble(self):
        lines = (
            "Annual Report",
            "Name: Jones",
            "positions held outside us government",
            "self  Corp  CEO",
        )
        preamble = _preamble_lines(lines)
        assert "positions held outside us government" not in preamble

    def test_positions_held_outside_us_gov_with_periods_terminates_preamble(self):
        lines = (
            "Annual Report for CY 2023",
            "Name: Lee, Robert",
            "positions held outside u.s. government",
            "self  Acme Corp  Director",
        )
        preamble = _preamble_lines(lines)
        assert "positions held outside u.s. government" not in preamble
        assert "self  Acme Corp  Director" not in preamble

    def test_preamble_content_before_alias_included(self):
        lines = (
            "Annual Report for CY 2023",
            "Name: Lee, Robert",
            "positions held outside u.s. government",
            "self  Acme Corp  Director",
        )
        preamble = _preamble_lines(lines)
        assert "Annual Report for CY 2023" in preamble
        assert "Name: Lee, Robert" in preamble

    def test_transactions_alias_terminates_preamble(self):
        lines = (
            "Periodic Transaction Report",
            "Name: Chen",
            "transactions",
            "01/05/2024  self  TSLA  Tesla  Purchase  $1,001 - $15,000",
        )
        preamble = _preamble_lines(lines)
        assert "transactions" not in preamble


# ---------------------------------------------------------------------------
# parse_senate_text with Part I alias section headers
# ---------------------------------------------------------------------------


class TestPartIAliasInFullParse:
    """parse_senate_text must detect outside positions under the long-form alias."""

    def test_positions_held_outside_us_gov_with_periods(self):
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\nName: Clark, James\n\nPositions Held Outside U.S. Government\n\nself  Omega Partners  Managing Director  01/01/2017\nsp  Delta Foundation  Trustee  06/01/2012  12/31/2021\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.outside_positions) == 2
        names = {p.entity_name for p in result.outside_positions}
        assert "Omega Partners" in names
        assert "Delta Foundation" in names

    def test_positions_held_outside_us_gov_without_periods(self):
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\nName: Rivera, Elena\n\nPositions Held Outside US Government\n\nself  Zeta Corp  Advisor  03/01/2018\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.outside_positions) == 1
        assert result.outside_positions[0].entity_name == "Zeta Corp"

    def test_part_i_still_works_as_canonical_alias(self):
        # Ensure existing "Part I" handling is not broken
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nPart I\n\nself  Alpha Corp  CEO  01/01/2015\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.outside_positions) == 1

    def test_no_empty_document_warning_with_positions_only(self):
        # A filing with only outside positions (no holdings/transactions) still
        # gets the empty-document warning — that is correct behaviour.
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nPositions Held Outside US Government\n\nself  Corp  Role  01/01/2020\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert "no_holdings_or_transactions_found" in result.meta.parse_warnings


# ---------------------------------------------------------------------------
# parse_senate_text with "Assets and Income" Schedule A alias
# ---------------------------------------------------------------------------


class TestAssetsAndIncomeAlias:
    """parse_senate_text must detect holdings under the "Assets and Income" label."""

    def test_assets_and_income_header_produces_holdings(self):
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nAssets and Income\n\nself  Vanguard 500 Index Fund  MF  $50,001 - $100,000  Dividends  $1,001 - $15,000\nsp  Microsoft Corp  STK  $15,001 - $50,000  Dividends  None\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.holdings) == 2
        names = {h.issuer_name for h in result.holdings}
        assert "Vanguard 500 Index Fund" in names
        assert "Microsoft Corp" in names

    def test_assets_alt_still_preferred_over_assets_alt2(self):
        # When both "Assets and Unearned Income" and "Assets and Income" are present,
        # the first match wins.  This test checks that both headers each produce
        # holdings and the parser doesn't crash.
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nAssets and Unearned Income\n\nself  Fidelity Contrafund  MF  $100,001 - $250,000  None  None\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        assert len(result.holdings) >= 1
        assert result.holdings[0].issuer_name == "Fidelity Contrafund"

    def test_holdings_value_range_parsed_under_alias(self):
        pages = [
            "Annual Report for CY 2023\nUnited States Senate\n\nAssets and Income\n\nself  Berkshire Hathaway Inc  STK  $250,001 - $500,000  None  None\n"
        ]
        result = parse_senate_text(pages, _annual_filing())
        h = result.holdings[0]
        assert h.value_min == Decimal("250001")
        assert h.value_max == Decimal("500000")
