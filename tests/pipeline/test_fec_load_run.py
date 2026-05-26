from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import LoadSummary, TableWriteResult, WarnErrorSummary, build_load_summary
from src.ingest.fec.models import (
    CandidateCommitteeLinkage,
    CommitteeRecord,
    ContributionRecord,
)
from src.pipeline.fec_load_run import FecLoadInputs, run_fec_load

_MODULE = "src.pipeline.fec_load_run"


def _summary(table: str) -> LoadSummary:
    return build_load_summary(
        [TableWriteResult(table=table, inserted=1)],
        warn_error=WarnErrorSummary(),
        run_id=42,
    )


def _committee() -> CommitteeRecord:
    return CommitteeRecord(
        fec_committee_id="C00431445",
        committee_name="Rep A for Congress",
        treasurer_name="Treasurer",
        city="Detroit",
        state="MI",
        committee_type="H",
        designation_code="P",
    )


def _contribution() -> ContributionRecord:
    return ContributionRecord(
        fec_committee_id="C00431445",
        donor_name="ENERGY PAC",
        city="Detroit",
        state="MI",
        zip_code="48201",
        employer="ENERGY PAC",
        occupation="PAC",
        contribution_date=dt.date(2024, 5, 1),
        amount=Decimal("2500.00"),
        transaction_type="10",
        memo_text=None,
        sub_id="4073020241987654321",
        entity_type="COM",
    )


def _linkage() -> CandidateCommitteeLinkage:
    return CandidateCommitteeLinkage(
        fec_candidate_id="H4MI00001",
        fec_committee_id="C00431445",
        linkage_type="P",
        election_year=2024,
    )


def _inputs() -> FecLoadInputs:
    return FecLoadInputs(
        committees=[_committee()],
        contributions=[_contribution()],
        linkages=[_linkage()],
        committee_source_artifact_id=10,
        contribution_source_artifact_id=11,
        linkage_source_artifact_id=12,
    )


def test_run_fec_load_phases_committee_before_contribution_and_linkage() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}
    with (
        patch(
            f"{_MODULE}.execute_load_plan",
            side_effect=[_summary("fec_committee"), _summary("contribution")],
        ) as exec_spy,
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        result = run_fec_load(_inputs(), conn, run_id=42)

    assert result.total_inserted == 2
    assert exec_spy.call_count == 2
    first_ops = exec_spy.call_args_list[0].args[1]
    second_ops = exec_spy.call_args_list[1].args[1]
    assert [op["table"] for op in first_ops] == ["fec_committee"]
    assert [op["table"] for op in second_ops] == [
        "contribution",
        "fec_candidate_committee_linkage",
    ]


def test_run_fec_load_injects_source_artifact_ids() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}
    captured_ops: list[list[dict[str, object]]] = []

    def _capture(_conn, operations, **_kwargs):
        captured_ops.append(operations)
        return _summary(str(operations[0]["table"]))

    with (
        patch(f"{_MODULE}.execute_load_plan", side_effect=_capture),
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        run_fec_load(_inputs(), conn, run_id=42)

    assert captured_ops[0][0]["rows"][0]["source_artifact_id"] == 10
    assert captured_ops[1][0]["rows"][0]["source_artifact_id"] == 11
    assert captured_ops[1][1]["rows"][0]["source_artifact_id"] == 12


def test_run_fec_load_resolves_contribution_recipient_committee_id() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}
    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")) as exec_spy,
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        run_fec_load(_inputs(), conn, run_id=42)

    resolvers = exec_spy.call_args_list[1].kwargs["resolvers"]
    target_column, resolver = resolvers["recipient_fec_committee_id_raw"]
    assert target_column == "recipient_fec_committee_id"
    assert resolver({"recipient_fec_committee_id_raw": "C00431445"}) == 501


def test_run_fec_load_filters_linkages_for_missing_committees() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}
    inputs = _inputs()
    inputs.linkages.append(
        CandidateCommitteeLinkage(
            fec_candidate_id="H4MI99999",
            fec_committee_id="C99999999",
            linkage_type="P",
            election_year=2024,
        )
    )

    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")) as exec_spy,
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        result = run_fec_load(inputs, conn, run_id=42)

    linkage_rows = exec_spy.call_args_list[1].args[1][1]["rows"]
    assert [row["fec_committee_id"] for row in linkage_rows] == ["C00431445"]
    assert result.warn_error.warnings == [
        (
            "skipped 1 FEC candidate-committee linkage row(s) whose committee "
            "was absent from committee master"
        )
    ]


def test_run_fec_load_requires_contribution_source_artifact_when_rows_present() -> None:
    inputs = _inputs()
    inputs.contribution_source_artifact_id = None

    with pytest.raises(ValueError, match="contribution_source_artifact_id is required"):
        run_fec_load(inputs, MagicMock(), run_id=42)


def test_run_fec_load_defers_phase_commits_to_outer_transaction() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}

    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")) as exec_spy,
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        run_fec_load(_inputs(), conn, run_id=42)

    assert [call.kwargs["commit"] for call in exec_spy.call_args_list] == [False, False]
    conn.commit.assert_called_once_with()
    conn.rollback.assert_not_called()


def test_run_fec_load_rejects_autocommit_connection_before_phase_writes() -> None:
    conn = MagicMock()
    conn.autocommit = True
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}

    with (
        patch(f"{_MODULE}.execute_load_plan") as exec_spy,
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        with pytest.raises(RuntimeError, match="autocommit"):
            run_fec_load(_inputs(), conn, run_id=42)

    exec_spy.assert_not_called()
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


def test_run_fec_load_commit_false_defers_outer_commit() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}

    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")),
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        run_fec_load(_inputs(), conn, run_id=42, commit=False)

    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


def test_run_fec_load_final_commit_failure_rolls_back_outer_transaction() -> None:
    conn = MagicMock()
    conn.commit.side_effect = RuntimeError("commit failed")
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}

    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")),
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        with pytest.raises(RuntimeError, match="commit failed"):
            run_fec_load(_inputs(), conn, run_id=42)

    conn.commit.assert_called_once()
    conn.rollback.assert_called_once()


def test_run_fec_load_rolls_back_when_lookup_refresh_fails() -> None:
    conn = MagicMock()

    with (
        patch(f"{_MODULE}.execute_load_plan", return_value=_summary("fec_committee")),
        patch(f"{_MODULE}.load_lookup_bundle", side_effect=RuntimeError("lookup failed")),
    ):
        with pytest.raises(RuntimeError, match="lookup failed"):
            run_fec_load(_inputs(), conn, run_id=42)

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()


def test_run_fec_load_rolls_back_when_second_phase_fails() -> None:
    conn = MagicMock()
    bundle = MagicMock()
    bundle.fec_committee_map = {"C00431445": 501}

    with (
        patch(
            f"{_MODULE}.execute_load_plan",
            side_effect=[_summary("fec_committee"), RuntimeError("second phase failed")],
        ),
        patch(f"{_MODULE}.load_lookup_bundle", return_value=bundle),
    ):
        with pytest.raises(RuntimeError, match="second phase failed"):
            run_fec_load(_inputs(), conn, run_id=42)

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()
