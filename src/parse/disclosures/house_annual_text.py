"""Parse House annual and amendment financial disclosures from page text.

Single public function: parse_house_annual(page_texts, filing) -> ParseResult.

Annual-specific assumptions baked in here:
- Schedule A holds asset/holding rows; there are no transaction rows in annual filings.
- Schedule D holds outside-position rows for the filer (owner defaults to self).
- Each data row in both schedules starts with a bare integer row counter.
- Amendment filings share the same section structure as annual filings.
- Income type text (column 3 in Schedule A) is not stored; only the income amount
  label (column 4) maps to Holding.income_label.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.parse.disclosures.header_fields import HeaderFields, extract_header_fields
from src.parse.disclosures.holding_rows import HoldingColumnMap, holding_rows_from_table
from src.parse.disclosures.models import Filing
from src.parse.disclosures.outside_position_rows import outside_position_rows_from_table
from src.parse.disclosures.parse_result import ParserMeta, ParseResult
from src.parse.disclosures.text_lines import drop_empty, flatten_lines, pages_to_line_lists

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARSER_NAME = "house_annual_text"
PARSER_VERSION = "1.0"

# House annual schedules: A = assets, D = outside positions.
# B = transactions (PTR only), C = earned income, E–F = agreements/liabilities.
_RE_SCHED_A = re.compile(r"\bschedule\s+a\b", re.IGNORECASE)
_RE_SCHED_D = re.compile(r"\bschedule\s+d\b", re.IGNORECASE)

# Any schedule whose letter is not A ends a section being collected.
# Pattern: schedule [b-z] — 'a' excluded so Schedule A header is not a stop trigger.
_RE_ANY_LATER_SCHED = re.compile(r"\bschedule\s+[b-z]\b", re.IGNORECASE)

# Stop regex used when collecting Schedule D: schedules E through Z end the section.
# Schedule D itself may repeat on continuation pages ("SCHEDULE D CONTINUED"), so the
# stop pattern explicitly excludes the letter d — preventing premature termination when
# a multi-page Schedule D has its header reprinted at the top of each page.
_RE_AFTER_SCHED_D = re.compile(r"\bschedule\s+[e-z]\b", re.IGNORECASE)

# Standard House annual Schedule A column layout (0-based, after row-counter stripped):
#   0: owner abbreviation (SP / JT / DC / Self)
#   1: asset / issuer name
#   2: asset value range label
#   3: income type description  — no Holding field; skipped via col map
#   4: income amount range label
_SCHEDULE_A_COL_MAP = HoldingColumnMap(
    owner=0,
    issuer_name=1,
    value_label=2,
    income_label=4,
)

# Minimum cells required after stripping the row counter from a Schedule A data row.
# owner + issuer + value = 3; income columns are optional.
_SCHEDULE_A_MIN_CELLS = 3

# Minimum cells after row counter for a Schedule D data row (organization name).
_SCHEDULE_D_MIN_CELLS = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _section_lines(
    lines: Sequence[str],
    start_re: re.Pattern[str],
    stop_re: re.Pattern[str],
) -> tuple[str, ...]:
    """Collect lines inside the first section whose header matches *start_re*.

    Scanning begins after the line that first matches *start_re*.
    Collection stops (exclusive) when *stop_re* matches a subsequent line,
    or at end-of-document if no stop is encountered.

    Returns an empty tuple when no start match is found.

    House-specific: section headers may carry additional title text on the same
    line (e.g. "SCHEDULE A: ASSETS AND UNEARNED INCOME"), so both regexes use
    search rather than fullmatch.
    """
    collecting = False
    result: list[str] = []
    for line in lines:
        if not collecting:
            if start_re.search(line):
                collecting = True
        else:
            if stop_re.search(line):
                break
            result.append(line)
    return tuple(result)


def _split_cells(line: str) -> list[str]:
    """Split a table line into cells at two-or-more consecutive whitespace chars."""
    return re.split(r"\s{2,}", line.strip())


def _is_numbered_row(cells: list[str]) -> bool:
    """True when the leading cell is a bare non-negative integer row counter."""
    return bool(cells) and cells[0].strip().isdigit()


def _schedule_a_table(section_lines: tuple[str, ...]) -> list[list[str]]:
    """Convert Schedule A text lines to cell rows for holding_rows_from_table.

    Strips the leading row-counter cell.  Rows with fewer than
    _SCHEDULE_A_MIN_CELLS remaining cells are discarded (column-header lines
    or continuation lines with no asset data).
    """
    rows: list[list[str]] = []
    for line in section_lines:
        cells = _split_cells(line)
        if not _is_numbered_row(cells):
            continue
        data = cells[1:]  # drop row counter; col map starts at 0
        if len(data) < _SCHEDULE_A_MIN_CELLS:
            continue
        rows.append(data)
    return rows


def _schedule_d_table(section_lines: tuple[str, ...]) -> list[dict[str, str]]:
    """Convert Schedule D text lines to keyed dicts for outside_position_rows_from_table.

    Annual Schedule D column order after the row counter:
      0: organization name
      1: type of position
      2: from date   (optional)
      3: to date     (optional)

    Owner is set to 'self' for every row: House annual Schedule D lists the
    filer's own outside positions, not positions held by a spouse or dependent.
    """
    rows: list[dict[str, str]] = []
    for line in section_lines:
        cells = _split_cells(line)
        if not _is_numbered_row(cells):
            continue
        data = cells[1:]
        if len(data) < _SCHEDULE_D_MIN_CELLS:
            continue
        row: dict[str, str] = {"owner": "self"}
        row["organization"] = data[0]
        if len(data) >= 2:
            row["position"] = data[1]
        if len(data) >= 3:
            row["from"] = data[2]
        if len(data) >= 4:
            row["to"] = data[3]
        rows.append(row)
    return rows


def _header_warnings(header: HeaderFields) -> tuple[str, ...]:
    out: list[str] = []
    if header.filing_year is None:
        out.append("filing year not found in document header")
    if header.member_name is None:
        out.append("member name not found in document header")
    if header.filing_type is None:
        out.append("filing type could not be determined from document text")
    return tuple(out)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_house_annual(page_texts: list[str], filing: Filing) -> ParseResult:
    """Parse a House annual or amendment disclosure from extracted page text.

    Steps:
    1. Flatten page texts into a clean line sequence.
    2. Extract header fields (year, member name, filing type, amendment flag).
    3. Slice the Schedule A section and parse holding rows.
    4. Slice the Schedule D section and parse outside-position rows.
    5. Return a ParseResult with no transaction rows (annual filings have none).

    Arguments:
        page_texts: one string per extracted PDF page, in physical page order.
        filing: the Filing record pre-populated from the catalog; member identity
                is resolved by the caller before this function is invoked.
    """
    page_lines = pages_to_line_lists(page_texts)
    all_lines: tuple[str, ...] = drop_empty(flatten_lines(page_lines))

    header: HeaderFields = extract_header_fields(all_lines)

    sched_a = _section_lines(all_lines, _RE_SCHED_A, _RE_ANY_LATER_SCHED)
    sched_d = _section_lines(all_lines, _RE_SCHED_D, _RE_AFTER_SCHED_D)

    holdings = holding_rows_from_table(_schedule_a_table(sched_a), _SCHEDULE_A_COL_MAP)
    outside_positions = outside_position_rows_from_table(_schedule_d_table(sched_d))

    meta = ParserMeta(
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
        parse_warnings=_header_warnings(header),
    )

    return ParseResult(
        filing=filing,
        holdings=tuple(holdings),
        transactions=(),
        outside_positions=tuple(outside_positions),
        meta=meta,
    )
