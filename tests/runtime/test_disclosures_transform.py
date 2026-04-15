"""Tests for src/runtime/disclosures_transform.py.

No DB, no network, no filesystem access.  All inputs are real typed objects
from the parse layer; transform logic is exercised end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

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
from src.parse.disclosures.parse_result import ParseResult, ParserMeta
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    ParseContext,
)
from src.runtime.disclosures_transform import (
    BatchTransformResult,
    SKIP_NO_PARSE_RESULT,
    SKIP_NO_PARSED_DOCUMENT,
    SKIP_UNRESOLVED_MEMBER_IDENTITY,
    SkippedSession,
    build_parse_context,
    transform_parse_sessions,
    transform_parsed_disclosure,
    transform_single_session,
)


# ---------------------------------------------------------------------------
# Minimal builders
# ---------------------------------------------------------------------------


def _filing(
    *,
    member_bioguide_id: str = "A000001",
    chamber: Chamber = Chamber.SENATE,
    filing_year: int = 2023,
    filing_type: FilingType = FilingType.ANNUAL,
    is_amended: bool = False,
    amendment_number: int = 0,
    supersedes_filing_source_id: str | None = None,
    source_record_id: str | None = "DOC001",
) -> Filing:
    return Filing(
        member_bioguide_id=member_bioguide_id,
        chamber=chamber,
        filing_year=filing_year,
        filing_type=filing_type,
        is_amended=is_amended,
        amendment_number=amendment_number,
        supersedes_filing_source_id=supersedes_filing_source_id,
        source_record_id=source_record_id,
    )


def _holding(*, line_number: int = 1, owner_type: OwnerType = OwnerType.SELF) -> Holding:
    return Holding(
        line_number=line_number,
        owner_type=owner_type,
        issuer_name="Acme Corp",
        issuer_ticker="ACME",
        value_min=Decimal("15001"),
        value_max=Decimal("50000"),
    )


def _transaction(*, line_number: int = 1) -> Transaction:
    return Transaction(
        line_number=line_number,
        owner_type=OwnerType.SELF,
        issuer_name="Acme Corp",
        transaction_type=TransactionType.PURCHASE,
        transaction_date=date(2023, 6, 1),
        amount_min=Decimal("1001"),
        amount_max=Decimal("15000"),
    )


def _outside_position(*, line_number: int = 1) -> OutsidePosition:
    return OutsidePosition(
        line_number=line_number,
        owner_type=OwnerType.SELF,
        entity_name="Lobbying LLC",
        position_title="Advisor",
    )


def _parse_result(
    filing: Filing | None = None,
    *,
    holdings: tuple[Holding, ...] = (),
    transactions: tuple[Transaction, ...] = (),
    outside_positions: tuple[OutsidePosition, ...] = (),
) -> ParseResult:
    f = filing if filing is not None else _filing()
    return ParseResult(
        filing=f,
        holdings=holdings,
        transactions=transactions,
        outside_positions=outside_positions,
        meta=ParserMeta(parser_name="test_parser"),
    )


@dataclass
class _FakeSession:
    """Minimal stand-in for a ParseSessionResult used in batch tests."""

    parse_result: Any
    run_id: int | None = None


# ---------------------------------------------------------------------------
# build_parse_context
# ---------------------------------------------------------------------------


class TestBuildParseContext:
    def test_returns_parse_context(self):
        ctx = build_parse_context()
        assert isinstance(ctx, ParseContext)

    def test_all_ids_none_by_default(self):
        ctx = build_parse_context()
        assert ctx.parse_run_id is None
        assert ctx.source_artifact_id is None
        assert ctx.ingestion_run_id is None

    def test_ids_forwarded_correctly(self):
        ctx = build_parse_context(
            parse_run_id=10,
            source_artifact_id=20,
            ingestion_run_id=30,
        )
        assert ctx.parse_run_id == 10
        assert ctx.source_artifact_id == 20
        assert ctx.ingestion_run_id == 30

    def test_partial_ids_allowed(self):
        ctx = build_parse_context(parse_run_id=5)
        assert ctx.parse_run_id == 5
        assert ctx.source_artifact_id is None
        assert ctx.ingestion_run_id is None


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_disclosure_transform_result(self):
        result = transform_parsed_disclosure(
            _filing(), [], [], [],
        )
        assert isinstance(result, DisclosureTransformResult)

    def test_disclosure_payload_present(self):
        result = transform_parsed_disclosure(_filing(), [], [], [])
        assert result.disclosure is not None

    def test_empty_line_items_yield_empty_lists(self):
        result = transform_parsed_disclosure(_filing(), [], [], [])
        assert result.holdings == []
        assert result.transactions == []
        assert result.outside_positions == []

    def test_holdings_count_matches_input(self):
        holdings = [_holding(line_number=i) for i in range(1, 4)]
        result = transform_parsed_disclosure(_filing(), holdings, [], [])
        assert len(result.holdings) == 3

    def test_transactions_count_matches_input(self):
        txns = [_transaction(line_number=i) for i in range(1, 3)]
        result = transform_parsed_disclosure(_filing(), [], txns, [])
        assert len(result.transactions) == 2

    def test_outside_positions_count_matches_input(self):
        ops = [_outside_position(line_number=i) for i in range(1, 3)]
        result = transform_parsed_disclosure(_filing(), [], [], ops)
        assert len(result.outside_positions) == 2


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – disclosure payload wiring
# ---------------------------------------------------------------------------


class TestDisclosurePayload:
    def test_member_bioguide_id_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(member_bioguide_id="B000042"), [], [], [],
        )
        assert result.disclosure.member_bioguide_id == "B000042"

    def test_chamber_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(chamber=Chamber.HOUSE), [], [], [],
        )
        assert result.disclosure.chamber == "house"

    def test_filing_year_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(filing_year=2022), [], [], [],
        )
        assert result.disclosure.filing_year == 2022

    def test_filing_type_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(filing_type=FilingType.PTR), [], [], [],
        )
        assert result.disclosure.filing_type == "ptr"

    def test_amendment_number_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(amendment_number=2, is_amended=True), [], [], [],
        )
        assert result.disclosure.amendment_number == 2

    def test_is_amended_forwarded(self):
        result = transform_parsed_disclosure(
            _filing(is_amended=True, amendment_number=1), [], [], [],
        )
        assert result.disclosure.is_amended is True


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – provenance wiring
# ---------------------------------------------------------------------------


class TestProvenanceWiring:
    def test_null_provenance_accepted(self):
        # No error when all provenance IDs are None (dry-run / test mode).
        result = transform_parsed_disclosure(
            _filing(), [], [], [],
            parse_run_id=None,
            source_artifact_id=None,
            ingestion_run_id=None,
        )
        assert isinstance(result, DisclosureTransformResult)

    def test_provenance_ids_appear_in_review_items(self):
        # An outside position always emits a review item; provenance should
        # flow through to that review item.
        op = _outside_position()
        result = transform_parsed_disclosure(
            _filing(), [], [], [op],
            parse_run_id=11,
            source_artifact_id=22,
            ingestion_run_id=33,
        )
        review = result.review_items[0]
        assert review.parse_run_id == 11
        assert review.source_artifact_id == 22
        assert review.ingestion_run_id == 33

    def test_partial_provenance_ids_flow_through(self):
        op = _outside_position()
        result = transform_parsed_disclosure(
            _filing(), [], [], [op],
            parse_run_id=7,
        )
        review = result.review_items[0]
        assert review.parse_run_id == 7
        assert review.source_artifact_id is None
        assert review.ingestion_run_id is None


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – amendment review items
# ---------------------------------------------------------------------------


class TestAmendmentReview:
    def test_amendment_filing_emits_review_item(self):
        result = transform_parsed_disclosure(
            _filing(is_amended=True, amendment_number=1), [], [], [],
        )
        amendment_reviews = [
            r for r in result.review_items if r.reason_code == "amendment_filing"
        ]
        assert len(amendment_reviews) == 1

    def test_non_amendment_emits_no_amendment_review(self):
        result = transform_parsed_disclosure(
            _filing(is_amended=False, filing_type=FilingType.ANNUAL), [], [], [],
        )
        amendment_reviews = [
            r for r in result.review_items if r.reason_code == "amendment_filing"
        ]
        assert amendment_reviews == []


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – holding payload content
# ---------------------------------------------------------------------------


class TestHoldingPayload:
    def test_holding_issuer_name_preserved(self):
        h = Holding(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="Widget Inc",
        )
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        assert result.holdings[0].issuer_name == "Widget Inc"

    def test_holding_owner_type_as_string(self):
        h = _holding(owner_type=OwnerType.SPOUSE)
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        assert result.holdings[0].owner_type == "spouse"

    def test_unknown_owner_type_emits_review_item(self):
        h = _holding(owner_type=OwnerType.OTHER)
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "unknown_owner_type" in codes

    def test_trust_owner_emits_review_item(self):
        h = _holding(owner_type=OwnerType.TRUST)
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "unresolved_trust" in codes


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – transaction payload content
# ---------------------------------------------------------------------------


class TestTransactionPayload:
    def test_transaction_type_as_string(self):
        t = _transaction()
        result = transform_parsed_disclosure(_filing(), [], [t], [])
        assert result.transactions[0].transaction_type == "purchase"

    def test_transaction_date_preserved(self):
        t = _transaction()
        result = transform_parsed_disclosure(_filing(), [], [t], [])
        assert result.transactions[0].transaction_date == date(2023, 6, 1)

    def test_unknown_transaction_type_emits_review_item(self):
        t = Transaction(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="Acme",
            transaction_type=TransactionType.OTHER,
            transaction_date=date(2023, 1, 1),
        )
        result = transform_parsed_disclosure(_filing(), [], [t], [])
        codes = {r.reason_code for r in result.review_items}
        assert "unknown_transaction_type" in codes


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – outside position sidecar
# ---------------------------------------------------------------------------


class TestOutsidePositionSidecar:
    def test_outside_position_entity_name_preserved(self):
        op = _outside_position()
        result = transform_parsed_disclosure(_filing(), [], [], [op])
        assert result.outside_positions[0].entity_name == "Lobbying LLC"

    def test_outside_position_always_emits_review_item(self):
        op = _outside_position()
        result = transform_parsed_disclosure(_filing(), [], [], [op])
        op_reviews = [
            r for r in result.review_items if r.reason_code == "no_canonical_table_v1"
        ]
        assert len(op_reviews) == 1

    def test_multiple_outside_positions_each_emit_review_item(self):
        ops = [_outside_position(line_number=i) for i in range(1, 4)]
        result = transform_parsed_disclosure(_filing(), [], [], ops)
        op_reviews = [
            r for r in result.review_items if r.reason_code == "no_canonical_table_v1"
        ]
        assert len(op_reviews) == 3


# ---------------------------------------------------------------------------
# transform_parsed_disclosure – amount label normalisation
# ---------------------------------------------------------------------------


class TestAmountNormalisation:
    def test_known_value_label_resolves_to_decimals(self):
        h = Holding(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="Acme",
            value_label="$15,001 - $50,000",
        )
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        payload = result.holdings[0]
        assert payload.value_min == Decimal("15001")
        assert payload.value_max == Decimal("50000")

    def test_unknown_value_label_emits_review_item(self):
        h = Holding(
            line_number=1,
            owner_type=OwnerType.SELF,
            issuer_name="Acme",
            value_label="$UNKNOWN_RANGE",
        )
        result = transform_parsed_disclosure(_filing(), [h], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "unknown_amount_range" in codes


# ---------------------------------------------------------------------------
# transform_parse_sessions – return type
# ---------------------------------------------------------------------------


class TestTransformParseSessionsReturnType:
    def test_returns_batch_transform_result(self):
        result = transform_parse_sessions(None, [])
        assert isinstance(result, BatchTransformResult)

    def test_empty_input_yields_empty_transformed_and_skipped(self):
        result = transform_parse_sessions(None, [])
        assert result.transformed == []
        assert result.skipped == []

    def test_conn_is_unused(self):
        # conn is a positional placeholder; passing None must not raise.
        result = transform_parse_sessions(None, [])
        assert isinstance(result, BatchTransformResult)


# ---------------------------------------------------------------------------
# transform_parse_sessions – resolved sessions
# ---------------------------------------------------------------------------


class TestTransformParseSessionsResolved:
    def test_resolved_session_appears_in_transformed(self):
        session = _FakeSession(
            run_id=1,
            parse_result={"parsed_document": _parse_result()},
        )
        result = transform_parse_sessions(None, [session])
        assert len(result.transformed) == 1
        assert result.skipped == []

    def test_resolved_session_produces_disclosure_transform_result(self):
        session = _FakeSession(
            parse_result={"parsed_document": _parse_result()},
        )
        result = transform_parse_sessions(None, [session])
        assert isinstance(result.transformed[0], DisclosureTransformResult)

    def test_multiple_resolved_sessions_all_transformed(self):
        sessions = [
            _FakeSession(
                run_id=i,
                parse_result={"parsed_document": _parse_result(_filing(source_record_id=str(i)))},
            )
            for i in range(1, 5)
        ]
        result = transform_parse_sessions(None, sessions)
        assert len(result.transformed) == 4
        assert result.skipped == []

    def test_run_id_forwarded_as_parse_run_id(self):
        session = _FakeSession(
            run_id=99,
            parse_result={
                "parsed_document": _parse_result(
                    _filing(), outside_positions=(_outside_position(),)
                ),
            },
        )
        result = transform_parse_sessions(None, [session])
        # Outside positions always emit review items; parse_run_id should flow through.
        review = result.transformed[0].review_items[0]
        assert review.parse_run_id == 99

    def test_source_artifact_id_forwarded(self):
        session = _FakeSession(
            run_id=1,
            parse_result={
                "parsed_document": _parse_result(
                    _filing(), outside_positions=(_outside_position(),)
                ),
                "source_artifact_id": 55,
            },
        )
        result = transform_parse_sessions(None, [session])
        review = result.transformed[0].review_items[0]
        assert review.source_artifact_id == 55

    def test_member_resolved_from_resolved_member_field(self):
        # Parser left bioguide_id blank; resolved_member supplies it.
        bare_filing = _filing(member_bioguide_id="")
        pr = _parse_result(bare_filing)

        @dataclass
        class _Resolution:
            bioguide_id: str

        session = _FakeSession(
            parse_result={
                "parsed_document": pr,
                "resolved_member": _Resolution(bioguide_id="C000099"),
            },
        )
        result = transform_parse_sessions(None, [session])
        assert len(result.transformed) == 1
        assert result.transformed[0].disclosure.member_bioguide_id == "C000099"

    def test_member_resolved_from_resolved_member_dict(self):
        bare_filing = _filing(member_bioguide_id="")
        pr = _parse_result(bare_filing)
        session = _FakeSession(
            parse_result={
                "parsed_document": pr,
                "resolved_member": {"bioguide_id": "D000077"},
            },
        )
        result = transform_parse_sessions(None, [session])
        assert len(result.transformed) == 1
        assert result.transformed[0].disclosure.member_bioguide_id == "D000077"


# ---------------------------------------------------------------------------
# transform_parse_sessions – skipped sessions
# ---------------------------------------------------------------------------


class TestTransformParseSessionsSkipped:
    def test_session_without_parse_result_dict_is_skipped(self):
        session = _FakeSession(parse_result=None)
        result = transform_parse_sessions(None, [session])
        assert result.transformed == []
        assert len(result.skipped) == 1

    def test_no_parse_result_reason_code(self):
        session = _FakeSession(parse_result=None)
        result = transform_parse_sessions(None, [session])
        assert result.skipped[0].reason_code == "no_parse_result"

    def test_session_without_parsed_document_is_skipped(self):
        session = _FakeSession(parse_result={"parsed_document": "not a ParseResult"})
        result = transform_parse_sessions(None, [session])
        assert len(result.skipped) == 1
        assert result.skipped[0].reason_code == "no_parsed_document"

    def test_missing_parsed_document_key_is_skipped(self):
        session = _FakeSession(parse_result={})
        result = transform_parse_sessions(None, [session])
        assert result.skipped[0].reason_code == "no_parsed_document"

    def test_unresolved_member_identity_is_skipped(self):
        bare_filing = _filing(member_bioguide_id="")
        pr = _parse_result(bare_filing)
        session = _FakeSession(parse_result={"parsed_document": pr})
        result = transform_parse_sessions(None, [session])
        assert len(result.skipped) == 1
        assert result.skipped[0].reason_code == "unresolved_member_identity"

    def test_skipped_session_captures_run_id(self):
        session = _FakeSession(run_id=42, parse_result=None)
        result = transform_parse_sessions(None, [session])
        assert result.skipped[0].run_id == 42

    def test_skipped_session_run_id_none_when_absent(self):
        session = _FakeSession(parse_result=None)  # run_id defaults to None
        result = transform_parse_sessions(None, [session])
        assert result.skipped[0].run_id is None

    def test_skipped_is_skipped_session_instance(self):
        session = _FakeSession(parse_result=None)
        result = transform_parse_sessions(None, [session])
        assert isinstance(result.skipped[0], SkippedSession)


# ---------------------------------------------------------------------------
# transform_parse_sessions – mixed batches
# ---------------------------------------------------------------------------


class TestTransformParseSessionsMixed:
    def test_mixed_batch_transformed_count(self):
        good = _FakeSession(parse_result={"parsed_document": _parse_result()})
        bad_no_dict = _FakeSession(parse_result=None)
        bad_no_doc = _FakeSession(parse_result={"parsed_document": 42})
        bad_unresolved = _FakeSession(
            parse_result={"parsed_document": _parse_result(_filing(member_bioguide_id=""))}
        )
        result = transform_parse_sessions(None, [good, bad_no_dict, bad_no_doc, bad_unresolved])
        assert len(result.transformed) == 1
        assert len(result.skipped) == 3

    def test_mixed_batch_reason_codes_are_distinct(self):
        bad_no_dict = _FakeSession(parse_result=None)
        bad_no_doc = _FakeSession(parse_result={"parsed_document": "nope"})
        bad_unresolved = _FakeSession(
            parse_result={"parsed_document": _parse_result(_filing(member_bioguide_id=""))}
        )
        result = transform_parse_sessions(None, [bad_no_dict, bad_no_doc, bad_unresolved])
        codes = [s.reason_code for s in result.skipped]
        assert codes == [
            "no_parse_result",
            "no_parsed_document",
            "unresolved_member_identity",
        ]

    def test_ordering_preserved(self):
        # Transformed results appear in the same order as input sessions.
        sessions = [
            _FakeSession(
                run_id=i,
                parse_result={"parsed_document": _parse_result(_filing(source_record_id=str(i)))},
            )
            for i in range(1, 4)
        ]
        result = transform_parse_sessions(None, sessions)
        disclosed = [r.disclosure.source_record_id for r in result.transformed]
        assert disclosed == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# transform_single_session
# ---------------------------------------------------------------------------


class TestTransformSingleSession:
    def test_valid_session_returns_disclosure_transform_result(self):
        session = _FakeSession(
            run_id=1,
            parse_result={"parsed_document": _parse_result()},
        )
        result = transform_single_session(session)
        assert isinstance(result, DisclosureTransformResult)

    def test_none_parse_result_returns_skipped(self):
        session = _FakeSession(parse_result=None)
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == "no_parse_result"

    def test_no_parsed_document_returns_skipped(self):
        session = _FakeSession(parse_result={"parsed_document": "not a ParseResult"})
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == "no_parsed_document"

    def test_missing_parsed_document_key_returns_skipped(self):
        session = _FakeSession(parse_result={})
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == "no_parsed_document"

    def test_unresolved_member_returns_skipped(self):
        bare_filing = _filing(member_bioguide_id="")
        session = _FakeSession(
            run_id=7,
            parse_result={"parsed_document": _parse_result(bare_filing)},
        )
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == "unresolved_member_identity"
        assert result.run_id == 7

    def test_skipped_session_captures_run_id(self):
        session = _FakeSession(run_id=55, parse_result=None)
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.run_id == 55

    def test_skipped_run_id_none_when_absent(self):
        session = _FakeSession(parse_result=None)
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.run_id is None

    def test_resolved_member_from_resolved_member_field(self):
        bare_filing = _filing(member_bioguide_id="")
        pr = _parse_result(bare_filing)

        @dataclass
        class _Res:
            bioguide_id: str

        session = _FakeSession(
            parse_result={
                "parsed_document": pr,
                "resolved_member": _Res(bioguide_id="E000111"),
            },
        )
        result = transform_single_session(session)
        assert isinstance(result, DisclosureTransformResult)
        assert result.disclosure.member_bioguide_id == "E000111"

    def test_provenance_forwarded_from_session(self):
        op = _outside_position()
        session = _FakeSession(
            run_id=12,
            parse_result={
                "parsed_document": _parse_result(_filing(), outside_positions=(op,)),
                "source_artifact_id": 34,
                "ingestion_run_id": 56,
            },
        )
        result = transform_single_session(session)
        assert isinstance(result, DisclosureTransformResult)
        review = result.review_items[0]
        assert review.parse_run_id == 12
        assert review.source_artifact_id == 34
        assert review.ingestion_run_id == 56

    def test_single_session_consistent_with_batch(self):
        # transform_single_session and transform_parse_sessions must agree.
        session = _FakeSession(
            run_id=1,
            parse_result={"parsed_document": _parse_result()},
        )
        single = transform_single_session(session)
        batch = transform_parse_sessions(None, [session])
        assert isinstance(single, DisclosureTransformResult)
        assert single.disclosure.member_bioguide_id == batch.transformed[0].disclosure.member_bioguide_id


# ---------------------------------------------------------------------------
# Amendment inconsistency review items flowing through runtime layer
# ---------------------------------------------------------------------------


class TestAmendmentInconsistencyThroughRuntime:
    def test_supersedes_mismatch_review_emitted(self):
        # supersedes_filing_source_id set but is_amended=False and type=annual
        f = _filing(
            is_amended=False,
            amendment_number=0,
            supersedes_filing_source_id="PREV-001",
        )
        result = transform_parsed_disclosure(f, [], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" in codes

    def test_amendment_number_without_flag_review_emitted(self):
        f = _filing(is_amended=False, amendment_number=3)
        result = transform_parsed_disclosure(f, [], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_number_without_flag" in codes

    def test_supersedes_mismatch_higher_priority_than_amendment_filing(self):
        # amendment_supersedes_mismatch is more urgent than amendment_filing
        f = _filing(is_amended=True, amendment_number=1, supersedes_filing_source_id="PREV-001")
        result = transform_parsed_disclosure(f, [], [], [])
        # is_amended=True triggers amendment_filing; supersedes is present so no mismatch
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_filing" in codes
        assert "amendment_supersedes_mismatch" not in codes

    def test_clean_non_amendment_no_inconsistency_review(self):
        f = _filing(is_amended=False, amendment_number=0, supersedes_filing_source_id=None)
        result = transform_parsed_disclosure(f, [], [], [])
        codes = {r.reason_code for r in result.review_items}
        assert "amendment_supersedes_mismatch" not in codes
        assert "amendment_number_without_flag" not in codes


# ---------------------------------------------------------------------------
# Outside position with unknown owner type
# ---------------------------------------------------------------------------


class TestOutsidePositionUnknownOwner:
    def test_other_owner_on_outside_position_emits_two_review_items(self):
        op = OutsidePosition(
            line_number=1,
            owner_type=OwnerType.OTHER,
            entity_name="Shadow Corp",
        )
        result = transform_parsed_disclosure(_filing(), [], [], [op])
        reason_codes = [r.reason_code for r in result.review_items]
        assert "no_canonical_table_v1" in reason_codes
        assert "unknown_owner_type" in reason_codes

    def test_self_owner_on_outside_position_emits_one_review_item(self):
        op = OutsidePosition(
            line_number=1,
            owner_type=OwnerType.SELF,
            entity_name="Lobbying LLC",
        )
        result = transform_parsed_disclosure(_filing(), [], [], [op])
        assert len([r for r in result.review_items if r.reason_code == "no_canonical_table_v1"]) == 1
        assert not any(r.reason_code == "unknown_owner_type" for r in result.review_items)


# ---------------------------------------------------------------------------
# Skip-reason constants
# ---------------------------------------------------------------------------


class TestSkipReasonConstants:
    """Verify that the skip-reason constants match the values produced by
    transform_single_session and transform_parse_sessions."""

    def test_skip_constants_are_strings(self):
        for const in (SKIP_NO_PARSE_RESULT, SKIP_NO_PARSED_DOCUMENT, SKIP_UNRESOLVED_MEMBER_IDENTITY):
            assert isinstance(const, str)

    def test_no_parse_result_matches_constant(self):
        session = _FakeSession(parse_result=None)
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == SKIP_NO_PARSE_RESULT

    def test_no_parsed_document_matches_constant(self):
        session = _FakeSession(parse_result={"parsed_document": "not a ParseResult"})
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == SKIP_NO_PARSED_DOCUMENT

    def test_unresolved_member_matches_constant(self):
        bare_filing = _filing(member_bioguide_id="")
        session = _FakeSession(parse_result={"parsed_document": _parse_result(bare_filing)})
        result = transform_single_session(session)
        assert isinstance(result, SkippedSession)
        assert result.reason_code == SKIP_UNRESOLVED_MEMBER_IDENTITY

    def test_batch_skip_reasons_use_same_constants(self):
        bad_no_dict = _FakeSession(parse_result=None)
        bad_no_doc = _FakeSession(parse_result={"parsed_document": "nope"})
        bad_unresolved = _FakeSession(
            parse_result={"parsed_document": _parse_result(_filing(member_bioguide_id=""))}
        )
        result = transform_parse_sessions(None, [bad_no_dict, bad_no_doc, bad_unresolved])
        codes = [s.reason_code for s in result.skipped]
        assert codes == [
            SKIP_NO_PARSE_RESULT,
            SKIP_NO_PARSED_DOCUMENT,
            SKIP_UNRESOLVED_MEMBER_IDENTITY,
        ]
