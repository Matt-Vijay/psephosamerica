"""Realistic multi-page fixture tests for the House annual disclosure text parser.

Fixtures are derived from actual PDF extraction patterns produced by pypdf on
House annual and amendment disclosure PDFs.  Key characteristics reproduced:

- Section headers carry full titles on the same line
  ("SCHEDULE A: ASSETS AND UNEARNED INCOME")
- Column-header rows appear inside each section (not numbered → skipped)
- Separator lines of dashes appear inside sections (not numbered → skipped)
- Page-number footers appear inside the Schedule A body (not numbered → skipped)
- Continuation page headers repeat the schedule title (also not numbered → skipped)
- All four owner abbreviations appear: SP, JT, DC, Self
- Income column is sometimes absent (fewer than 5 cells after row counter)
- Income amount cell contains placeholder text "None (or less than $201)"
- Schedule D spans two pages with its header reprinted on page 2
- Amendment cover uses the "Annual Report" + "Amendment" pattern

No network, no database, no file I/O.  All fixtures are inline strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.parse.disclosures.house_annual_text import (
    PARSER_NAME,
    PARSER_VERSION,
    parse_house_annual,
)
from src.parse.disclosures.models import (
    Chamber,
    Filing,
    FilingType,
    OwnerType,
)


# ---------------------------------------------------------------------------
# Shared fixture builder
# ---------------------------------------------------------------------------


def _filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="J000001",
        chamber=Chamber.HOUSE,
        filing_year=2023,
        filing_type=FilingType.ANNUAL,
        amendment_number=0,
        is_amended=False,
    )
    defaults.update(kwargs)
    return Filing(**defaults)


# ---------------------------------------------------------------------------
# Realistic page text fixtures
# ---------------------------------------------------------------------------

# Cover page: real-world boilerplate with form number, instructions blurb, and
# the standard labeled fields.
_COVER_PAGE = """\
ANNUAL FINANCIAL DISCLOSURE REPORT
U.S. House of Representatives
For Calendar Year 2023

Member Name: JOHNSON, ROBERT T.
District: TX-05
Date Filed: May 14, 2024
"""

# Schedule A, page 1: full section title, column header row, separator line,
# five data rows using all four owner abbreviations, and a page-footer line.
_SCHED_A_PAGE1 = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
-----  ----------  --------------  --------------  -------------

1  SP  Apple Inc. (AAPL)  $250,001 - $500,000  Dividends  $15,001 - $50,000
2  JT  U.S. Treasury Bonds  $100,001 - $250,000  Interest  $1,001 - $15,000
3  DC  529 College Savings Plan  $15,001 - $50,000  None (or less than $201)  $1 - $1,000
4  Self  iShares Core S&P 500 ETF (IVV)  $500,001 - $1,000,000  Dividends  $15,001 - $50,000
5  SP  Vanguard Total Market Index Fund  $50,001 - $100,000  Dividends  $1,001 - $15,000

Page 1 of 2
"""

# Schedule A, page 2: continuation header (matches _RE_SCHED_A — must not
# stop collection), more data rows, then the Schedule B boundary.
_SCHED_A_PAGE2 = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME (CONTINUED)

OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
-----  ----------  --------------  --------------  -------------

6  JT  Municipal Bond Fund  $50,001 - $100,000  Interest  $1,001 - $15,000
7  Self  Real Estate Investment Trust  $100,001 - $250,000  Dividends  $1,001 - $15,000

Page 2 of 2

Schedule B

None
"""

# Schedule D, page 1: realistic header and four outside positions, first two
# have full date ranges, last two have truncated data.
_SCHED_D_PAGE1 = """\
SCHEDULE D: OUTSIDE POSITIONS

ORGANIZATION  POSITION  FROM DATE  TO DATE
------------  --------  ---------  -------

1  Texas Bar Association  Member  01/01/2005  Present
2  Regional Medical Center  Board of Directors  03/15/2018  -

Page 1 of 2
"""

# Schedule D, page 2: continuation header (matches _RE_SCHED_D — must NOT
# stop collection under the hardened _RE_AFTER_SCHED_D stop pattern).
_SCHED_D_PAGE2 = """\
SCHEDULE D: OUTSIDE POSITIONS (CONTINUED)

ORGANIZATION  POSITION  FROM DATE  TO DATE
------------  --------  ---------  -------

3  University Advisory Board  Advisor  08/01/2019
4  Community Foundation  Trustee

Schedule E

None
"""

# Amendment cover: uses "Annual Report" (triggers FilingType.ANNUAL detection)
# plus "Amendment" keyword and explicit amendment number.
_AMENDMENT_COVER = """\
ANNUAL FINANCIAL DISCLOSURE REPORT - AMENDMENT
U.S. House of Representatives
For Calendar Year 2022
Amendment No. 2

Member Name: GARCIA, MARIA L.
District: FL-12
Date Filed: March 3, 2024
"""

# Minimal Schedule A with rows that have no income columns (only 3 data cells
# after the row counter: owner, name, value).  Tests the MIN_CELLS=3 threshold.
_SCHED_A_NO_INCOME = """\
Schedule A

1  SP  Berkshire Hathaway (BRK.B)  $1,000,001 - $5,000,000
2  JT  Money Market Fund  $50,001 - $100,000

Schedule B
"""


# ---------------------------------------------------------------------------
# Full realistic document assembly
# ---------------------------------------------------------------------------


class TestRealisticFullDocument:
    """Full four-page document: cover + 2 Schedule A pages + 1 Schedule D page."""

    def _result(self):
        pages = [_COVER_PAGE, _SCHED_A_PAGE1, _SCHED_A_PAGE2]
        return parse_house_annual(pages, _filing())

    def test_returns_parser_name(self) -> None:
        assert self._result().meta.parser_name == PARSER_NAME

    def test_returns_parser_version(self) -> None:
        assert self._result().meta.parser_version == PARSER_VERSION

    def test_seven_holdings_extracted(self) -> None:
        assert len(self._result().holdings) == 7

    def test_no_transactions(self) -> None:
        assert self._result().transactions == ()

    def test_no_warnings_for_complete_header(self) -> None:
        result = self._result()
        assert result.meta.parse_warnings == ()

    def test_line_numbers_sequential_across_pages(self) -> None:
        nums = [h.line_number for h in self._result().holdings]
        assert nums == list(range(1, 8))

    def test_filing_reference_preserved(self) -> None:
        f = _filing(member_bioguide_id="J000001")
        result = parse_house_annual([_COVER_PAGE, _SCHED_A_PAGE1, _SCHED_A_PAGE2], f)
        assert result.filing is f


# ---------------------------------------------------------------------------
# Owner type coverage
# ---------------------------------------------------------------------------


class TestOwnerTypeCoverage:
    """Each of the four owner abbreviations used in House annual Schedule A."""

    def _holdings(self):
        return parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings

    def test_sp_maps_to_spouse(self) -> None:
        # Row 1 and row 5 use "SP"
        assert self._holdings()[0].owner_type == OwnerType.SPOUSE

    def test_jt_maps_to_joint(self) -> None:
        # Row 2 uses "JT"
        assert self._holdings()[1].owner_type == OwnerType.JOINT

    def test_dc_maps_to_dependent(self) -> None:
        # Row 3 uses "DC"
        assert self._holdings()[2].owner_type == OwnerType.DEPENDENT

    def test_self_maps_to_self(self) -> None:
        # Row 4 uses "Self"
        assert self._holdings()[3].owner_type == OwnerType.SELF


# ---------------------------------------------------------------------------
# Column headers, separators, and page footers are ignored
# ---------------------------------------------------------------------------


class TestNoiseLineFiltering:
    """Lines inside a section that are not numbered rows must be silently ignored."""

    def test_column_header_row_not_harvested(self) -> None:
        # "OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT"
        # is not a numbered row; it must not appear as a holding.
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("ASSET NAME" in n for n in names)

    def test_separator_line_not_harvested(self) -> None:
        # "-----  ----------  ..." is not a numbered row.
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("---" in n for n in names)

    def test_page_footer_not_harvested(self) -> None:
        # "Page 1 of 2" is not a numbered row.
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("Page" in n for n in names)

    def test_section_title_not_harvested(self) -> None:
        # "SCHEDULE A: ASSETS AND UNEARNED INCOME" is not a numbered row.
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("ASSETS" in n for n in names)


# ---------------------------------------------------------------------------
# Continuation page handling for Schedule A
# ---------------------------------------------------------------------------


class TestScheduleAContinuation:
    """Schedule A split across two pages; continuation header must not end section."""

    def _holdings(self):
        return parse_house_annual([_SCHED_A_PAGE1, _SCHED_A_PAGE2], _filing()).holdings

    def test_rows_from_both_pages_collected(self) -> None:
        assert len(self._holdings()) == 7

    def test_continuation_header_not_harvested_as_holding(self) -> None:
        names = [h.issuer_name for h in self._holdings()]
        assert not any("CONTINUED" in n for n in names)

    def test_last_holding_from_page_two(self) -> None:
        # Row 7 is on page 2
        assert self._holdings()[6].issuer_name == "Real Estate Investment Trust"

    def test_schedule_b_header_stops_schedule_a(self) -> None:
        # "None" after Schedule B must not appear as a holding
        holdings = self._holdings()
        names = [h.issuer_name for h in holdings]
        assert "None" not in names

    def test_value_labels_from_page_two(self) -> None:
        h6 = self._holdings()[5]  # row 6: Municipal Bond Fund
        assert h6.value_label == "$50,001 - $100,000"
        assert h6.value_min == Decimal("50001")
        assert h6.value_max == Decimal("100000")


# ---------------------------------------------------------------------------
# Income column edge cases
# ---------------------------------------------------------------------------


class TestIncomeColumnEdgeCases:
    def test_income_placeholder_preserved_as_label(self) -> None:
        # Row 3 has "None (or less than $201)" as income type — col 3 is skipped;
        # col 4 is "$1 - $1,000" which is a recognized range.
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        h3 = holdings[2]  # 0-based index 2 = row 3 (DC / 529 plan)
        assert h3.income_label == "$1 - $1,000"
        assert h3.income_min == Decimal("1")
        assert h3.income_max == Decimal("1000")

    def test_no_income_columns_yields_none(self) -> None:
        # Rows with only owner+name+value (3 cells) have no income label.
        holdings = parse_house_annual([_SCHED_A_NO_INCOME], _filing()).holdings
        for h in holdings:
            assert h.income_label is None
            assert h.income_min is None
            assert h.income_max is None

    def test_no_income_columns_still_extracts_two_rows(self) -> None:
        holdings = parse_house_annual([_SCHED_A_NO_INCOME], _filing()).holdings
        assert len(holdings) == 2

    def test_no_income_columns_value_label_resolved(self) -> None:
        holdings = parse_house_annual([_SCHED_A_NO_INCOME], _filing()).holdings
        h1 = holdings[0]
        assert h1.value_label == "$1,000,001 - $5,000,000"
        assert h1.value_min == Decimal("1000001")
        assert h1.value_max == Decimal("5000000")

    def test_issuer_name_with_parenthetical_ticker(self) -> None:
        holdings = parse_house_annual([_SCHED_A_PAGE1], _filing()).holdings
        # Row 1: "Apple Inc. (AAPL)" — clean_asset_name strips [bracket] noise
        # but preserves (AAPL) since it's not a [filing id] pattern.
        assert "AAPL" in holdings[0].issuer_name


# ---------------------------------------------------------------------------
# Multi-page Schedule D with continuation header (hardened stop regex)
# ---------------------------------------------------------------------------


class TestScheduleDContinuation:
    """Schedule D spanning two pages must collect all rows.

    Before the hardening fix, the stop regex _RE_ANY_LATER_SCHED matched
    'schedule [b-z]', which includes 'd'.  The continuation header
    'SCHEDULE D: OUTSIDE POSITIONS (CONTINUED)' would have stopped
    collection prematurely, dropping rows 3 and 4.  The fix introduces
    _RE_AFTER_SCHED_D which only matches 'schedule [e-z]'.
    """

    def _positions(self):
        pages = [_SCHED_D_PAGE1, _SCHED_D_PAGE2]
        return parse_house_annual(pages, _filing()).outside_positions

    def test_four_positions_from_two_pages(self) -> None:
        assert len(self._positions()) == 4

    def test_continuation_header_not_harvested_as_position(self) -> None:
        names = [p.entity_name for p in self._positions()]
        assert not any("CONTINUED" in n for n in names)

    def test_column_header_not_harvested_as_position(self) -> None:
        names = [p.entity_name for p in self._positions()]
        assert not any("ORGANIZATION" in n for n in names)

    def test_positions_from_page_one(self) -> None:
        positions = self._positions()
        assert positions[0].entity_name == "Texas Bar Association"
        assert positions[0].position_title == "Member"
        assert positions[1].entity_name == "Regional Medical Center"
        assert positions[1].position_title == "Board of Directors"

    def test_positions_from_page_two(self) -> None:
        positions = self._positions()
        assert positions[2].entity_name == "University Advisory Board"
        assert positions[3].entity_name == "Community Foundation"

    def test_all_positions_default_to_self_owner(self) -> None:
        for p in self._positions():
            assert p.owner_type == OwnerType.SELF

    def test_present_date_token_yields_none(self) -> None:
        # "Present" is a blank date token → from_date for position 1 is None
        assert self._positions()[0].to_date is None

    def test_dash_date_token_yields_none(self) -> None:
        # "-" is a blank date token → to_date for position 2 is None
        assert self._positions()[1].to_date is None

    def test_valid_from_date_page_one(self) -> None:
        assert self._positions()[0].from_date == date(2005, 1, 1)

    def test_schedule_e_stops_schedule_d(self) -> None:
        # "Schedule E" on page 2 must stop Schedule D collection.
        # "Trustee" from position 4 is inside D; there must be no position
        # with entity_name "None" (which follows Schedule E).
        names = [p.entity_name for p in self._positions()]
        assert "None" not in names

    def test_line_numbers_sequential(self) -> None:
        nums = [p.line_number for p in self._positions()]
        assert nums == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Amendment document interaction
# ---------------------------------------------------------------------------


class TestAmendmentRealistic:
    """Amendment cover followed by Schedule A and D pages."""

    def _result(self):
        pages = [_AMENDMENT_COVER, _SCHED_A_PAGE1, _SCHED_D_PAGE1]
        f = _filing(
            filing_type=FilingType.AMENDMENT,
            filing_year=2022,
            is_amended=True,
            amendment_number=2,
        )
        return parse_house_annual(pages, f)

    def test_holdings_extracted_despite_amendment_cover(self) -> None:
        assert len(self._result().holdings) == 5

    def test_positions_extracted_despite_amendment_cover(self) -> None:
        assert len(self._result().outside_positions) == 2

    def test_amendment_filing_preserved_on_result(self) -> None:
        result = self._result()
        assert result.filing.is_amended is True
        assert result.filing.amendment_number == 2

    def test_no_transactions_in_amendment(self) -> None:
        assert self._result().transactions == ()

    def test_no_warnings_when_header_complete(self) -> None:
        # Cover has "Annual Report", year "2022", and "GARCIA, MARIA L."
        result = self._result()
        assert result.meta.parse_warnings == ()


# ---------------------------------------------------------------------------
# Annual-specific invariants
# ---------------------------------------------------------------------------


class TestAnnualSpecificInvariants:
    """Invariants that must hold for all annual (and amendment) filings."""

    def test_transactions_always_empty_single_page(self) -> None:
        result = parse_house_annual([_SCHED_A_PAGE1], _filing())
        assert result.transactions == ()

    def test_transactions_always_empty_multi_page(self) -> None:
        result = parse_house_annual(
            [_COVER_PAGE, _SCHED_A_PAGE1, _SCHED_A_PAGE2, _SCHED_D_PAGE1],
            _filing(),
        )
        assert result.transactions == ()

    def test_schedule_d_owner_always_self_regardless_of_content(self) -> None:
        result = parse_house_annual([_SCHED_D_PAGE1, _SCHED_D_PAGE2], _filing())
        for p in result.outside_positions:
            assert p.owner_type == OwnerType.SELF

    def test_filing_object_identity_preserved(self) -> None:
        f = _filing()
        result = parse_house_annual([_COVER_PAGE, _SCHED_A_PAGE1], f)
        assert result.filing is f

    def test_holdings_is_tuple(self) -> None:
        result = parse_house_annual([_SCHED_A_PAGE1], _filing())
        assert isinstance(result.holdings, tuple)

    def test_outside_positions_is_tuple(self) -> None:
        result = parse_house_annual([_SCHED_D_PAGE1], _filing())
        assert isinstance(result.outside_positions, tuple)

    def test_transactions_is_tuple(self) -> None:
        result = parse_house_annual([], _filing())
        assert isinstance(result.transactions, tuple)


# ---------------------------------------------------------------------------
# Large realistic Schedule A
# ---------------------------------------------------------------------------


class TestLargeScheduleA:
    """Full 7-row Schedule A spanning two pages validates row numbering and data."""

    def _holdings(self):
        return parse_house_annual([_SCHED_A_PAGE1, _SCHED_A_PAGE2], _filing()).holdings

    def test_count(self) -> None:
        assert len(self._holdings()) == 7

    def test_row_1_issuer(self) -> None:
        assert "Apple" in self._holdings()[0].issuer_name

    def test_row_4_owner_self(self) -> None:
        # "Self" → OwnerType.SELF
        assert self._holdings()[3].owner_type == OwnerType.SELF

    def test_row_4_value_range(self) -> None:
        h = self._holdings()[3]
        assert h.value_min == Decimal("500001")
        assert h.value_max == Decimal("1000000")

    def test_row_6_from_page_2(self) -> None:
        h = self._holdings()[5]
        assert h.issuer_name == "Municipal Bond Fund"
        assert h.owner_type == OwnerType.JOINT

    def test_row_7_from_page_2(self) -> None:
        h = self._holdings()[6]
        assert h.issuer_name == "Real Estate Investment Trust"
        assert h.owner_type == OwnerType.SELF


# ---------------------------------------------------------------------------
# Dotted row counter ("1." pypdf artefact)
# ---------------------------------------------------------------------------

# Schedule A where pypdf emits "1." instead of "1" for the row counter.
# This arises when the PDF uses a numbered-list style that pypdf faithfully
# renders with the trailing period.
_SCHED_A_DOTTED = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
-----  ----------  --------------  --------------  -------------

1.  SP  Microsoft Corp. (MSFT)  $100,001 - $250,000  Dividends  $1,001 - $15,000
2.  JT  S&P 500 Index Fund (IVV)  $50,001 - $100,000  Dividends  $1,001 - $15,000
3.  Self  U.S. Treasury Bills  $15,001 - $50,000  Interest  $1 - $1,000
4.  DC  529 College Savings Plan  $15,001 - $50,000  None (or less than $201)  $1 - $1,000

Schedule B

None
"""

# Schedule D where pypdf emits dotted counters.
_SCHED_D_DOTTED = """\
SCHEDULE D: OUTSIDE POSITIONS

ORGANIZATION  POSITION  FROM DATE  TO DATE
------------  --------  ---------  -------

1.  State Bar Association  Member  01/01/2010  -
2.  University Board  Trustee

Schedule E
"""


class TestDottedRowCounters:
    """pypdf sometimes appends a period to row counter digits (e.g. "1." → "1.")."""

    def test_four_holdings_from_dotted_schedule_a(self) -> None:
        result = parse_house_annual([_SCHED_A_DOTTED], _filing())
        assert len(result.holdings) == 4

    def test_dotted_counter_owner_resolved(self) -> None:
        holdings = parse_house_annual([_SCHED_A_DOTTED], _filing()).holdings
        assert holdings[0].owner_type == OwnerType.SPOUSE   # "SP"
        assert holdings[1].owner_type == OwnerType.JOINT    # "JT"
        assert holdings[2].owner_type == OwnerType.SELF     # "Self"
        assert holdings[3].owner_type == OwnerType.DEPENDENT  # "DC"

    def test_dotted_counter_issuer_names_intact(self) -> None:
        holdings = parse_house_annual([_SCHED_A_DOTTED], _filing()).holdings
        assert "Microsoft" in holdings[0].issuer_name
        assert "S&P 500" in holdings[1].issuer_name
        assert holdings[2].issuer_name == "U.S. Treasury Bills"

    def test_dotted_counter_value_range_resolved(self) -> None:
        holdings = parse_house_annual([_SCHED_A_DOTTED], _filing()).holdings
        assert holdings[0].value_min == Decimal("100001")
        assert holdings[0].value_max == Decimal("250000")

    def test_dotted_counter_income_label_resolved(self) -> None:
        holdings = parse_house_annual([_SCHED_A_DOTTED], _filing()).holdings
        assert holdings[2].income_label == "$1 - $1,000"
        assert holdings[2].income_min == Decimal("1")
        assert holdings[2].income_max == Decimal("1000")

    def test_dotted_counter_schedule_d_two_positions(self) -> None:
        result = parse_house_annual([_SCHED_D_DOTTED], _filing())
        assert len(result.outside_positions) == 2

    def test_dotted_counter_schedule_d_entity_names(self) -> None:
        positions = parse_house_annual([_SCHED_D_DOTTED], _filing()).outside_positions
        assert positions[0].entity_name == "State Bar Association"
        assert positions[1].entity_name == "University Board"

    def test_dotted_counter_schedule_d_date_blank_token(self) -> None:
        # "-" is a blank date token → to_date is None.
        positions = parse_house_annual([_SCHED_D_DOTTED], _filing()).outside_positions
        assert positions[0].to_date is None

    def test_dotted_counter_noise_lines_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_DOTTED], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("ASSET NAME" in n for n in names)
        assert not any("---" in n for n in names)


# ---------------------------------------------------------------------------
# Noisy separator variants inside section bodies
# ---------------------------------------------------------------------------

# Real House PDFs include separators of many styles.  None of them start with
# a digit, so all must be silently discarded.
_SCHED_A_HEAVY_NOISE = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME
============================================================
OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
* * * * * * * * * * * * * * * * * * * * * * * * *
. . . . . . . . . . . . . . . . . . . . . . . . .
__________________________________________________________

1  SP  Alphabet Inc. (GOOGL)  $500,001 - $1,000,000  Dividends  $15,001 - $50,000
============================================================
2  JT  Amazon.com Inc. (AMZN)  $250,001 - $500,000  Dividends  $1,001 - $15,000
__________________________________________________________

Schedule B
"""


class TestNoisySeparators:
    """Varied separator lines (equals, asterisks, dots, underscores) are all ignored."""

    def test_two_holdings_despite_heavy_noise(self) -> None:
        result = parse_house_annual([_SCHED_A_HEAVY_NOISE], _filing())
        assert len(result.holdings) == 2

    def test_equals_separator_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_HEAVY_NOISE], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("===" in n for n in names)

    def test_asterisk_separator_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_HEAVY_NOISE], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("* *" in n for n in names)

    def test_dot_separator_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_HEAVY_NOISE], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any(". . ." in n for n in names)

    def test_data_rows_between_separators_extracted(self) -> None:
        holdings = parse_house_annual([_SCHED_A_HEAVY_NOISE], _filing()).holdings
        assert holdings[0].issuer_name == "Alphabet Inc. (GOOGL)"
        assert holdings[1].issuer_name == "Amazon.com Inc. (AMZN)"


# ---------------------------------------------------------------------------
# Instruction text paragraphs inside section bodies
# ---------------------------------------------------------------------------

# House PDFs often include instruction text at the top of each schedule section.
# These paragraphs are multi-word prose lines that do not start with a digit.
_SCHED_A_WITH_INSTRUCTIONS = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

This schedule must include all assets held by you, your spouse, or dependent
children with a value exceeding $1,000 at the end of the reporting period or
that generated more than $200 in income during the calendar year. Please see
the instructions for additional guidance on excluded assets.

OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
-----  ----------  --------------  --------------  -------------

1  SP  Coca-Cola Co. (KO)  $100,001 - $250,000  Dividends  $1,001 - $15,000
2  DC  529 Education Plan  $15,001 - $50,000  None (or less than $201)  $1 - $1,000

FOOTNOTE: Values reflect the fair market value as of December 31, 2023.

Schedule B
"""


class TestInstructionTextFiltering:
    """Instruction paragraphs and footnote lines inside a section are ignored."""

    def test_two_holdings_despite_instruction_paragraphs(self) -> None:
        result = parse_house_annual([_SCHED_A_WITH_INSTRUCTIONS], _filing())
        assert len(result.holdings) == 2

    def test_instruction_text_not_harvested_as_issuer(self) -> None:
        holdings = parse_house_annual([_SCHED_A_WITH_INSTRUCTIONS], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("schedule must include" in n.lower() for n in names)
        assert not any("instructions" in n.lower() for n in names)

    def test_footnote_line_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_WITH_INSTRUCTIONS], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("FOOTNOTE" in n for n in names)

    def test_correct_issuer_names_extracted(self) -> None:
        holdings = parse_house_annual([_SCHED_A_WITH_INSTRUCTIONS], _filing()).holdings
        assert holdings[0].issuer_name == "Coca-Cola Co. (KO)"
        assert holdings[1].issuer_name == "529 Education Plan"

    def test_income_placeholder_in_instruction_context(self) -> None:
        holdings = parse_house_annual([_SCHED_A_WITH_INSTRUCTIONS], _filing()).holdings
        h2 = holdings[1]
        assert h2.income_label == "$1 - $1,000"


# ---------------------------------------------------------------------------
# Over $50,000,000 value label
# ---------------------------------------------------------------------------

_SCHED_A_OVER_50M = """\
Schedule A

1  Self  Diversified Holdings Trust  Over $50,000,000  Dividends  $5,000,001 - $25,000,000

Schedule B
"""


class TestOverFiftyMillionLabel:
    """The highest House disclosure range ("Over $50,000,000") normalizes correctly."""

    def test_holding_extracted(self) -> None:
        result = parse_house_annual([_SCHED_A_OVER_50M], _filing())
        assert len(result.holdings) == 1

    def test_value_label_preserved(self) -> None:
        h = parse_house_annual([_SCHED_A_OVER_50M], _filing()).holdings[0]
        assert h.value_label == "Over $50,000,000"

    def test_value_min_max_resolved(self) -> None:
        h = parse_house_annual([_SCHED_A_OVER_50M], _filing()).holdings[0]
        assert h.value_min == Decimal("50000001")
        assert h.value_max == Decimal("50000001")

    def test_income_range_resolved(self) -> None:
        h = parse_house_annual([_SCHED_A_OVER_50M], _filing()).holdings[0]
        assert h.income_min == Decimal("5000001")
        assert h.income_max == Decimal("25000000")


# ---------------------------------------------------------------------------
# Unrecognized value and income labels
# ---------------------------------------------------------------------------

_SCHED_A_UNRECOGNIZED_LABELS = """\
Schedule A

1  Self  Private Equity Fund L.P.  See Footnote  N/A  N/A
2  SP  Apple Inc  $15,001 - $50,000  Dividends  $1,001 - $15,000

Schedule B
"""


class TestUnrecognizedLabelsRealistic:
    """Non-standard labels are preserved; numeric fields are None."""

    def test_two_holdings_extracted(self) -> None:
        result = parse_house_annual([_SCHED_A_UNRECOGNIZED_LABELS], _filing())
        assert len(result.holdings) == 2

    def test_unrecognized_value_label_preserved_as_string(self) -> None:
        h = parse_house_annual([_SCHED_A_UNRECOGNIZED_LABELS], _filing()).holdings[0]
        assert h.value_label == "See Footnote"

    def test_unrecognized_label_value_min_max_none(self) -> None:
        h = parse_house_annual([_SCHED_A_UNRECOGNIZED_LABELS], _filing()).holdings[0]
        assert h.value_min is None
        assert h.value_max is None

    def test_standard_label_on_next_row_still_resolved(self) -> None:
        h = parse_house_annual([_SCHED_A_UNRECOGNIZED_LABELS], _filing()).holdings[1]
        assert h.value_min == Decimal("15001")
        assert h.value_max == Decimal("50000")


# ---------------------------------------------------------------------------
# Schedule A continuation without colon or parentheses
# ---------------------------------------------------------------------------

# Some PDFs render continuation headers without the full title decoration:
# "SCHEDULE A CONTINUED" instead of "SCHEDULE A: ASSETS AND UNEARNED INCOME (CONTINUED)".
_SCHED_A_BARE_CONT_PAGE1 = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

1  SP  First Corp  $50,001 - $100,000  Dividends  $1,001 - $15,000
2  JT  Second Fund  $100,001 - $250,000  Interest  $1,001 - $15,000
"""

_SCHED_A_BARE_CONT_PAGE2 = """\
SCHEDULE A CONTINUED

1  Self  Third Asset  $15,001 - $50,000  Dividends  $1 - $1,000

Schedule B
"""


class TestScheduleAContinuationBareHeader:
    """Bare continuation header "SCHEDULE A CONTINUED" is not a stop trigger."""

    def test_three_holdings_across_two_pages(self) -> None:
        result = parse_house_annual([_SCHED_A_BARE_CONT_PAGE1, _SCHED_A_BARE_CONT_PAGE2], _filing())
        assert len(result.holdings) == 3

    def test_bare_continuation_header_not_harvested(self) -> None:
        holdings = parse_house_annual(
            [_SCHED_A_BARE_CONT_PAGE1, _SCHED_A_BARE_CONT_PAGE2], _filing()
        ).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("CONTINUED" in n for n in names)

    def test_row_from_bare_continuation_page_extracted(self) -> None:
        holdings = parse_house_annual(
            [_SCHED_A_BARE_CONT_PAGE1, _SCHED_A_BARE_CONT_PAGE2], _filing()
        ).holdings
        assert holdings[2].issuer_name == "Third Asset"

    def test_schedule_b_stops_collection(self) -> None:
        result = parse_house_annual([_SCHED_A_BARE_CONT_PAGE1, _SCHED_A_BARE_CONT_PAGE2], _filing())
        # Only 3 data rows; Schedule B boundary respected.
        assert len(result.holdings) == 3


# ---------------------------------------------------------------------------
# Row numbers restarting on continuation page
# ---------------------------------------------------------------------------

# Some House PDFs restart row numbering at 1 on each continuation page.
# The internal line_number is assigned sequentially by holding_rows_from_table,
# not derived from the PDF row counter.
_SCHED_A_RESTART_P1 = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

1  SP  First Holding Corp  $50,001 - $100,000  Dividends  $1,001 - $15,000
2  JT  Second Fund LLC  $100,001 - $250,000  Interest  $1,001 - $15,000
"""

_SCHED_A_RESTART_P2 = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME (CONTINUED)

1  Self  Third Asset Trust  $15,001 - $50,000  Dividends  $1 - $1,000
2  SP  Fourth Holding Inc  $50,001 - $100,000  None  $1,001 - $15,000

Schedule B
"""


class TestRowNumbersRestartOnContinuationPage:
    """PDF row counter restarting at 1 on page 2 must not drop or duplicate rows."""

    def test_four_holdings_collected(self) -> None:
        result = parse_house_annual([_SCHED_A_RESTART_P1, _SCHED_A_RESTART_P2], _filing())
        assert len(result.holdings) == 4

    def test_internal_line_numbers_sequential(self) -> None:
        holdings = parse_house_annual([_SCHED_A_RESTART_P1, _SCHED_A_RESTART_P2], _filing()).holdings
        assert [h.line_number for h in holdings] == [1, 2, 3, 4]

    def test_issuers_from_both_pages(self) -> None:
        holdings = parse_house_annual([_SCHED_A_RESTART_P1, _SCHED_A_RESTART_P2], _filing()).holdings
        assert holdings[0].issuer_name == "First Holding Corp"
        assert holdings[1].issuer_name == "Second Fund LLC"
        assert holdings[2].issuer_name == "Third Asset Trust"
        assert holdings[3].issuer_name == "Fourth Holding Inc"

    def test_continuation_header_not_harvested(self) -> None:
        holdings = parse_house_annual([_SCHED_A_RESTART_P1, _SCHED_A_RESTART_P2], _filing()).holdings
        names = [h.issuer_name for h in holdings]
        assert not any("CONTINUED" in n for n in names)


# ---------------------------------------------------------------------------
# Schedule D minimal row (organization name only)
# ---------------------------------------------------------------------------

_SCHED_D_MINIMAL_ROWS = """\
SCHEDULE D: OUTSIDE POSITIONS

1  Community Land Trust

2  Local School Board  Board Member

Schedule E
"""


class TestScheduleDMinimalRow:
    """Schedule D rows with only an organization name (no position, no dates) are valid."""

    def test_two_positions_extracted(self) -> None:
        result = parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing())
        assert len(result.outside_positions) == 2

    def test_minimal_row_entity_name(self) -> None:
        positions = parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing()).outside_positions
        assert positions[0].entity_name == "Community Land Trust"

    def test_minimal_row_position_title_none(self) -> None:
        positions = parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing()).outside_positions
        assert positions[0].position_title is None

    def test_minimal_row_dates_none(self) -> None:
        positions = parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing()).outside_positions
        assert positions[0].from_date is None
        assert positions[0].to_date is None

    def test_full_row_still_extracted(self) -> None:
        positions = parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing()).outside_positions
        assert positions[1].entity_name == "Local School Board"
        assert positions[1].position_title == "Board Member"

    def test_all_positions_owner_self(self) -> None:
        for p in parse_house_annual([_SCHED_D_MINIMAL_ROWS], _filing()).outside_positions:
            assert p.owner_type == OwnerType.SELF


# ---------------------------------------------------------------------------
# Unknown owner abbreviation in realistic context
# ---------------------------------------------------------------------------

_SCHED_A_UNKNOWN_OWNER = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

OWNER  ASSET NAME  VALUE OF ASSET  TYPE OF INCOME  INCOME AMOUNT
-----  ----------  --------------  --------------  -------------

1  SP  Apple Inc (AAPL)  $15,001 - $50,000  Dividends  $1,001 - $15,000
2  UNK  Mystery Holdings LLC  $50,001 - $100,000  Interest  $1,001 - $15,000
3  Self  Treasury Bond  $100,001 - $250,000  Interest  $1,001 - $15,000

Schedule B
"""


class TestUnknownOwnerAbbreviation:
    """Unknown owner tokens produce OwnerType.OTHER; parsing continues normally."""

    def test_three_holdings_despite_unknown_owner(self) -> None:
        result = parse_house_annual([_SCHED_A_UNKNOWN_OWNER], _filing())
        assert len(result.holdings) == 3

    def test_unknown_owner_maps_to_other(self) -> None:
        holdings = parse_house_annual([_SCHED_A_UNKNOWN_OWNER], _filing()).holdings
        assert holdings[1].owner_type == OwnerType.OTHER

    def test_surrounding_rows_unaffected(self) -> None:
        holdings = parse_house_annual([_SCHED_A_UNKNOWN_OWNER], _filing()).holdings
        assert holdings[0].owner_type == OwnerType.SPOUSE
        assert holdings[2].owner_type == OwnerType.SELF

    def test_issuer_name_intact_for_unknown_owner_row(self) -> None:
        holdings = parse_house_annual([_SCHED_A_UNKNOWN_OWNER], _filing()).holdings
        assert holdings[1].issuer_name == "Mystery Holdings LLC"


# ---------------------------------------------------------------------------
# Amendment header followed by multi-page schedules
# ---------------------------------------------------------------------------

_AMENDMENT_COVER_FULL = """\
ANNUAL FINANCIAL DISCLOSURE REPORT - AMENDMENT
U.S. House of Representatives
For Calendar Year 2021
Amendment No. 3

Member Name: PATEL, PRIYA K.
District: CA-17
Date Filed: April 5, 2023
"""

_SCHED_A_AMENDMENT = """\
SCHEDULE A: ASSETS AND UNEARNED INCOME

1  SP  Apple Inc (AAPL)  $250,001 - $500,000  Dividends  $15,001 - $50,000
2  Self  Vanguard Index Fund  $500,001 - $1,000,000  Dividends  $15,001 - $50,000
3  JT  U.S. Treasury Bonds  $100,001 - $250,000  Interest  $1,001 - $15,000

Schedule B

None
"""

_SCHED_D_AMENDMENT = """\
SCHEDULE D: OUTSIDE POSITIONS

1  State Bar Association  Member  01/01/2008  -
2  University Advisory Council  Advisor  09/01/2015

Schedule E
"""


class TestAmendmentWithFullSchedules:
    """Amendment cover + multi-page schedules: all rows collected, filing preserved."""

    def _result(self):
        f = _filing(
            filing_type=FilingType.AMENDMENT,
            filing_year=2021,
            is_amended=True,
            amendment_number=3,
        )
        pages = [_AMENDMENT_COVER_FULL, _SCHED_A_AMENDMENT, _SCHED_D_AMENDMENT]
        return parse_house_annual(pages, f)

    def test_three_holdings_extracted(self) -> None:
        assert len(self._result().holdings) == 3

    def test_two_positions_extracted(self) -> None:
        assert len(self._result().outside_positions) == 2

    def test_no_transactions(self) -> None:
        assert self._result().transactions == ()

    def test_amendment_filing_preserved(self) -> None:
        result = self._result()
        assert result.filing.is_amended is True
        assert result.filing.amendment_number == 3

    def test_no_warnings_for_complete_header(self) -> None:
        assert self._result().meta.parse_warnings == ()

    def test_holdings_line_numbers_sequential(self) -> None:
        nums = [h.line_number for h in self._result().holdings]
        assert nums == [1, 2, 3]

    def test_positions_line_numbers_sequential(self) -> None:
        nums = [p.line_number for p in self._result().outside_positions]
        assert nums == [1, 2]

    def test_dash_to_date_yields_none(self) -> None:
        positions = self._result().outside_positions
        assert positions[0].to_date is None

    def test_from_date_parsed_correctly(self) -> None:
        positions = self._result().outside_positions
        assert positions[0].from_date == date(2008, 1, 1)

    def test_incomplete_date_row_from_date_parsed(self) -> None:
        positions = self._result().outside_positions
        # Row 2 has only from_date, no to_date column.
        assert positions[1].from_date == date(2015, 9, 1)
        assert positions[1].to_date is None
