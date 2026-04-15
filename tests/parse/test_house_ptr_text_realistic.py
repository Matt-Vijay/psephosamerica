"""Realistic multi-page PTR text fixture tests for the House PTR parser.

All tests are pure — no network, no database, no binary fixtures.
Page-text strings are generated inline and model real PDF-extracted output:
extra whitespace, repeated page headers, OCR-style artefacts, footer noise,
amendment markers, and mixed-validity rows.

Coverage:
- Clean multi-page PTR (table header on page 2, continuation on page 3)
- Amendment PTR: Amendment No. header + asterisk-prefixed rows
- Mixed valid/malformed rows in realistic context
- Header/type conflicts (Annual label, Senate label) → warnings, not overrides
- Multi-word transaction types (Sale (Full), Sale (Partial), Exchange)
- Repeated table headers on continuation pages are silently skipped
- Footer lines (page numbers, totals) do not produce false transactions
"""

from __future__ import annotations

from datetime import date

import pytest

from src.parse.disclosures.house_ptr_text import parse_house_ptr
from src.parse.disclosures.models import Chamber, FilingType, OwnerType, TransactionType

# ---------------------------------------------------------------------------
# Shared fixtures — multi-page page-text strings
# ---------------------------------------------------------------------------

# A clean 3-page PTR.  Page 1 is the cover sheet; page 2 is the table with
# the first four rows; page 3 is a continuation page with two more rows and
# typical footer noise.

_CLEAN_PAGE_1 = """\
U.S. House of Representatives
Periodic Transaction Report

Member Name: JOHNSON, ROBERT K.
For Calendar Year: 2023
Date Filed: 01/22/2024
"""

_CLEAN_PAGE_2 = """\
# Owner  Asset  Transaction Type  Date  Amount

1  Self  Apple Inc. (AAPL)  Purchase  12/01/2023  $1,001 - $15,000
2  Self  Microsoft Corporation (MSFT)  Sale (Full)  12/05/2023  $15,001 - $50,000
3  SP   Amazon.com Inc. (AMZN)  Sale (Partial)  12/10/2023  $50,001 - $100,000
4  JT   Tesla Inc (TSLA)  Purchase  12/15/2023  $1,001 - $15,000
"""

_CLEAN_PAGE_3 = """\
# Owner  Asset  Transaction Type  Date  Amount

5  DC   Nvidia Corp (NVDA)  Purchase  12/20/2023  $1,001 - $15,000
6  Self  Alphabet Inc (GOOGL)  Sale  12/22/2023  $15,001 - $50,000

---
Page 2 of 2
"""

_CLEAN_PAGES = [_CLEAN_PAGE_1, _CLEAN_PAGE_2, _CLEAN_PAGE_3]


# An amendment PTR.  Page 1 has the amendment header; page 2 has a mix of
# asterisk-marked amended rows and a normal row.

_AMEND_PAGE_1 = """\
U.S. House of Representatives
Periodic Transaction Report
Amendment No. 2

Member Name: SMITH, ALICE M.
For Calendar Year: 2023
Date Filed: 03/01/2024
"""

_AMEND_PAGE_2 = """\
# Owner  Asset  Transaction Type  Date  Amount

* 1  Self  Apple Inc. (AAPL)  Purchase  12/01/2023  $1,001 - $15,000
2  SP   Nvidia Corp (NVDA)  Sale (Full)  12/08/2023  $15,001 - $50,000
[A] 3  JT   Alphabet Inc (GOOGL)  Exchange  11/30/2023  $50,001 - $100,000
"""

_AMEND_PAGES = [_AMEND_PAGE_1, _AMEND_PAGE_2]


# A PTR with realistic malformed rows mixed in: a footer total, a page-number
# line with a date, and a row that has a date but no amount.

_MALFORMED_PAGE_1 = """\
U.S. House of Representatives
Periodic Transaction Report

Member Name: LEE, DAVID P.
For Calendar Year: 2023
Date Filed: 02/10/2024
"""

_MALFORMED_PAGE_2 = """\
# Owner  Asset  Transaction Type  Date  Amount

1  Self  Apple Inc. (AAPL)  Purchase  11/01/2023  $1,001 - $15,000
This line is a stray note with no date or amount.
2  Self  Tesla Inc (TSLA)  Purchase  11/05/2023
Total transactions as of 12/31/2023: see attached schedule
3  SP   Microsoft Corp (MSFT)  Sale (Full)  11/10/2023  $15,001 - $50,000
"""

_MALFORMED_PAGES = [_MALFORMED_PAGE_1, _MALFORMED_PAGE_2]


# A PTR whose header contains "Annual Financial Disclosure Report" instead of
# "Periodic Transaction Report" — simulates a misclassified document.

_ANNUAL_LABEL_PAGE_1 = """\
U.S. House of Representatives
Annual Financial Disclosure Report

Member Name: BROWN, CAROL J.
For Calendar Year: 2023
Date Filed: 05/15/2024
"""

_ANNUAL_LABEL_PAGE_2 = """\
# Owner  Asset  Transaction Type  Date  Amount

1  Self  Apple Inc. (AAPL)  Purchase  04/01/2023  $1,001 - $15,000
"""

_ANNUAL_LABEL_PAGES = [_ANNUAL_LABEL_PAGE_1, _ANNUAL_LABEL_PAGE_2]


# A PTR document where the header line says "U.S. Senate" — wrong chamber.

_SENATE_LABEL_PAGE_1 = """\
U.S. Senate
Periodic Transaction Report

Member Name: WALKER, JAMES R.
For Calendar Year: 2023
Date Filed: 02/28/2024
"""

_SENATE_LABEL_PAGE_2 = """\
# Owner  Asset  Transaction Type  Date  Amount

1  Self  Apple Inc. (AAPL)  Purchase  12/01/2023  $1,001 - $15,000
"""

_SENATE_LABEL_PAGES = [_SENATE_LABEL_PAGE_1, _SENATE_LABEL_PAGE_2]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _tx_types(result) -> list[TransactionType]:
    return [t.transaction_type for t in result.transactions]


def _owner_types(result) -> list[OwnerType]:
    return [t.owner_type for t in result.transactions]


# ---------------------------------------------------------------------------
# Clean multi-page PTR
# ---------------------------------------------------------------------------


class TestCleanMultiPagePTR:
    def test_transaction_count(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert len(result.transactions) == 6

    def test_filing_year(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert result.filing.filing_year == 2023

    def test_filed_at(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert result.filing.filed_at == date(2024, 1, 22)

    def test_chamber_and_filing_type(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert result.filing.chamber == Chamber.HOUSE
        assert result.filing.filing_type == FilingType.PTR

    def test_is_not_amended(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert result.filing.is_amended is False
        assert result.filing.amendment_number == 0

    def test_transaction_types(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        types = _tx_types(result)
        # rows 1,4,5 are Purchase; rows 2,3,6 are Sale
        assert types[0] == TransactionType.PURCHASE  # Apple Purchase
        assert types[1] == TransactionType.SALE       # MSFT Sale (Full)
        assert types[2] == TransactionType.SALE       # AMZN Sale (Partial)
        assert types[3] == TransactionType.PURCHASE   # Tesla Purchase
        assert types[4] == TransactionType.PURCHASE   # Nvidia Purchase
        assert types[5] == TransactionType.SALE       # Alphabet Sale

    def test_owner_types(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        types = _owner_types(result)
        assert types[0] == OwnerType.SELF
        assert types[1] == OwnerType.SELF
        assert types[2] == OwnerType.SPOUSE
        assert types[3] == OwnerType.JOINT
        assert types[4] == OwnerType.DEPENDENT
        assert types[5] == OwnerType.SELF

    def test_tickers_extracted(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        tickers = [t.issuer_ticker for t in result.transactions]
        assert tickers == ["AAPL", "MSFT", "AMZN", "TSLA", "NVDA", "GOOGL"]

    def test_page3_footer_produces_no_false_transactions(self) -> None:
        """Lines like 'Page 2 of 2' and 'Printed 01/22/2024' must be skipped."""
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        # All 6 rows are real; footer noise adds none.
        assert len(result.transactions) == 6

    def test_repeated_table_header_does_not_appear_as_transaction(self) -> None:
        """The '# Owner  Asset ...' line on page 3 has no date; silently skipped."""
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert len(result.transactions) == 6

    def test_line_numbers_sequential(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert [t.line_number for t in result.transactions] == [1, 2, 3, 4, 5, 6]

    def test_no_parse_warnings_on_clean_filing(self) -> None:
        result = parse_house_ptr(_CLEAN_PAGES, member_bioguide_id="J000123")
        assert result.meta.parse_warnings == ()


# ---------------------------------------------------------------------------
# Amendment PTR with asterisk-prefixed rows
# ---------------------------------------------------------------------------


class TestAmendmentPTR:
    def test_is_amended_true(self) -> None:
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        assert result.filing.is_amended is True

    def test_amendment_number(self) -> None:
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        assert result.filing.amendment_number == 2

    def test_asterisk_row_parsed_correctly(self) -> None:
        """'* 1 Self Apple ...' must parse the owner and ticker, not '*'."""
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        tx = result.transactions[0]
        assert tx.owner_type == OwnerType.SELF
        assert tx.issuer_ticker == "AAPL"
        assert tx.transaction_type == TransactionType.PURCHASE

    def test_bracket_a_row_parsed_correctly(self) -> None:
        """'[A] 3 JT Alphabet ...' must treat [A] as a marker, not the owner."""
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        tx = result.transactions[2]
        assert tx.owner_type == OwnerType.JOINT
        assert tx.issuer_ticker == "GOOGL"
        assert tx.transaction_type == TransactionType.EXCHANGE

    def test_total_transaction_count(self) -> None:
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        assert len(result.transactions) == 3

    def test_sale_full_in_amendment_row(self) -> None:
        result = parse_house_ptr(_AMEND_PAGES, member_bioguide_id="S000999")
        assert result.transactions[1].transaction_type == TransactionType.SALE


# ---------------------------------------------------------------------------
# Mixed valid / malformed rows
# ---------------------------------------------------------------------------


class TestMalformedRowsRealistic:
    def test_valid_rows_extracted(self) -> None:
        """Rows 1 and 3 are valid; row 2 has no amount → 2 transactions."""
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        assert len(result.transactions) == 2

    def test_row_without_amount_produces_warning(self) -> None:
        """Row '2 Self Tesla ... 11/05/2023' lacks an amount → warning emitted."""
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        assert any("amount" in w for w in result.meta.parse_warnings)

    def test_stray_note_line_silently_skipped(self) -> None:
        """A plain-text line with no date is silently dropped; no warning emitted
        for that specific line.  Warnings only appear for lines that contain a
        date but are missing an amount (the Tesla row and the footer total line).
        """
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        # Two lines have a date but no amount: the Tesla row and the footer
        # 'Total transactions as of 12/31/2023' line.
        amount_warnings = [w for w in result.meta.parse_warnings if "amount" in w]
        assert len(amount_warnings) == 2

    def test_footer_total_line_not_parsed(self) -> None:
        """'Total transactions as of 12/31/2023: see attached schedule' has a
        date (12/31/2023) but no amount → skipped with warning, not a tx."""
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        # Expect exactly 2 real transactions despite the date in the footer line.
        assert len(result.transactions) == 2

    def test_valid_rows_correct_types(self) -> None:
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        assert result.transactions[0].transaction_type == TransactionType.PURCHASE
        assert result.transactions[1].transaction_type == TransactionType.SALE

    def test_valid_rows_correct_dates(self) -> None:
        result = parse_house_ptr(_MALFORMED_PAGES, member_bioguide_id="L000001")
        assert result.transactions[0].transaction_date == date(2023, 11, 1)
        assert result.transactions[1].transaction_date == date(2023, 11, 10)


# ---------------------------------------------------------------------------
# Header / type conflicts
# ---------------------------------------------------------------------------


class TestHeaderTypeConflictRealistic:
    def test_annual_label_produces_filing_type_warning(self) -> None:
        """Document with 'Annual Financial Disclosure Report' header instead
        of 'Periodic Transaction Report' triggers a filing-type warning."""
        result = parse_house_ptr(_ANNUAL_LABEL_PAGES, member_bioguide_id="B000001")
        assert any("filing type" in w for w in result.meta.parse_warnings)

    def test_annual_label_filing_type_stays_ptr(self) -> None:
        """Fixed PTR invariant must not be overridden by the conflicting header."""
        result = parse_house_ptr(_ANNUAL_LABEL_PAGES, member_bioguide_id="B000001")
        assert result.filing.filing_type == FilingType.PTR

    def test_annual_label_transactions_still_parsed(self) -> None:
        """Header conflict must not block transaction row parsing."""
        result = parse_house_ptr(_ANNUAL_LABEL_PAGES, member_bioguide_id="B000001")
        assert len(result.transactions) == 1

    def test_senate_label_produces_chamber_warning(self) -> None:
        """Document with 'U.S. Senate' header triggers a chamber warning."""
        result = parse_house_ptr(_SENATE_LABEL_PAGES, member_bioguide_id="W000001")
        assert any("chamber" in w for w in result.meta.parse_warnings)

    def test_senate_label_chamber_stays_house(self) -> None:
        """Fixed HOUSE invariant must not be overridden by the Senate label."""
        result = parse_house_ptr(_SENATE_LABEL_PAGES, member_bioguide_id="W000001")
        assert result.filing.chamber == Chamber.HOUSE

    def test_senate_label_transactions_still_parsed(self) -> None:
        result = parse_house_ptr(_SENATE_LABEL_PAGES, member_bioguide_id="W000001")
        assert len(result.transactions) == 1


# ---------------------------------------------------------------------------
# Multi-word transaction type specifics
# ---------------------------------------------------------------------------


class TestMultiWordTransactionTypes:
    """Ensure 'Sale (Full)' and 'Sale (Partial)' survive realistic spacing."""

    @pytest.mark.parametrize(
        "tx_type_text, expected",
        [
            ("Sale (Full)", TransactionType.SALE),
            ("Sale (Partial)", TransactionType.SALE),
            ("sale (full)", TransactionType.SALE),
            ("SALE (PARTIAL)", TransactionType.SALE),
        ],
    )
    def test_sale_variant(self, tx_type_text: str, expected: TransactionType) -> None:
        page = "\n".join(
            [
                "U.S. House of Representatives",
                "Periodic Transaction Report",
                "Member Name: TEST, USER",
                "For Calendar Year: 2023",
                "Date Filed: 01/01/2024",
                "# Owner  Asset  Transaction Type  Date  Amount",
                f"1  Self  Apple Inc. (AAPL)  {tx_type_text}  12/01/2023  $1,001 - $15,000",
            ]
        )
        result = parse_house_ptr([page], member_bioguide_id="T000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].transaction_type == expected

    def test_exchange_not_confused_with_sale(self) -> None:
        """'Exchange' transaction type is extracted even when issuer has no
        ticker, ensuring the phrase-order fix in _TX_TYPE_PHRASES holds."""
        page = "\n".join(
            [
                "U.S. House of Representatives",
                "Periodic Transaction Report",
                "Member Name: TEST, USER",
                "For Calendar Year: 2023",
                "Date Filed: 01/01/2024",
                "# Owner  Asset  Transaction Type  Date  Amount",
                "1  Self  iShares ETF Trust  Exchange  08/10/2023  $15,001 - $50,000",
            ]
        )
        result = parse_house_ptr([page], member_bioguide_id="T000001")
        assert len(result.transactions) == 1
        assert result.transactions[0].transaction_type == TransactionType.EXCHANGE
