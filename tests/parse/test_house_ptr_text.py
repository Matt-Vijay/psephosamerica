"""Tests for the House PTR text parser.

All tests are pure — no network, no database.  Inputs are hand-crafted page
text strings that match the structural patterns the parser is expected to
handle.

Coverage:
- Empty and minimal inputs
- Header field extraction (year, date, amendment)
- Transaction table detection and row parsing
- Ticker extraction and issuer name cleaning
- Owner and transaction-type token handling
- Rows with missing date or amount are skipped with warnings
- Amendment flag propagation
- Fixed invariants: chamber=HOUSE, filing_type=PTR
- Amendment row markers (* and [A]) stripped before field extraction
- Multi-word transaction types (Sale (Full), Sale (Partial))
- Header/type conflicts produce warnings; fixed invariants are not overridden
"""

from __future__ import annotations

from datetime import date

from src.parse.disclosures.house_ptr_text import parse_house_ptr
from src.parse.disclosures.models import Chamber, FilingType, OwnerType, TransactionType
from src.parse.disclosures.parse_result import ParseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TABLE_HEADER = "# Owner  Asset  Transaction Type  Date  Amount"

_FILING_YEAR_LINE = "For Calendar Year: 2023"
_FILING_DATE_LINE = "Date Filed: 01/15/2024"
_MEMBER_NAME_LINE = "Name: SMITH, JOHN"
_CHAMBER_LINE = "U.S. House of Representatives"
_PTR_LINE = "Periodic Transaction Report"


def _pages(*lines: str) -> list[str]:
    """Wrap lines into a single-page list for parse_house_ptr."""
    return ["\n".join(lines)]


def _standard_header() -> tuple[str, ...]:
    return (
        _CHAMBER_LINE,
        _PTR_LINE,
        _FILING_YEAR_LINE,
        _FILING_DATE_LINE,
        _MEMBER_NAME_LINE,
    )


def _one_page(
    *extra_header: str,
    rows: tuple[str, ...] = (),
) -> list[str]:
    """Build a single-page PTR with optional extra header lines and data rows."""
    lines = list(_standard_header()) + list(extra_header) + [_TABLE_HEADER] + list(rows)
    return _pages(*lines)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_parse_result(self) -> None:
        result = parse_house_ptr([])
        assert isinstance(result, ParseResult)

    def test_meta_parser_name(self) -> None:
        result = parse_house_ptr([])
        assert result.meta.parser_name == "house_ptr_text"

    def test_meta_parser_version(self) -> None:
        result = parse_house_ptr([])
        assert result.meta.parser_version == "1.0"


# ---------------------------------------------------------------------------
# Fixed invariants
# ---------------------------------------------------------------------------


class TestFixedInvariants:
    def test_chamber_is_always_house(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.chamber == Chamber.HOUSE

    def test_filing_type_is_always_ptr(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.filing_type == FilingType.PTR

    def test_holdings_always_empty(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.holdings == ()

    def test_outside_positions_always_empty(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.outside_positions == ()


# ---------------------------------------------------------------------------
# Header extraction
# ---------------------------------------------------------------------------


class TestHeaderExtraction:
    def test_filing_year_extracted(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.filing_year == 2023

    def test_filing_date_extracted(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.filed_at == date(2024, 1, 15)

    def test_missing_year_sets_zero_and_warns(self) -> None:
        pages = _pages(
            _CHAMBER_LINE,
            _PTR_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages)
        assert result.filing.filing_year == 0
        assert any("filing year" in w for w in result.meta.parse_warnings)

    def test_missing_date_warns(self) -> None:
        pages = _pages(
            _CHAMBER_LINE,
            _PTR_LINE,
            _FILING_YEAR_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages)
        assert any("filing date" in w for w in result.meta.parse_warnings)

    def test_missing_member_name_warns(self) -> None:
        pages = _pages(
            _CHAMBER_LINE,
            _PTR_LINE,
            _FILING_YEAR_LINE,
            _FILING_DATE_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages)
        assert any("member name" in w for w in result.meta.parse_warnings)

    def test_amendment_flag_propagated(self) -> None:
        pages = _pages(
            _CHAMBER_LINE,
            _PTR_LINE,
            "Amendment No. 1",
            _FILING_YEAR_LINE,
            _FILING_DATE_LINE,
            _MEMBER_NAME_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages)
        assert result.filing.is_amended is True
        assert result.filing.amendment_number == 1

    def test_non_amendment_is_not_amended(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.is_amended is False
        assert result.filing.amendment_number == 0


# ---------------------------------------------------------------------------
# member_bioguide_id parameter
# ---------------------------------------------------------------------------


class TestBioguideId:
    def test_supplied_id_propagated(self) -> None:
        result = parse_house_ptr(_one_page(), member_bioguide_id="S000001")
        assert result.filing.member_bioguide_id == "S000001"

    def test_empty_id_warns(self) -> None:
        result = parse_house_ptr(_one_page())
        assert result.filing.member_bioguide_id == ""
        assert any("member_bioguide_id" in w for w in result.meta.parse_warnings)


# ---------------------------------------------------------------------------
# Transaction table detection
# ---------------------------------------------------------------------------


class TestTableDetection:
    def test_no_table_header_warns_and_returns_no_transactions(self) -> None:
        pages = _pages(_CHAMBER_LINE, _PTR_LINE, _FILING_YEAR_LINE)
        result = parse_house_ptr(pages)
        assert result.transactions == ()
        assert any("table header" in w for w in result.meta.parse_warnings)

    def test_table_header_case_insensitive(self) -> None:
        pages = _pages(
            *_standard_header(),
            "# OWNER  ASSET  TRANSACTION TYPE  DATE  AMOUNT",
        )
        result = parse_house_ptr(pages)
        # No table-not-found warning.
        assert not any("table header" in w for w in result.meta.parse_warnings)


# ---------------------------------------------------------------------------
# Transaction row parsing
# ---------------------------------------------------------------------------


class TestTransactionRowParsing:
    def test_single_purchase_row(self) -> None:
        pages = _one_page(
            rows=("1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.line_number == 1
        assert tx.owner_type == OwnerType.SELF
        assert "Apple" in tx.issuer_name
        assert tx.issuer_ticker == "AAPL"
        assert tx.transaction_type == TransactionType.PURCHASE
        assert tx.transaction_date == date(2023, 12, 1)

    def test_sale_full_row(self) -> None:
        pages = _one_page(
            rows=("2 SP Microsoft Corp (MSFT) Sale (Full) 12/05/2023 $15,001 - $50,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SPOUSE
        assert tx.issuer_ticker == "MSFT"
        assert tx.transaction_type == TransactionType.SALE

    def test_multiple_rows_in_order(self) -> None:
        pages = _one_page(
            rows=(
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
                "2 SP Google LLC (GOOGL) Sale (Full) 12/10/2023 $50,001 - $100,000",
                "3 JT Tesla Inc (TSLA) Purchase 12/15/2023 $15,001 - $50,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 3
        assert [t.line_number for t in result.transactions] == [1, 2, 3]

    def test_joint_owner_code(self) -> None:
        pages = _one_page(
            rows=("1 JT Amazon.com Inc (AMZN) Purchase 11/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert result.transactions[0].owner_type == OwnerType.JOINT

    def test_dependent_child_owner_code(self) -> None:
        pages = _one_page(
            rows=("1 DC Nvidia Corp (NVDA) Purchase 10/15/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert result.transactions[0].owner_type == OwnerType.DEPENDENT

    def test_no_ticker_in_row(self) -> None:
        pages = _one_page(
            rows=("1 Self Municipal Bond Fund Purchase 09/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].issuer_ticker is None

    def test_over_amount_label_parsed(self) -> None:
        pages = _one_page(
            rows=("1 Self Apple Inc. (AAPL) Sale 06/01/2023 Over $50,000,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1

    def test_row_without_date_is_skipped(self) -> None:
        pages = _one_page(
            rows=(
                "This line has no date or amount",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1

    def test_row_without_amount_is_skipped_with_warning(self) -> None:
        pages = _one_page(
            rows=(
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023",
                "2 SP Microsoft Corp (MSFT) Sale (Full) 12/05/2023 $15,001 - $50,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert any("amount" in w for w in result.meta.parse_warnings)

    def test_asterisk_amendment_marker_stripped(self) -> None:
        """Row prefixed with * is parsed as if the marker were absent."""
        pages = _one_page(
            rows=("* 1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SELF
        assert "Apple" in tx.issuer_name
        assert tx.issuer_ticker == "AAPL"
        assert tx.transaction_type == TransactionType.PURCHASE

    def test_bracket_a_amendment_marker_stripped(self) -> None:
        """Row prefixed with [A] is parsed as if the marker were absent."""
        pages = _one_page(
            rows=("[A] 2 SP Microsoft Corp (MSFT) Sale (Full) 12/05/2023 $15,001 - $50,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SPOUSE
        assert tx.issuer_ticker == "MSFT"
        assert tx.transaction_type == TransactionType.SALE

    def test_sale_partial_row(self) -> None:
        """Multi-word 'Sale (Partial)' maps to TransactionType.SALE."""
        pages = _one_page(
            rows=("1 JT Amazon.com Inc. (AMZN) Sale (Partial) 11/20/2023 $50,001 - $100,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].transaction_type == TransactionType.SALE

    def test_multi_page_transactions(self) -> None:
        page1 = "\n".join(
            list(_standard_header())
            + [_TABLE_HEADER, "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000"]
        )
        page2 = "2 SP Microsoft Corp (MSFT) Sale (Full) 12/10/2023 $15,001 - $50,000"
        result = parse_house_ptr([page1, page2], member_bioguide_id="S000001")
        assert len(result.transactions) == 2

    def test_transaction_date_on_filing(self) -> None:
        pages = _one_page(
            rows=("1 Self Apple Inc. (AAPL) Purchase 08/15/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert result.transactions[0].transaction_date == date(2023, 8, 15)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_page_list(self) -> None:
        result = parse_house_ptr([])
        assert result.transactions == ()
        assert isinstance(result.meta.parse_warnings, tuple)

    def test_blank_pages(self) -> None:
        result = parse_house_ptr(["", "   \n\n  ", ""])
        assert result.transactions == ()

    def test_no_parse_warnings_on_clean_filing(self) -> None:
        pages = _one_page(
            rows=("1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        # Only acceptable warning: none (all fields present, clean row).
        assert result.meta.parse_warnings == ()

    def test_parse_warnings_is_tuple(self) -> None:
        result = parse_house_ptr([])
        assert isinstance(result.meta.parse_warnings, tuple)

    def test_transactions_is_tuple(self) -> None:
        result = parse_house_ptr(_one_page())
        assert isinstance(result.transactions, tuple)


# ---------------------------------------------------------------------------
# Header/type conflict warnings
# ---------------------------------------------------------------------------


class TestHeaderConflictWarnings:
    """Header signals that conflict with fixed HOUSE/PTR invariants must
    produce a warning; they must NOT silently override the fixed Filing
    identity (chamber stays HOUSE, filing_type stays PTR regardless).
    """

    def test_annual_label_in_ptr_produces_warning(self) -> None:
        """A document that reads 'Annual Financial Disclosure Report' instead
        of 'Periodic Transaction Report' triggers a filing-type warning."""
        pages = _pages(
            _CHAMBER_LINE,
            "Annual Financial Disclosure Report",  # wrong filing type
            _FILING_YEAR_LINE,
            _FILING_DATE_LINE,
            _MEMBER_NAME_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert any("filing type" in w for w in result.meta.parse_warnings)
        # Fixed invariant: filing_type is always PTR regardless.
        assert result.filing.filing_type == FilingType.PTR

    def test_senate_label_in_ptr_produces_warning(self) -> None:
        """A document whose header says 'U.S. Senate' triggers a chamber
        warning; chamber on the Filing stays HOUSE."""
        pages = _pages(
            "U.S. Senate",  # wrong chamber
            _PTR_LINE,
            _FILING_YEAR_LINE,
            _FILING_DATE_LINE,
            _MEMBER_NAME_LINE,
            _TABLE_HEADER,
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert any("chamber" in w for w in result.meta.parse_warnings)
        # Fixed invariant: chamber is always HOUSE regardless.
        assert result.filing.chamber == Chamber.HOUSE

    def test_conflicting_signals_do_not_override_transactions(self) -> None:
        """Even when the header is confusing, valid transaction rows are parsed."""
        pages = _pages(
            "U.S. Senate",
            _PTR_LINE,
            _FILING_YEAR_LINE,
            _FILING_DATE_LINE,
            _MEMBER_NAME_LINE,
            _TABLE_HEADER,
            "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
