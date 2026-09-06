"""Typed records for disclosure data, matching the locked v1 schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

# --- Enums matching schema CHECK constraints ---


class Chamber(str, Enum):
    HOUSE = "house"
    SENATE = "senate"


class FilingType(str, Enum):
    ANNUAL = "annual"
    PTR = "ptr"
    AMENDMENT = "amendment"


class OwnerType(str, Enum):
    SELF = "self"
    SPOUSE = "spouse"
    JOINT = "joint"
    DEPENDENT = "dependent"
    TRUST = "trust"
    OTHER = "other"


class TransactionType(str, Enum):
    PURCHASE = "purchase"
    SALE = "sale"
    EXCHANGE = "exchange"
    GIFT = "gift"
    INCOME = "income"
    OTHER = "other"


@dataclass(frozen=True)
class Filing:
    """Maps to the `financial_disclosure` table."""

    member_bioguide_id: str
    chamber: Chamber
    filing_year: int
    filing_type: FilingType
    filed_at: date | None = None
    filing_period_start: date | None = None
    filing_period_end: date | None = None
    amendment_number: int = 0
    is_amended: bool = False
    supersedes_filing_source_id: str | None = None
    source_artifact_sha256: str | None = None
    source_record_id: str | None = None


@dataclass(frozen=True)
class Holding:
    """Maps to the `holding` table."""

    line_number: int
    owner_type: OwnerType
    issuer_name: str
    issuer_ticker: str | None = None
    asset_description: str | None = None
    asset_category: str | None = None
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    value_label: str | None = None
    income_min: Decimal | None = None
    income_max: Decimal | None = None
    income_label: str | None = None
    is_liquid: bool | None = None
    source_record_id: str | None = None


@dataclass(frozen=True)
class Transaction:
    """Maps to the `transaction` table."""

    line_number: int
    owner_type: OwnerType
    issuer_name: str
    transaction_type: TransactionType
    transaction_date: date
    issuer_ticker: str | None = None
    asset_description: str | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    amount_label: str | None = None
    source_record_id: str | None = None


@dataclass(frozen=True)
class OutsidePosition:
    """No canonical table in v1; parsed and stored as holding-adjacent metadata."""

    line_number: int
    owner_type: OwnerType
    entity_name: str
    position_title: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    source_record_id: str | None = None
