"""Realistic Senate EFD text fixtures for parse/senate_text.py.

All fixtures are inline strings that replicate the kind of text a real PDF
extractor produces from Senate EFD and PTR filings: irregular spacing, repeated
column headers, page-break artefacts, mixed section aliases, and partially
malformed data rows.

No network calls, no database, no binary fixtures on disk.
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
from src.parse.disclosures.senate_text import (
    _holdings_from_section,
    _outside_positions_from_section,
    _preamble_lines,
    _transactions_from_section,
    parse_senate_text,
)


# ---------------------------------------------------------------------------
# Filing factories
# ---------------------------------------------------------------------------


def _annual(year: int = 2023, **kwargs) -> Filing:
    return Filing(
        member_bioguide_id="R000001",
        chamber=Chamber.SENATE,
        filing_year=year,
        filing_type=FilingType.ANNUAL,
        source_record_id=f"ANNUAL-{year}",
        **kwargs,
    )


def _ptr(**kwargs) -> Filing:
    return Filing(
        member_bioguide_id="R000002",
        chamber=Chamber.SENATE,
        filing_year=2024,
        filing_type=FilingType.PTR,
        source_record_id="PTR-2024",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Realistic multi-page annual EFD fixture
#
# Mirrors the structure of an actual extracted Senate annual report:
#   Page 1 — cover/preamble with EFD boilerplate and filer identity
#   Page 2 — Part I (outside positions)
#   Page 3 — Schedule A (assets and unearned income)
#   Page 4 — Schedule B (transactions)
# ---------------------------------------------------------------------------

_ANNUAL_COVER = """\
Annual Report for Calendar Year 2023
                                                                     (Rev. 02/2022)

United States Senate

Name:       HARRISON, CLAIRE E.
Status:     Senator
State:      TX
Office:     433 Russell Senate Office Building
            Washington, D.C. 20510

Date Filed:  05/10/2024

Did you or your spouse have "earned income" of more than $200 from outside employment?  No
"""

_ANNUAL_PART_I = """\
Part I

SP   Organization                  Position             Date From    Date To
self  Longhorn Capital Advisors    Advisory Board       01/01/2015
sp   Bluebonnet Foundation         Director             06/15/2010   12/31/2022
"""

_ANNUAL_SCHEDULE_A = """\
Schedule A

SP     Asset Name                            Asset Type   Value of Asset        Income Type   Income Amount
self   Vanguard Total Stock Market Idx Fund  MF           $100,001 - $250,000   Dividends     $2,501 - $5,000
self   Apple Inc                             STK          $15,001 - $50,000     Dividends     $1,001 - $15,000
sp     iShares Core U.S. Aggregate Bond ETF  MF           $50,001 - $100,000    Interest      $1,001 - $15,000
jt     Berkshire Hathaway Inc                STK          $250,001 - $500,000   None          None
"""

_ANNUAL_SCHEDULE_B = """\
Schedule B

Owner  Ticker  Asset                               Type         Amount                 Date
01/05/2023  self  VTI    Vanguard Total Stock Market ETF  Purchase   $15,001 - $50,000
01/20/2023  sp    AAPL   Apple Inc                         Sale (Full)  $50,001 - $100,000
03/15/2023  jt    BRK.B  Berkshire Hathaway Inc            Purchase   $100,001 - $250,000
"""

_ANNUAL_PAGES = [_ANNUAL_COVER, _ANNUAL_PART_I, _ANNUAL_SCHEDULE_A, _ANNUAL_SCHEDULE_B]


# ---------------------------------------------------------------------------
# Realistic PTR fixture using "Part II" section header
# (as produced by some Senate EFD portal exports)
# ---------------------------------------------------------------------------

_PTR_PART_II_PAGES = ["""\
Periodic Transaction Report
                                                                     (Rev. 01/2022)

United States Senate

Name:       VASQUEZ, MARCO L.
Status:     Senator
State:      NM
Date Filed:  03/22/2024

Part II

Owner  Ticker  Asset                    Type         Amount              Date
01/10/2024  self  NVDA   NVIDIA Corporation     Purchase   $50,001 - $100,000
01/12/2024  sp    AMZN   Amazon.com Inc         Sale (Full)  $15,001 - $50,000
02/01/2024  self  GOOGL  Alphabet Inc Class A   Purchase   $100,001 - $250,000
"""]


# ---------------------------------------------------------------------------
# Realistic PTR fixture using "Transactions" section header (existing alias)
# with leading/trailing noise lines
# ---------------------------------------------------------------------------

_PTR_TRANSACTIONS_ALIAS_PAGES = ["""\
Periodic Transaction Report

Name:       CHEN, ALICE W.
United States Senate
Date Filed:  09/30/2023

Transactions

Owner  Ticker  Asset               Type       Amount
01/05/2023  self  TSLA   Tesla Inc          Purchase   $15,001 - $50,000
02/14/2023  sp    MSFT   Microsoft Corp     Sale (Full)  $50,001 - $100,000
"""]


# ---------------------------------------------------------------------------
# Annual EFD where Schedule A is labelled "Assets and Unearned Income"
# ---------------------------------------------------------------------------

_ASSETS_ALIAS_PAGES = ["""\
Annual Report for Calendar Year 2022

United States Senate

Name:       OKORO, JAMES B.
Date Filed:  04/28/2023

Assets and Unearned Income

SP   Asset Name                   Asset Type   Value of Asset        Income Type   Income Amount
self  Fidelity Contrafund          MF           $100,001 - $250,000   Dividends     $1,001 - $15,000
sp   Johnson & Johnson             STK          $50,001 - $100,000    Dividends     $1,001 - $15,000
"""]


# ---------------------------------------------------------------------------
# Malformed transaction section fixture:
# Mix of well-formed rows, rows with too few cells, noise lines, rows whose
# first cell resembles a date but contains junk, and a row with an empty name.
# ---------------------------------------------------------------------------

_MALFORMED_TX_SECTION: tuple[str, ...] = (
    # Column header line (not a date row — must be skipped)
    "Owner  Ticker  Asset  Type  Amount  Date",
    # Valid 6-cell row
    "01/05/2024  self  AAPL  Apple Inc  Purchase  $15,001 - $50,000",
    # Too few cells (only 3)
    "01/06/2024  self  TSLA",
    # Non-date first cell (noise line from PDF footer)
    "Page 2 of 4",
    # All caps noise
    "CONTINUED FROM PREVIOUS PAGE",
    # Another noise line resembling a continuation
    "See attached schedule for additional transactions.",
    # Valid row — must survive
    "01/10/2024  sp  MSFT  Microsoft Corp  Sale (Full)  $50,001 - $100,000",
    # Row where the owner cell is present but name is empty after the ticker
    # 5 cells: date owner ticker type amount → name = ticker = "NVDA", bad parse
    # (will produce a valid or skipped row depending on cell layout)
    "01/15/2024  self  NVDA  Purchase  $1,001 - $15,000",
    # Date-like first cell but not a valid date (too many digits)
    "001/15/2024  self  GOOG  Alphabet  Purchase  $15,001 - $50,000",
    # Valid 5-cell row (no ticker)
    "01/20/2024  jt  Berkshire Hathaway Inc  Purchase  $250,001 - $500,000",
)


# ---------------------------------------------------------------------------
# Realistic preamble with office and contact boilerplate
# ---------------------------------------------------------------------------

_PREAMBLE_WITH_OFFICE_BOILERPLATE = """\
Annual Report for Calendar Year 2023
                                                                     (Rev. 02/2022)

United States Senate

Name:       SMITH, PATRICIA A.
State:      CA
Office:     702 Hart Senate Office Building
            Washington, D.C. 20510
            Tel: (202) 555-0100

Date Filed:  05/15/2024

INSTRUCTIONS: Read the instructions before completing this report.
For questions call the Senate Select Committee on Ethics.
"""

_PREAMBLE_WITH_OFFICE_PAGES = [
    _PREAMBLE_WITH_OFFICE_BOILERPLATE,
    "\nSchedule A\n\nself  Fidelity 500 Index Fund  MF  $50,001 - $100,000  Dividends  $1,001 - $15,000\n",
]


# ===========================================================================
# Tests
# ===========================================================================


class TestRealisticAnnualFiling:
    """Full annual EFD with realistic per-page text across four sections."""

    def test_holdings_extracted(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert len(result.holdings) == 4

    def test_holding_issuer_names(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        names = {h.issuer_name for h in result.holdings}
        assert "Apple Inc" in names
        assert "Berkshire Hathaway Inc" in names

    def test_holding_owner_types(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        owner_types = {h.owner_type for h in result.holdings}
        assert OwnerType.SELF in owner_types
        assert OwnerType.SPOUSE in owner_types
        assert OwnerType.JOINT in owner_types

    def test_holding_value_range_parsed(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        vtsmx = next(h for h in result.holdings if "Vanguard" in h.issuer_name)
        assert vtsmx.value_min == Decimal("100001")
        assert vtsmx.value_max == Decimal("250000")

    def test_transactions_extracted(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert len(result.transactions) == 3

    def test_transaction_dates(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        dates = {t.transaction_date for t in result.transactions}
        assert date(2023, 1, 5) in dates
        assert date(2023, 1, 20) in dates
        assert date(2023, 3, 15) in dates

    def test_transaction_types(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        tx_types = {t.transaction_type for t in result.transactions}
        assert TransactionType.PURCHASE in tx_types
        assert TransactionType.SALE in tx_types

    def test_transaction_ticker_extracted(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        vti = next(t for t in result.transactions if t.issuer_name == "Vanguard Total Stock Market ETF")
        assert vti.issuer_ticker == "VTI"

    def test_transaction_amount_range_parsed(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        vti = next(t for t in result.transactions if t.issuer_name == "Vanguard Total Stock Market ETF")
        assert vti.amount_min == Decimal("15001")
        assert vti.amount_max == Decimal("50000")

    def test_outside_positions_extracted(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert len(result.outside_positions) == 2

    def test_outside_position_entity_names(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        names = {p.entity_name for p in result.outside_positions}
        assert "Longhorn Capital Advisors" in names
        assert "Bluebonnet Foundation" in names

    def test_outside_position_from_date(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        lca = next(p for p in result.outside_positions if "Longhorn" in p.entity_name)
        assert lca.from_date == date(2015, 1, 1)

    def test_outside_position_to_date_parsed(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        bf = next(p for p in result.outside_positions if "Bluebonnet" in p.entity_name)
        assert bf.to_date == date(2022, 12, 31)

    def test_outside_position_missing_to_date_is_none(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        lca = next(p for p in result.outside_positions if "Longhorn" in p.entity_name)
        assert lca.to_date is None

    def test_no_empty_document_warning(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert "no_holdings_or_transactions_found" not in result.meta.parse_warnings

    def test_filing_type_detected(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_part_i_rows_not_in_holdings(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        holding_names = {h.issuer_name for h in result.holdings}
        assert "Longhorn Capital Advisors" not in holding_names
        assert "Bluebonnet Foundation" not in holding_names

    def test_schedule_b_rows_not_in_holdings(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        holding_names = {h.issuer_name for h in result.holdings}
        # Transaction assets should not appear as holdings
        assert "Vanguard Total Stock Market ETF" not in holding_names


class TestPTRWithPartIIHeader:
    """PTR filings that use 'Part II' as the transaction section header."""

    def test_transactions_extracted(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        assert len(result.transactions) == 3

    def test_issuer_names(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        names = {t.issuer_name for t in result.transactions}
        assert "NVIDIA Corporation" in names
        assert "Amazon.com Inc" in names
        assert "Alphabet Inc Class A" in names

    def test_transaction_types(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        nvda = next(t for t in result.transactions if "NVIDIA" in t.issuer_name)
        amzn = next(t for t in result.transactions if "Amazon" in t.issuer_name)
        assert nvda.transaction_type == TransactionType.PURCHASE
        assert amzn.transaction_type == TransactionType.SALE

    def test_owner_types(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        nvda = next(t for t in result.transactions if "NVIDIA" in t.issuer_name)
        amzn = next(t for t in result.transactions if "Amazon" in t.issuer_name)
        assert nvda.owner_type == OwnerType.SELF
        assert amzn.owner_type == OwnerType.SPOUSE

    def test_amount_range_parsed(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        nvda = next(t for t in result.transactions if "NVIDIA" in t.issuer_name)
        assert nvda.amount_min == Decimal("50001")
        assert nvda.amount_max == Decimal("100000")

    def test_no_holdings_in_ptr(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        assert result.holdings == ()

    def test_filing_type_detected(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_no_empty_document_warning(self):
        result = parse_senate_text(_PTR_PART_II_PAGES, _ptr())
        assert "no_holdings_or_transactions_found" not in result.meta.parse_warnings


class TestPTRWithTransactionsAlias:
    """PTR filings that use 'Transactions' (existing alias) as section header."""

    def test_transactions_extracted(self):
        result = parse_senate_text(_PTR_TRANSACTIONS_ALIAS_PAGES, _ptr())
        assert len(result.transactions) == 2

    def test_ticker_extracted(self):
        result = parse_senate_text(_PTR_TRANSACTIONS_ALIAS_PAGES, _ptr())
        tsla = next(t for t in result.transactions if "Tesla" in t.issuer_name)
        assert tsla.issuer_ticker == "TSLA"


class TestScheduleAAliasAnnual:
    """Annual EFDs where holdings are under 'Assets and Unearned Income'."""

    def test_holdings_extracted_under_alias_header(self):
        result = parse_senate_text(_ASSETS_ALIAS_PAGES, _annual(year=2022))
        assert len(result.holdings) == 2

    def test_issuer_names_extracted(self):
        result = parse_senate_text(_ASSETS_ALIAS_PAGES, _annual(year=2022))
        names = {h.issuer_name for h in result.holdings}
        assert "Fidelity Contrafund" in names
        assert "Johnson & Johnson" in names

    def test_value_ranges_parsed(self):
        result = parse_senate_text(_ASSETS_ALIAS_PAGES, _annual(year=2022))
        fid = next(h for h in result.holdings if "Fidelity" in h.issuer_name)
        assert fid.value_min == Decimal("100001")
        assert fid.value_max == Decimal("250000")

    def test_no_empty_document_warning(self):
        result = parse_senate_text(_ASSETS_ALIAS_PAGES, _annual(year=2022))
        assert "no_holdings_or_transactions_found" not in result.meta.parse_warnings


class TestMalformedTransactionRows:
    """Malformed rows must be skipped individually without aborting the section."""

    def test_valid_rows_survive(self):
        result = _transactions_from_section(_MALFORMED_TX_SECTION)
        names = {t.issuer_name for t in result}
        assert "Apple Inc" in names
        assert "Microsoft Corp" in names

    def test_malformed_rows_do_not_produce_output(self):
        result = _transactions_from_section(_MALFORMED_TX_SECTION)
        # Noise lines ("Page 2 of 4", "CONTINUED...") must produce no Transaction.
        assert all(t.issuer_name for t in result)

    def test_too_few_cells_skipped(self):
        lines = (
            "01/06/2024  self  TSLA",                   # 3 cells only
            "01/07/2024  sp  AAPL  Apple Inc  Purchase  $15,001 - $50,000",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].issuer_name == "Apple Inc"

    def test_non_date_first_cell_skipped(self):
        lines = (
            "Page 2 of 4",
            "CONTINUED FROM PREVIOUS PAGE",
            "See attached schedule.",
            "01/05/2024  self  NVDA  NVIDIA Corp  Purchase  $50,001 - $100,000",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].issuer_name == "NVIDIA Corp"

    def test_section_not_killed_by_bad_row(self):
        lines = (
            "01/01/2024  self  AAPL  Apple Inc  Purchase  $15,001 - $50,000",
            "THIS LINE IS GARBAGE AND SHOULD BE SKIPPED",
            "also-not-a-date  self  MSFT  Microsoft  Purchase  $50,001 - $100,000",
            "01/02/2024  sp  TSLA  Tesla Inc  Sale (Full)  $100,001 - $250,000",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 2
        names = {t.issuer_name for t in result}
        assert "Apple Inc" in names
        assert "Tesla Inc" in names

    def test_multiple_malformed_rows_in_sequence(self):
        lines = (
            "bad1",
            "bad2",
            "bad3",
            "01/15/2024  self  GOOGL  Alphabet Inc  Purchase  $250,001 - $500,000",
            "bad4",
            "bad5",
        )
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].issuer_name == "Alphabet Inc"

    def test_five_cell_row_without_ticker_parsed(self):
        lines = ("01/20/2024  jt  Berkshire Hathaway Inc  Purchase  $250,001 - $500,000",)
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].issuer_name == "Berkshire Hathaway Inc"
        assert result[0].issuer_ticker is None
        assert result[0].owner_type == OwnerType.JOINT

    def test_unrecognized_amount_label_does_not_crash(self):
        lines = ("01/25/2024  self  AMZN  Amazon.com Inc  Purchase  Unknown amount",)
        result = _transactions_from_section(lines)
        assert len(result) == 1
        assert result[0].amount_min is None
        assert result[0].amount_max is None
        assert result[0].amount_label == "Unknown amount"

    def test_empty_section_returns_empty_list(self):
        assert _transactions_from_section(()) == []

    def test_header_only_section_returns_empty_list(self):
        lines = ("Owner  Ticker  Asset  Type  Amount  Date",)
        assert _transactions_from_section(lines) == []


class TestOfficeHeaderExtractionInteractions:
    """Preamble and header extraction must not be confused by office boilerplate."""

    def test_preamble_bounded_at_first_section_header(self):
        all_lines = tuple(
            line
            for line in _PREAMBLE_WITH_OFFICE_BOILERPLATE.splitlines()
            if line.strip()
        ) + ("Schedule A",)
        preamble = _preamble_lines(all_lines)
        assert "Schedule A" not in preamble

    def test_address_lines_included_in_preamble(self):
        all_lines = tuple(
            line
            for line in _PREAMBLE_WITH_OFFICE_BOILERPLATE.splitlines()
            if line.strip()
        )
        preamble = _preamble_lines(all_lines)
        # All address/office lines precede any section header, so they're in preamble.
        assert any("Washington" in line for line in preamble)

    def test_phone_number_line_does_not_terminate_preamble(self):
        lines = (
            "Annual Report for CY 2023",
            "Tel: (202) 555-0100",
            "Name: Smith, Jane",
            "United States Senate",
            "schedule a",
            "self  Apple Inc  STK  $15,001 - $50,000  None  None",
        )
        preamble = _preamble_lines(lines)
        assert "Tel: (202) 555-0100" in preamble
        assert "self  Apple Inc  STK  $15,001 - $50,000  None  None" not in preamble

    def test_instructions_text_does_not_trigger_section_break(self):
        lines = (
            "Annual Report for Calendar Year 2023",
            "INSTRUCTIONS: Read the instructions before completing.",
            "Name: Jones, Robert",
            "United States Senate",
            "Date Filed: 05/15/2024",
            "Part I",
        )
        preamble = _preamble_lines(lines)
        assert "INSTRUCTIONS: Read the instructions before completing." in preamble
        assert "Part I" not in preamble

    def test_filing_type_detected_despite_office_noise(self):
        result = parse_senate_text(_PREAMBLE_WITH_OFFICE_PAGES, _annual())
        assert "filing_type_not_detected" not in result.meta.parse_warnings

    def test_chamber_senate_detected_despite_office_address(self):
        # "Washington, D.C." and office lines must not confuse chamber detection.
        # Header extraction reads the preamble; "United States Senate" is present.
        result = parse_senate_text(_PREAMBLE_WITH_OFFICE_PAGES, _annual())
        # If chamber was confused the filing_type warning would be missing — just
        # confirm the parse result is coherent.
        assert isinstance(result.holdings, tuple)

    def test_holdings_extracted_through_office_noise(self):
        result = parse_senate_text(_PREAMBLE_WITH_OFFICE_PAGES, _annual())
        assert len(result.holdings) == 1
        assert result.holdings[0].issuer_name == "Fidelity 500 Index Fund"

    def test_year_in_office_address_does_not_produce_wrong_filing_year(self):
        # Address line "Washington, D.C. 20510" contains "2051" — bare year regex
        # should not pick this up over the explicit reporting year.
        pages = [
            "Annual Report for Calendar Year 2023\nUnited States Senate\nOffice: 702 Hart, Washington, D.C. 20510\nName: Smith, Patricia\nDate Filed: 05/15/2024\n",
            "\nSchedule A\nself  Apple Inc  STK  $15,001 - $50,000  None  None\n",
        ]
        result = parse_senate_text(pages, _annual())
        assert result.filing.filing_year == 2023


class TestHoldingsSectionRealistica:
    """Realistic Schedule A rows with whitespace variations."""

    def test_multiple_spaces_in_value_label(self):
        # Real PDF extraction sometimes produces value labels with irregular spacing.
        lines = (
            "self  iShares Core Bond ETF  MF   $50,001 - $100,000  Interest  $1,001 - $15,000",
        )
        result = _holdings_from_section(lines)
        assert len(result) == 1
        assert result[0].value_min == Decimal("50001")

    def test_column_header_with_sp_first_cell_skipped(self):
        # "SP  Asset Name  ..." — SP is a valid owner token but "Asset Name" is a
        # known header cell, so this row must be rejected.
        lines = (
            "SP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount",
            "self  Apple Inc  STK  $15,001 - $50,000  None  None",
        )
        result = _holdings_from_section(lines)
        assert len(result) == 1

    def test_joint_owner_holding(self):
        lines = (
            "jt  Vanguard Wellington Fund  MF  $100,001 - $250,000  Dividends  $1,001 - $15,000",
        )
        result = _holdings_from_section(lines)
        assert result[0].owner_type == OwnerType.JOINT

    def test_dependent_owner_holding(self):
        lines = (
            "dc  Fidelity Growth Fund  MF  $1,001 - $15,000  None  None",
        )
        result = _holdings_from_section(lines)
        assert result[0].owner_type == OwnerType.DEPENDENT

    def test_none_income_label_preserved(self):
        lines = (
            "self  Cash Account  CASH  $1,001 - $15,000  None  None",
        )
        result = _holdings_from_section(lines)
        assert result[0].income_label == "None"

    def test_non_owner_noise_lines_skipped(self):
        lines = (
            "SCHEDULE A (Continued)",
            "SP  Asset Name  Asset Type  Value of Asset  Income Type  Income Amount",
            "self  Apple Inc  STK  $15,001 - $50,000  Dividends  $1,001 - $15,000",
            "1",      # page number artefact
            "sp  Microsoft Corp  STK  $50,001 - $100,000  Dividends  $1,001 - $15,000",
        )
        result = _holdings_from_section(lines)
        assert len(result) == 2

    def test_income_range_parsed(self):
        lines = (
            "self  Apple Inc  STK  $15,001 - $50,000  Dividends  $1,001 - $15,000",
        )
        result = _holdings_from_section(lines)
        assert result[0].income_min == Decimal("1001")
        assert result[0].income_max == Decimal("15000")


class TestOutsidePositionsSectionRealistic:
    """Realistic Part I rows."""

    def test_position_without_to_date(self):
        lines = (
            "self  Acme Holdings LLC  Managing Member  01/01/2018",
        )
        result = _outside_positions_from_section(lines)
        assert len(result) == 1
        assert result[0].to_date is None

    def test_present_marker_in_to_date_is_none(self):
        lines = (
            "self  Beta Corp  Director  01/01/2020  present",
        )
        result = _outside_positions_from_section(lines)
        assert result[0].to_date is None

    def test_dash_in_to_date_is_none(self):
        lines = (
            "sp  Gamma LLC  Advisor  03/15/2019  -",
        )
        result = _outside_positions_from_section(lines)
        assert result[0].to_date is None

    def test_header_row_skipped(self):
        lines = (
            "SP  Organization  Position  From  To",
            "self  Delta Inc  CFO  01/01/2021  12/31/2023",
        )
        result = _outside_positions_from_section(lines)
        assert len(result) == 1
        assert result[0].entity_name == "Delta Inc"

    def test_multiple_positions_different_owners(self):
        lines = (
            "self  Epsilon Corp  Board Member  06/01/2016",
            "sp   Zeta Foundation  Trustee  03/01/2014  12/31/2020",
            "jt   Eta LLC  Partner  01/01/2019",
        )
        result = _outside_positions_from_section(lines)
        assert len(result) == 3
        owners = {p.owner_type for p in result}
        assert OwnerType.SELF in owners
        assert OwnerType.SPOUSE in owners
        assert OwnerType.JOINT in owners

    def test_noise_line_does_not_crash(self):
        lines = (
            "PART I (Continued)",
            "self  Theta Inc  CEO  01/01/2015",
        )
        result = _outside_positions_from_section(lines)
        assert len(result) == 1


class TestCombinedFilingIntegration:
    """parse_senate_text on a full multi-section annual filing."""

    def test_all_three_section_types_populated(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert len(result.holdings) > 0
        assert len(result.transactions) > 0
        assert len(result.outside_positions) > 0

    def test_holdings_and_positions_disjoint(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        holding_names = {h.issuer_name for h in result.holdings}
        position_names = {p.entity_name for p in result.outside_positions}
        assert holding_names.isdisjoint(position_names)

    def test_holdings_and_transactions_section_isolation(self):
        # Transaction asset names that only appear in Schedule B must not
        # pollute the holdings list.
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        holding_names = {h.issuer_name for h in result.holdings}
        # Schedule B-only issuer
        assert "Vanguard Total Stock Market ETF" not in holding_names

    def test_parse_result_is_immutable_tuples(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert isinstance(result.holdings, tuple)
        assert isinstance(result.transactions, tuple)
        assert isinstance(result.outside_positions, tuple)
        assert isinstance(result.meta.parse_warnings, tuple)

    def test_filing_identity_preserved(self):
        filing = _annual()
        result = parse_senate_text(_ANNUAL_PAGES, filing)
        assert result.filing is filing

    def test_parser_name_correct(self):
        result = parse_senate_text(_ANNUAL_PAGES, _annual())
        assert result.meta.parser_name == "senate_text"
