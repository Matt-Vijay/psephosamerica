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
        pages = _one_page(rows=("1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
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
        pages = _one_page(rows=("1 JT Amazon.com Inc (AMZN) Purchase 11/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert result.transactions[0].owner_type == OwnerType.JOINT

    def test_dependent_child_owner_code(self) -> None:
        pages = _one_page(rows=("1 DC Nvidia Corp (NVDA) Purchase 10/15/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert result.transactions[0].owner_type == OwnerType.DEPENDENT

    def test_no_ticker_in_row(self) -> None:
        pages = _one_page(rows=("1 Self Municipal Bond Fund Purchase 09/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].issuer_ticker is None

    def test_over_amount_label_parsed(self) -> None:
        pages = _one_page(rows=("1 Self Apple Inc. (AAPL) Sale 06/01/2023 Over $50,000,000",))
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
        pages = _one_page(rows=("* 1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
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
        pages = _one_page(rows=("1 Self Apple Inc. (AAPL) Purchase 08/15/2023 $1,001 - $15,000",))
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
        pages = _one_page(rows=("1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
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


# ---------------------------------------------------------------------------
# Amendment markers — deeper
# ---------------------------------------------------------------------------


class TestAmendmentMarkersDeeper:
    """Edge cases for amendment row marker stripping."""

    def test_multiple_consecutive_markers_stripped(self) -> None:
        """Both '* [A]' markers before the row number must be stripped so
        that the owner and issuer are correctly identified."""
        pages = _one_page(
            rows=("* [A] 1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SELF
        assert tx.issuer_ticker == "AAPL"
        assert tx.transaction_type == TransactionType.PURCHASE

    def test_lowercase_bracket_a_marker_stripped(self) -> None:
        """'[a]' (lowercase) is equivalent to '[A]' and must be stripped."""
        pages = _one_page(
            rows=("[a] 1 SP Microsoft Corp (MSFT) Sale (Full) 12/05/2023 $15,001 - $50,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SPOUSE
        assert tx.issuer_ticker == "MSFT"
        assert tx.transaction_type == TransactionType.SALE

    def test_marker_without_row_number_still_parsed(self) -> None:
        """An amendment marker directly before the owner (no row number) is
        stripped, leaving the owner as the first substantive token."""
        pages = _one_page(rows=("* Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SELF
        assert "Apple" in tx.issuer_name
        assert tx.issuer_ticker == "AAPL"

    def test_asterisk_only_row_with_no_subsequent_content_skipped(self) -> None:
        """A lone '*' on a line has no date → silently skipped."""
        pages = _one_page(
            rows=(
                "*",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1


# ---------------------------------------------------------------------------
# Footer/header bleed
# ---------------------------------------------------------------------------


class TestFooterHeaderBleed:
    """Lines that look like repeated page headers or footers but contain
    dates must be silently discarded — they must produce neither false
    transactions nor spurious 'no amount' warnings.
    """

    def test_date_filed_bleed_silently_skipped(self) -> None:
        """'Date Filed: 01/15/2024' repeated mid-document produces no warning."""
        pages = _one_page(
            rows=(
                "Date Filed: 01/15/2024",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert not any("amount" in w for w in result.meta.parse_warnings)

    def test_for_calendar_year_bleed_silently_skipped(self) -> None:
        """'For Calendar Year: 2023' repeated mid-table is not a transaction."""
        pages = _one_page(
            rows=(
                "For Calendar Year: 2023",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert not any("amount" in w for w in result.meta.parse_warnings)

    def test_member_name_bleed_silently_skipped(self) -> None:
        """'Member Name: ...' repeated mid-table produces no transaction."""
        pages = _one_page(
            rows=(
                "Member Name: SMITH, JOHN",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1

    def test_page_number_line_silently_skipped(self) -> None:
        """'Page 1 of 3' (no date) is silently dropped; no warning emitted."""
        pages = _one_page(
            rows=(
                "Page 1 of 3",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1

    def test_filed_date_variant_silently_skipped(self) -> None:
        """'Filed Date: 01/22/2024' (alternative label order) is also filtered."""
        pages = _one_page(
            rows=(
                "Filed Date: 01/22/2024",
                "1 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert not any("amount" in w for w in result.meta.parse_warnings)

    def test_non_bleed_date_line_still_warns(self) -> None:
        """A line with a date but no amount that is NOT a header bleed still
        produces a warning — e.g. a row with only a date token."""
        pages = _one_page(
            rows=(
                "1 Self Tesla Inc (TSLA) Purchase 11/05/2023",  # missing amount
                "2 SP Apple Inc. (AAPL) Sale 12/01/2023 $15,001 - $50,000",
            )
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert any("amount" in w for w in result.meta.parse_warnings)


# ---------------------------------------------------------------------------
# Empty issuer warning
# ---------------------------------------------------------------------------


class TestEmptyIssuerWarning:
    """After ticker extraction the issuer name must not be empty."""

    def test_issuer_only_ticker_warns_and_skips(self) -> None:
        """A row where the 'issuer' field is just '(AAPL)' collapses to an
        empty string after ticker removal → warning emitted, row skipped."""
        pages = _one_page(rows=("1 Self (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 0
        assert any("issuer" in w for w in result.meta.parse_warnings)


# ---------------------------------------------------------------------------
# Row without leading row number
# ---------------------------------------------------------------------------


class TestRowWithoutRowNumber:
    def test_row_missing_row_number_still_parsed(self) -> None:
        """A line that starts directly with the owner code (no integer prefix)
        must be parsed correctly — the row-number skip is optional."""
        pages = _one_page(rows=("Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SELF
        assert "Apple" in tx.issuer_name
        assert tx.issuer_ticker == "AAPL"
        assert tx.transaction_type == TransactionType.PURCHASE

    def test_two_digit_row_number_skipped_correctly(self) -> None:
        """A two-digit row number (e.g. '12') must be consumed as the row
        number, not mistaken for the owner."""
        pages = _one_page(rows=("12 Self Apple Inc. (AAPL) Purchase 12/01/2023 $1,001 - $15,000",))
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].owner_type == OwnerType.SELF


# ---------------------------------------------------------------------------
# "Sale (Part)" transaction type mapping
# ---------------------------------------------------------------------------


class TestSalePartMapping:
    """'Sale (Part)' text extracted from PDFs must normalize to SALE, not OTHER.
    The parser intentionally omits "sale (part)" from _TX_TYPE_PHRASES so that
    the shorter "sale" phrase matches the start of the text and is passed to
    normalize_tx_type, which maps it to TransactionType.SALE.
    """

    def test_sale_part_maps_to_sale(self) -> None:
        pages = _one_page(
            rows=("1 Self Apple Inc. (AAPL) Sale (Part) 12/01/2023 $15,001 - $50,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].transaction_type == TransactionType.SALE

    def test_sale_part_issuer_name_intact(self) -> None:
        """The issuer name must not include 'Sale' or '(Part)' tokens."""
        pages = _one_page(
            rows=("1 JT Microsoft Corp (MSFT) Sale (Part) 11/20/2023 $50,001 - $100,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        name = result.transactions[0].issuer_name
        assert "Microsoft" in name
        assert "Sale" not in name
        assert "Part" not in name


# ---------------------------------------------------------------------------
# Issuer name containing transaction-type words
# ---------------------------------------------------------------------------


class TestIssuerWithTxTypeWord:
    """Issuers whose names contain words that match transaction-type phrases
    must not have those names silently truncated or mis-parsed.
    """

    def test_issuer_starting_with_exchange_word(self) -> None:
        """'ExchangeHub Corp (EXHB) Purchase ...' — 'exchange' appears in the
        issuer name; the transaction type 'Purchase' must still be captured."""
        pages = _one_page(
            rows=("1 Self ExchangeHub Corp (EXHB) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.transaction_type == TransactionType.PURCHASE
        assert tx.issuer_ticker == "EXHB"

    def test_issuer_containing_sale_not_confused_with_tx_type(self) -> None:
        """Issuer name 'SalePoint Inc (SALE)' must not steal the tx-type token
        when the actual transaction type is 'Purchase'."""
        pages = _one_page(
            rows=("1 Self SalePoint Inc (SALE) Purchase 12/01/2023 $1,001 - $15,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.transaction_type == TransactionType.PURCHASE
        # issuer name should contain "SalePoint" not be truncated at "Sale"
        assert "SalePoint" in tx.issuer_name

    def test_issuer_starting_with_exchange_also_has_exchange_tx(self) -> None:
        """'ExchangeHub Corp (EXHB) Exchange ...' — the rightmost word-bounded
        'Exchange' token (the actual tx type) must be captured, not the
        prefix inside 'ExchangeHub'."""
        pages = _one_page(
            rows=("1 Self ExchangeHub Corp (EXHB) Exchange 12/01/2023 $15,001 - $50,000",)
        )
        result = parse_house_ptr(pages, member_bioguide_id="S000001")
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.transaction_type == TransactionType.EXCHANGE
        assert "ExchangeHub" in tx.issuer_name
        assert tx.issuer_ticker == "EXHB"
