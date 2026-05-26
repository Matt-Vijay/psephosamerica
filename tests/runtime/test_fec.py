from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import TableWriteResult, WarnErrorSummary, build_load_summary
from src.runtime.fec import FecBulkFilePaths, run_fec_local_load_runtime

_MODULE = "src.runtime.fec"


def _write_fec_files(tmp_path: Path) -> FecBulkFilePaths:
    cm = tmp_path / "cm.txt"
    cm.write_text(
        "C00431445|Rep A for Congress|Treasurer|||Detroit|MI|48201|P|H|DEM|Q|||H4MI00001\n",
        encoding="latin-1",
    )
    ccl = tmp_path / "ccl.txt"
    ccl.write_text("H4MI00001|2024|2024|C00431445|H|P|123\n", encoding="latin-1")
    indiv = tmp_path / "itcont.txt"
    indiv.write_text(
        "C00431445|N|Q1|P|IMG|10|IND|SMITH, JANE|Detroit|MI|48201|Solar Co|Engineer|05012024|2500|||4073020241987654321|||4073020241987654321\n",
        encoding="latin-1",
    )
    return FecBulkFilePaths(
        committee_master=cm,
        candidate_committee_linkage=ccl,
        individual_contributions=indiv,
    )


def _load_summary(run_id: int):
    return build_load_summary(
        [
            TableWriteResult(table="fec_committee", inserted=1),
            TableWriteResult(table="contribution", inserted=1),
            TableWriteResult(table="fec_candidate_committee_linkage", inserted=1),
        ],
        warn_error=WarnErrorSummary(),
        run_id=run_id,
    )


def _phase_summary(run_id: int):
    return build_load_summary(
        [
            TableWriteResult(table="fec_committee", inserted=1),
            TableWriteResult(table="fec_candidate_committee_linkage", inserted=1),
        ],
        warn_error=WarnErrorSummary(),
        run_id=run_id,
    )


def _contribution_summary(run_id: int):
    return build_load_summary(
        [TableWriteResult(table="contribution", inserted=1)],
        warn_error=WarnErrorSummary(),
        run_id=run_id,
    )


def test_run_fec_local_load_runtime_parses_artifacts_and_delegates(tmp_path: Path) -> None:
    files = _write_fec_files(tmp_path)
    conn = MagicMock()
    artifact_rows = [{"id": 10}, {"id": 11}, {"id": 12}]

    with (
        patch(
            f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "fec-bulk"}
        ) as ensure,
        patch(f"{_MODULE}.start_ingestion_run", return_value=77) as start,
        patch(f"{_MODULE}.create_source_artifact", side_effect=artifact_rows) as create,
        patch(
            f"{_MODULE}.run_fec_load",
            side_effect=[_phase_summary(77), _contribution_summary(77)],
        ) as load,
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
    ):
        result = run_fec_local_load_runtime(conn, files)

    ensure.assert_called_once_with(
        conn,
        slug="fec-bulk",
        name="Federal Election Commission Bulk Data",
        source_kind="official",
        base_url="https://www.fec.gov",
    )
    start.assert_called_once()
    assert start.call_args.kwargs["parameters"]["stage"] == "fec_local_load"
    assert create.call_count == 3
    assert all(call.kwargs["artifact_kind"] == "txt" for call in create.call_args_list)
    assert all(
        call.kwargs["source_url"].startswith("https://www.fec.gov")
        for call in create.call_args_list
    )
    phase_inputs = load.call_args_list[0].args[0]
    contribution_inputs = load.call_args_list[1].args[0]
    assert len(phase_inputs.committees) == 1
    assert phase_inputs.contributions == []
    assert len(phase_inputs.linkages) == 1
    assert phase_inputs.committee_source_artifact_id == 10
    assert phase_inputs.contribution_source_artifact_id is None
    assert phase_inputs.linkage_source_artifact_id == 11
    assert contribution_inputs.committees == []
    assert len(contribution_inputs.contributions) == 1
    assert contribution_inputs.linkages == []
    assert contribution_inputs.contribution_source_artifact_id == 12
    assert load.call_count == 2
    finish.assert_called_once_with(conn, 77, record_count=3)
    assert result.parsed_counts == {
        "committees": 1,
        "candidate_committee_linkages": 1,
        "individual_contributions": 1,
    }
    assert result.load_summary.total_inserted == 3


def test_run_fec_local_load_runtime_marks_failed_on_load_error(tmp_path: Path) -> None:
    files = _write_fec_files(tmp_path)
    conn = MagicMock()
    artifact_rows = [{"id": 10}, {"id": 11}, {"id": 12}]

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "fec-bulk"}),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77),
        patch(f"{_MODULE}.create_source_artifact", side_effect=artifact_rows),
        patch(f"{_MODULE}.run_fec_load", side_effect=RuntimeError("load failed")),
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
        patch(f"{_MODULE}.fail_ingestion_run") as fail,
    ):
        with pytest.raises(RuntimeError, match="load failed"):
            run_fec_local_load_runtime(conn, files)

    conn.rollback.assert_called()
    fail.assert_called_once_with(conn, 77, "load failed")
    finish.assert_not_called()


def test_run_fec_local_load_runtime_rejects_boolean_source_artifact_id(
    tmp_path: Path,
) -> None:
    files = _write_fec_files(tmp_path)
    conn = MagicMock()

    with (
        patch(f"{_MODULE}.ensure_data_source", return_value={"id": 5, "slug": "fec-bulk"}),
        patch(f"{_MODULE}.start_ingestion_run", return_value=77),
        patch(f"{_MODULE}.create_source_artifact", return_value={"id": True}),
        patch(f"{_MODULE}.run_fec_load") as load,
        patch(f"{_MODULE}.finish_ingestion_run") as finish,
        patch(f"{_MODULE}.fail_ingestion_run") as fail,
    ):
        with pytest.raises(TypeError, match="source_artifact id"):
            run_fec_local_load_runtime(conn, files)

    load.assert_not_called()
    conn.rollback.assert_called()
    fail.assert_called_once_with(conn, 77, "expected source_artifact id to be int, got True")
    finish.assert_not_called()


def test_run_fec_local_load_runtime_rejects_missing_input_before_db(tmp_path: Path) -> None:
    files = FecBulkFilePaths(
        committee_master=tmp_path / "missing-cm.txt",
        candidate_committee_linkage=tmp_path / "missing-ccl.txt",
        individual_contributions=tmp_path / "missing-itcont.txt",
    )
    conn = MagicMock()

    with (
        patch(f"{_MODULE}.ensure_data_source") as ensure,
        patch(f"{_MODULE}.start_ingestion_run") as start,
    ):
        with pytest.raises(FileNotFoundError, match="missing-cm"):
            run_fec_local_load_runtime(conn, files)

    ensure.assert_not_called()
    start.assert_not_called()
