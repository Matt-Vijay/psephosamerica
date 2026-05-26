"""Tests for the disclosure canonical load-plan layer.

All tests are pure (no DB, no network).  They verify that:
- plan_disclosure_load returns four TableBatch objects in FK-safe order.
- Disclosure rows carry the expected natural-key fields.
- Holding and transaction rows carry the correct natural-key back-reference
  to their parent disclosure.
- Amendment-derived review items are preserved in the review_queue batch.
- Outside positions appear only in sidecars, never in canonical batches.
- Multiple filings produce correctly-partitioned, ordered row sets.
- An empty input produces four empty batches and no sidecars.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.load.disclosures import (
    DisclosureLoadPlan,
    TableBatch,
    plan_disclosure_load,
)
from src.parse.disclosures.models import (
    Chamber,
    Filing,
    FilingType,
    Holding,
    OutsidePosition,
    OwnerType,
    Transaction,
    TransactionType,
)
from src.parse.disclosures.transform import (
    ParseContext,
    transform_filing,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CTX = ParseContext(parse_run_id=1, source_artifact_id=2, ingestion_run_id=3)

_TABLE_ORDER = ("financial_disclosure", "holding", "transaction", "review_queue")


def _filing(**kwargs) -> Filing:
    defaults = dict(
        member_bioguide_id="A000001",
        chamber=Chamber.SENATE,
        filing_year=2023,
        filing_type=FilingType.ANNUAL,
        filed_at=date(2024, 5, 15),
        filing_period_start=date(2023, 1, 1),
        filing_period_end=date(2023, 12, 31),
        amendment_number=0,
        is_amended=False,
        source_record_id="FILING-001",
        source_artifact_sha256="a" * 64,
    )
    defaults.update(kwargs)
    return Filing(**defaults)


def _holding(**kwargs) -> Holding:
    defaults = dict(
        line_number=1,
        owner_type=OwnerType.SELF,
        issuer_name="Apple Inc",
        issuer_ticker="AAPL",
        value_min=Decimal("1001"),
        value_max=Decimal("15000"),
        value_label="$1,001 - $15,000",
    )
    defaults.update(kwargs)
    return Holding(**defaults)


def _transaction(**kwargs) -> Transaction:
    defaults = dict(
        line_number=1,
        owner_type=OwnerType.SELF,
        issuer_name="Tesla Inc",
        issuer_ticker="TSLA",
        transaction_type=TransactionType.PURCHASE,
        transaction_date=date(2023, 6, 1),
        amount_min=Decimal("1001"),
        amount_max=Decimal("15000"),
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


def _outside_position(**kwargs) -> OutsidePosition:
    defaults = dict(
        line_number=1,
        owner_type=OwnerType.SELF,
        entity_name="Acme Corp",
        position_title="Board Member",
    )
    defaults.update(kwargs)
    return OutsidePosition(**defaults)


def _result(
    filing=None,
    holdings=None,
    transactions=None,
    outside_positions=None,
    ctx=CTX,
):
    """Helper: produce a DisclosureTransformResult from minimal inputs."""
    return transform_filing(
        filing or _filing(),
        holdings or [],
        transactions or [],
        outside_positions or [],
        ctx,
    )


# ---------------------------------------------------------------------------
# Structure: batch count, order, types
# ---------------------------------------------------------------------------


class TestPlanStructure:
    def test_returns_disclosure_load_plan(self) -> None:
        plan = plan_disclosure_load([_result()])
        assert isinstance(plan, DisclosureLoadPlan)

    def test_always_produces_four_batches(self) -> None:
        plan = plan_disclosure_load([_result()])
        assert len(plan.batches) == 4

    def test_batch_order_matches_fk_dependency(self) -> None:
        plan = plan_disclosure_load([_result()])
        actual = tuple(b.table for b in plan.batches)
        assert actual == _TABLE_ORDER

    def test_each_batch_is_table_batch(self) -> None:
        plan = plan_disclosure_load([_result()])
        for batch in plan.batches:
            assert isinstance(batch, TableBatch)

    def test_empty_input_produces_four_empty_batches(self) -> None:
        plan = plan_disclosure_load([])
        assert len(plan.batches) == 4
        for batch in plan.batches:
            assert batch.rows == ()
        assert plan.sidecars == ()

    def test_sidecars_field_is_tuple(self) -> None:
        plan = plan_disclosure_load([_result()])
        assert isinstance(plan.sidecars, tuple)


# ---------------------------------------------------------------------------
# financial_disclosure batch
# ---------------------------------------------------------------------------


class TestDisclosureBatch:
    def _batch(self, results=None):
        plan = plan_disclosure_load(results or [_result()])
        return next(b for b in plan.batches if b.table == "financial_disclosure")

    def test_one_row_per_result(self) -> None:
        results = [_result(), _result(_filing(member_bioguide_id="B000002", source_record_id="F2"))]
        batch = self._batch(results)
        assert len(batch.rows) == 2

    def test_row_contains_member_bioguide_id(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["member_bioguide_id"] == "A000001"

    def test_row_contains_chamber(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["chamber"] == "senate"

    def test_row_contains_filing_year(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["filing_year"] == 2023

    def test_row_contains_filing_type(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["filing_type"] == "annual"

    def test_row_contains_amendment_number(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["amendment_number"] == 0

    def test_row_contains_is_amended(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["is_amended"] is False

    def test_row_contains_source_record_id(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["source_record_id"] == "FILING-001"

    def test_row_contains_source_artifact_id(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["source_artifact_id"] == 2

    def test_row_contains_supersedes_filing_source_id(self) -> None:
        filing = _filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-000",
        )
        batch = self._batch([_result(filing)])
        assert batch.rows[0]["supersedes_filing_source_id"] == "FILING-000"

    def test_row_supersedes_is_none_for_non_amendment(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["supersedes_filing_source_id"] is None

    def test_row_contains_filed_at(self) -> None:
        batch = self._batch()
        assert batch.rows[0]["filed_at"] == date(2024, 5, 15)

    def test_multiple_results_preserve_order(self) -> None:
        results = [
            _result(_filing(member_bioguide_id="A000001", source_record_id="F1")),
            _result(_filing(member_bioguide_id="B000002", source_record_id="F2")),
            _result(_filing(member_bioguide_id="C000003", source_record_id="F3")),
        ]
        batch = self._batch(results)
        ids = [r["member_bioguide_id"] for r in batch.rows]
        assert ids == ["A000001", "B000002", "C000003"]


# ---------------------------------------------------------------------------
# holding batch
# ---------------------------------------------------------------------------


class TestHoldingBatch:
    def _batch(self, results):
        plan = plan_disclosure_load(results)
        return next(b for b in plan.batches if b.table == "holding")

    def test_empty_when_no_holdings(self) -> None:
        batch = self._batch([_result()])
        assert batch.rows == ()

    def test_one_row_per_holding(self) -> None:
        holdings = [_holding(line_number=1), _holding(line_number=2)]
        batch = self._batch([_result(holdings=holdings)])
        assert len(batch.rows) == 2

    def test_row_carries_disclosure_natural_key(self) -> None:
        batch = self._batch([_result(holdings=[_holding()])])
        row = batch.rows[0]
        assert row["disclosure_member_bioguide_id"] == "A000001"
        assert row["disclosure_filing_year"] == 2023
        assert row["disclosure_filing_type"] == "annual"
        assert row["disclosure_amendment_number"] == 0

    def test_row_carries_holding_fields(self) -> None:
        h = _holding(
            line_number=3,
            owner_type=OwnerType.SPOUSE,
            issuer_name="Microsoft",
            issuer_ticker="MSFT",
            asset_description="Common Stock",
            asset_category="equity",
            value_min=Decimal("100001"),
            value_max=Decimal("250000"),
            value_label="$100,001 - $250,000",
            income_min=Decimal("1001"),
            income_max=Decimal("15000"),
            income_label="$1,001 - $15,000",
            is_liquid=True,
            source_record_id="H-003",
        )
        batch = self._batch([_result(holdings=[h])])
        row = batch.rows[0]
        assert row["line_number"] == 3
        assert row["owner_type"] == "spouse"
        assert row["issuer_name"] == "Microsoft"
        assert row["issuer_ticker"] == "MSFT"
        assert row["asset_description"] == "Common Stock"
        assert row["asset_category"] == "equity"
        assert row["value_min"] == Decimal("100001")
        assert row["value_max"] == Decimal("250000")
        assert row["value_label"] == "$100,001 - $250,000"
        assert row["income_min"] == Decimal("1001")
        assert row["income_max"] == Decimal("15000")
        assert row["income_label"] == "$1,001 - $15,000"
        assert row["is_liquid"] is True
        assert row["source_record_id"] == "H-003"

    def test_row_contains_source_artifact_id(self) -> None:
        batch = self._batch([_result(holdings=[_holding()])])
        assert batch.rows[0]["source_artifact_id"] == 2

    def test_holdings_from_different_filings_carry_correct_disclosure_ref(self) -> None:
        filing_a = _filing(member_bioguide_id="A000001", filing_year=2022, source_record_id="FA")
        filing_b = _filing(member_bioguide_id="B000002", filing_year=2023, source_record_id="FB")
        result_a = _result(filing_a, holdings=[_holding(line_number=1)])
        result_b = _result(filing_b, holdings=[_holding(line_number=1)])
        batch = self._batch([result_a, result_b])
        assert len(batch.rows) == 2
        assert batch.rows[0]["disclosure_member_bioguide_id"] == "A000001"
        assert batch.rows[0]["disclosure_filing_year"] == 2022
        assert batch.rows[1]["disclosure_member_bioguide_id"] == "B000002"
        assert batch.rows[1]["disclosure_filing_year"] == 2023

    def test_holdings_not_in_disclosure_batch(self) -> None:
        plan = plan_disclosure_load([_result(holdings=[_holding()])])
        disc_batch = next(b for b in plan.batches if b.table == "financial_disclosure")
        for row in disc_batch.rows:
            assert "line_number" not in row
            assert "issuer_name" not in row


# ---------------------------------------------------------------------------
# transaction batch
# ---------------------------------------------------------------------------


class TestTransactionBatch:
    def _batch(self, results):
        plan = plan_disclosure_load(results)
        return next(b for b in plan.batches if b.table == "transaction")

    def test_empty_when_no_transactions(self) -> None:
        batch = self._batch([_result()])
        assert batch.rows == ()

    def test_one_row_per_transaction(self) -> None:
        txs = [_transaction(line_number=1), _transaction(line_number=2)]
        batch = self._batch([_result(transactions=txs)])
        assert len(batch.rows) == 2

    def test_row_carries_disclosure_natural_key(self) -> None:
        batch = self._batch([_result(transactions=[_transaction()])])
        row = batch.rows[0]
        assert row["disclosure_member_bioguide_id"] == "A000001"
        assert row["disclosure_filing_year"] == 2023
        assert row["disclosure_filing_type"] == "annual"
        assert row["disclosure_amendment_number"] == 0

    def test_row_carries_transaction_fields(self) -> None:
        t = _transaction(
            line_number=5,
            owner_type=OwnerType.JOINT,
            issuer_name="Amazon",
            issuer_ticker="AMZN",
            transaction_type=TransactionType.SALE,
            transaction_date=date(2023, 9, 15),
            amount_min=Decimal("50001"),
            amount_max=Decimal("100000"),
            amount_label="$50,001 - $100,000",
            asset_description="Common Stock",
            source_record_id="TX-005",
        )
        batch = self._batch([_result(transactions=[t])])
        row = batch.rows[0]
        assert row["line_number"] == 5
        assert row["owner_type"] == "joint"
        assert row["issuer_name"] == "Amazon"
        assert row["issuer_ticker"] == "AMZN"
        assert row["transaction_type"] == "sale"
        assert row["transaction_date"] == date(2023, 9, 15)
        assert row["amount_min"] == Decimal("50001")
        assert row["amount_max"] == Decimal("100000")
        assert row["amount_label"] == "$50,001 - $100,000"
        assert row["asset_description"] == "Common Stock"
        assert row["source_record_id"] == "TX-005"

    def test_row_contains_source_artifact_id(self) -> None:
        batch = self._batch([_result(transactions=[_transaction()])])
        assert batch.rows[0]["source_artifact_id"] == 2

    def test_transactions_from_different_filings_carry_correct_disclosure_ref(self) -> None:
        filing_a = _filing(member_bioguide_id="A000001", amendment_number=0, source_record_id="FA")
        filing_b = _filing(member_bioguide_id="B000002", amendment_number=0, source_record_id="FB")
        result_a = _result(filing_a, transactions=[_transaction(line_number=1)])
        result_b = _result(filing_b, transactions=[_transaction(line_number=1)])
        batch = self._batch([result_a, result_b])
        assert batch.rows[0]["disclosure_member_bioguide_id"] == "A000001"
        assert batch.rows[1]["disclosure_member_bioguide_id"] == "B000002"


# ---------------------------------------------------------------------------
# review_queue batch
# ---------------------------------------------------------------------------


class TestReviewQueueBatch:
    def _batch(self, results):
        plan = plan_disclosure_load(results)
        return next(b for b in plan.batches if b.table == "review_queue")

    def test_empty_for_clean_filing(self) -> None:
        batch = self._batch([_result()])
        assert batch.rows == ()

    def test_amendment_review_item_preserved(self) -> None:
        filing = _filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-000",
            source_record_id="FILING-001A",
        )
        batch = self._batch([_result(filing)])
        amendment_rows = [r for r in batch.rows if r["reason_code"] == "amendment_filing"]
        assert len(amendment_rows) == 1
        row = amendment_rows[0]
        assert row["review_type"] == "amendment"
        assert row["entity_type"] == "financial_disclosure"
        assert row["status"] == "open"

    def test_amendment_review_item_payload_preserved(self) -> None:
        filing = _filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=2,
            supersedes_filing_source_id="FILING-PREV",
            source_record_id="FILING-AMEND2",
        )
        batch = self._batch([_result(filing)])
        row = next(r for r in batch.rows if r["reason_code"] == "amendment_filing")
        assert row["payload"]["amendment_number"] == 2
        assert row["payload"]["supersedes_filing_source_id"] == "FILING-PREV"

    def test_outside_position_review_item_preserved(self) -> None:
        op = _outside_position()
        batch = self._batch([_result(outside_positions=[op])])
        op_rows = [r for r in batch.rows if r["reason_code"] == "no_canonical_table_v1"]
        assert len(op_rows) == 1

    def test_holding_trust_review_preserved(self) -> None:
        h = _holding(owner_type=OwnerType.TRUST)
        batch = self._batch([_result(holdings=[h])])
        trust_rows = [r for r in batch.rows if r["reason_code"] == "unresolved_trust"]
        assert len(trust_rows) == 1

    def test_review_row_contains_required_fields(self) -> None:
        h = _holding(owner_type=OwnerType.OTHER)
        batch = self._batch([_result(holdings=[h])])
        row = next(r for r in batch.rows if r["reason_code"] == "unknown_owner_type")
        assert "review_type" in row
        assert "entity_type" in row
        assert "entity_key" in row
        assert "reason_code" in row
        assert "priority" in row
        assert "payload" in row
        assert "status" in row

    def test_review_row_carries_provenance_ids(self) -> None:
        ctx = ParseContext(parse_run_id=10, source_artifact_id=20, ingestion_run_id=30)
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_filing(_filing(), [h], [], [], ctx)
        plan = plan_disclosure_load([result])
        batch = next(b for b in plan.batches if b.table == "review_queue")
        row = next(r for r in batch.rows if r["reason_code"] == "unknown_owner_type")
        assert row["parse_run_id"] == 10
        assert row["source_artifact_id"] == 20
        assert row["ingestion_run_id"] == 30

    def test_review_items_from_multiple_filings_all_present(self) -> None:
        filing_a = _filing(
            member_bioguide_id="A000001",
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            source_record_id="FA",
        )
        filing_b = _filing(
            member_bioguide_id="B000002",
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            source_record_id="FB",
        )
        batch = self._batch([_result(filing_a), _result(filing_b)])
        amendment_rows = [r for r in batch.rows if r["reason_code"] == "amendment_filing"]
        assert len(amendment_rows) == 2

    def test_priority_within_schema_bounds(self) -> None:
        filing = _filing(is_amended=True, amendment_number=1)
        h = _holding(owner_type=OwnerType.TRUST)
        t = _transaction(transaction_type=TransactionType.OTHER)
        op = _outside_position()
        result = transform_filing(filing, [h], [t], [op], CTX)
        plan = plan_disclosure_load([result])
        batch = next(b for b in plan.batches if b.table == "review_queue")
        for row in batch.rows:
            assert 1 <= row["priority"] <= 100


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------


class TestSidecars:
    def test_no_sidecars_when_no_outside_positions(self) -> None:
        plan = plan_disclosure_load([_result()])
        assert plan.sidecars == ()

    def test_sidecar_count_matches_outside_positions(self) -> None:
        ops = [_outside_position(line_number=i) for i in range(1, 4)]
        plan = plan_disclosure_load([_result(outside_positions=ops)])
        assert len(plan.sidecars) == 3

    def test_sidecar_fields_are_preserved(self) -> None:
        op = _outside_position(
            line_number=2,
            owner_type=OwnerType.SPOUSE,
            entity_name="Big Law Firm",
            position_title="Partner",
            from_date=date(2019, 6, 1),
            to_date=date(2022, 12, 31),
        )
        plan = plan_disclosure_load([_result(outside_positions=[op])])
        sc = plan.sidecars[0]
        assert sc.line_number == 2
        assert sc.owner_type == "spouse"
        assert sc.entity_name == "Big Law Firm"
        assert sc.position_title == "Partner"
        assert sc.from_date == date(2019, 6, 1)
        assert sc.to_date == date(2022, 12, 31)

    def test_sidecars_not_in_any_canonical_batch(self) -> None:
        op = _outside_position(entity_name="SomeOrg")
        plan = plan_disclosure_load([_result(outside_positions=[op])])
        for batch in plan.batches:
            for row in batch.rows:
                assert "entity_name" not in row or batch.table == "review_queue"

    def test_sidecars_accumulated_across_multiple_filings(self) -> None:
        filing_a = _filing(member_bioguide_id="A000001", source_record_id="FA")
        filing_b = _filing(member_bioguide_id="B000002", source_record_id="FB")
        ops_a = [_outside_position(line_number=1, entity_name="OrgA")]
        ops_b = [
            _outside_position(line_number=1, entity_name="OrgB1"),
            _outside_position(line_number=2, entity_name="OrgB2"),
        ]
        plan = plan_disclosure_load(
            [
                _result(filing_a, outside_positions=ops_a),
                _result(filing_b, outside_positions=ops_b),
            ]
        )
        assert len(plan.sidecars) == 3
        names = [sc.entity_name for sc in plan.sidecars]
        assert names == ["OrgA", "OrgB1", "OrgB2"]


# ---------------------------------------------------------------------------
# Mixed scenario: full filing
# ---------------------------------------------------------------------------


class TestFullFilingPlan:
    def test_all_batches_populated_for_rich_filing(self) -> None:
        filing = _filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-000",
            source_record_id="FILING-001A",
        )
        holdings = [_holding(line_number=i) for i in range(1, 3)]
        txs = [_transaction(line_number=i) for i in range(1, 3)]
        ops = [_outside_position(line_number=1)]
        result = transform_filing(filing, holdings, txs, ops, CTX)
        plan = plan_disclosure_load([result])

        disc_batch = next(b for b in plan.batches if b.table == "financial_disclosure")
        hold_batch = next(b for b in plan.batches if b.table == "holding")
        tx_batch = next(b for b in plan.batches if b.table == "transaction")
        rq_batch = next(b for b in plan.batches if b.table == "review_queue")

        assert len(disc_batch.rows) == 1
        assert len(hold_batch.rows) == 2
        assert len(tx_batch.rows) == 2
        assert len(rq_batch.rows) >= 1  # at minimum the amendment review item
        assert len(plan.sidecars) == 1

    def test_disclosure_batch_precedes_holding_batch(self) -> None:
        plan = plan_disclosure_load([_result(holdings=[_holding()])])
        tables = [b.table for b in plan.batches]
        assert tables.index("financial_disclosure") < tables.index("holding")

    def test_disclosure_batch_precedes_transaction_batch(self) -> None:
        plan = plan_disclosure_load([_result(transactions=[_transaction()])])
        tables = [b.table for b in plan.batches]
        assert tables.index("financial_disclosure") < tables.index("transaction")

    def test_clean_filing_produces_empty_review_queue(self) -> None:
        holdings = [_holding(line_number=i) for i in range(1, 4)]
        txs = [_transaction(line_number=i) for i in range(1, 3)]
        plan = plan_disclosure_load([_result(holdings=holdings, transactions=txs)])
        rq_batch = next(b for b in plan.batches if b.table == "review_queue")
        assert rq_batch.rows == ()
