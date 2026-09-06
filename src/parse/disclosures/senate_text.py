"""Senate EFD disclosure text parser.

Input:  list[str] of per-page text strings from a Senate disclosure PDF.
Output: ParseResult — holdings, transactions, outside positions, and warnings.

Chamber-specific responsibilities kept here:
- Preamble detection and header-field extraction.
- Senate section header set (extends the shared SECTION_HEADERS).
- Column layout patterns for Schedule A, Schedule B, and Part I.
- Date-anchored row detection for PTR files that omit "Schedule B".
"""

from __future__ import annotations

import re

from src.parse.disclosures.header_fields import extract_header_fields
from src.parse.disclosures.holding_rows import HoldingColumnMap, holding_rows_from_table
from src.parse.disclosures.models import Filing, Holding, OutsidePosition, Transaction
from src.parse.disclosures.outside_position_rows import outside_position_rows_from_table
from src.parse.disclosures.parse_result import ParseResult, ParserMeta
from src.parse.disclosures.text_lines import (
    SECTION_HEADERS,
    drop_empty,
    flatten_lines,
    pages_to_line_lists,
    slice_section,
)
from src.parse.disclosures.transaction_rows import transaction_from_cells

_PARSER_NAME = "senate_text"
_PARSER_VERSION = "1.0"

# Canonical Senate section header tokens (lower-cased exact-match).
# Extends the shared set with Senate-specific aliases used in EFD filings.
_SENATE_SECTION_HEADERS: frozenset[str] = SECTION_HEADERS | frozenset(
    {
        "transactions",
        "assets and income",
        "assets and unearned income",  # Senate EFD Schedule A variant label
        "outside positions",
        "positions held outside u.s. government",  # EFD Part I long-form alias
        "positions held outside us government",  # EFD Part I alias without periods
    }
)

# Named Senate section headers consumed by slice_section.
_SCHEDULE_A = "schedule a"
_ASSETS_ALT = "assets and unearned income"  # EFD Schedule A alias
_ASSETS_ALT2 = "assets and income"  # Some older EFD forms omit "unearned"

_SCHEDULE_B = "schedule b"
_TRANSACTIONS_ALT = "transactions"  # PTR filings sometimes omit "Schedule B"
_TRANSACTIONS_ALT2 = "part ii"  # Some PTR filings label transactions "Part II"

_PART_I = "part i"
_PART_I_ALT = "positions held outside u.s. government"  # EFD Part I long-form alias
_PART_I_ALT2 = "positions held outside us government"  # EFD Part I alias without periods

# Column separator: two or more spaces, or a hard tab, used in Senate EFD text.
_COL_SEP: re.Pattern[str] = re.compile(r"  +|\t")

# Owner tokens that appear as the first cell of data rows in all three sections.
# Kept in sync with the normalization map in normalize.normalize_owner_label.
_OWNER_TOKENS: frozenset[str] = frozenset(
    {"self", "sp", "jt", "dc", "joint", "spouse", "dep. child", "dependent", "dependent child"}
)

# Second-cell values that unambiguously identify a column-header line.
# Used to reject header rows whose first cell happens to be a valid owner token
# (e.g. "SP  Asset Name  Asset Type  ...").
# Only multi-word phrases or terms that cannot plausibly be issuer/entity names.
_COLUMN_HEADER_CELLS: frozenset[str] = frozenset(
    {
        "asset name",
        "asset type",
        "value of asset",
        "income type",
        "income amount",
        "organization",
        "transaction type",
        "transaction date",
    }
)

# Regex that matches a MM/DD/YYYY date; anchors to the start of the cell.
_DATE_RE: re.Pattern[str] = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")

# Senate Schedule A column layout (0-based):
#   0: owner  1: asset name  2: asset type  3: value range  4: income type  5: income range
_SENATE_HOLDING_COLMAP = HoldingColumnMap(
    owner=0,
    issuer_name=1,
    asset_category=2,
    value_label=3,
    income_label=5,
)


# Internal helpers


def _split_cells(line: str) -> list[str]:
    """Split a Senate data line on 2+ spaces or a tab; drop blank fragments."""
    return [c.strip() for c in _COL_SEP.split(line) if c.strip()]


def _is_owner_row(cells: list[str]) -> bool:
    """Return True when the first cell is a recognised owner token.

    A row is also rejected when its second cell is a known column-header label
    (e.g. "SP  Asset Name  …") even though "sp" is a valid owner token.
    """
    if not cells or cells[0].lower() not in _OWNER_TOKENS:
        return False
    if len(cells) > 1 and cells[1].lower() in _COLUMN_HEADER_CELLS:
        return False
    return True


def _is_date_row(cells: list[str]) -> bool:
    """Return True when the first cell is a MM/DD/YYYY date string."""
    return bool(cells) and bool(_DATE_RE.match(cells[0]))


def _preamble_lines(all_lines: tuple[str, ...]) -> tuple[str, ...]:
    """Return lines that precede the first Senate section header.

    The preamble contains filer identity and filing-type boilerplate.
    """
    result: list[str] = []
    for line in all_lines:
        if line.strip().lower() in _SENATE_SECTION_HEADERS:
            break
        result.append(line)
    return tuple(result)


def _senate_slice(all_lines: tuple[str, ...], header: str) -> tuple[str, ...]:
    """Slice a named section using the Senate-extended section header set."""
    return slice_section(all_lines, header, _SENATE_SECTION_HEADERS)


# Section-level row extractors (Senate column formats)


def _holdings_from_section(section_lines: tuple[str, ...]) -> list[Holding]:
    """Parse Senate Schedule A lines into Holding objects.

    Each data line is split on the Senate column separator.  Lines whose
    first cell is not a recognised owner token are treated as column headers,
    continuation lines, or PDF artefacts and are skipped.

    Schedule A column layout (0-based): owner | name | type | value | income_type | income
    """
    table_rows: list[list[str]] = []
    for line in section_lines:
        cells = _split_cells(line)
        if _is_owner_row(cells):
            table_rows.append(cells)
    return holding_rows_from_table(table_rows, _SENATE_HOLDING_COLMAP)


def _transactions_from_section(section_lines: tuple[str, ...]) -> list[Transaction]:
    """Parse Senate Schedule B or PTR lines into Transaction objects.

    Senate transaction row format (6 cells): DATE  OWNER  TICKER  ASSET  TYPE  AMOUNT
    Short row format (5 cells, no ticker):   DATE  OWNER  ASSET   TYPE   AMOUNT

    Rows whose first cell is not a MM/DD/YYYY date are skipped (headers, notes).
    ValueError from date or field parsing causes a row to be silently skipped.
    """
    result: list[Transaction] = []
    line_number = 1
    for line in section_lines:
        cells = _split_cells(line)
        if not _is_date_row(cells):
            continue
        if len(cells) >= 6:
            owner, ticker, name, tx_type, amount = cells[1], cells[2], cells[3], cells[4], cells[5]
        elif len(cells) == 5:
            owner, ticker, name, tx_type, amount = cells[1], "", cells[2], cells[3], cells[4]
        else:
            continue
        if not name.strip():
            # Empty asset name: row is a PDF artefact or continuation fragment.
            continue
        try:
            result.append(
                transaction_from_cells(
                    line_number=line_number,
                    owner_raw=owner,
                    issuer_name_raw=name,
                    tx_type_raw=tx_type,
                    tx_date_raw=cells[0],
                    amount_raw=amount,
                    issuer_ticker_raw=ticker,
                )
            )
            line_number += 1
        except ValueError:
            continue
    return result


def _outside_positions_from_section(
    section_lines: tuple[str, ...],
) -> list[OutsidePosition]:
    """Parse Senate Part I lines into OutsidePosition objects.

    Part I row format: OWNER  ORGANIZATION  POSITION  FROM  TO

    Lines whose first cell is not a recognised owner token are skipped.
    """
    raw_rows: list[dict[str, str]] = []
    for line in section_lines:
        cells = _split_cells(line)
        if not _is_owner_row(cells):
            continue
        raw_rows.append(
            {
                "owner": cells[0],
                "organization": cells[1] if len(cells) > 1 else "",
                "position": cells[2] if len(cells) > 2 else "",
                "from": cells[3] if len(cells) > 3 else "",
                "to": cells[4] if len(cells) > 4 else "",
            }
        )
    return outside_position_rows_from_table(raw_rows)


# Public entry point


def parse_senate_text(page_texts: list[str], filing: Filing) -> ParseResult:
    """Parse a Senate EFD disclosure from per-page text strings.

    Steps:
      1. Flatten pages to a sequence of non-empty lines.
      2. Extract preamble lines (before the first section header) for
         filing-level header field detection.
      3. Slice each named Senate section from the full line sequence.
         Schedule B is attempted first with its canonical header; PTR
         filings that omit "Schedule B" are caught by the "transactions"
         alias.
      4. Delegate each section to the appropriate row extractor.
      5. Emit parse warnings for structurally empty or ambiguous documents.

    *filing* carries member identity and index-derived metadata; this
    function does not resolve member_bioguide_id from the PDF text.
    """
    pages = pages_to_line_lists(page_texts)
    all_lines: tuple[str, ...] = drop_empty(flatten_lines(pages))

    preamble = _preamble_lines(all_lines)
    header = extract_header_fields(preamble)

    schedule_a = _senate_slice(all_lines, _SCHEDULE_A)
    if not schedule_a:
        # Senate EFD filings occasionally label this section
        # "Assets and Unearned Income" instead of "Schedule A".
        schedule_a = _senate_slice(all_lines, _ASSETS_ALT)
    if not schedule_a:
        # Older Senate EFD forms use the shorter "Assets and Income" label.
        schedule_a = _senate_slice(all_lines, _ASSETS_ALT2)

    schedule_b = _senate_slice(all_lines, _SCHEDULE_B)
    if not schedule_b:
        # PTR filings sometimes label the transaction table "Transactions"
        # rather than "Schedule B".
        schedule_b = _senate_slice(all_lines, _TRANSACTIONS_ALT)
    if not schedule_b:
        # Some PTR filings use "Part II" for the transaction section.
        schedule_b = _senate_slice(all_lines, _TRANSACTIONS_ALT2)

    part_i = _senate_slice(all_lines, _PART_I)
    if not part_i:
        # Some EFD exports spell out the full section title.
        part_i = _senate_slice(all_lines, _PART_I_ALT)
    if not part_i:
        # Variant without periods in "U.S."
        part_i = _senate_slice(all_lines, _PART_I_ALT2)

    holdings = _holdings_from_section(schedule_a)
    transactions = _transactions_from_section(schedule_b)
    outside_positions = _outside_positions_from_section(part_i)

    warnings: list[str] = []
    if not holdings and not transactions:
        warnings.append("no_holdings_or_transactions_found")
    if header.filing_type is None:
        warnings.append("filing_type_not_detected")

    return ParseResult(
        filing=filing,
        holdings=tuple(holdings),
        transactions=tuple(transactions),
        outside_positions=tuple(outside_positions),
        meta=ParserMeta(
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            parse_warnings=tuple(warnings),
        ),
    )
