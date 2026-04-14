"""Canonical load-plan layer for financial disclosures.

Takes DisclosureTransformResult objects (from the parse/transform layer) and
produces ordered table-batch plans ready for the DB write layer, plus sidecar
outputs for outside positions.

No DB writes occur here.  FK resolution (e.g. ``member_id`` from
``member_bioguide_id``) is deferred to the write layer.

Batch order respects FK dependencies:
  1. financial_disclosure   (parent; must be inserted before children)
  2. holding                (FK → financial_disclosure)
  3. transaction            (FK → financial_disclosure)
  4. review_queue           (references provenance IDs only; no FK to holdings/txs)

Outside positions are returned as sidecars and are NOT included in canonical
batches — they have no v1 schema table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    FinancialDisclosurePayload,
    HoldingPayload,
    OutsidePositionSidecar,
    ReviewQueuePayload,
    TransactionPayload,
)

# ---------------------------------------------------------------------------
# Plan types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableBatch:
    """Ordered collection of row dicts for a single canonical table.

    Rows are in the order they should be inserted.  Columns that require a DB
    identity FK are represented as natural-key fields so the write layer can
    resolve them without coupling the plan layer to DB state.

    Convention: fields prefixed with ``disclosure_`` in holding/transaction
    rows are natural-key back-references to the parent ``financial_disclosure``
    row.  The write layer must resolve these before inserting.
    """

    table: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DisclosureLoadPlan:
    """Complete load plan for a batch of disclosure transform results.

    ``batches`` is an ordered sequence of TableBatch objects.  The order
    respects FK dependencies and must be preserved by the write layer.

    ``sidecars`` contains outside-position payloads that have no canonical
    table in v1.  Callers may archive them or write them to review_queue
    payload columns.
    """

    batches: tuple[TableBatch, ...]
    sidecars: tuple[OutsidePositionSidecar, ...]


# ---------------------------------------------------------------------------
# Row serialisers (pure functions)
# ---------------------------------------------------------------------------


def _disclosure_row(p: FinancialDisclosurePayload) -> dict[str, Any]:
    """Produce a financial_disclosure row dict.

    ``member_bioguide_id`` is the natural lookup key for ``member_id``; the
    write layer must resolve it before inserting.

    ``supersedes_filing_source_id`` is the ``source_record_id`` of the filing
    this amendment supersedes; the write layer resolves it to the FK
    ``supersedes_financial_disclosure_id``.
    """
    return {
        "member_bioguide_id": p.member_bioguide_id,
        "chamber": p.chamber,
        "filing_year": p.filing_year,
        "filing_type": p.filing_type,
        "amendment_number": p.amendment_number,
        "is_amended": p.is_amended,
        "filed_at": p.filed_at,
        "filing_period_start": p.filing_period_start,
        "filing_period_end": p.filing_period_end,
        "source_record_id": p.source_record_id,
        "supersedes_filing_source_id": p.supersedes_filing_source_id,
    }


def _disclosure_natural_key(p: FinancialDisclosurePayload) -> dict[str, Any]:
    """Return natural-key fields for back-referencing the parent disclosure.

    Included verbatim in holding and transaction rows so the write layer can
    locate the ``financial_disclosure_id`` FK without additional state.
    """
    return {
        "disclosure_member_bioguide_id": p.member_bioguide_id,
        "disclosure_filing_year": p.filing_year,
        "disclosure_filing_type": p.filing_type,
        "disclosure_amendment_number": p.amendment_number,
    }


def _holding_row(p: HoldingPayload, disclosure_ref: dict[str, Any]) -> dict[str, Any]:
    """Produce a holding row dict with a natural-key reference to the parent disclosure."""
    return {
        **disclosure_ref,
        "line_number": p.line_number,
        "owner_type": p.owner_type,
        "issuer_name": p.issuer_name,
        "issuer_ticker": p.issuer_ticker,
        "asset_description": p.asset_description,
        "asset_category": p.asset_category,
        "value_min": p.value_min,
        "value_max": p.value_max,
        "value_label": p.value_label,
        "income_min": p.income_min,
        "income_max": p.income_max,
        "income_label": p.income_label,
        "is_liquid": p.is_liquid,
        "source_record_id": p.source_record_id,
    }


def _transaction_row(p: TransactionPayload, disclosure_ref: dict[str, Any]) -> dict[str, Any]:
    """Produce a transaction row dict with a natural-key reference to the parent disclosure."""
    return {
        **disclosure_ref,
        "line_number": p.line_number,
        "owner_type": p.owner_type,
        "issuer_name": p.issuer_name,
        "issuer_ticker": p.issuer_ticker,
        "asset_description": p.asset_description,
        "transaction_type": p.transaction_type,
        "transaction_date": p.transaction_date,
        "amount_min": p.amount_min,
        "amount_max": p.amount_max,
        "amount_label": p.amount_label,
        "source_record_id": p.source_record_id,
    }


def _review_queue_row(p: ReviewQueuePayload) -> dict[str, Any]:
    """Produce a review_queue row dict."""
    return {
        "review_type": p.review_type,
        "entity_type": p.entity_type,
        "entity_key": p.entity_key,
        "reason_code": p.reason_code,
        "priority": p.priority,
        "payload": p.payload,
        "status": p.status,
        "summary": p.summary,
        "parse_run_id": p.parse_run_id,
        "source_artifact_id": p.source_artifact_id,
        "ingestion_run_id": p.ingestion_run_id,
    }


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

# Canonical table names in FK-safe insertion order.
_TABLE_ORDER: tuple[str, ...] = (
    "financial_disclosure",
    "holding",
    "transaction",
    "review_queue",
)


def plan_disclosure_load(results: list[DisclosureTransformResult]) -> DisclosureLoadPlan:
    """Build a canonical load plan from a batch of disclosure transform results.

    Iterates ``results`` in order, accumulating rows into per-table lists.
    The returned plan preserves FK dependency order across all input results:
    every ``financial_disclosure`` row appears before the holding/transaction
    rows that reference it.

    Amendment-derived review items are preserved alongside all other review
    items; their ordering within the review_queue batch mirrors the order they
    were emitted by the transform layer.

    Outside positions are collected as sidecars and are NOT placed in any
    canonical table batch.

    Args:
        results: One DisclosureTransformResult per filing, in any order.

    Returns:
        A DisclosureLoadPlan with four TableBatch objects (always present,
        possibly empty) in FK-safe insertion order, plus outside-position
        sidecars.
    """
    disclosure_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    transaction_rows: list[dict[str, Any]] = []
    review_queue_rows: list[dict[str, Any]] = []
    sidecars: list[OutsidePositionSidecar] = []

    for result in results:
        d_row = _disclosure_row(result.disclosure)
        d_ref = _disclosure_natural_key(result.disclosure)

        disclosure_rows.append(d_row)

        for holding in result.holdings:
            holding_rows.append(_holding_row(holding, d_ref))

        for transaction in result.transactions:
            transaction_rows.append(_transaction_row(transaction, d_ref))

        for review_item in result.review_items:
            review_queue_rows.append(_review_queue_row(review_item))

        sidecars.extend(result.outside_positions)

    batches = tuple(
        TableBatch(table=table, rows=tuple(rows))
        for table, rows in zip(
            _TABLE_ORDER,
            [disclosure_rows, holding_rows, transaction_rows, review_queue_rows],
        )
    )

    return DisclosureLoadPlan(
        batches=batches,
        sidecars=tuple(sidecars),
    )
