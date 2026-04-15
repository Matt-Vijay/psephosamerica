"""Parse House Periodic Transaction Report (PTR) page texts into a ParseResult.

One public function: parse_house_ptr.

PTR-specific assumptions (explicit):
- Chamber is always HOUSE; this parser does not apply to Senate filings.
- Filing type is always PTR; holdings and outside positions are not present.
- The transaction table is identified by a column-header line containing the
  phrase "transaction type" and at least one of "owner" or "asset".
- Every valid transaction row contains a MM/DD/YYYY date and an amount label
  matching the canonical House disclosure ranges.  Lines missing either are
  skipped with a parse warning.
- member_bioguide_id cannot be resolved from text; the caller must supply it.
- Amendment row markers (* or [A]) may prefix a data row; they are stripped
  before field extraction and do not affect transaction parsing.
- Header signals that conflict with the fixed HOUSE/PTR invariants produce
  parse warnings; they never silently override the fixed filing identity.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.parse.disclosures.header_fields import HeaderFields, extract_header_fields
from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult, ParserMeta
from src.parse.disclosures.text_lines import drop_empty, flatten_lines, pages_to_line_lists
from src.parse.disclosures.transaction_rows import transaction_rows_from_table

_PARSER_NAME = "house_ptr_text"
_PARSER_VERSION = "1.0"

# Date: MM/DD/YYYY anywhere on the line.
_RE_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

# Amount: canonical House ranges or "Over $X".
_RE_AMOUNT = re.compile(
    r"(\$[\d,]+\s*[-\u2013]\s*\$[\d,]+|Over\s+\$[\d,]+)",
    re.IGNORECASE,
)

# Ticker: "(AAPL)" — 1–5 uppercase letters enclosed in parentheses.
_RE_TICKER = re.compile(r"\(([A-Z]{1,5})\)")

# Known PTR transaction type phrases, longest-match first within each family.
# Unambiguous single-word types (exchange, gift, income) are checked before
# "sale" so that issuers whose names contain the word "sale" do not capture
# those transaction types incorrectly.
# Note: "sale (part)" is intentionally absent — normalize_tx_type maps neither
# "sale (part)" nor its mixed-case variants.  The bare "sale" phrase below
# correctly matches the start of "Sale (Part)" text extracted from PDFs and
# normalizes to TransactionType.SALE via the shared normalize helper.
_TX_TYPE_PHRASES: tuple[str, ...] = (
    "sale (full)",
    "sale (partial)",
    "exchange",
    "gift",
    "income",
    "purchase",
    "sale",
)

# Tokens that mark individual rows as amended; stripped before field parsing.
# Both bare asterisk (*) and bracket forms ([A]) appear in extracted PTR text.
_AMENDMENT_ROW_MARKERS: frozenset[str] = frozenset({"*", "[a]"})

# Lines that match these patterns at the start are header or footer bleed from
# repeated page headers or printed footers.  They may contain dates (e.g.
# "Date Filed: 01/15/2024") that would otherwise trigger a spurious "no amount"
# warning.  Silently skip them before the amount-presence check.
_RE_HEADER_FOOTER_BLEED = re.compile(
    r"^(?:"
    r"date\s+filed\s*:"           # "Date Filed: 01/15/2024"
    r"|filed\s*date\s*:"          # "Filed Date: 01/15/2024"
    r"|for\s+calendar\s+year\s*:" # "For Calendar Year: 2023"
    r"|calendar\s+year\s*:"       # "Calendar Year: 2023"
    r"|reporting\s+year\s*:"      # "Reporting Year: 2023"
    r"|member\s+name\s*:"         # "Member Name: SMITH, JOHN"
    r"|page\s+\d+\s+of\s+\d+"     # "Page 1 of 3"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_house_ptr(
    page_texts: list[str],
    *,
    member_bioguide_id: str = "",
) -> ParseResult:
    """Parse a House PTR document from extracted page-text strings.

    *member_bioguide_id* must be supplied by the caller; it cannot be
    resolved from the PDF text alone.  Passing an empty string is valid
    when the ID has not yet been resolved — the caller should treat this
    as a parse warning and resolve it before loading.
    """
    pages = pages_to_line_lists(page_texts)
    all_lines: tuple[str, ...] = drop_empty(flatten_lines(pages))

    header = extract_header_fields(all_lines)
    warnings = _header_warnings(header, member_bioguide_id)

    filing = _build_filing(header, member_bioguide_id)

    table_idx = _find_table_header(all_lines)
    if table_idx < 0:
        warnings.append("transaction table header not found")
        return ParseResult(
            filing=filing,
            holdings=(),
            transactions=(),
            outside_positions=(),
            meta=ParserMeta(
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                parse_warnings=tuple(warnings),
            ),
        )

    data_lines = all_lines[table_idx + 1 :]
    row_dicts, row_warnings = _extract_row_dicts(data_lines)
    warnings.extend(row_warnings)

    transactions = transaction_rows_from_table(row_dicts)

    return ParseResult(
        filing=filing,
        holdings=(),
        transactions=tuple(transactions),
        outside_positions=(),
        meta=ParserMeta(
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            parse_warnings=tuple(warnings),
        ),
    )


# ---------------------------------------------------------------------------
# Header → Filing
# ---------------------------------------------------------------------------


def _build_filing(header: HeaderFields, member_bioguide_id: str) -> Filing:
    # Chamber and FilingType are fixed invariants for this parser.
    return Filing(
        member_bioguide_id=member_bioguide_id,
        chamber=Chamber.HOUSE,
        filing_year=header.filing_year or 0,
        filing_type=FilingType.PTR,
        filed_at=header.filed_at,
        amendment_number=header.amendment_number,
        is_amended=header.is_amended,
    )


def _header_warnings(header: HeaderFields, member_bioguide_id: str) -> list[str]:
    warnings: list[str] = []
    if not member_bioguide_id:
        warnings.append("member_bioguide_id not supplied; caller must resolve before load")
    if header.member_name is None:
        warnings.append("member name not found in header")
    if header.filing_year is None:
        warnings.append("filing year not found in header; filing_year set to 0")
    if header.filed_at is None:
        warnings.append("filing date not found in header")
    # Warn on unexpected chamber/type signals — the parser's invariants may be wrong.
    if header.chamber is not None and header.chamber != Chamber.HOUSE:
        warnings.append(
            f"unexpected chamber {header.chamber.value!r} detected; expected house"
        )
    if header.filing_type is not None and header.filing_type != FilingType.PTR:
        warnings.append(
            f"unexpected filing type {header.filing_type.value!r} detected; expected ptr"
        )
    return warnings


# ---------------------------------------------------------------------------
# Table detection
# ---------------------------------------------------------------------------


def _find_table_header(lines: Sequence[str]) -> int:
    """Return the index of the transaction table column-header line.

    The column-header line must contain "transaction type" and at least one
    of "owner" or "asset" (all case-insensitive).  Returns -1 when not found.
    """
    for i, line in enumerate(lines):
        lower = line.lower()
        if "transaction type" in lower and ("owner" in lower or "asset" in lower):
            return i
    return -1


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _extract_row_dicts(
    lines: Sequence[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """Convert data lines after the table header into row dicts.

    Each dict has keys: owner, issuer_name, ticker, tx_type, tx_date, amount.
    Lines without a recognisable date or amount are skipped with a warning.
    """
    rows: list[dict[str, str]] = []
    warnings: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Silently skip known header/footer bleed lines (repeated page headers,
        # printed footers) that may contain dates but are never transaction rows.
        if _RE_HEADER_FOOTER_BLEED.match(stripped):
            continue

        date_m = _RE_DATE.search(stripped)
        if not date_m:
            continue  # Not a transaction data line.

        amount_m = _RE_AMOUNT.search(stripped)
        if not amount_m:
            warnings.append(f"no amount label on transaction line: {stripped[:80]!r}")
            continue

        tx_date = date_m.group(1)
        amount = amount_m.group(1).strip()

        # Work with the portion of the line before the date.
        before_date = stripped[: date_m.start()].strip()
        owner, issuer_raw, tx_type = _split_before_date(before_date)

        # Extract ticker from issuer field and strip it from the name.
        ticker_m = _RE_TICKER.search(issuer_raw)
        ticker = ticker_m.group(1) if ticker_m else ""
        issuer_name = _RE_TICKER.sub("", issuer_raw).strip()

        if not issuer_name:
            warnings.append(f"empty issuer name on transaction line: {stripped[:80]!r}")
            continue

        rows.append(
            {
                "owner": owner,
                "issuer_name": issuer_name,
                "ticker": ticker,
                "tx_type": tx_type,
                "tx_date": tx_date,
                "amount": amount,
            }
        )

    return rows, warnings


def _split_before_date(text: str) -> tuple[str, str, str]:
    """Split the pre-date portion of a row into (owner, issuer_name, tx_type).

    Strips optional leading amendment markers (* or [A]) and an optional
    row-number token.  Identifies the owner as the first remaining token.
    Locates the transaction type phrase by longest-match right-scan, leaving
    everything before it as the issuer name.
    """
    parts = text.split()
    if not parts:
        return ("", "", "")

    idx = 0
    # Skip leading amendment markers (* or [A]) — case-insensitive.
    while idx < len(parts) and parts[idx].lower() in _AMENDMENT_ROW_MARKERS:
        idx += 1

    # Skip optional row number (a bare integer).
    if idx < len(parts) and parts[idx].isdigit():
        idx += 1

    if idx >= len(parts):
        return ("", "", "")

    owner = parts[idx]
    remaining = " ".join(parts[idx + 1 :])

    # Locate transaction type by longest-match right-scan with word-boundary
    # guards.  Both the start and end of each phrase must fall on word
    # boundaries (not run into adjacent letters) so that issuer tokens that
    # happen to contain a phrase substring (e.g. "ExchangeHub") are not
    # mistakenly captured as the transaction type.
    tx_type = ""
    cut = len(remaining)
    remaining_lower = remaining.lower()
    for phrase in _TX_TYPE_PHRASES:
        pos = remaining_lower.rfind(phrase)
        if pos < 0:
            continue
        # Start-boundary: the character immediately before the phrase must
        # not be alphabetic (prevents matching inside a compound word).
        if pos > 0 and remaining_lower[pos - 1].isalpha():
            continue
        # End-boundary: the character immediately after the phrase must not
        # be alphabetic (prevents matching "sale" inside "salepoint").
        end = pos + len(phrase)
        if phrase[-1].isalpha() and end < len(remaining_lower) and remaining_lower[end].isalpha():
            continue
        tx_type = remaining[pos : pos + len(phrase)]
        cut = pos
        break

    issuer_name = remaining[:cut].strip()
    return (owner, issuer_name, tx_type)
