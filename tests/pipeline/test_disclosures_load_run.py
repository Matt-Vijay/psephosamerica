"""Tests for src/pipeline/disclosures_load_run.py.

No live DB — executor and lookup loader are both mocked.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.lookups import LookupBundle
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    FinancialDisclosurePayload,
    HoldingPayload,
    OutsidePositionSidecar,
    ReviewQueuePayload,
    TransactionPayload,
)
from src.pipeline.disclosures_load_run import (
    _resolve_child_rows,
    _resolve_disclosure_rows,
    run_disclosures_load,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BIOGUIDE = "A000001"
_MEMBER_ID = 101
_FD_ID = 201


def _disclosure(bioguide_id: str = _BIOGUIDE) -> FinancialDisclosurePayload:
    return FinancialDisclosurePayload(
        member_bioguide_id=bioguide_id,
        chamber="senate",
        filing_year=2024,
        filing_type="annual",
        amendment_number=0,
        is_amended=False,
        filed_at=date(2024, 5, 15),
    )


def _holding(line: int = 1) -> HoldingPayload:
    return HoldingPayload(
        line_number=line,
        owner_type="self",
        issuer_name="Acme Corp",
        issuer_ticker="ACME",
    )


def _transaction(line: int = 1) -> TransactionPayload:
    return TransactionPayload(
        line_number=line,
        owner_type="self",
        issuer_name="Beta Inc",
        transaction_type="purchase",
        transaction_date=date(2024, 3, 1),
    )


def _outside(line: int = 1) -> OutsidePositionSidecar:
    return OutsidePositionSidecar(
        line_number=line,
        owner_type="self",
        entity_name="Gamma LLC",
    )


def _review_item() -> ReviewQueuePayload:
    return ReviewQueuePayload(
        review_type="normalization",
        entity_type="holding",
        entity_key="line_1",
        reason_code="unknown_amount_range",
        priority=40,
        payload={"value_label": "???"},
        status="open",
    )


def _transform_result(
    bioguide_id: str = _BIOGUIDE,
    holdings: list[HoldingPayload] | None = None,
    transactions: list[TransactionPayload] | None = None,
    outside_positions: list[OutsidePositionSidecar] | None = None,
    review_items: list[ReviewQueuePayload] | None = None,
) -> DisclosureTransformResult:
    return DisclosureTransformResult(
        disclosure=_disclosure(bioguide_id),
        holdings=holdings or [],
        transactions=transactions or [],
        outside_positions=outside_positions or [],
        review_items=review_items or [],
    )


def _empty_summary(table: str = "financial_disclosure") -> LoadSummary:
    return build_load_summary(
        [TableWriteResult(table=table, inserted=1)],
        WarnErrorSummary(),
    )


def _bundle_initial() -> LookupBundle:
    b = LookupBundle()
    b.bioguide_map[_BIOGUIDE] = _MEMBER_ID
    b.disclosure_source_record_id_map["FILING-000"] = 200
    return b


def _bundle_refreshed() -> LookupBundle:
    b = LookupBundle()
    b.bioguide_map[_BIOGUIDE] = _MEMBER_ID
    b.disclosure_natural_key_map[(_MEMBER_ID, 2024, "annual", 0)] = _FD_ID
    return b


# ---------------------------------------------------------------------------
# _resolve_disclosure_rows
# ---------------------------------------------------------------------------


class TestResolveDisclosureRows:
    def _rows_from(self, d: FinancialDisclosurePayload) -> tuple[dict, ...]:
        from src.load.disclosures import plan_disclosure_load
        from src.parse.disclosures.transform import DisclosureTransformResult

        plan = plan_disclosure_load([DisclosureTransformResult(disclosure=d)])
        fd_batch = next(b for b in plan.batches if b.table == "financial_disclosure")
        return fd_batch.rows

    def test_resolves_member_id(self):
        bundle = _bundle_initial()
        warn_error = WarnErrorSummary()
        rows = self._rows_from(_disclosure())

        out = _resolve_disclosure_rows(rows, bundle, warn_error)

        assert len(out) == 1
        assert out[0]["member_id"] == _MEMBER_ID
        assert "member_bioguide_id" not in out[0]

    def test_resolves_supersedes_filing_source_id(self):
        bundle = _bundle_initial()
        warn_error = WarnErrorSummary()
        disclosure = _disclosure()
        disclosure = FinancialDisclosurePayload(
            **{
                **disclosure.__dict__,
                "filing_type": "amendment",
                "amendment_number": 1,
                "supersedes_filing_source_id": "FILING-000",
            }
        )
        rows = self._rows_from(disclosure)

        out = _resolve_disclosure_rows(rows, bundle, warn_error)

        assert "supersedes_filing_source_id" not in out[0]
        assert out[0]["supersedes_financial_disclosure_id"] == 200

    def test_unresolved_supersedes_filing_source_id_warns_but_keeps_row(self):
        bundle = _bundle_initial()
        warn_error = WarnErrorSummary()
        disclosure = _disclosure()
        disclosure = FinancialDisclosurePayload(
            **{
                **disclosure.__dict__,
                "filing_type": "amendment",
                "amendment_number": 1,
                "supersedes_filing_source_id": "MISSING-FILING",
            }
        )
        rows = self._rows_from(disclosure)

        out = _resolve_disclosure_rows(rows, bundle, warn_error)

        assert len(out) == 1
        assert "supersedes_filing_source_id" not in out[0]
        assert "supersedes_financial_disclosure_id" not in out[0]
        assert warn_error.warning_count == 1
        assert "MISSING-FILING" in warn_error.warnings[0]

    def test_unknown_bioguide_skipped_with_warning(self):
        bundle = LookupBundle()  # empty — no members
        warn_error = WarnErrorSummary()
        rows = self._rows_from(_disclosure("UNKNOWN"))

        out = _resolve_disclosure_rows(rows, bundle, warn_error)

        assert out == []
        assert warn_error.warning_count == 1
        assert "UNKNOWN" in warn_error.warnings[0]


# ---------------------------------------------------------------------------
# _resolve_child_rows
# ---------------------------------------------------------------------------


class TestResolveChildRows:
    def _child_rows(self, table: str) -> tuple[dict, ...]:
        from src.load.disclosures import plan_disclosure_load

        result = DisclosureTransformResult(
            disclosure=_disclosure(),
            holdings=[_holding()] if table == "holding" else [],
            transactions=[_transaction()] if table == "transaction" else [],
        )
        plan = plan_disclosure_load([result])
        batch = next(b for b in plan.batches if b.table == table)
        return batch.rows

    def test_resolves_fd_id_for_holding(self):
        bundle = _bundle_refreshed()
        warn_error = WarnErrorSummary()
        rows = self._child_rows("holding")

        out = _resolve_child_rows(rows, bundle, warn_error, "holding")

        assert len(out) == 1
        assert out[0]["financial_disclosure_id"] == _FD_ID
        assert not any(k.startswith("disclosure_") for k in out[0])

    def test_resolves_fd_id_for_transaction(self):
        bundle = _bundle_refreshed()
        warn_error = WarnErrorSummary()
        rows = self._child_rows("transaction")

        out = _resolve_child_rows(rows, bundle, warn_error, "transaction")

        assert len(out) == 1
        assert out[0]["financial_disclosure_id"] == _FD_ID

    def test_unknown_member_skipped_with_warning(self):
        bundle = LookupBundle()  # empty
        warn_error = WarnErrorSummary()
        rows = self._child_rows("holding")

        out = _resolve_child_rows(rows, bundle, warn_error, "holding")

        assert out == []
        assert warn_error.warning_count == 1

    def test_unresolved_fd_skipped_with_warning(self):
        bundle = _bundle_initial()  # has member but no disclosure map
        warn_error = WarnErrorSummary()
        rows = self._child_rows("holding")

        out = _resolve_child_rows(rows, bundle, warn_error, "holding")

        assert out == []
        assert warn_error.warning_count == 1


# ---------------------------------------------------------------------------
# run_disclosures_load — orchestration
# ---------------------------------------------------------------------------


class TestRunDisclosuresLoad:
    _TARGET = "src.pipeline.disclosures_load_run.execute_load_plan"

    def _loader_side_effects(self):
        """Return (initial_bundle, refreshed_bundle) for loader side_effect."""
        return [_bundle_initial(), _bundle_refreshed()]

    def test_empty_results_no_executor_calls(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        with patch(self._TARGET) as mock_exec:
            summary, sidecars = run_disclosures_load([], conn, lookup_loader=loader)

        mock_exec.assert_not_called()
        assert sidecars == ()
        assert summary.total_attempted == 0

    def test_loader_called_twice(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        with patch(self._TARGET, return_value=_empty_summary()):
            run_disclosures_load(
                [_transform_result(holdings=[_holding()])],
                conn,
                lookup_loader=loader,
            )

        assert loader.call_count == 2
        loader.assert_called_with(conn)

    def test_financial_disclosure_written_before_holding(self):
        call_order: list[str] = []
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        def _fake_exec(conn, ops, **kwargs):
            call_order.append(ops[0]["table"])
            return _empty_summary(ops[0]["table"])

        result = _transform_result(holdings=[_holding()])
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader)

        assert call_order.index("financial_disclosure") < call_order.index("holding")

    def test_holding_and_transaction_written_after_refresh(self):
        call_order: list[str] = []
        call_count = [0]

        def _loader(c):
            call_count[0] += 1
            if call_count[0] == 1:
                return _bundle_initial()
            return _bundle_refreshed()

        def _fake_exec(conn, ops, **kwargs):
            table = ops[0]["table"]
            call_order.append((table, call_count[0]))
            return _empty_summary(table)

        conn = MagicMock()
        result = _transform_result(holdings=[_holding()], transactions=[_transaction()])

        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=_loader)

        holding_call_num = next(n for t, n in call_order if t == "holding")
        # holding must be written after the second loader call (refresh)
        assert holding_call_num == 2

    def test_review_queue_written_in_phase3(self):
        call_order: list[str] = []
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        def _fake_exec(conn, ops, **kwargs):
            call_order.append(ops[0]["table"])
            return _empty_summary(ops[0]["table"])

        result = _transform_result(
            holdings=[_holding()],
            review_items=[_review_item()],
        )
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader)

        assert "review_queue" in call_order
        assert call_order[-1] == "review_queue"

    def test_sidecars_returned(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        sidecar = _outside()
        result = _transform_result(outside_positions=[sidecar])

        with patch(self._TARGET, return_value=_empty_summary()):
            _, sidecars = run_disclosures_load([result], conn, lookup_loader=loader)

        assert len(sidecars) == 1
        assert sidecars[0].entity_name == "Gamma LLC"

    def test_returns_load_summary(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        with patch(self._TARGET, return_value=_empty_summary()):
            summary, _ = run_disclosures_load(
                [_transform_result(holdings=[_holding()])],
                conn,
                lookup_loader=loader,
            )

        assert isinstance(summary, LoadSummary)

    def test_run_id_propagated_to_executor(self):
        captured: list[int | None] = []
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        def _fake_exec(conn, ops, **kwargs):
            captured.append(kwargs.get("run_id"))
            return _empty_summary(ops[0]["table"])

        result = _transform_result(holdings=[_holding()])
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader, run_id=42)

        assert all(r == 42 for r in captured)

    def test_phase_writes_defer_commit_to_outer_transaction(self):
        captured: list[bool | None] = []
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        def _fake_exec(conn, ops, **kwargs):
            captured.append(kwargs.get("commit"))
            return _empty_summary(ops[0]["table"])

        result = _transform_result(holdings=[_holding()], transactions=[_transaction()])
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader)

        assert captured == [False, False, False]

    def test_same_batch_amendment_supersession_is_resolved_after_refresh(self):
        captured: list[tuple[str, list[dict]]] = []
        conn = MagicMock()
        original = _disclosure()
        original = FinancialDisclosurePayload(
            **{
                **original.__dict__,
                "source_record_id": "FILING-000",
            }
        )
        amendment = _disclosure()
        amendment = FinancialDisclosurePayload(
            **{
                **amendment.__dict__,
                "filing_type": "amendment",
                "amendment_number": 1,
                "source_record_id": "FILING-001",
                "supersedes_filing_source_id": "FILING-000",
            }
        )
        result = DisclosureTransformResult(disclosure=original)
        amendment_result = DisclosureTransformResult(disclosure=amendment)
        initial_bundle = LookupBundle()
        initial_bundle.bioguide_map[_BIOGUIDE] = _MEMBER_ID
        refreshed_bundle = LookupBundle()
        refreshed_bundle.bioguide_map[_BIOGUIDE] = _MEMBER_ID
        refreshed_bundle.disclosure_natural_key_map[(_MEMBER_ID, 2024, "annual", 0)] = 200
        refreshed_bundle.disclosure_natural_key_map[(_MEMBER_ID, 2024, "amendment", 1)] = 201
        refreshed_bundle.disclosure_source_record_id_map["FILING-000"] = 200
        loader = MagicMock(side_effect=[initial_bundle, refreshed_bundle])

        def _fake_exec(conn, ops, **kwargs):
            captured.append((ops[0]["table"], list(ops[0]["rows"])))
            return _empty_summary(ops[0]["table"])

        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result, amendment_result], conn, lookup_loader=loader)

        fd_writes = [rows for table, rows in captured if table == "financial_disclosure"]
        assert len(fd_writes) == 2
        assert len(fd_writes[1]) == 1
        update_row = fd_writes[1][0]
        assert update_row["member_id"] == _MEMBER_ID
        assert update_row["chamber"] == "senate"
        assert update_row["filing_year"] == 2024
        assert update_row["filing_type"] == "amendment"
        assert update_row["amendment_number"] == 1
        assert update_row["supersedes_financial_disclosure_id"] == 200
        assert "supersedes_filing_source_id" not in update_row

    def test_success_commits_once_after_all_phases(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        result = _transform_result(holdings=[_holding()])

        with patch(self._TARGET, return_value=_empty_summary()):
            run_disclosures_load([result], conn, lookup_loader=loader)

        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_commit_true_rejects_autocommit_connection_before_phase_writes(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        conn.autocommit = True
        result = _transform_result(holdings=[_holding()])

        with patch(self._TARGET) as mock_exec:
            with pytest.raises(RuntimeError, match="autocommit"):
                run_disclosures_load([result], conn, lookup_loader=loader)

        mock_exec.assert_not_called()
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_final_commit_failure_rolls_back_outer_transaction(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        conn.commit.side_effect = RuntimeError("commit failed")
        result = _transform_result(holdings=[_holding()])

        with patch(self._TARGET, return_value=_empty_summary()):
            with pytest.raises(RuntimeError, match="commit failed"):
                run_disclosures_load([result], conn, lookup_loader=loader)

        conn.commit.assert_called_once()
        conn.rollback.assert_called_once()

    def test_commit_false_defers_final_commit(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        result = _transform_result(holdings=[_holding()])

        with patch(self._TARGET, return_value=_empty_summary()):
            run_disclosures_load([result], conn, lookup_loader=loader, commit=False)

        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_phase_failure_rolls_back_outer_transaction(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        result = _transform_result(holdings=[_holding()])

        with patch(
            self._TARGET,
            side_effect=[_empty_summary("financial_disclosure"), RuntimeError("phase failed")],
        ):
            with pytest.raises(RuntimeError, match="phase failed"):
                run_disclosures_load([result], conn, lookup_loader=loader)

        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_phase_failure_with_commit_false_defers_rollback_to_caller(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        result = _transform_result(holdings=[_holding()])

        with patch(
            self._TARGET,
            side_effect=[_empty_summary("financial_disclosure"), RuntimeError("phase failed")],
        ):
            with pytest.raises(RuntimeError, match="phase failed"):
                run_disclosures_load(
                    [result],
                    conn,
                    lookup_loader=loader,
                    commit=False,
                )

        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_lookup_refresh_failure_after_financial_disclosure_write_rolls_back(self):
        loader = MagicMock(side_effect=[_bundle_initial(), RuntimeError("lookup failed")])
        conn = MagicMock()
        result = _transform_result(holdings=[_holding()])

        with patch(self._TARGET, return_value=_empty_summary()) as mock_exec:
            with pytest.raises(RuntimeError, match="lookup failed"):
                run_disclosures_load([result], conn, lookup_loader=loader)

        mock_exec.assert_called_once()
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_unresolved_member_produces_warning(self):
        loader = MagicMock(side_effect=[LookupBundle(), LookupBundle()])
        conn = MagicMock()
        result = _transform_result()  # bioguide not in empty bundle

        with patch(self._TARGET) as mock_exec:
            summary, _ = run_disclosures_load([result], conn, lookup_loader=loader)

        assert summary.warn_error.warning_count >= 1
        # financial_disclosure phase skips unresolvable row — no insert call
        mock_exec.assert_not_called()

    def test_multiple_results_batched_together(self):
        captured_row_counts: dict[str, int] = {}
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()

        # Two separate members; bundle covers both
        bundle_1 = LookupBundle()
        bundle_1.bioguide_map["A000001"] = 101
        bundle_1.bioguide_map["B000002"] = 102
        bundle_2 = LookupBundle()
        bundle_2.bioguide_map["A000001"] = 101
        bundle_2.bioguide_map["B000002"] = 102
        bundle_2.disclosure_natural_key_map[(101, 2024, "annual", 0)] = 201
        bundle_2.disclosure_natural_key_map[(102, 2024, "annual", 0)] = 202
        loader.side_effect = [bundle_1, bundle_2]

        def _fake_exec(conn, ops, **kwargs):
            table = ops[0]["table"]
            captured_row_counts[table] = len(ops[0]["rows"])
            return _empty_summary(table)

        results = [
            _transform_result("A000001", holdings=[_holding(1)]),
            _transform_result("B000002", holdings=[_holding(1)]),
        ]
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load(results, conn, lookup_loader=loader)

        assert captured_row_counts["financial_disclosure"] == 2
        assert captured_row_counts["holding"] == 2

    def test_empty_batches_not_sent_to_executor(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        sent_tables: list[str] = []

        def _fake_exec(conn, ops, **kwargs):
            sent_tables.append(ops[0]["table"])
            return _empty_summary(ops[0]["table"])

        # No holdings, no transactions, no review items
        result = _transform_result()
        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader)

        assert "holding" not in sent_tables
        assert "transaction" not in sent_tables
        assert "review_queue" not in sent_tables

    def test_child_cleanup_deletes_stale_lines_before_child_upserts(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        events: list[tuple] = []
        cursor = conn.cursor.return_value.__enter__.return_value

        def _capture_delete(sql, params=None):
            events.append(("delete", sql, params))

        def _fake_exec(conn, ops, **kwargs):
            events.append(("upsert", ops[0]["table"], list(ops[0]["rows"])))
            return _empty_summary(ops[0]["table"])

        cursor.execute.side_effect = _capture_delete
        result = _transform_result(
            holdings=[_holding(1)],
            transactions=[_transaction(1)],
        )

        with patch(self._TARGET, side_effect=_fake_exec):
            run_disclosures_load([result], conn, lookup_loader=loader)

        holding_delete_index = next(
            i
            for i, event in enumerate(events)
            if event[0] == "delete" and 'DELETE FROM "holding"' in event[1]
        )
        holding_upsert_index = next(
            i for i, event in enumerate(events) if event[0] == "upsert" and event[1] == "holding"
        )
        transaction_delete_index = next(
            i
            for i, event in enumerate(events)
            if event[0] == "delete" and 'DELETE FROM "transaction"' in event[1]
        )
        transaction_upsert_index = next(
            i
            for i, event in enumerate(events)
            if event[0] == "upsert" and event[1] == "transaction"
        )

        assert holding_delete_index < holding_upsert_index
        assert transaction_delete_index < transaction_upsert_index
        assert events[holding_delete_index][2] == (_FD_ID, 1)
        assert events[transaction_delete_index][2] == (_FD_ID, 1)
        assert '"line_number" NOT IN (%s)' in events[holding_delete_index][1]
        assert '"line_number" NOT IN (%s)' in events[transaction_delete_index][1]

    def test_child_cleanup_deletes_all_child_rows_when_reparse_has_none(self):
        loader = MagicMock(side_effect=self._loader_side_effects())
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        deletes: list[tuple[str, tuple]] = []

        def _capture_delete(sql, params=None):
            deletes.append((sql, params))

        cursor.execute.side_effect = _capture_delete
        result = _transform_result()

        with patch(self._TARGET, return_value=_empty_summary()):
            run_disclosures_load([result], conn, lookup_loader=loader)

        holding_delete = next(
            sql_params for sql_params in deletes if 'DELETE FROM "holding"' in sql_params[0]
        )
        transaction_delete = next(
            sql_params for sql_params in deletes if 'DELETE FROM "transaction"' in sql_params[0]
        )
        assert holding_delete == (
            'DELETE FROM "holding" WHERE "financial_disclosure_id" = %s',
            (_FD_ID,),
        )
        assert transaction_delete == (
            'DELETE FROM "transaction" WHERE "financial_disclosure_id" = %s',
            (_FD_ID,),
        )
