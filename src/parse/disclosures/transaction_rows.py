"""Parse individual transaction rows into Transaction model instances.

Pure helpers — no I/O, no database, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from src.parse.disclosures.models import Transaction
from src.parse.disclosures.normalize import (
    clean_asset_name,
    normalize_amount_range,
    normalize_owner_label,
    normalize_tx_type,
)

# Date formats seen in House and Senate PTR filings, in priority order.
_DATE_FORMATS: tuple[str, ...] = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")


def _parse_tx_date(raw: str) -> date:
    """Parse common disclosure date strings into a date.

    Raises ValueError for unrecognized formats; callers handle the failure.
    """
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized transaction date: {raw!r}")


def _clean_ticker(raw: str) -> str | None:
    stripped = raw.strip()
    return stripped if stripped else None


def transaction_from_cells(
    *,
    line_number: int,
    owner_raw: str,
    issuer_name_raw: str,
    tx_type_raw: str,
    tx_date_raw: str,
    amount_raw: str,
    issuer_ticker_raw: str = "",
    asset_description_raw: str = "",
    source_record_id: str | None = None,
) -> Transaction:
    """Build one Transaction from explicit raw cell values.

    Applies normalization via the shared helpers; callers pass raw strings
    exactly as they appear in the extracted table cell.

    amount_min/max are derived from amount_raw when the label is recognized.
    If the label is unrecognized both remain None; amount_label preserves the
    raw string so the review layer can flag it later.

    Raises ValueError if tx_date_raw cannot be parsed.
    """
    owner_type = normalize_owner_label(owner_raw)
    issuer_name = clean_asset_name(issuer_name_raw)
    transaction_type = normalize_tx_type(tx_type_raw)
    transaction_date = _parse_tx_date(tx_date_raw)
    ticker = _clean_ticker(issuer_ticker_raw)
    description = asset_description_raw.strip() or None

    amount_label = amount_raw.strip() or None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    if amount_label is not None:
        pair = normalize_amount_range(amount_label)
        if pair is not None:
            amount_min, amount_max = pair

    return Transaction(
        line_number=line_number,
        owner_type=owner_type,
        issuer_name=issuer_name,
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        issuer_ticker=ticker,
        asset_description=description,
        amount_min=amount_min,
        amount_max=amount_max,
        amount_label=amount_label,
        source_record_id=source_record_id,
    )


def transaction_rows_from_table(
    rows: Sequence[dict[str, str]],
    *,
    line_number_start: int = 1,
) -> list[Transaction]:
    """Parse a sequence of raw table row dicts into Transaction objects.

    Required keys per row: "owner", "issuer_name", "tx_type", "tx_date",
    "amount".  Optional keys: "ticker", "description", "source_record_id".

    line_number is assigned sequentially from line_number_start.
    ValueError from date parsing propagates to the caller.
    """
    result: list[Transaction] = []
    for i, row in enumerate(rows):
        result.append(
            transaction_from_cells(
                line_number=line_number_start + i,
                owner_raw=row["owner"],
                issuer_name_raw=row["issuer_name"],
                tx_type_raw=row["tx_type"],
                tx_date_raw=row["tx_date"],
                amount_raw=row["amount"],
                issuer_ticker_raw=row.get("ticker", ""),
                asset_description_raw=row.get("description", ""),
                source_record_id=row.get("source_record_id"),
            )
        )
    return result
