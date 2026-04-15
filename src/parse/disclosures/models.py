"""Typed records for disclosure data, matching the locked v1 schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


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
    filed_at: Optional[date] = None
    filing_period_start: Optional[date] = None
    filing_period_end: Optional[date] = None
    amendment_number: int = 0
    is_amended: bool = False
    supersedes_filing_source_id: Optional[str] = None
    source_artifact_sha256: Optional[str] = None
    source_record_id: Optional[str] = None


@dataclass(frozen=True)
class Holding:
    """Maps to the `holding` table."""

    line_number: int
    owner_type: OwnerType
    issuer_name: str
    issuer_ticker: Optional[str] = None
    asset_description: Optional[str] = None
    asset_category: Optional[str] = None
    value_min: Optional[Decimal] = None
    value_max: Optional[Decimal] = None
    value_label: Optional[str] = None
    income_min: Optional[Decimal] = None
    income_max: Optional[Decimal] = None
    income_label: Optional[str] = None
    is_liquid: Optional[bool] = None
    source_record_id: Optional[str] = None


@dataclass(frozen=True)
class Transaction:
    """Maps to the `transaction` table."""

    line_number: int
    owner_type: OwnerType
    issuer_name: str
    transaction_type: TransactionType
    transaction_date: date
    issuer_ticker: Optional[str] = None
    asset_description: Optional[str] = None
    amount_min: Optional[Decimal] = None
    amount_max: Optional[Decimal] = None
    amount_label: Optional[str] = None
    source_record_id: Optional[str] = None


@dataclass(frozen=True)
class OutsidePosition:
    """No canonical table in v1; parsed and stored as holding-adjacent metadata."""

    line_number: int
    owner_type: OwnerType
    entity_name: str
    position_title: Optional[str] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    source_record_id: Optional[str] = None
