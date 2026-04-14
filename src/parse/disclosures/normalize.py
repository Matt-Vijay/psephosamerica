"""Deterministic normalization for disclosure field values.

All functions are pure: no network, no database, no side effects.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from src.parse.disclosures.models import OwnerType, TransactionType


# ---------------------------------------------------------------------------
# Amount range normalization
# ---------------------------------------------------------------------------

# Canonical disclosure amount ranges from House/Senate filings.
_AMOUNT_RANGES: dict[str, tuple[Decimal, Decimal]] = {
    "$1 - $1,000": (Decimal("1"), Decimal("1000")),
    "$1,001 - $15,000": (Decimal("1001"), Decimal("15000")),
    "$15,001 - $50,000": (Decimal("15001"), Decimal("50000")),
    "$50,001 - $100,000": (Decimal("50001"), Decimal("100000")),
    "$100,001 - $250,000": (Decimal("100001"), Decimal("250000")),
    "$250,001 - $500,000": (Decimal("250001"), Decimal("500000")),
    "$500,001 - $1,000,000": (Decimal("500001"), Decimal("1000000")),
    "$1,000,001 - $5,000,000": (Decimal("1000001"), Decimal("5000000")),
    "$5,000,001 - $25,000,000": (Decimal("5000001"), Decimal("25000000")),
    "$25,000,001 - $50,000,000": (Decimal("25000001"), Decimal("50000000")),
    "Over $50,000,000": (Decimal("50000001"), Decimal("50000001")),
}

# Build a lookup keyed on the whitespace/case-normalized label.
_NORMALIZED_RANGES: dict[str, tuple[Decimal, Decimal]] = {
    re.sub(r"\s+", " ", k).strip().lower(): v for k, v in _AMOUNT_RANGES.items()
}


def normalize_amount_range(raw: str) -> Optional[tuple[Decimal, Decimal]]:
    """Parse a disclosure amount-range label into (min, max) Decimals.

    Returns None for unrecognized values (caller should flag for review).
    """
    key = re.sub(r"\s+", " ", raw).strip().lower()
    return _NORMALIZED_RANGES.get(key)


# ---------------------------------------------------------------------------
# Transaction type normalization
# ---------------------------------------------------------------------------

_TX_TYPE_MAP: dict[str, TransactionType] = {
    "p": TransactionType.PURCHASE,
    "purchase": TransactionType.PURCHASE,
    "buy": TransactionType.PURCHASE,
    "s": TransactionType.SALE,
    "sale": TransactionType.SALE,
    "sale (full)": TransactionType.SALE,
    "sale (partial)": TransactionType.SALE,
    "sell": TransactionType.SALE,
    "sold": TransactionType.SALE,
    "e": TransactionType.EXCHANGE,
    "exchange": TransactionType.EXCHANGE,
    "gift": TransactionType.GIFT,
    "income": TransactionType.INCOME,
    "dividend": TransactionType.INCOME,
    "interest": TransactionType.INCOME,
    "capital gains": TransactionType.INCOME,
}


def normalize_tx_type(raw: str) -> TransactionType:
    """Map raw transaction-type text to canonical TransactionType.

    Falls back to OTHER for unrecognized values.
    """
    key = raw.strip().lower()
    return _TX_TYPE_MAP.get(key, TransactionType.OTHER)


# ---------------------------------------------------------------------------
# Owner label normalization
# ---------------------------------------------------------------------------

_OWNER_MAP: dict[str, OwnerType] = {
    "self": OwnerType.SELF,
    "sp": OwnerType.SPOUSE,
    "spouse": OwnerType.SPOUSE,
    "jt": OwnerType.JOINT,
    "joint": OwnerType.JOINT,
    "dc": OwnerType.DEPENDENT,
    "dep. child": OwnerType.DEPENDENT,
    "dependent": OwnerType.DEPENDENT,
    "dependent child": OwnerType.DEPENDENT,
    "trust": OwnerType.TRUST,
}


def normalize_owner_label(raw: str) -> OwnerType:
    """Map raw owner text to canonical OwnerType.

    Falls back to OTHER for unrecognized values.
    """
    key = raw.strip().lower()
    return _OWNER_MAP.get(key, OwnerType.OTHER)


# ---------------------------------------------------------------------------
# Asset name cleanup
# ---------------------------------------------------------------------------

# Patterns to strip from asset names.
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\s*\(filing\s+id[^)]*\)", re.IGNORECASE),
    re.compile(r"\s*\[.*?\]"),
    re.compile(r"\s+"),  # collapse whitespace (applied last)
]


def clean_asset_name(raw: str) -> str:
    """Deterministic cleanup of raw asset/issuer name strings.

    Strips common noise patterns, collapses whitespace, and title-cases.
    """
    text = raw.strip()
    for pat in _NOISE_PATTERNS:
        text = pat.sub(" " if pat.pattern == r"\s+" else "", text)
    text = text.strip()
    if not text:
        return raw.strip()
    return text
