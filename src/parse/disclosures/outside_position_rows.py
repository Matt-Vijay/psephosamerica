"""Pure row-level parsers for outside-position tables in congressional disclosures.

Converts raw cell values — strings as they come from PDF extraction or OCR —
into OutsidePosition model instances.

Public surface:
    outside_position_from_cells(...)       one row from explicit cell values
    outside_position_rows_from_table(...)  all rows from a keyed table
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Optional

from src.parse.disclosures.models import OutsidePosition, OwnerType
from src.parse.disclosures.normalize import normalize_owner_label


# ---------------------------------------------------------------------------
# Internal cell helpers
# ---------------------------------------------------------------------------

# Congressional disclosures use MM/DD/YYYY most often; ISO and two-digit year
# appear in amended and older filings.
_DATE_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%m-%d-%Y",
)

# Cells that carry no date value regardless of their literal content.
_BLANK_DATE_TOKENS: frozenset[str] = frozenset({"-", "—", "n/a", "present", "current", "ongoing"})


def _parse_date(raw: str) -> Optional[date]:
    """Return a date from a raw cell string, or None when blank or unrecognized."""
    text = raw.strip()
    if not text or text.lower() in _BLANK_DATE_TOKENS:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _clean_cell(raw: Optional[str]) -> Optional[str]:
    """Collapse whitespace and return None for blank or placeholder values."""
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    if not text or text in ("-", "—", "N/A", "n/a"):
        return None
    return text


# ---------------------------------------------------------------------------
# Public parsers
# ---------------------------------------------------------------------------


def outside_position_from_cells(
    line_number: int,
    owner_raw: str,
    entity_name_raw: str,
    position_title_raw: Optional[str],
    from_date_raw: Optional[str],
    to_date_raw: Optional[str],
    source_record_id: Optional[str] = None,
) -> OutsidePosition:
    """Parse one outside-position row from explicit PDF cell values.

    Caller maps table columns to arguments; this function only handles
    type coercion and normalization.  owner_raw and entity_name_raw are
    required non-empty inputs; all other arguments are optional.
    """
    owner: OwnerType = normalize_owner_label(owner_raw)
    entity_name: str = _clean_cell(entity_name_raw) or entity_name_raw.strip()
    position_title: Optional[str] = _clean_cell(position_title_raw)
    from_date: Optional[date] = _parse_date(from_date_raw) if from_date_raw is not None else None
    to_date: Optional[date] = _parse_date(to_date_raw) if to_date_raw is not None else None

    return OutsidePosition(
        line_number=line_number,
        owner_type=owner,
        entity_name=entity_name,
        position_title=position_title,
        from_date=from_date,
        to_date=to_date,
        source_record_id=source_record_id,
    )


def outside_position_rows_from_table(
    rows: Sequence[Mapping[str, str]],
    *,
    owner_col: str = "owner",
    entity_col: str = "organization",
    position_col: str = "position",
    from_col: str = "from",
    to_col: str = "to",
    source_record_id: Optional[str] = None,
) -> list[OutsidePosition]:
    """Parse all data rows from a keyed outside-position table.

    Each entry in *rows* is a mapping of column name to raw cell string.
    Column names are case-sensitive; the defaults match normalized header names
    produced by most congressional disclosure PDF extractors.

    Rows where the entity column is blank or missing are silently skipped —
    they are artefacts of PDF table extraction splitting merged header cells
    into apparent data rows.

    line_number is 1-based and counts only the rows that produce a result.
    """
    result: list[OutsidePosition] = []
    for line_number, row in enumerate(rows, start=1):
        entity_raw = row.get(entity_col, "")
        if not _clean_cell(entity_raw):
            continue

        result.append(
            outside_position_from_cells(
                line_number=line_number,
                owner_raw=row.get(owner_col, ""),
                entity_name_raw=entity_raw,
                position_title_raw=row.get(position_col),
                from_date_raw=row.get(from_col),
                to_date_raw=row.get(to_col),
                source_record_id=source_record_id,
            )
        )
    return result
