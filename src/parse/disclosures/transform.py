"""Transform parsed disclosures into canonical row payloads.  No DB writes, no network."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from src.parse.disclosures.models import (
    Filing,
    Holding,
    OutsidePosition,
    Transaction,
)
from src.parse.disclosures.normalize import normalize_amount_range

# Stable review_type constants

REVIEW_TYPE_AMENDMENT = "amendment"
REVIEW_TYPE_NORMALIZATION = "normalization"
REVIEW_TYPE_CLASSIFICATION = "classification"
REVIEW_TYPE_OUTSIDE_POSITION = "outside_position"

# Stable reason_code constants

REASON_AMENDMENT_FILING = "amendment_filing"
REASON_AMENDMENT_SUPERSEDES_MISMATCH = "amendment_supersedes_mismatch"
REASON_AMENDMENT_NUMBER_WITHOUT_FLAG = "amendment_number_without_flag"
REASON_UNKNOWN_AMOUNT_RANGE = "unknown_amount_range"
REASON_UNKNOWN_INCOME_RANGE = "unknown_income_range"
REASON_UNKNOWN_OWNER_TYPE = "unknown_owner_type"
REASON_UNKNOWN_TRANSACTION_TYPE = "unknown_transaction_type"
REASON_UNRESOLVED_TRUST = "unresolved_trust"
REASON_NO_CANONICAL_TABLE_V1 = "no_canonical_table_v1"


# Parse context (caller-supplied provenance refs)


@dataclass(frozen=True)
class ParseContext:
    """Provenance references supplied by the caller.

    IDs are None when the row has not been persisted yet (e.g. in tests or
    dry-run mode). Downstream writers populate them before insert.
    """

    parse_run_id: int | None = None
    source_artifact_id: int | None = None
    ingestion_run_id: int | None = None


# Canonical row payloads


@dataclass(frozen=True)
class FinancialDisclosurePayload:
    """Row-shaped payload for the ``financial_disclosure`` table.

    ``member_bioguide_id`` is the natural lookup key.  The DB writer must
    resolve it to ``member_id`` before inserting.

    ``supersedes_filing_source_id`` is the source-record ID of the filing
    this amendment supersedes (if any).  The DB writer must resolve it to
    ``supersedes_financial_disclosure_id`` before inserting.
    """

    member_bioguide_id: str
    chamber: str
    filing_year: int
    filing_type: str
    amendment_number: int
    is_amended: bool
    filed_at: date | None = None
    filing_period_start: date | None = None
    filing_period_end: date | None = None
    source_record_id: str | None = None
    source_artifact_id: int | None = None
    source_artifact_sha256: str | None = None
    # Natural key of the superseded filing; caller resolves to FK.
    supersedes_filing_source_id: str | None = None


@dataclass(frozen=True)
class HoldingPayload:
    """Row-shaped payload for the ``holding`` table.

    ``financial_disclosure_id`` is set by the DB writer after the parent
    ``financial_disclosure`` row is inserted.
    """

    line_number: int
    owner_type: str
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
    source_artifact_id: int | None = None


@dataclass(frozen=True)
class TransactionPayload:
    line_number: int
    owner_type: str
    issuer_name: str
    transaction_type: str
    transaction_date: date
    issuer_ticker: str | None = None
    asset_description: str | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    amount_label: str | None = None
    source_record_id: str | None = None
    source_artifact_id: int | None = None


@dataclass(frozen=True)
class OutsidePositionSidecar:
    """No canonical table in v1; stored in review_queue.payload or archived."""

    line_number: int
    owner_type: str
    entity_name: str
    position_title: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    source_record_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "owner_type": self.owner_type,
            "entity_name": self.entity_name,
            "position_title": self.position_title,
            "from_date": self.from_date.isoformat() if self.from_date else None,
            "to_date": self.to_date.isoformat() if self.to_date else None,
            "source_record_id": self.source_record_id,
        }


@dataclass(frozen=True)
class ReviewQueuePayload:
    """Row-shaped payload for the ``review_queue`` table.

    ``priority`` follows the schema CHECK (1-100); lower is more urgent.
    ``status`` is always ``'open'`` at emission time.
    """

    review_type: str
    entity_type: str
    entity_key: str
    reason_code: str
    priority: int  # 1 = most urgent, 100 = least urgent
    payload: dict[str, Any]
    summary: str | None = None
    status: str = "open"
    parse_run_id: int | None = None
    source_artifact_id: int | None = None
    ingestion_run_id: int | None = None


# Transform result


@dataclass
class DisclosureTransformResult:
    """All payloads produced from a single Filing plus its line items."""

    disclosure: FinancialDisclosurePayload
    holdings: list[HoldingPayload] = field(default_factory=list)
    transactions: list[TransactionPayload] = field(default_factory=list)
    # No canonical table in v1 — see OutsidePositionSidecar docstring.
    outside_positions: list[OutsidePositionSidecar] = field(default_factory=list)
    review_items: list[ReviewQueuePayload] = field(default_factory=list)


# Internal review helpers

# Priority constants (1 = most urgent).
_PRIORITY_AMENDMENT_INCONSISTENCY = 10
_PRIORITY_AMENDMENT = 20
_PRIORITY_TRUST_OR_OPTION = 30
_PRIORITY_UNKNOWN_AMOUNT = 40
_PRIORITY_UNKNOWN_OWNER = 50
_PRIORITY_UNKNOWN_TX_TYPE = 50
_PRIORITY_OUTSIDE_POSITION = 60


def _review(
    ctx: ParseContext,
    *,
    review_type: str,
    entity_type: str,
    entity_key: str,
    reason_code: str,
    priority: int,
    payload: dict[str, Any],
    summary: str | None = None,
) -> ReviewQueuePayload:
    return ReviewQueuePayload(
        review_type=review_type,
        entity_type=entity_type,
        entity_key=entity_key,
        reason_code=reason_code,
        priority=priority,
        payload=payload,
        summary=summary,
        status="open",
        parse_run_id=ctx.parse_run_id,
        source_artifact_id=ctx.source_artifact_id,
        ingestion_run_id=ctx.ingestion_run_id,
    )


# Individual item transforms


def _transform_holding(
    h: Holding,
    ctx: ParseContext,
    review_items: list[ReviewQueuePayload],
) -> HoldingPayload:
    value_min = h.value_min
    value_max = h.value_max
    income_min = h.income_min
    income_max = h.income_max

    if value_min is None and value_max is None and h.value_label:
        pair = normalize_amount_range(h.value_label)
        if pair is not None:
            value_min, value_max = pair
        else:
            review_items.append(
                _review(
                    ctx,
                    review_type=REVIEW_TYPE_NORMALIZATION,
                    entity_type="holding",
                    entity_key=f"line_{h.line_number}",
                    reason_code=REASON_UNKNOWN_AMOUNT_RANGE,
                    priority=_PRIORITY_UNKNOWN_AMOUNT,
                    payload={"value_label": h.value_label, "line_number": h.line_number},
                    summary=f"Unrecognized value range label on holding line {h.line_number}: {h.value_label!r}",
                )
            )

    if income_min is None and income_max is None and h.income_label:
        pair = normalize_amount_range(h.income_label)
        if pair is not None:
            income_min, income_max = pair
        else:
            review_items.append(
                _review(
                    ctx,
                    review_type=REVIEW_TYPE_NORMALIZATION,
                    entity_type="holding",
                    entity_key=f"line_{h.line_number}",
                    reason_code=REASON_UNKNOWN_INCOME_RANGE,
                    priority=_PRIORITY_UNKNOWN_AMOUNT,
                    payload={"income_label": h.income_label, "line_number": h.line_number},
                    summary=f"Unrecognized income range label on holding line {h.line_number}: {h.income_label!r}",
                )
            )

    owner_str = h.owner_type.value
    if owner_str == "other":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_NORMALIZATION,
                entity_type="holding",
                entity_key=f"line_{h.line_number}",
                reason_code=REASON_UNKNOWN_OWNER_TYPE,
                priority=_PRIORITY_UNKNOWN_OWNER,
                payload={"owner_type": owner_str, "line_number": h.line_number},
                summary=f"Unknown owner type on holding line {h.line_number}",
            )
        )

    if owner_str == "trust":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_CLASSIFICATION,
                entity_type="holding",
                entity_key=f"line_{h.line_number}",
                reason_code=REASON_UNRESOLVED_TRUST,
                priority=_PRIORITY_TRUST_OR_OPTION,
                payload={
                    "issuer_name": h.issuer_name,
                    "asset_category": h.asset_category,
                    "line_number": h.line_number,
                },
                summary=f"Trust-owned holding on line {h.line_number} requires classification: {h.issuer_name!r}",
            )
        )

    return HoldingPayload(
        line_number=h.line_number,
        owner_type=owner_str,
        issuer_name=h.issuer_name,
        issuer_ticker=h.issuer_ticker,
        asset_description=h.asset_description,
        asset_category=h.asset_category,
        value_min=value_min,
        value_max=value_max,
        value_label=h.value_label,
        income_min=income_min,
        income_max=income_max,
        income_label=h.income_label,
        is_liquid=h.is_liquid,
        source_record_id=h.source_record_id,
        source_artifact_id=ctx.source_artifact_id,
    )


def _transform_transaction(
    t: Transaction,
    ctx: ParseContext,
    review_items: list[ReviewQueuePayload],
) -> TransactionPayload:
    amount_min = t.amount_min
    amount_max = t.amount_max

    if amount_min is None and amount_max is None and t.amount_label:
        pair = normalize_amount_range(t.amount_label)
        if pair is not None:
            amount_min, amount_max = pair
        else:
            review_items.append(
                _review(
                    ctx,
                    review_type=REVIEW_TYPE_NORMALIZATION,
                    entity_type="transaction",
                    entity_key=f"line_{t.line_number}",
                    reason_code=REASON_UNKNOWN_AMOUNT_RANGE,
                    priority=_PRIORITY_UNKNOWN_AMOUNT,
                    payload={"amount_label": t.amount_label, "line_number": t.line_number},
                    summary=f"Unrecognized amount label on transaction line {t.line_number}: {t.amount_label!r}",
                )
            )

    owner_str = t.owner_type.value
    if owner_str == "other":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_NORMALIZATION,
                entity_type="transaction",
                entity_key=f"line_{t.line_number}",
                reason_code=REASON_UNKNOWN_OWNER_TYPE,
                priority=_PRIORITY_UNKNOWN_OWNER,
                payload={"owner_type": owner_str, "line_number": t.line_number},
                summary=f"Unknown owner type on transaction line {t.line_number}",
            )
        )

    tx_str = t.transaction_type.value
    if tx_str == "other":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_NORMALIZATION,
                entity_type="transaction",
                entity_key=f"line_{t.line_number}",
                reason_code=REASON_UNKNOWN_TRANSACTION_TYPE,
                priority=_PRIORITY_UNKNOWN_TX_TYPE,
                payload={"transaction_type": tx_str, "line_number": t.line_number},
                summary=f"Unknown transaction type on transaction line {t.line_number}",
            )
        )

    return TransactionPayload(
        line_number=t.line_number,
        owner_type=owner_str,
        issuer_name=t.issuer_name,
        transaction_type=tx_str,
        transaction_date=t.transaction_date,
        issuer_ticker=t.issuer_ticker,
        asset_description=t.asset_description,
        amount_min=amount_min,
        amount_max=amount_max,
        amount_label=t.amount_label,
        source_record_id=t.source_record_id,
        source_artifact_id=ctx.source_artifact_id,
    )


def _transform_outside_position(
    op: OutsidePosition,
    ctx: ParseContext,
    review_items: list[ReviewQueuePayload],
) -> OutsidePositionSidecar:
    sidecar = OutsidePositionSidecar(
        line_number=op.line_number,
        owner_type=op.owner_type.value,
        entity_name=op.entity_name,
        position_title=op.position_title,
        from_date=op.from_date,
        to_date=op.to_date,
        source_record_id=op.source_record_id,
    )
    review_items.append(
        _review(
            ctx,
            review_type=REVIEW_TYPE_OUTSIDE_POSITION,
            entity_type="outside_position",
            entity_key=f"line_{op.line_number}",
            reason_code=REASON_NO_CANONICAL_TABLE_V1,
            priority=_PRIORITY_OUTSIDE_POSITION,
            payload=sidecar.as_dict(),
            summary=f"Outside position on line {op.line_number} ({op.entity_name!r}) has no canonical table in v1 — stored as sidecar",
        )
    )

    if op.owner_type.value == "other":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_NORMALIZATION,
                entity_type="outside_position",
                entity_key=f"line_{op.line_number}",
                reason_code=REASON_UNKNOWN_OWNER_TYPE,
                priority=_PRIORITY_UNKNOWN_OWNER,
                payload={"owner_type": op.owner_type.value, "line_number": op.line_number},
                summary=f"Unknown owner type on outside position line {op.line_number}",
            )
        )

    return sidecar


# Top-level transform function


def transform_filing(
    filing: Filing,
    holdings: list[Holding],
    transactions: list[Transaction],
    outside_positions: list[OutsidePosition],
    ctx: ParseContext,
) -> DisclosureTransformResult:
    review_items: list[ReviewQueuePayload] = []

    # --- Financial disclosure payload ---
    disclosure_payload = FinancialDisclosurePayload(
        member_bioguide_id=filing.member_bioguide_id,
        chamber=filing.chamber.value,
        filing_year=filing.filing_year,
        filing_type=filing.filing_type.value,
        amendment_number=filing.amendment_number,
        is_amended=filing.is_amended,
        filed_at=filing.filed_at,
        filing_period_start=filing.filing_period_start,
        filing_period_end=filing.filing_period_end,
        source_record_id=filing.source_record_id,
        source_artifact_id=ctx.source_artifact_id,
        source_artifact_sha256=filing.source_artifact_sha256,
        supersedes_filing_source_id=filing.supersedes_filing_source_id,
    )

    # --- Amendment review item ---
    # Amendments always require a review-queue entry so an operator can link
    # the new row to the superseded filing and confirm the supersession chain.
    _entity_key = (
        filing.source_record_id
        or f"{filing.member_bioguide_id}:{filing.filing_year}:{filing.amendment_number}"
    )
    if filing.is_amended or filing.filing_type.value == "amendment":
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_AMENDMENT,
                entity_type="financial_disclosure",
                entity_key=_entity_key,
                reason_code=REASON_AMENDMENT_FILING,
                priority=_PRIORITY_AMENDMENT,
                payload={
                    "member_bioguide_id": filing.member_bioguide_id,
                    "filing_year": filing.filing_year,
                    "filing_type": filing.filing_type.value,
                    "amendment_number": filing.amendment_number,
                    "supersedes_filing_source_id": filing.supersedes_filing_source_id,
                },
                summary=(
                    f"Amendment #{filing.amendment_number} for "
                    f"{filing.member_bioguide_id} {filing.filing_year} "
                    f"{filing.filing_type.value} — supersession chain must be verified"
                ),
            )
        )

    # --- Amendment/supersedes inconsistency checks ---
    # Emit high-priority review items when amendment metadata is internally
    # inconsistent so the load path can gate on them before writing.
    _is_amendment_type = filing.filing_type.value == "amendment"
    if filing.supersedes_filing_source_id and not filing.is_amended and not _is_amendment_type:
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_AMENDMENT,
                entity_type="financial_disclosure",
                entity_key=_entity_key,
                reason_code=REASON_AMENDMENT_SUPERSEDES_MISMATCH,
                priority=_PRIORITY_AMENDMENT_INCONSISTENCY,
                payload={
                    "member_bioguide_id": filing.member_bioguide_id,
                    "filing_year": filing.filing_year,
                    "filing_type": filing.filing_type.value,
                    "supersedes_filing_source_id": filing.supersedes_filing_source_id,
                    "is_amended": filing.is_amended,
                },
                summary=(
                    f"Filing {_entity_key} has supersedes_filing_source_id "
                    f"({filing.supersedes_filing_source_id!r}) but is_amended=False "
                    f"and filing_type={filing.filing_type.value!r} — inconsistent amendment metadata"
                ),
            )
        )

    if filing.amendment_number > 0 and not filing.is_amended and not _is_amendment_type:
        review_items.append(
            _review(
                ctx,
                review_type=REVIEW_TYPE_AMENDMENT,
                entity_type="financial_disclosure",
                entity_key=_entity_key,
                reason_code=REASON_AMENDMENT_NUMBER_WITHOUT_FLAG,
                priority=_PRIORITY_AMENDMENT_INCONSISTENCY,
                payload={
                    "member_bioguide_id": filing.member_bioguide_id,
                    "filing_year": filing.filing_year,
                    "filing_type": filing.filing_type.value,
                    "amendment_number": filing.amendment_number,
                    "is_amended": filing.is_amended,
                },
                summary=(
                    f"Filing {_entity_key} has amendment_number={filing.amendment_number} "
                    f"but is_amended=False and filing_type={filing.filing_type.value!r} — "
                    f"amendment_number set without corresponding amendment flag"
                ),
            )
        )

    # --- Holdings ---
    holding_payloads = [_transform_holding(h, ctx, review_items) for h in holdings]

    # --- Transactions ---
    transaction_payloads = [_transform_transaction(t, ctx, review_items) for t in transactions]

    # --- Outside positions (sidecar only) ---
    outside_position_sidecars = [
        _transform_outside_position(op, ctx, review_items) for op in outside_positions
    ]

    return DisclosureTransformResult(
        disclosure=disclosure_payload,
        holdings=holding_payloads,
        transactions=transaction_payloads,
        outside_positions=outside_position_sidecars,
        review_items=review_items,
    )


# Explicit batch API at the parse layer


@dataclass(frozen=True)
class FilingBundle:
    """All parsed inputs for a single disclosure document.

    Groups the filing identity record with its extracted line items so that
    callers can pass a typed batch to ``batch_transform_filings`` without
    relying on parallel positional lists.
    """

    filing: Filing
    holdings: list[Holding]
    transactions: list[Transaction]
    outside_positions: list[OutsidePosition]


def batch_transform_filings(
    bundles: Sequence[FilingBundle],
    ctx: ParseContext,
) -> list[DisclosureTransformResult]:
    """Transform a sequence of filing bundles under a shared ParseContext.

    Each bundle is transformed independently; results appear in the same
    order as the input sequence.  Use ``transform_filing`` directly when
    only one document is being processed.
    """
    return [
        transform_filing(b.filing, b.holdings, b.transactions, b.outside_positions, ctx)
        for b in bundles
    ]
