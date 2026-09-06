"""Tests for the disclosure transform layer.

All tests are pure (no DB, no network). They verify that:
- Filing / Holding / Transaction / OutsidePosition models produce correct
  canonical row payloads.
- Review-queue payloads are emitted under the right conditions.
- Amendment metadata is handled explicitly.
- Outside positions are emitted as sidecar payloads, not canonical rows.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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
    DisclosureTransformResult,
    FinancialDisclosurePayload,
    HoldingPayload,
    OutsidePositionSidecar,
    ParseContext,
    TransactionPayload,
    transform_filing,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CTX = ParseContext(parse_run_id=1, source_artifact_id=2, ingestion_run_id=3)
CTX_EMPTY = ParseContext()  # no provenance IDs


def _annual_filing(**kwargs) -> Filing:
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
        amount_label="$1,001 - $15,000",
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


def _outside_position(**kwargs) -> OutsidePosition:
    defaults = dict(
        line_number=1,
        owner_type=OwnerType.SELF,
        entity_name="Acme Corp",
        position_title="Board Member",
        from_date=date(2020, 1, 1),
        to_date=date(2023, 12, 31),
        source_record_id="OP-001",
    )
    defaults.update(kwargs)
    return OutsidePosition(**defaults)


# ---------------------------------------------------------------------------
# FinancialDisclosurePayload
# ---------------------------------------------------------------------------


class TestDisclosurePayload:
    def test_basic_annual_filing(self) -> None:
        filing = _annual_filing()
        result = transform_filing(filing, [], [], [], CTX)
        d = result.disclosure

        assert isinstance(d, FinancialDisclosurePayload)
        assert d.member_bioguide_id == "A000001"
        assert d.chamber == "senate"
        assert d.filing_year == 2023
        assert d.filing_type == "annual"
        assert d.amendment_number == 0
        assert d.is_amended is False
        assert d.filed_at == date(2024, 5, 15)
        assert d.filing_period_start == date(2023, 1, 1)
        assert d.filing_period_end == date(2023, 12, 31)
        assert d.source_record_id == "FILING-001"
        assert d.source_artifact_id == 2
        assert d.source_artifact_sha256 == "a" * 64
        assert d.supersedes_filing_source_id is None

    def test_ptr_filing(self) -> None:
        filing = _annual_filing(
            filing_type=FilingType.PTR, filing_period_start=None, filing_period_end=None
        )
        result = transform_filing(filing, [], [], [], CTX)
        assert result.disclosure.filing_type == "ptr"

    def test_chamber_values(self) -> None:
        house_filing = _annual_filing(chamber=Chamber.HOUSE)
        result = transform_filing(house_filing, [], [], [], CTX)
        assert result.disclosure.chamber == "house"

    def test_no_review_for_clean_annual(self) -> None:
        filing = _annual_filing()
        result = transform_filing(filing, [], [], [], CTX)
        assert result.review_items == []


# ---------------------------------------------------------------------------
# Amendment handling
# ---------------------------------------------------------------------------


class TestAmendmentHandling:
    def test_is_amended_emits_review_item(self) -> None:
        filing = _annual_filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-001",
            source_record_id="FILING-002",
        )
        result = transform_filing(filing, [], [], [], CTX)
        amendments = [r for r in result.review_items if r.reason_code == "amendment_filing"]
        assert len(amendments) == 1
        item = amendments[0]
        assert item.review_type == "amendment"
        assert item.entity_type == "financial_disclosure"
        assert item.priority <= 25  # high urgency
        assert item.payload["amendment_number"] == 1
        assert item.payload["supersedes_filing_source_id"] == "FILING-001"

    def test_amendment_payload_contains_supersession_key(self) -> None:
        filing = _annual_filing(
            is_amended=True,
            amendment_number=2,
            supersedes_filing_source_id="FILING-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        assert item.payload["supersedes_filing_source_id"] == "FILING-PREV"

    def test_disclosure_payload_preserves_amendment_fields(self) -> None:
        filing = _annual_filing(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-001",
        )
        result = transform_filing(filing, [], [], [], CTX)
        d = result.disclosure
        assert d.is_amended is True
        assert d.amendment_number == 1
        assert d.supersedes_filing_source_id == "FILING-001"
        assert d.filing_type == "amendment"

    def test_non_amendment_no_review(self) -> None:
        filing = _annual_filing(is_amended=False, amendment_number=0)
        result = transform_filing(filing, [], [], [], CTX)
        assert not any(r.reason_code == "amendment_filing" for r in result.review_items)

    def test_context_ids_propagated_to_amendment_review(self) -> None:
        ctx = ParseContext(parse_run_id=99, source_artifact_id=88, ingestion_run_id=77)
        filing = _annual_filing(is_amended=True, amendment_number=1)
        result = transform_filing(filing, [], [], [], ctx)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        assert item.parse_run_id == 99
        assert item.source_artifact_id == 88
        assert item.ingestion_run_id == 77


# ---------------------------------------------------------------------------
# Holding payloads
# ---------------------------------------------------------------------------


class TestHoldingPayload:
    def test_clean_holding_no_review(self) -> None:
        h = _holding()
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        assert len(result.holdings) == 1

    def test_source_artifact_id_forwarded(self) -> None:
        result = transform_filing(_annual_filing(), [_holding()], [], [], CTX)
        assert result.holdings[0].source_artifact_id == 2
        assert result.review_items == []

    def test_holding_fields_mapped(self) -> None:
        h = _holding(
            line_number=3,
            owner_type=OwnerType.SPOUSE,
            issuer_name="Microsoft Corp",
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
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        hp = result.holdings[0]
        assert isinstance(hp, HoldingPayload)
        assert hp.line_number == 3
        assert hp.owner_type == "spouse"
        assert hp.issuer_name == "Microsoft Corp"
        assert hp.issuer_ticker == "MSFT"
        assert hp.value_min == Decimal("100001")
        assert hp.value_max == Decimal("250000")
        assert hp.income_min == Decimal("1001")
        assert hp.income_max == Decimal("15000")
        assert hp.is_liquid is True
        assert hp.source_record_id == "H-003"

    def test_holding_normalizes_value_label_when_decimals_absent(self) -> None:
        h = _holding(value_min=None, value_max=None, value_label="$1,001 - $15,000")
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        hp = result.holdings[0]
        assert hp.value_min == Decimal("1001")
        assert hp.value_max == Decimal("15000")
        assert result.review_items == []

    def test_holding_normalizes_income_label_when_decimals_absent(self) -> None:
        h = _holding(income_min=None, income_max=None, income_label="$15,001 - $50,000")
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        hp = result.holdings[0]
        assert hp.income_min == Decimal("15001")
        assert hp.income_max == Decimal("50000")
        assert not any(r.reason_code == "unknown_income_range" for r in result.review_items)

    def test_unrecognized_value_label_emits_review(self) -> None:
        h = _holding(value_min=None, value_max=None, value_label="something weird")
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_amount_range"]
        assert len(reviews) == 1
        assert reviews[0].entity_type == "holding"
        assert "something weird" in reviews[0].payload["value_label"]

    def test_unrecognized_income_label_emits_review(self) -> None:
        h = _holding(income_min=None, income_max=None, income_label="bogus range")
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_income_range"]
        assert len(reviews) == 1

    def test_other_owner_type_emits_review(self) -> None:
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_owner_type"]
        assert len(reviews) == 1
        assert reviews[0].entity_type == "holding"

    def test_trust_owner_emits_review(self) -> None:
        h = _holding(owner_type=OwnerType.TRUST)
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unresolved_trust"]
        assert len(reviews) == 1
        assert reviews[0].entity_type == "holding"

    def test_multiple_holdings_preserved_in_order(self) -> None:
        holdings = [
            _holding(line_number=1, issuer_name="Apple Inc"),
            _holding(line_number=2, issuer_name="Google LLC"),
            _holding(line_number=3, issuer_name="Tesla Inc"),
        ]
        result = transform_filing(_annual_filing(), holdings, [], [], CTX)
        assert [hp.line_number for hp in result.holdings] == [1, 2, 3]
        assert [hp.issuer_name for hp in result.holdings] == [
            "Apple Inc",
            "Google LLC",
            "Tesla Inc",
        ]


# ---------------------------------------------------------------------------
# Transaction payloads
# ---------------------------------------------------------------------------


class TestTransactionPayload:
    def test_clean_transaction_no_review(self) -> None:
        t = _transaction()
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        assert len(result.transactions) == 1
        assert result.review_items == []

    def test_transaction_fields_mapped(self) -> None:
        t = _transaction(
            line_number=5,
            owner_type=OwnerType.JOINT,
            issuer_name="Amazon Inc",
            issuer_ticker="AMZN",
            transaction_type=TransactionType.SALE,
            transaction_date=date(2023, 9, 15),
            amount_min=Decimal("50001"),
            amount_max=Decimal("100000"),
            amount_label="$50,001 - $100,000",
            asset_description="Common Stock",
            source_record_id="TX-005",
        )
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        tp = result.transactions[0]
        assert isinstance(tp, TransactionPayload)
        assert tp.line_number == 5
        assert tp.owner_type == "joint"
        assert tp.issuer_name == "Amazon Inc"
        assert tp.transaction_type == "sale"
        assert tp.transaction_date == date(2023, 9, 15)
        assert tp.amount_min == Decimal("50001")
        assert tp.amount_max == Decimal("100000")
        assert tp.source_record_id == "TX-005"

    def test_source_artifact_id_forwarded(self) -> None:
        result = transform_filing(_annual_filing(), [], [_transaction()], [], CTX)
        assert result.transactions[0].source_artifact_id == 2

    def test_transaction_normalizes_amount_label(self) -> None:
        t = _transaction(amount_min=None, amount_max=None, amount_label="$1,001 - $15,000")
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        tp = result.transactions[0]
        assert tp.amount_min == Decimal("1001")
        assert tp.amount_max == Decimal("15000")
        assert result.review_items == []

    def test_unrecognized_amount_label_emits_review(self) -> None:
        t = _transaction(amount_min=None, amount_max=None, amount_label="way too much")
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_amount_range"]
        assert len(reviews) == 1
        assert reviews[0].entity_type == "transaction"

    def test_other_transaction_type_emits_review(self) -> None:
        t = _transaction(transaction_type=TransactionType.OTHER)
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_transaction_type"]
        assert len(reviews) == 1

    def test_other_owner_type_on_transaction_emits_review(self) -> None:
        t = _transaction(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual_filing(), [], [t], [], CTX)
        reviews = [r for r in result.review_items if r.reason_code == "unknown_owner_type"]
        assert len(reviews) == 1

    def test_all_transaction_types_mapped(self) -> None:
        for tx_type in [
            TransactionType.PURCHASE,
            TransactionType.SALE,
            TransactionType.EXCHANGE,
            TransactionType.GIFT,
            TransactionType.INCOME,
        ]:
            t = _transaction(transaction_type=tx_type)
            result = transform_filing(_annual_filing(), [], [t], [], CTX)
            assert result.transactions[0].transaction_type == tx_type.value

    def test_multiple_transactions_in_order(self) -> None:
        txs = [_transaction(line_number=i) for i in range(1, 4)]
        result = transform_filing(_annual_filing(), [], txs, [], CTX)
        assert [tp.line_number for tp in result.transactions] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Outside position sidecars
# ---------------------------------------------------------------------------


class TestOutsidePositionSidecar:
    def test_outside_position_emits_sidecar_and_review(self) -> None:
        op = _outside_position()
        result = transform_filing(_annual_filing(), [], [], [op], CTX)
        assert len(result.outside_positions) == 1
        # Every outside position also generates a review item.
        reviews = [r for r in result.review_items if r.reason_code == "no_canonical_table_v1"]
        assert len(reviews) == 1

    def test_sidecar_fields_mapped(self) -> None:
        op = _outside_position(
            line_number=2,
            owner_type=OwnerType.SPOUSE,
            entity_name="Big Law Firm",
            position_title="Partner",
            from_date=date(2019, 6, 1),
            to_date=date(2022, 12, 31),
            source_record_id="OP-002",
        )
        result = transform_filing(_annual_filing(), [], [], [op], CTX)
        sc = result.outside_positions[0]
        assert isinstance(sc, OutsidePositionSidecar)
        assert sc.line_number == 2
        assert sc.owner_type == "spouse"
        assert sc.entity_name == "Big Law Firm"
        assert sc.position_title == "Partner"
        assert sc.from_date == date(2019, 6, 1)
        assert sc.to_date == date(2022, 12, 31)
        assert sc.source_record_id == "OP-002"

    def test_sidecar_as_dict(self) -> None:
        op = _outside_position(
            entity_name="Corp X",
            from_date=date(2021, 3, 1),
            to_date=date(2023, 3, 1),
        )
        result = transform_filing(_annual_filing(), [], [], [op], CTX)
        d = result.outside_positions[0].as_dict()
        assert d["entity_name"] == "Corp X"
        assert d["from_date"] == "2021-03-01"
        assert d["to_date"] == "2023-03-01"

    def test_review_item_payload_contains_sidecar_data(self) -> None:
        op = _outside_position(entity_name="SomeOrg")
        result = transform_filing(_annual_filing(), [], [], [op], CTX)
        review = next(r for r in result.review_items if r.reason_code == "no_canonical_table_v1")
        assert review.payload["entity_name"] == "SomeOrg"
        assert review.review_type == "outside_position"

    def test_multiple_outside_positions(self) -> None:
        ops = [_outside_position(line_number=i, entity_name=f"Org{i}") for i in range(1, 4)]
        result = transform_filing(_annual_filing(), [], [], ops, CTX)
        assert len(result.outside_positions) == 3
        assert (
            len([r for r in result.review_items if r.reason_code == "no_canonical_table_v1"]) == 3
        )


# ---------------------------------------------------------------------------
# Review queue metadata
# ---------------------------------------------------------------------------


class TestReviewQueueMetadata:
    def test_context_ids_propagated(self) -> None:
        ctx = ParseContext(parse_run_id=10, source_artifact_id=20, ingestion_run_id=30)
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual_filing(), [h], [], [], ctx)
        review = next(r for r in result.review_items if r.reason_code == "unknown_owner_type")
        assert review.parse_run_id == 10
        assert review.source_artifact_id == 20
        assert review.ingestion_run_id == 30

    def test_empty_context_ids_are_none(self) -> None:
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual_filing(), [h], [], [], CTX_EMPTY)
        review = next(r for r in result.review_items if r.reason_code == "unknown_owner_type")
        assert review.parse_run_id is None
        assert review.source_artifact_id is None
        assert review.ingestion_run_id is None

    def test_review_status_is_open(self) -> None:
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual_filing(), [h], [], [], CTX)
        for item in result.review_items:
            assert item.status == "open"

    def test_priority_within_schema_bounds(self) -> None:
        filing = _annual_filing(is_amended=True, amendment_number=1)
        h = _holding(owner_type=OwnerType.TRUST)
        t = _transaction(transaction_type=TransactionType.OTHER)
        op = _outside_position()
        result = transform_filing(filing, [h], [t], [op], CTX)
        for item in result.review_items:
            assert 1 <= item.priority <= 100, (
                f"priority {item.priority} out of [1,100] for {item.reason_code}"
            )

    def test_amendment_review_higher_priority_than_normalization(self) -> None:
        filing = _annual_filing(is_amended=True, amendment_number=1)
        h = _holding(value_min=None, value_max=None, value_label="bogus")
        result = transform_filing(filing, [h], [], [], CTX)
        amendment_item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        normalization_item = next(
            r for r in result.review_items if r.reason_code == "unknown_amount_range"
        )
        assert amendment_item.priority < normalization_item.priority


# ---------------------------------------------------------------------------
# Mixed scenario: full filing with multiple item types
# ---------------------------------------------------------------------------


class TestFullFilingTransform:
    def test_all_item_types_together(self) -> None:
        filing = _annual_filing(
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="FILING-001",
            source_record_id="FILING-002",
        )
        holdings = [
            _holding(line_number=1, issuer_name="Apple Inc"),
            _holding(line_number=2, owner_type=OwnerType.TRUST, issuer_name="Trust XYZ"),
        ]
        transactions = [
            _transaction(line_number=1),
            _transaction(line_number=2, transaction_type=TransactionType.OTHER),
        ]
        outside_positions = [_outside_position(line_number=1)]
        result = transform_filing(filing, holdings, transactions, outside_positions, CTX)

        assert isinstance(result, DisclosureTransformResult)
        assert len(result.holdings) == 2
        assert len(result.transactions) == 2
        assert len(result.outside_positions) == 1
        # Expect: amendment + trust + unknown_tx_type + outside_position
        reason_codes = {r.reason_code for r in result.review_items}
        assert "amendment_filing" in reason_codes
        assert "unresolved_trust" in reason_codes
        assert "unknown_transaction_type" in reason_codes
        assert "no_canonical_table_v1" in reason_codes

    def test_clean_filing_produces_no_review_items(self) -> None:
        filing = _annual_filing()
        holdings = [_holding(line_number=i) for i in range(1, 4)]
        transactions = [_transaction(line_number=i) for i in range(1, 3)]
        result = transform_filing(filing, holdings, transactions, [], CTX)
        assert result.review_items == []
        assert len(result.holdings) == 3
        assert len(result.transactions) == 2

    def test_empty_filing_produces_empty_result(self) -> None:
        result = transform_filing(_annual_filing(), [], [], [], CTX)
        assert result.holdings == []
        assert result.transactions == []
        assert result.outside_positions == []
        assert result.review_items == []
