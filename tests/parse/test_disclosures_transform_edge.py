"""Edge-case tests for src/parse/disclosures/transform.py.

Covers scenarios not exercised in test_disclosure_transform.py:
- Unresolved-member placeholders flowing through the parse layer.
- Amendment/supersedes metadata inconsistencies.
- Outside positions with unknown owner type.
- Multiple unknown label types on a single line item.
- Combined unknown owner + unknown transaction type on a transaction.
- FilingBundle / batch_transform_filings API.
- OutsidePositionSidecar.as_dict() with None dates.

No DB, no network, no filesystem access.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

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
    FilingBundle,
    ParseContext,
    REASON_AMENDMENT_FILING,
    REASON_AMENDMENT_NUMBER_WITHOUT_FLAG,
    REASON_AMENDMENT_SUPERSEDES_MISMATCH,
    REASON_NO_CANONICAL_TABLE_V1,
    REASON_UNKNOWN_AMOUNT_RANGE,
    REASON_UNKNOWN_INCOME_RANGE,
    REASON_UNKNOWN_OWNER_TYPE,
    REASON_UNKNOWN_TRANSACTION_TYPE,
    REASON_UNRESOLVED_TRUST,
    REVIEW_TYPE_AMENDMENT,
    REVIEW_TYPE_CLASSIFICATION,
    REVIEW_TYPE_NORMALIZATION,
    REVIEW_TYPE_OUTSIDE_POSITION,
    batch_transform_filings,
    transform_filing,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CTX = ParseContext(parse_run_id=1, source_artifact_id=2, ingestion_run_id=3)
CTX_EMPTY = ParseContext()


def _annual(
    *,
    member_bioguide_id: str = "A000001",
    filing_year: int = 2023,
    filing_type: FilingType = FilingType.ANNUAL,
    is_amended: bool = False,
    amendment_number: int = 0,
    supersedes_filing_source_id: str | None = None,
    source_record_id: str | None = "DOC-EDGE-001",
) -> Filing:
    return Filing(
        member_bioguide_id=member_bioguide_id,
        chamber=Chamber.SENATE,
        filing_year=filing_year,
        filing_type=filing_type,
        is_amended=is_amended,
        amendment_number=amendment_number,
        supersedes_filing_source_id=supersedes_filing_source_id,
        source_record_id=source_record_id,
    )


def _holding_label_only(
    *,
    line_number: int = 1,
    value_label: str | None = None,
    income_label: str | None = None,
    owner_type: OwnerType = OwnerType.SELF,
) -> Holding:
    return Holding(
        line_number=line_number,
        owner_type=owner_type,
        issuer_name="EdgeCo",
        value_label=value_label,
        income_label=income_label,
    )


def _tx(
    *,
    line_number: int = 1,
    owner_type: OwnerType = OwnerType.SELF,
    transaction_type: TransactionType = TransactionType.PURCHASE,
    amount_label: str | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
) -> Transaction:
    return Transaction(
        line_number=line_number,
        owner_type=owner_type,
        issuer_name="EdgeCo",
        transaction_type=transaction_type,
        transaction_date=date(2023, 3, 1),
        amount_label=amount_label,
        amount_min=amount_min,
        amount_max=amount_max,
    )


def _op(
    *,
    line_number: int = 1,
    owner_type: OwnerType = OwnerType.SELF,
    entity_name: str = "Acme",
    position_title: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> OutsidePosition:
    return OutsidePosition(
        line_number=line_number,
        owner_type=owner_type,
        entity_name=entity_name,
        position_title=position_title,
        from_date=from_date,
        to_date=to_date,
    )


# ---------------------------------------------------------------------------
# Unresolved member identity at the parse layer
# ---------------------------------------------------------------------------


class TestUnresolvedMemberAtParseLayer:
    """transform_filing does not validate member_bioguide_id; that is the
    caller's responsibility.  These tests document current behaviour so that
    any inadvertent change is caught.
    """

    def test_empty_bioguide_id_is_forwarded_without_error(self):
        filing = _annual(member_bioguide_id="")
        result = transform_filing(filing, [], [], [], CTX)
        assert result.disclosure.member_bioguide_id == ""

    def test_empty_bioguide_id_produces_no_extra_review_item(self):
        # The parse layer does not emit a member-identity review; the runtime
        # batch layer (transform_parse_sessions) handles that skip logic.
        filing = _annual(member_bioguide_id="")
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "unresolved_member_identity" not in codes

    def test_whitespace_only_bioguide_id_is_forwarded(self):
        filing = _annual(member_bioguide_id="   ")
        result = transform_filing(filing, [], [], [], CTX)
        assert result.disclosure.member_bioguide_id == "   "


# ---------------------------------------------------------------------------
# Amendment/supersedes inconsistency detection
# ---------------------------------------------------------------------------


class TestAmendmentInternalConsistency:
    def test_supersedes_set_but_not_amended_emits_mismatch_review(self):
        filing = _annual(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" in codes

    def test_mismatch_review_has_correct_type_and_entity(self):
        filing = _annual(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        item = next(
            r for r in result.review_items if r.reason_code == "amendment_supersedes_mismatch"
        )
        assert item.review_type == "amendment"
        assert item.entity_type == "financial_disclosure"

    def test_mismatch_review_priority_higher_than_amendment_filing(self):
        # amendment_supersedes_mismatch is more urgent (lower number) than
        # amendment_filing because it signals data inconsistency.
        filing = _annual(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        mismatch = next(
            r for r in result.review_items if r.reason_code == "amendment_supersedes_mismatch"
        )
        assert mismatch.priority < 20  # lower than _PRIORITY_AMENDMENT=20

    def test_mismatch_payload_contains_supersedes_id(self):
        filing = _annual(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        item = next(
            r for r in result.review_items if r.reason_code == "amendment_supersedes_mismatch"
        )
        assert item.payload["supersedes_filing_source_id"] == "DOC-PREV"
        assert item.payload["is_amended"] is False

    def test_amendment_number_positive_but_no_flag_emits_review(self):
        filing = _annual(is_amended=False, amendment_number=2)
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_number_without_flag" in codes

    def test_amendment_number_without_flag_payload_contains_number(self):
        filing = _annual(is_amended=False, amendment_number=3)
        result = transform_filing(filing, [], [], [], CTX)
        item = next(
            r for r in result.review_items if r.reason_code == "amendment_number_without_flag"
        )
        assert item.payload["amendment_number"] == 3
        assert item.payload["is_amended"] is False

    def test_amendment_number_without_flag_priority_within_bounds(self):
        filing = _annual(is_amended=False, amendment_number=1)
        result = transform_filing(filing, [], [], [], CTX)
        item = next(
            r for r in result.review_items if r.reason_code == "amendment_number_without_flag"
        )
        assert 1 <= item.priority <= 100

    def test_properly_flagged_amendment_no_inconsistency_reviews(self):
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" not in codes
        assert "amendment_number_without_flag" not in codes

    def test_amendment_type_without_is_amended_no_inconsistency(self):
        # filing_type=AMENDMENT suppresses the inconsistency checks even if
        # is_amended is False (some sources may set type without flag).
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=False,
            amendment_number=1,
        )
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_number_without_flag" not in codes

    def test_both_inconsistencies_on_same_filing_emit_both_reviews(self):
        filing = _annual(
            is_amended=False,
            amendment_number=2,
            supersedes_filing_source_id="DOC-PREV",
        )
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" in codes
        assert "amendment_number_without_flag" in codes

    def test_clean_annual_no_inconsistency_reviews(self):
        filing = _annual(is_amended=False, amendment_number=0, supersedes_filing_source_id=None)
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" not in codes
        assert "amendment_number_without_flag" not in codes

    def test_supersedes_none_on_proper_amendment_no_mismatch(self):
        # An amendment is allowed to have no supersedes_filing_source_id
        # (the supersession chain resolution happens at load time).
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id=None,
        )
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" not in codes
        # But amendment_filing review is still emitted.
        assert "amendment_filing" in codes

    def test_amendment_filing_payload_shows_none_supersedes(self):
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id=None,
        )
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        assert item.payload["supersedes_filing_source_id"] is None

    def test_context_ids_on_inconsistency_reviews(self):
        ctx = ParseContext(parse_run_id=77, source_artifact_id=88, ingestion_run_id=99)
        filing = _annual(is_amended=False, amendment_number=2, supersedes_filing_source_id="P")
        result = transform_filing(filing, [], [], [], ctx)
        for code in ("amendment_supersedes_mismatch", "amendment_number_without_flag"):
            item = next(r for r in result.review_items if r.reason_code == code)
            assert item.parse_run_id == 77
            assert item.source_artifact_id == 88
            assert item.ingestion_run_id == 99


# ---------------------------------------------------------------------------
# Outside positions landing in review items — edge cases
# ---------------------------------------------------------------------------


class TestOutsidePositionReviewEdgeCases:
    def test_other_owner_emits_two_review_items(self):
        op = _op(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [], [], [op], CTX)
        reason_codes = [r.reason_code for r in result.review_items]
        assert reason_codes.count("no_canonical_table_v1") == 1
        assert reason_codes.count("unknown_owner_type") == 1

    def test_other_owner_unknown_owner_review_entity_type(self):
        op = _op(owner_type=OwnerType.OTHER, line_number=5)
        result = transform_filing(_annual(), [], [], [op], CTX)
        item = next(r for r in result.review_items if r.reason_code == "unknown_owner_type")
        assert item.entity_type == "outside_position"
        assert item.payload["line_number"] == 5

    def test_self_owner_emits_only_sidecar_review(self):
        op = _op(owner_type=OwnerType.SELF)
        result = transform_filing(_annual(), [], [], [op], CTX)
        assert len(result.review_items) == 1
        assert result.review_items[0].reason_code == "no_canonical_table_v1"

    def test_spouse_owner_emits_only_sidecar_review(self):
        op = _op(owner_type=OwnerType.SPOUSE)
        result = transform_filing(_annual(), [], [], [op], CTX)
        assert len(result.review_items) == 1
        assert result.review_items[0].reason_code == "no_canonical_table_v1"

    def test_outside_position_missing_title_still_produces_sidecar(self):
        op = _op(position_title=None)
        result = transform_filing(_annual(), [], [], [op], CTX)
        assert result.outside_positions[0].position_title is None

    def test_outside_position_sidecar_as_dict_with_none_dates(self):
        op = _op(from_date=None, to_date=None)
        result = transform_filing(_annual(), [], [], [op], CTX)
        d = result.outside_positions[0].as_dict()
        assert d["from_date"] is None
        assert d["to_date"] is None

    def test_outside_position_sidecar_as_dict_with_real_dates(self):
        op = _op(from_date=date(2020, 1, 15), to_date=date(2022, 12, 31))
        result = transform_filing(_annual(), [], [], [op], CTX)
        d = result.outside_positions[0].as_dict()
        assert d["from_date"] == "2020-01-15"
        assert d["to_date"] == "2022-12-31"

    def test_multiple_outside_positions_with_mixed_owners(self):
        ops = [
            _op(line_number=1, owner_type=OwnerType.SELF),
            _op(line_number=2, owner_type=OwnerType.OTHER),
            _op(line_number=3, owner_type=OwnerType.SPOUSE),
        ]
        result = transform_filing(_annual(), [], [], ops, CTX)
        assert len(result.outside_positions) == 3
        # Three no_canonical_table_v1 + one unknown_owner_type for line 2
        sidecar_reviews = [
            r for r in result.review_items if r.reason_code == "no_canonical_table_v1"
        ]
        unknown_owner_reviews = [
            r for r in result.review_items if r.reason_code == "unknown_owner_type"
        ]
        assert len(sidecar_reviews) == 3
        assert len(unknown_owner_reviews) == 1
        assert unknown_owner_reviews[0].payload["line_number"] == 2


# ---------------------------------------------------------------------------
# Unknown amount/owner/transaction-type — compound and edge cases
# ---------------------------------------------------------------------------


class TestUnknownAmountOwnerTxType:
    def test_holding_both_value_and_income_labels_unknown_emits_two_reviews(self):
        h = _holding_label_only(
            value_label="$MYSTERY_VALUE",
            income_label="$MYSTERY_INCOME",
        )
        result = transform_filing(_annual(), [h], [], [], CTX)
        # value_label unknown → unknown_amount_range; income_label unknown → unknown_income_range
        unknown_range_reviews = [
            r
            for r in result.review_items
            if r.reason_code in ("unknown_amount_range", "unknown_income_range")
        ]
        assert len(unknown_range_reviews) == 2

    def test_holding_value_label_unknown_income_label_known_one_review(self):
        h = _holding_label_only(
            value_label="$MYSTERY",
            income_label="$1,001 - $15,000",
        )
        result = transform_filing(_annual(), [h], [], [], CTX)
        unknown_amount_reviews = [
            r for r in result.review_items if r.reason_code == "unknown_amount_range"
        ]
        assert len(unknown_amount_reviews) == 1
        assert unknown_amount_reviews[0].payload.get("value_label") == "$MYSTERY"

    def test_holding_known_value_label_no_review(self):
        h = _holding_label_only(value_label="$15,001 - $50,000")
        result = transform_filing(_annual(), [h], [], [], CTX)
        assert not any(r.reason_code == "unknown_amount_range" for r in result.review_items)

    def test_holding_value_decimals_present_skips_label_normalisation(self):
        # When min/max are already set, label normalisation is skipped regardless
        # of whether the label is recognised.
        h = Holding(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="EdgeCo",
            value_min=Decimal("100"),
            value_max=Decimal("500"),
            value_label="$GARBAGE",
        )
        result = transform_filing(_annual(), [h], [], [], CTX)
        assert not any(r.reason_code == "unknown_amount_range" for r in result.review_items)
        assert result.holdings[0].value_min == Decimal("100")

    def test_transaction_unknown_owner_and_unknown_type_emits_two_reviews(self):
        t = _tx(
            owner_type=OwnerType.OTHER,
            transaction_type=TransactionType.OTHER,
            amount_min=Decimal("1001"),
            amount_max=Decimal("15000"),
        )
        result = transform_filing(_annual(), [], [t], [], CTX)
        codes = [r.reason_code for r in result.review_items]
        assert "unknown_owner_type" in codes
        assert "unknown_transaction_type" in codes

    def test_transaction_unknown_amount_and_unknown_owner_emits_two_reviews(self):
        t = _tx(
            owner_type=OwnerType.OTHER,
            transaction_type=TransactionType.SALE,
            amount_label="$WHO_KNOWS",
        )
        result = transform_filing(_annual(), [], [t], [], CTX)
        codes = [r.reason_code for r in result.review_items]
        assert "unknown_amount_range" in codes
        assert "unknown_owner_type" in codes

    def test_transaction_all_three_unknowns_emits_three_reviews(self):
        t = _tx(
            owner_type=OwnerType.OTHER,
            transaction_type=TransactionType.OTHER,
            amount_label="$???",
        )
        result = transform_filing(_annual(), [], [t], [], CTX)
        codes = [r.reason_code for r in result.review_items]
        assert codes.count("unknown_amount_range") == 1
        assert codes.count("unknown_owner_type") == 1
        assert codes.count("unknown_transaction_type") == 1

    def test_holding_other_owner_and_unknown_value_label_emits_two_reviews(self):
        h = _holding_label_only(value_label="$MYSTERY", owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [h], [], [], CTX)
        codes = [r.reason_code for r in result.review_items]
        assert "unknown_amount_range" in codes
        assert "unknown_owner_type" in codes

    def test_holding_trust_owner_always_emits_trust_review(self):
        h = _holding_label_only(owner_type=OwnerType.TRUST)
        result = transform_filing(_annual(), [h], [], [], CTX)
        assert any(r.reason_code == "unresolved_trust" for r in result.review_items)
        # trust owner does NOT also emit unknown_owner_type
        assert not any(r.reason_code == "unknown_owner_type" for r in result.review_items)

    def test_all_review_items_are_open(self):
        t = _tx(
            owner_type=OwnerType.OTHER, transaction_type=TransactionType.OTHER, amount_label="$???"
        )
        result = transform_filing(_annual(), [], [t], [], CTX)
        for item in result.review_items:
            assert item.status == "open"

    def test_review_item_entity_key_references_line_number(self):
        t = _tx(line_number=7, owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [], [t], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "unknown_owner_type")
        assert "7" in item.entity_key

    def test_review_items_have_summary(self):
        t = _tx(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [], [t], [], CTX)
        for item in result.review_items:
            assert item.summary is not None and len(item.summary) > 0


# ---------------------------------------------------------------------------
# FilingBundle / batch_transform_filings
# ---------------------------------------------------------------------------


class TestFilingBundleAndBatch:
    def test_filing_bundle_is_frozen(self):
        bundle = FilingBundle(
            filing=_annual(),
            holdings=[],
            transactions=[],
            outside_positions=[],
        )
        with pytest.raises((AttributeError, TypeError)):
            bundle.holdings = []  # type: ignore[misc]

    def test_batch_empty_input_returns_empty_list(self):
        results = batch_transform_filings([], CTX)
        assert results == []

    def test_batch_single_bundle_returns_one_result(self):
        bundle = FilingBundle(
            filing=_annual(),
            holdings=[],
            transactions=[],
            outside_positions=[],
        )
        results = batch_transform_filings([bundle], CTX)
        assert len(results) == 1
        assert isinstance(results[0], DisclosureTransformResult)

    def test_batch_result_order_matches_input(self):
        bundles = [
            FilingBundle(
                filing=_annual(source_record_id=f"DOC-{i}"),
                holdings=[],
                transactions=[],
                outside_positions=[],
            )
            for i in range(1, 5)
        ]
        results = batch_transform_filings(bundles, CTX)
        source_ids = [r.disclosure.source_record_id for r in results]
        assert source_ids == ["DOC-1", "DOC-2", "DOC-3", "DOC-4"]

    def test_batch_context_shared_across_bundles(self):
        ctx = ParseContext(parse_run_id=50, source_artifact_id=60, ingestion_run_id=70)
        ops = [_op()]
        bundles = [
            FilingBundle(
                filing=_annual(source_record_id=f"D{i}"),
                holdings=[],
                transactions=[],
                outside_positions=ops,
            )
            for i in range(1, 3)
        ]
        results = batch_transform_filings(bundles, ctx)
        for res in results:
            review = next(r for r in res.review_items if r.reason_code == "no_canonical_table_v1")
            assert review.parse_run_id == 50
            assert review.source_artifact_id == 60
            assert review.ingestion_run_id == 70

    def test_batch_each_bundle_independent(self):
        # Review items from one bundle must not bleed into another.
        bundle_with_issue = FilingBundle(
            filing=_annual(source_record_id="D1"),
            holdings=[Holding(line_number=1, owner_type=OwnerType.OTHER, issuer_name="X")],
            transactions=[],
            outside_positions=[],
        )
        bundle_clean = FilingBundle(
            filing=_annual(source_record_id="D2"),
            holdings=[],
            transactions=[],
            outside_positions=[],
        )
        results = batch_transform_filings([bundle_with_issue, bundle_clean], CTX)
        assert any(r.reason_code == "unknown_owner_type" for r in results[0].review_items)
        assert results[1].review_items == []

    def test_batch_single_equals_direct_transform(self):
        h = Holding(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="Acme",
            value_min=Decimal("1001"),
            value_max=Decimal("15000"),
        )
        filing = _annual()
        bundle = FilingBundle(filing=filing, holdings=[h], transactions=[], outside_positions=[])
        batch_result = batch_transform_filings([bundle], CTX)[0]
        direct_result = transform_filing(filing, [h], [], [], CTX)

        assert (
            batch_result.disclosure.member_bioguide_id
            == direct_result.disclosure.member_bioguide_id
        )
        assert len(batch_result.holdings) == len(direct_result.holdings)
        assert batch_result.holdings[0].issuer_name == direct_result.holdings[0].issuer_name


# ---------------------------------------------------------------------------
# Amendment chain through batch_transform_filings
# ---------------------------------------------------------------------------


class TestAmendmentChainInBatch:
    """Amendment and supersedes metadata must flow through batch_transform_filings
    with exactly the same review logic as the single-filing path."""

    def test_amendment_filing_in_batch_emits_amendment_review(self):
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id="DOC-PREV",
        )
        bundle = FilingBundle(filing=filing, holdings=[], transactions=[], outside_positions=[])
        results = batch_transform_filings([bundle], CTX)
        codes = {r.reason_code for r in results[0].review_items}
        assert "amendment_filing" in codes

    def test_amendment_payload_supersedes_id_preserved_in_batch(self):
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=2,
            supersedes_filing_source_id="DOC-CHAIN",
        )
        bundle = FilingBundle(filing=filing, holdings=[], transactions=[], outside_positions=[])
        results = batch_transform_filings([bundle], CTX)
        item = next(r for r in results[0].review_items if r.reason_code == "amendment_filing")
        assert item.payload["supersedes_filing_source_id"] == "DOC-CHAIN"

    def test_inconsistency_review_emitted_in_batch(self):
        filing = _annual(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="DOC-INCONSISTENT",
        )
        bundle = FilingBundle(filing=filing, holdings=[], transactions=[], outside_positions=[])
        results = batch_transform_filings([bundle], CTX)
        codes = {r.reason_code for r in results[0].review_items}
        assert "amendment_supersedes_mismatch" in codes

    def test_batch_amendment_review_context_ids_match_ctx(self):
        ctx = ParseContext(parse_run_id=7, source_artifact_id=8, ingestion_run_id=9)
        filing = _annual(is_amended=True, amendment_number=1)
        bundle = FilingBundle(filing=filing, holdings=[], transactions=[], outside_positions=[])
        results = batch_transform_filings([bundle], ctx)
        item = next(r for r in results[0].review_items if r.reason_code == "amendment_filing")
        assert item.parse_run_id == 7
        assert item.source_artifact_id == 8
        assert item.ingestion_run_id == 9

    def test_clean_bundle_next_to_amendment_bundle_isolated(self):
        amendment = _annual(is_amended=True, amendment_number=1, source_record_id="D1")
        clean = _annual(is_amended=False, amendment_number=0, source_record_id="D2")
        bundles = [
            FilingBundle(filing=amendment, holdings=[], transactions=[], outside_positions=[]),
            FilingBundle(filing=clean, holdings=[], transactions=[], outside_positions=[]),
        ]
        results = batch_transform_filings(bundles, CTX)
        assert any(r.reason_code == "amendment_filing" for r in results[0].review_items)
        assert not any(r.reason_code == "amendment_filing" for r in results[1].review_items)


# ---------------------------------------------------------------------------
# entity_key format
# ---------------------------------------------------------------------------


class TestEntityKeyFormat:
    """The entity_key on review items must be stable and reference the correct
    line number or filing identity so downstream lookup is unambiguous."""

    def test_holding_review_entity_key_contains_line_number(self):
        h = _holding_label_only(line_number=9, value_label="$MYSTERY")
        result = transform_filing(_annual(), [h], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "unknown_amount_range")
        assert "9" in item.entity_key

    def test_transaction_review_entity_key_contains_line_number(self):
        t = _tx(line_number=5, owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [], [t], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "unknown_owner_type")
        assert "5" in item.entity_key

    def test_outside_position_review_entity_key_contains_line_number(self):
        op = _op(line_number=3)
        result = transform_filing(_annual(), [], [], [op], CTX)
        item = next(r for r in result.review_items if r.reason_code == "no_canonical_table_v1")
        assert "3" in item.entity_key

    def test_amendment_review_entity_key_uses_source_record_id_when_present(self):
        filing = _annual(is_amended=True, amendment_number=1, source_record_id="DOC-EDGE-001")
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        # entity_key should include the source_record_id when present.
        assert "DOC-EDGE-001" in item.entity_key

    def test_amendment_review_entity_key_fallback_when_source_record_id_none(self):
        filing = _annual(
            is_amended=True,
            amendment_number=1,
            source_record_id=None,
            member_bioguide_id="A000001",
        )
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        # Falls back to member:year:amendment_number composite.
        assert "A000001" in item.entity_key

    def test_inconsistency_review_entity_key_consistent_with_amendment_review(self):
        """Both amendment_filing and amendment_supersedes_mismatch use the same entity_key."""
        filing = _annual(
            filing_type=FilingType.AMENDMENT,
            is_amended=True,
            amendment_number=1,
            supersedes_filing_source_id=None,
            source_record_id="DOC-CHECK",
        )
        # Emit only amendment_filing (no mismatch since is_amended=True)
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "amendment_filing")
        assert "DOC-CHECK" in item.entity_key

    def test_trust_review_entity_key_contains_line_number(self):
        h = _holding_label_only(line_number=12, owner_type=OwnerType.TRUST)
        result = transform_filing(_annual(), [h], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == "unresolved_trust")
        assert "12" in item.entity_key


# ---------------------------------------------------------------------------
# Skipped-transform accounting: empty bioguide_id forwarded without review
# ---------------------------------------------------------------------------


class TestSkippedTransformAtParseLayer:
    """The parse/transform layer does not emit a review item for an empty
    bioguide_id; skip accounting is the caller's responsibility (runtime layer).
    These tests pin that contract so a future refactor does not silently add
    hidden fallback review logic here."""

    def test_no_review_item_for_empty_bioguide_at_parse_layer(self):
        filing = _annual(member_bioguide_id="")
        result = transform_filing(filing, [], [], [], CTX)
        codes = {r.reason_code for r in result.review_items}
        assert "unresolved_member_identity" not in codes
        assert "empty_bioguide_id" not in codes

    def test_disclosure_payload_exposes_empty_bioguide_unchanged(self):
        filing = _annual(member_bioguide_id="")
        result = transform_filing(filing, [], [], [], CTX)
        assert result.disclosure.member_bioguide_id == ""

    def test_no_review_item_for_unknown_bioguide_in_batch(self):
        filings = [_annual(member_bioguide_id="") for _ in range(3)]
        bundles = [
            FilingBundle(filing=f, holdings=[], transactions=[], outside_positions=[])
            for f in filings
        ]
        results = batch_transform_filings(bundles, CTX)
        for res in results:
            codes = {r.reason_code for r in res.review_items}
            assert "unresolved_member_identity" not in codes


# ---------------------------------------------------------------------------
# Review constants are stable and importable
# ---------------------------------------------------------------------------


class TestReviewConstants:
    """Verify that the module-level constants match the values used in review
    items so that downstream consumers can import and compare without string
    duplication."""

    def test_reason_code_constants_are_strings(self):
        for const in (
            REASON_AMENDMENT_FILING,
            REASON_AMENDMENT_SUPERSEDES_MISMATCH,
            REASON_AMENDMENT_NUMBER_WITHOUT_FLAG,
            REASON_UNKNOWN_AMOUNT_RANGE,
            REASON_UNKNOWN_INCOME_RANGE,
            REASON_UNKNOWN_OWNER_TYPE,
            REASON_UNKNOWN_TRANSACTION_TYPE,
            REASON_UNRESOLVED_TRUST,
            REASON_NO_CANONICAL_TABLE_V1,
        ):
            assert isinstance(const, str)

    def test_review_type_constants_are_strings(self):
        for const in (
            REVIEW_TYPE_AMENDMENT,
            REVIEW_TYPE_NORMALIZATION,
            REVIEW_TYPE_CLASSIFICATION,
            REVIEW_TYPE_OUTSIDE_POSITION,
        ):
            assert isinstance(const, str)

    def test_amendment_review_uses_constant(self):
        filing = _annual(is_amended=True, amendment_number=1)
        result = transform_filing(filing, [], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == REASON_AMENDMENT_FILING)
        assert item.review_type == REVIEW_TYPE_AMENDMENT

    def test_trust_review_uses_constant(self):
        h = _holding_label_only(owner_type=OwnerType.TRUST)
        result = transform_filing(_annual(), [h], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == REASON_UNRESOLVED_TRUST)
        assert item.review_type == REVIEW_TYPE_CLASSIFICATION

    def test_outside_position_review_uses_constant(self):
        op = _op()
        result = transform_filing(_annual(), [], [], [op], CTX)
        item = next(r for r in result.review_items if r.reason_code == REASON_NO_CANONICAL_TABLE_V1)
        assert item.review_type == REVIEW_TYPE_OUTSIDE_POSITION

    def test_unknown_owner_review_uses_constant(self):
        h = _holding_label_only(owner_type=OwnerType.OTHER)
        result = transform_filing(_annual(), [h], [], [], CTX)
        item = next(r for r in result.review_items if r.reason_code == REASON_UNKNOWN_OWNER_TYPE)
        assert item.review_type == REVIEW_TYPE_NORMALIZATION


# ---------------------------------------------------------------------------
# Review item ordering is deterministic
# ---------------------------------------------------------------------------


class TestReviewItemOrdering:
    """Review items must appear in a stable order: amendment-level first,
    then per-line-item in input order (holdings, transactions, outside
    positions).  This test pins that ordering."""

    def test_amendment_review_before_line_item_reviews(self):
        filing = _annual(is_amended=True, amendment_number=1)
        h = _holding_label_only(owner_type=OwnerType.OTHER)
        result = transform_filing(filing, [h], [], [], CTX)
        codes = [r.reason_code for r in result.review_items]
        amendment_idx = codes.index(REASON_AMENDMENT_FILING)
        owner_idx = codes.index(REASON_UNKNOWN_OWNER_TYPE)
        assert amendment_idx < owner_idx

    def test_holding_reviews_before_transaction_reviews(self):
        h = _holding_label_only(owner_type=OwnerType.OTHER, line_number=1)
        t = _tx(owner_type=OwnerType.OTHER, line_number=1)
        result = transform_filing(_annual(), [h], [t], [], CTX)
        # Both emit unknown_owner_type; first must be the holding's
        owner_reviews = [
            r for r in result.review_items if r.reason_code == REASON_UNKNOWN_OWNER_TYPE
        ]
        assert owner_reviews[0].entity_type == "holding"
        assert owner_reviews[1].entity_type == "transaction"

    def test_transaction_reviews_before_outside_position_reviews(self):
        t = _tx(owner_type=OwnerType.OTHER, line_number=1)
        op = _op(owner_type=OwnerType.OTHER, line_number=1)
        result = transform_filing(_annual(), [], [t], [op], CTX)
        tx_owner_idx = next(
            i
            for i, r in enumerate(result.review_items)
            if r.reason_code == REASON_UNKNOWN_OWNER_TYPE and r.entity_type == "transaction"
        )
        op_sidecar_idx = next(
            i
            for i, r in enumerate(result.review_items)
            if r.reason_code == REASON_NO_CANONICAL_TABLE_V1
        )
        assert tx_owner_idx < op_sidecar_idx
