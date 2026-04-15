"""Tests for the House annual disclosure text parser.

All tests are pure: no network, no database, no file I/O.

Fixtures use controlled synthetic text where section headers appear on
their own lines and table rows use two-space cell separators, matching
the most common output of pypdf text extraction on House annual PDFs.
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
from src.parse.disclosures.parse_result import ParseResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="S000001",
        chamber=Chamber.HOUSE,
        filing_year=2023,
        filing_type=FilingType.ANNUAL,
        amendment_number=0,
        is_amended=False,
    )
    defaults.update(kwargs)
    return Filing(**defaults)


_COVER_PAGE = """\
U.S. House of Representatives
Annual Report
Member Name: SMITH, JOHN A.
Reporting Year: 2023
Date Filed: 05/15/2024
"""

_SCHEDULE_A_PAGE = """\
Schedule A

1  SP  Apple Inc (AAPL)  $15,001 - $50,000  Dividends  $1,001 - $15,000
2  Joint  Treasury Notes  $50,001 - $100,000  Interest  $1,001 - $15,000

Schedule B
"""

_SCHEDULE_D_PAGE = """\
Schedule D

1  Acme Corp  Board Member  01/01/2010  12/31/2023
2  State Bar  Member

Schedule E
"""

_AMENDMENT_COVER = """\
U.S. House of Representatives
Annual Report - Amendment
Amendment No. 1
Member Name: JONES, ALICE B.
Reporting Year: 2022
"""


# ---------------------------------------------------------------------------
# Return type and parser identity
# ---------------------------------------------------------------------------


class TestReturnContract:
    def test_returns_parse_result(self) -> None:
        result = parse_house_annual([], _filing())
        assert isinstance(result, ParseResult)

    def test_parser_name_in_meta(self) -> None:
        result = parse_house_annual([], _filing())
        assert result.meta.parser_name == PARSER_NAME

    def test_parser_version_in_meta(self) -> None:
        result = parse_house_annual([], _filing())
        assert result.meta.parser_version == PARSER_VERSION

    def test_supplied_filing_preserved(self) -> None:
        f = _filing(member_bioguide_id="X000099")
        result = parse_house_annual([], f)
        assert result.filing is f


# ---------------------------------------------------------------------------
# Annual filings never contain transaction rows
# ---------------------------------------------------------------------------


class TestNoTransactions:
    def test_transactions_always_empty(self) -> None:
        result = parse_house_annual([_COVER_PAGE, _SCHEDULE_A_PAGE], _filing())
        assert result.transactions == ()

    def test_transactions_empty_when_schedule_a_present(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.transactions == ()


# ---------------------------------------------------------------------------
# Empty and minimal documents
# ---------------------------------------------------------------------------


class TestEmptyDocument:
    def test_empty_page_list(self) -> None:
        result = parse_house_annual([], _filing())
        assert result.holdings == ()
        assert result.outside_positions == ()

    def test_blank_page(self) -> None:
        result = parse_house_annual(["", "   \n\n   "], _filing())
        assert result.holdings == ()
        assert result.outside_positions == ()

    def test_cover_page_only_no_holdings(self) -> None:
        result = parse_house_annual([_COVER_PAGE], _filing())
        assert result.holdings == ()

    def test_cover_page_only_no_positions(self) -> None:
        result = parse_house_annual([_COVER_PAGE], _filing())
        assert result.outside_positions == ()


# ---------------------------------------------------------------------------
# Holdings extraction (Schedule A)
# ---------------------------------------------------------------------------


class TestHoldingsExtraction:
    def test_two_holdings_extracted(self) -> None:
        result = parse_house_annual([_COVER_PAGE, _SCHEDULE_A_PAGE], _filing())
        assert len(result.holdings) == 2

    def test_holding_line_numbers_sequential(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert [h.line_number for h in result.holdings] == [1, 2]

    def test_first_holding_owner_spouse(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].owner_type == OwnerType.SPOUSE

    def test_second_holding_owner_joint(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[1].owner_type == OwnerType.JOINT

    def test_first_holding_issuer_name(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].issuer_name == "Apple Inc (AAPL)"

    def test_second_holding_issuer_name(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[1].issuer_name == "Treasury Notes"

    def test_holding_value_label_preserved(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].value_label == "$15,001 - $50,000"

    def test_holding_value_min_max_resolved(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].value_min == Decimal("15001")
        assert result.holdings[0].value_max == Decimal("50000")

    def test_holding_income_label_preserved(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].income_label == "$1,001 - $15,000"

    def test_holding_income_min_max_resolved(self) -> None:
        result = parse_house_annual([_SCHEDULE_A_PAGE], _filing())
        assert result.holdings[0].income_min == Decimal("1001")
        assert result.holdings[0].income_max == Decimal("15000")

    def test_no_holdings_without_schedule_a(self) -> None:
        page = "Annual Report\nMember Name: SMITH, JOHN A.\nReporting Year: 2023\n"
        result = parse_house_annual([page], _filing())
        assert result.holdings == ()

    def test_schedule_a_bounded_by_schedule_b(self) -> None:
        # A row appearing after "Schedule B" must not be harvested into holdings.
        page = (
            "Schedule A\n"
            "1  SP  Apple Inc  $15,001 - $50,000  Dividends  $1,001 - $15,000\n"
            "Schedule B\n"
            "1  Self  Fake Asset  $15,001 - $50,000  Dividends  $1,001 - $15,000\n"
        )
        result = parse_house_annual([page], _filing())
        assert len(result.holdings) == 1
        assert result.holdings[0].issuer_name == "Apple Inc"

    def test_row_with_too_few_cells_skipped(self) -> None:
        # A row with only row-counter + one cell lacks the required minimum.
        page = "Schedule A\n1  SP\nSchedule B\n"
        result = parse_house_annual([page], _filing())
        assert result.holdings == ()


# ---------------------------------------------------------------------------
# Outside positions extraction (Schedule D)
# ---------------------------------------------------------------------------


class TestOutsidePositionsExtraction:
    def test_two_positions_extracted(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert len(result.outside_positions) == 2

    def test_position_line_numbers_sequential(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert [p.line_number for p in result.outside_positions] == [1, 2]

    def test_position_owner_defaults_to_self(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        for pos in result.outside_positions:
            assert pos.owner_type == OwnerType.SELF

    def test_first_position_entity_name(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert result.outside_positions[0].entity_name == "Acme Corp"

    def test_first_position_title(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert result.outside_positions[0].position_title == "Board Member"

    def test_first_position_from_date(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert result.outside_positions[0].from_date == date(2010, 1, 1)

    def test_first_position_to_date(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert result.outside_positions[0].to_date == date(2023, 12, 31)

    def test_second_position_no_dates(self) -> None:
        result = parse_house_annual([_SCHEDULE_D_PAGE], _filing())
        assert result.outside_positions[1].from_date is None
        assert result.outside_positions[1].to_date is None

    def test_no_positions_without_schedule_d(self) -> None:
        result = parse_house_annual([_COVER_PAGE, _SCHEDULE_A_PAGE], _filing())
        assert result.outside_positions == ()

    def test_schedule_d_bounded_by_schedule_e(self) -> None:
        page = (
            "Schedule D\n"
            "1  Acme Corp  Director\n"
            "Schedule E\n"
            "1  Fake Corp  Trustee\n"
        )
        result = parse_house_annual([page], _filing())
        assert len(result.outside_positions) == 1
        assert result.outside_positions[0].entity_name == "Acme Corp"


# ---------------------------------------------------------------------------
# Multi-page documents
# ---------------------------------------------------------------------------


class TestMultiPage:
    def test_holdings_from_page_two(self) -> None:
        result = parse_house_annual([_COVER_PAGE, _SCHEDULE_A_PAGE], _filing())
        assert len(result.holdings) == 2

    def test_holdings_and_positions_across_pages(self) -> None:
        result = parse_house_annual(
            [_COVER_PAGE, _SCHEDULE_A_PAGE, _SCHEDULE_D_PAGE], _filing()
        )
        assert len(result.holdings) == 2
        assert len(result.outside_positions) == 2

    def test_schedule_a_split_across_pages(self) -> None:
        page_a = "Schedule A\n1  SP  Apple Inc  $15,001 - $50,000  Dividends  $1,001 - $15,000\n"
        page_b = "2  Joint  Treasury Notes  $50,001 - $100,000  Interest  $1,001 - $15,000\nSchedule B\n"
        result = parse_house_annual([page_a, page_b], _filing())
        assert len(result.holdings) == 2


# ---------------------------------------------------------------------------
# Amendment documents
# ---------------------------------------------------------------------------


class TestAmendmentDocument:
    def test_amendment_header_does_not_break_parsing(self) -> None:
        pages = [_AMENDMENT_COVER, _SCHEDULE_A_PAGE, _SCHEDULE_D_PAGE]
        result = parse_house_annual(pages, _filing(filing_type=FilingType.AMENDMENT, is_amended=True))
        assert len(result.holdings) == 2
        assert len(result.outside_positions) == 2

    def test_filing_is_amendment_reflected_in_result_filing(self) -> None:
        f = _filing(filing_type=FilingType.AMENDMENT, is_amended=True, amendment_number=1)
        result = parse_house_annual([_AMENDMENT_COVER], f)
        assert result.filing.is_amended is True
        assert result.filing.amendment_number == 1


# ---------------------------------------------------------------------------
# Parse warnings
# ---------------------------------------------------------------------------


class TestParseWarnings:
    def test_no_warnings_for_complete_header(self) -> None:
        result = parse_house_annual([_COVER_PAGE], _filing())
        assert result.meta.parse_warnings == ()

    def test_warning_when_year_missing(self) -> None:
        page = "U.S. House of Representatives\nAnnual Report\nMember Name: SMITH, JOHN A.\n"
        result = parse_house_annual([page], _filing())
        assert any("year" in w for w in result.meta.parse_warnings)

    def test_warning_when_name_missing(self) -> None:
        page = "U.S. House of Representatives\nAnnual Report\nReporting Year: 2023\n"
        result = parse_house_annual([page], _filing())
        assert any("name" in w for w in result.meta.parse_warnings)

    def test_warning_when_filing_type_absent_from_text(self) -> None:
        # A page with no type keywords produces a filing-type warning.
        page = "Member Name: SMITH, JOHN A.\nReporting Year: 2023\n"
        result = parse_house_annual([page], _filing())
        assert any("filing type" in w for w in result.meta.parse_warnings)

    def test_empty_document_emits_all_three_warnings(self) -> None:
        result = parse_house_annual([], _filing())
        warnings = result.meta.parse_warnings
        assert any("year" in w for w in warnings)
        assert any("name" in w for w in warnings)
        assert any("filing type" in w for w in warnings)
