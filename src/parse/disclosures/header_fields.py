"""Extract filing-level header fields from raw disclosure text.

Input: line-oriented text from the first pages of a House or Senate
financial-disclosure or PTR form.  All helpers are pure — no I/O, no
member resolution.

Hidden invariants:
- Amendment number 0 means the filing is not an amendment (or the number
  is not stated); is_amended may still be True when the amendment number
  is absent from the text.
- PTR detection takes priority over ANNUAL because the word "Annual" can
  appear inside PTR header boilerplate on some Senate forms.
- AMENDMENT detection is checked last: the word appears in the PTR
  amendment header too, so we only set FilingType.AMENDMENT when neither
  PTR nor ANNUAL was matched first.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from src.parse.disclosures.models import Chamber, FilingType

# Stable conflict tokens

CONFLICT_AMBIGUOUS_CHAMBER = "ambiguous_chamber:both_house_and_senate"
CONFLICT_YEAR_CONFLICT_PREFIX = "year_conflict:multiple_labeled_years="
CONFLICT_AMBIGUOUS_MEMBER_NAME = "ambiguous_member_name:multiple_labeled"


@dataclass(frozen=True)
class HeaderFields:
    """Filing-level header data extracted from raw disclosure text.

    ``conflicts`` carries zero or more short machine-readable tokens that
    describe intra-header inconsistencies found during extraction.  An empty
    tuple means the header is internally consistent.  Downstream callers may
    log or route-to-review filings whose conflict set is non-empty without
    having to re-scan the raw text.

    Possible tokens:
    - ``"ambiguous_chamber:both_house_and_senate"``
    - ``"year_conflict:multiple_labeled_years=[Y1, Y2]"``
    - ``"ambiguous_member_name:multiple_labeled"``
    """

    member_name: str | None
    chamber: Chamber | None
    filing_type: FilingType | None
    filing_year: int | None
    filed_at: date | None
    amendment_number: int
    is_amended: bool
    conflicts: tuple[str, ...] = ()


# ── Compiled patterns ─────────────────────────────────────────────────────────

_RE_MEMBER_NAME = re.compile(r"^(?:member\s+name|name)\s*:\s*(.+)$", re.IGNORECASE)

_RE_HOUSE = re.compile(r"\bu\.?s\.?\s+house\b|\bhouse\s+of\s+representatives\b", re.IGNORECASE)
_RE_SENATE = re.compile(r"\bu\.?s\.?\s+senate\b|\bsenate\b", re.IGNORECASE)

_RE_PTR = re.compile(r"\bperiodic\s+transaction\s+report\b", re.IGNORECASE)
_RE_ANNUAL = re.compile(
    r"\bannual\s+(?:financial\s+)?(?:disclosure\s+)?report\b"
    r"|\bannual\s+report\b",
    re.IGNORECASE,
)
_RE_AMENDMENT_WORD = re.compile(r"\bamendment\b", re.IGNORECASE)

_RE_YEAR_LABELED = re.compile(
    r"(?:reporting\s+year|calendar\s+year|for\s+(?:the\s+)?year)\s*:?\s*(\d{4})",
    re.IGNORECASE,
)
_RE_YEAR_BARE = re.compile(r"\b(20\d{2})\b")

_RE_DATE_LABELED = re.compile(r"(?:date\s+filed|filed(?:\s+date)?)\s*:\s*(.+)$", re.IGNORECASE)

_RE_AMENDMENT_NUMBER = re.compile(r"\bamendment\s+(?:no\.?|number|#)?\s*(\d+)\b", re.IGNORECASE)

_DATE_FORMATS = (
    "%B %d, %Y",  # May 15, 2024
    "%b %d, %Y",  # May 15, 2024
    "%m/%d/%Y",  # 05/15/2024
    "%Y-%m-%d",  # 2024-05-15
    "%m-%d-%Y",  # 05-15-2024
)


# ── Field extractors ──────────────────────────────────────────────────────────


def _extract_member_name(lines: Sequence[str]) -> str | None:
    for line in lines:
        m = _RE_MEMBER_NAME.match(line.strip())
        if m:
            name = m.group(1).strip()
            return name or None
    return None


def _extract_chamber(lines: Sequence[str]) -> Chamber | None:
    for line in lines:
        if _RE_HOUSE.search(line):
            return Chamber.HOUSE
        if _RE_SENATE.search(line):
            return Chamber.SENATE
    return None


def _extract_filing_type(lines: Sequence[str]) -> FilingType | None:
    has_ptr = any(_RE_PTR.search(line) for line in lines)
    if has_ptr:
        return FilingType.PTR

    has_annual = any(_RE_ANNUAL.search(line) for line in lines)
    if has_annual:
        return FilingType.ANNUAL

    has_amendment = any(_RE_AMENDMENT_WORD.search(line) for line in lines)
    if has_amendment:
        return FilingType.AMENDMENT

    return None


def _extract_filing_year(lines: Sequence[str]) -> int | None:
    for line in lines:
        m = _RE_YEAR_LABELED.search(line)
        if m:
            return int(m.group(1))
    for line in lines:
        m = _RE_YEAR_BARE.search(line)
        if m:
            return int(m.group(1))
    return None


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _extract_filed_at(lines: Sequence[str]) -> date | None:
    for line in lines:
        m = _RE_DATE_LABELED.search(line.strip())
        if m:
            return _parse_date(m.group(1))
    return None


def _extract_amendment_number(lines: Sequence[str]) -> int:
    for line in lines:
        m = _RE_AMENDMENT_NUMBER.search(line)
        if m:
            return int(m.group(1))
    return 0


def _extract_is_amended(
    lines: Sequence[str],
    filing_type: FilingType | None,
    amendment_number: int,
) -> bool:
    if amendment_number > 0:
        return True
    if filing_type == FilingType.AMENDMENT:
        return True
    return any(_RE_AMENDMENT_WORD.search(line) for line in lines)


def _detect_conflicts(lines: Sequence[str]) -> tuple[str, ...]:
    """Return machine-readable tokens for intra-header inconsistencies.

    Scans the same line set used by the other extractors.  Only structural
    conflicts that a downstream reviewer would need to adjudicate are
    reported — incidental co-occurrence of keywords (e.g. "Annual" inside
    a PTR title) is handled by extraction priority, not flagged here.
    """
    conflicts: list[str] = []

    # Both chamber markers present in the header text.
    has_house = any(_RE_HOUSE.search(line) for line in lines)
    has_senate = any(_RE_SENATE.search(line) for line in lines)
    if has_house and has_senate:
        conflicts.append(CONFLICT_AMBIGUOUS_CHAMBER)

    # Multiple distinct labeled reporting years (e.g. two "Reporting Year:" lines).
    labeled_years = [
        int(m.group(1)) for line in lines for m in [_RE_YEAR_LABELED.search(line)] if m
    ]
    unique_labeled = sorted(set(labeled_years))
    if len(unique_labeled) > 1:
        conflicts.append(f"{CONFLICT_YEAR_CONFLICT_PREFIX}{unique_labeled}")

    # Multiple distinct names under a labeled name field.
    named: list[str] = []
    for line in lines:
        m = _RE_MEMBER_NAME.match(line.strip())
        if m:
            name = m.group(1).strip()
            if name:
                named.append(name)
    if len(set(named)) > 1:
        conflicts.append(CONFLICT_AMBIGUOUS_MEMBER_NAME)

    return tuple(conflicts)


# ── Public entry point ────────────────────────────────────────────────────────


def extract_header_fields(lines: Sequence[str]) -> HeaderFields:
    """Return header fields parsed from line-oriented disclosure text.

    Lines should come from the first pages of a House or Senate financial-
    disclosure or PTR form.  Any field that cannot be determined from the
    supplied text is returned as ``None`` (or ``0`` / ``False`` for numeric
    and boolean fields).  Detected intra-header inconsistencies are returned
    in ``conflicts`` as compact tokens for downstream logging or review routing.
    """
    filing_type = _extract_filing_type(lines)
    amendment_number = _extract_amendment_number(lines)
    return HeaderFields(
        member_name=_extract_member_name(lines),
        chamber=_extract_chamber(lines),
        filing_type=filing_type,
        filing_year=_extract_filing_year(lines),
        filed_at=_extract_filed_at(lines),
        amendment_number=amendment_number,
        is_amended=_extract_is_amended(lines, filing_type, amendment_number),
        conflicts=_detect_conflicts(lines),
    )
