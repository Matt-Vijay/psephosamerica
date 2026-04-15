"""Parse holding rows from extracted table cells into Holding model objects.

Pure helpers only: no network, no database, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from src.parse.disclosures.models import Holding, OwnerType
from src.parse.disclosures.normalize import (
    clean_asset_name,
    normalize_amount_range,
    normalize_owner_label,
)


# ---------------------------------------------------------------------------
# Internal cell-level helpers
# ---------------------------------------------------------------------------

_LIQUID_TRUE: frozenset[str] = frozenset({"y", "yes", "1", "x", "true"})
_LIQUID_FALSE: frozenset[str] = frozenset({"n", "no", "0", "false"})


def _parse_is_liquid(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    key = raw.strip().lower()
    if key in _LIQUID_TRUE:
        return True
    if key in _LIQUID_FALSE:
        return False
    return None


def _parse_optional_str(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _resolve_amount_range(
    label: Optional[str],
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Return (min, max) when the label is recognized, else (None, None)."""
    if not label:
        return None, None
    pair = normalize_amount_range(label)
    if pair is not None:
        return pair
    return None, None


# ---------------------------------------------------------------------------
# Public: single-row parser
# ---------------------------------------------------------------------------


def holding_from_cells(
    *,
    line_number: int,
    owner_raw: str,
    issuer_name_raw: str,
    ticker_raw: Optional[str] = None,
    asset_description_raw: Optional[str] = None,
    asset_category_raw: Optional[str] = None,
    value_label_raw: Optional[str] = None,
    income_label_raw: Optional[str] = None,
    is_liquid_raw: Optional[str] = None,
    source_record_id: Optional[str] = None,
) -> Holding:
    """Parse one holding row from explicit cell values into a Holding.

    Amount ranges are resolved to (min, max) Decimal pairs when a recognized
    label is present.  Unrecognized labels are preserved as value_label /
    income_label so the caller can route them to the review queue.
    """
    owner_type: OwnerType = normalize_owner_label(owner_raw)
    issuer_name: str = clean_asset_name(issuer_name_raw)
    value_label = _parse_optional_str(value_label_raw)
    income_label = _parse_optional_str(income_label_raw)
    value_min, value_max = _resolve_amount_range(value_label)
    income_min, income_max = _resolve_amount_range(income_label)

    return Holding(
        line_number=line_number,
        owner_type=owner_type,
        issuer_name=issuer_name,
        issuer_ticker=_parse_optional_str(ticker_raw),
        asset_description=_parse_optional_str(asset_description_raw),
        asset_category=_parse_optional_str(asset_category_raw),
        value_min=value_min,
        value_max=value_max,
        value_label=value_label,
        income_min=income_min,
        income_max=income_max,
        income_label=income_label,
        is_liquid=_parse_is_liquid(is_liquid_raw),
        source_record_id=source_record_id,
    )


# ---------------------------------------------------------------------------
# Public: table parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldingColumnMap:
    """Column index assignments for a holding table.

    ``owner`` and ``issuer_name`` are always required.
    Set optional fields to None when the column is absent from this table format.
    """

    owner: int
    issuer_name: int
    ticker: Optional[int] = None
    asset_description: Optional[int] = None
    asset_category: Optional[int] = None
    value_label: Optional[int] = None
    income_label: Optional[int] = None
    is_liquid: Optional[int] = None
    source_record_id: Optional[int] = None


def _cell(row: Sequence[str], col: Optional[int]) -> Optional[str]:
    """Return a cell value by column index, or None if absent or out of range."""
    if col is None or col < 0 or col >= len(row):
        return None
    return row[col]


def holding_rows_from_table(
    rows: Sequence[Sequence[str]],
    col_map: HoldingColumnMap,
    *,
    start_line_number: int = 1,
) -> list[Holding]:
    """Parse all data rows from a holding table into Holding objects.

    Rows whose issuer_name cell is empty are skipped (header or blank separator).
    line_number starts at start_line_number and increments for each row that
    produces a Holding.
    """
    holdings: list[Holding] = []
    line_number = start_line_number
    for row in rows:
        issuer_raw = _cell(row, col_map.issuer_name) or ""
        if not issuer_raw.strip():
            continue

        owner_raw = _cell(row, col_map.owner) or ""

        holdings.append(
            holding_from_cells(
                line_number=line_number,
                owner_raw=owner_raw,
                issuer_name_raw=issuer_raw,
                ticker_raw=_cell(row, col_map.ticker),
                asset_description_raw=_cell(row, col_map.asset_description),
                asset_category_raw=_cell(row, col_map.asset_category),
                value_label_raw=_cell(row, col_map.value_label),
                income_label_raw=_cell(row, col_map.income_label),
                is_liquid_raw=_cell(row, col_map.is_liquid),
                source_record_id=_cell(row, col_map.source_record_id),
            )
        )
        line_number += 1

    return holdings
