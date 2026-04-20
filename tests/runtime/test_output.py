"""Tests for src/runtime/output.py.

Pure formatting — no DB, no network, no mocks of external boundaries.
All result objects are constructed directly from their dataclasses.
"""

from __future__ import annotations

import json
import datetime as dt
from pathlib import Path

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.pipeline.publish_pipeline import PublishResult
from src.pipeline.recompute_run import RecomputeRunResult
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_artifacts import DisclosureArtifactIngestResult
from src.runtime.disclosures_bundle_process import DisclosuresBundleProcessResult
from src.runtime.disclosures_load_from_parse import DisclosuresParseLoadResult
from src.runtime.disclosures_parse import DisclosureParseRuntimeResult
from src.runtime.history_backfill import (
    CongressDateWindow,
    HistoryBackfillAttempt,
    HistoryBackfillExecutionResult,
    HistoryBackfillPlan,
    HistoricalSnapshotTarget,
    LocalHistoryAggregateSummary,
    LocalHistoryBackfillResult,
)
from src.runtime.oracle_contracts import CongressStageSummary, LocalOracleRunResult
from src.runtime.output import (
    as_json,
    summarize_disclosure_artifact_ingest_result,
    summarize_history_verify_result,
    summarize_disclosures_bundle_process_result,
    summarize_local_history_backfill_result,
    summarize_load_result,
    summarize_local_oracle_run_result,
    summarize_parse_disclosures_result,
    summarize_process_disclosures_result,
    summarize_publish_result,
    summarize_publish_roundtrip_result,
    summarize_publish_verify_result,
    summarize_recompute_result,
    summarize_status,
)
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.history_verify_types import (
    HistoryVerifyIssue,
    HistoryVerifyResult,
    HistoryVerifyStageResult,
)
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)
from src.runtime.publish import PublishRuntimeResult
from src.runtime.recompute import RuntimeRecomputeResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DATA_SOURCE = {"id": 1, "slug": "congress-gov-api", "name": "Congress.gov API"}
_RUN_ID = 42


def _warn_error(warnings: int = 0, errors: int = 0) -> WarnErrorSummary:
    we = WarnErrorSummary()
    for i in range(warnings):
        we.add_warning(f"warn {i}")
    for i in range(errors):
        we.add_error(f"err {i}")
    return we


def _load_summary(
    tables: list[TableWriteResult] | None = None,
    warn_error: WarnErrorSummary | None = None,
    run_id: int = _RUN_ID,
) -> LoadSummary:
    return build_load_summary(
        tables or [],
        warn_error=warn_error or WarnErrorSummary(),
        run_id=run_id,
    )


def _congress_result(
    load_summary: LoadSummary | None = None,
) -> CongressLoadResult:
    return CongressLoadResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        load_summary=load_summary or _load_summary(),
    )


def _disclosures_result(
    load_summary: LoadSummary | None = None,
) -> DisclosuresLoadRuntimeResult:
    return DisclosuresLoadRuntimeResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        load_summary=load_summary or _load_summary(),
        sidecars=(),
    )


def _recompute_result(
    rule_fires: list | None = None,
    evidence_cards: list | None = None,
    load_summary: LoadSummary | None = None,
) -> RuntimeRecomputeResult:
    inner = RecomputeRunResult(
        rule_fires=rule_fires or [],
        evidence_cards=evidence_cards or [],
        load_summary=load_summary,
    )
    return RuntimeRecomputeResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        recompute_result=inner,
    )


def _publish_result(
    snapshot_id: str = "2024-06-01",
    planned: int = 10,
    written: int = 10,
    failures: list[str] | None = None,
) -> PublishRuntimeResult:
    from src.pipeline.stages import PipelineResult

    pub = PublishResult(
        snapshot_id=snapshot_id,
        planned_count=planned,
        written_count=written,
        verification_failures=failures or [],
        pipeline_result=PipelineResult(),
    )
    return PublishRuntimeResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        snapshot_id=snapshot_id,
        publish_result=pub,
    )


# ---------------------------------------------------------------------------
# as_json
# ---------------------------------------------------------------------------


class TestAsJson:
    def test_basic_dict_is_valid_json(self):
        raw = as_json({"key": "value", "n": 1})
        obj = json.loads(raw)
        assert obj == {"key": "value", "n": 1}

    def test_date_serialised_as_string(self):
        d = dt.date(2024, 1, 15)
        raw = as_json({"d": d})
        obj = json.loads(raw)
        assert obj["d"] == "2024-01-15"

    def test_path_serialised_as_string(self):
        p = Path("/tmp/snapshot")
        raw = as_json({"p": p})
        obj = json.loads(raw)
        assert obj["p"] == "/tmp/snapshot"

    def test_indent_produces_multiline(self):
        raw = as_json({"a": 1}, indent=2)
        assert "\n" in raw

    def test_no_indent_produces_compact(self):
        raw = as_json({"a": 1})
        assert "\n" not in raw

    def test_list_of_dicts_round_trips(self):
        data = [{"id": 1}, {"id": 2}]
        assert json.loads(as_json(data)) == data


# ---------------------------------------------------------------------------
# summarize_load_result — CongressLoadResult
# ---------------------------------------------------------------------------


class TestSummarizeLoadResultCongress:
    def test_run_id_present(self):
        result = summarize_load_result(_congress_result())
        assert result["run_id"] == _RUN_ID

    def test_data_source_slug(self):
        result = summarize_load_result(_congress_result())
        assert result["data_source"] == "congress-gov-api"

    def test_ok_true_when_no_errors(self):
        result = summarize_load_result(_congress_result())
        assert result["ok"] is True

    def test_ok_false_when_errors_present(self):
        we = _warn_error(errors=1)
        cr = _congress_result(load_summary=_load_summary(warn_error=we))
        result = summarize_load_result(cr)
        assert result["ok"] is False

    def test_counts_reflect_table_results(self):
        tables = [
            TableWriteResult(table="member", inserted=5, updated=2, skipped=1, rejected=0),
            TableWriteResult(table="bill", inserted=10, updated=0, skipped=0, rejected=1),
        ]
        cr = _congress_result(load_summary=_load_summary(tables=tables))
        result = summarize_load_result(cr)
        assert result["counts"]["inserted"] == 15
        assert result["counts"]["updated"] == 2
        assert result["counts"]["skipped"] == 1
        assert result["counts"]["rejected"] == 1

    def test_tables_list_length(self):
        tables = [
            TableWriteResult(table="member", inserted=1),
            TableWriteResult(table="committee", inserted=2),
        ]
        cr = _congress_result(load_summary=_load_summary(tables=tables))
        result = summarize_load_result(cr)
        assert len(result["tables"]) == 2

    def test_table_entry_has_expected_keys(self):
        tables = [TableWriteResult(table="vote_event", inserted=3)]
        cr = _congress_result(load_summary=_load_summary(tables=tables))
        entry = summarize_load_result(cr)["tables"][0]
        assert set(entry.keys()) == {"table", "inserted", "updated", "skipped", "rejected"}

    def test_warnings_count(self):
        we = _warn_error(warnings=3)
        cr = _congress_result(load_summary=_load_summary(warn_error=we))
        result = summarize_load_result(cr)
        assert result["warnings"] == 3

    def test_errors_count(self):
        we = _warn_error(errors=2)
        cr = _congress_result(load_summary=_load_summary(warn_error=we))
        result = summarize_load_result(cr)
        assert result["errors"] == 2

    def test_zero_counts_when_empty(self):
        result = summarize_load_result(_congress_result())
        c = result["counts"]
        assert c == {"inserted": 0, "updated": 0, "skipped": 0, "rejected": 0}

    def test_exact_top_level_keys(self):
        result = summarize_load_result(_congress_result())
        assert set(result.keys()) == {
            "run_id", "data_source", "ok", "counts", "warnings", "errors", "tables",
        }

    def test_counts_sub_keys(self):
        result = summarize_load_result(_congress_result())
        assert set(result["counts"].keys()) == {"inserted", "updated", "skipped", "rejected"}

    def test_json_serializable(self):
        tables = [TableWriteResult(table="member", inserted=5, updated=2, skipped=1, rejected=0)]
        cr = _congress_result(load_summary=_load_summary(tables=tables, warn_error=_warn_error(warnings=1)))
        raw = as_json(summarize_load_result(cr))
        obj = json.loads(raw)
        assert obj["run_id"] == _RUN_ID
        assert obj["ok"] is True


# ---------------------------------------------------------------------------
# summarize_load_result — DisclosuresLoadRuntimeResult
# ---------------------------------------------------------------------------


class TestSummarizeLoadResultDisclosures:
    def test_run_id_present(self):
        result = summarize_load_result(_disclosures_result())
        assert result["run_id"] == _RUN_ID

    def test_data_source_slug(self):
        result = summarize_load_result(_disclosures_result())
        assert result["data_source"] == "congress-gov-api"

    def test_counts_from_disclosures_load(self):
        tables = [TableWriteResult(table="holding", inserted=7, updated=1)]
        dr = _disclosures_result(load_summary=_load_summary(tables=tables))
        result = summarize_load_result(dr)
        assert result["counts"]["inserted"] == 7
        assert result["counts"]["updated"] == 1


# ---------------------------------------------------------------------------
# summarize_recompute_result
# ---------------------------------------------------------------------------


class TestSummarizeRecomputeResult:
    def test_run_id_present(self):
        result = summarize_recompute_result(_recompute_result())
        assert result["run_id"] == _RUN_ID

    def test_data_source_slug(self):
        result = summarize_recompute_result(_recompute_result())
        assert result["data_source"] == "congress-gov-api"

    def test_rule_fires_count(self):
        result = summarize_recompute_result(_recompute_result(rule_fires=["a", "b", "c"]))
        assert result["rule_fires"] == 3

    def test_evidence_cards_count(self):
        result = summarize_recompute_result(_recompute_result(evidence_cards=["x"]))
        assert result["evidence_cards"] == 1

    def test_zero_counts_when_empty(self):
        result = summarize_recompute_result(_recompute_result())
        assert result["rule_fires"] == 0
        assert result["evidence_cards"] == 0

    def test_exact_keys_without_load(self):
        result = summarize_recompute_result(_recompute_result(load_summary=None))
        assert set(result.keys()) == {"run_id", "data_source", "rule_fires", "evidence_cards"}

    def test_exact_keys_with_load(self):
        ls = _load_summary(tables=[TableWriteResult(table="rule_fire", inserted=1)])
        result = summarize_recompute_result(_recompute_result(load_summary=ls))
        assert set(result.keys()) == {"run_id", "data_source", "rule_fires", "evidence_cards", "load"}

    def test_json_serializable(self):
        result = summarize_recompute_result(_recompute_result(rule_fires=["a"], evidence_cards=["b"]))
        obj = json.loads(as_json(result))
        assert obj["rule_fires"] == 1
        assert obj["evidence_cards"] == 1

    def test_load_key_absent_when_no_load_summary(self):
        result = summarize_recompute_result(_recompute_result(load_summary=None))
        assert "load" not in result

    def test_load_key_present_when_load_summary_provided(self):
        ls = _load_summary(tables=[TableWriteResult(table="rule_fire", inserted=2)])
        result = summarize_recompute_result(_recompute_result(load_summary=ls))
        assert "load" in result
        assert result["load"]["counts"]["inserted"] == 2


# ---------------------------------------------------------------------------
# summarize_publish_result
# ---------------------------------------------------------------------------


class TestSummarizePublishResult:
    def test_run_id_present(self):
        result = summarize_publish_result(_publish_result())
        assert result["run_id"] == _RUN_ID

    def test_snapshot_id_forwarded(self):
        result = summarize_publish_result(_publish_result(snapshot_id="2024-01-01"))
        assert result["snapshot_id"] == "2024-01-01"

    def test_data_source_slug(self):
        result = summarize_publish_result(_publish_result())
        assert result["data_source"] == "congress-gov-api"

    def test_planned_and_written_counts(self):
        result = summarize_publish_result(_publish_result(planned=20, written=18))
        assert result["planned"] == 20
        assert result["written"] == 18

    def test_succeeded_true_when_no_failures(self):
        result = summarize_publish_result(_publish_result(failures=[]))
        assert result["succeeded"] is True

    def test_succeeded_false_when_failures_present(self):
        result = summarize_publish_result(_publish_result(failures=["hash mismatch: foo.json"]))
        assert result["succeeded"] is False

    def test_verification_failures_forwarded(self):
        failures = ["hash mismatch: a.json", "missing: b.json"]
        result = summarize_publish_result(_publish_result(failures=failures))
        assert result["verification_failures"] == failures

    def test_empty_verification_failures(self):
        result = summarize_publish_result(_publish_result())
        assert result["verification_failures"] == []

    def test_exact_keys(self):
        result = summarize_publish_result(_publish_result())
        assert set(result.keys()) == {
            "run_id", "snapshot_id", "data_source", "planned", "written",
            "succeeded", "verification_failures",
        }

    def test_json_serializable(self):
        result = summarize_publish_result(_publish_result(planned=5, written=4, failures=["x"]))
        obj = json.loads(as_json(result))
        assert obj["planned"] == 5
        assert obj["succeeded"] is False
        assert obj["verification_failures"] == ["x"]


# ---------------------------------------------------------------------------
# summarize_status
# ---------------------------------------------------------------------------

_STATUS_INGESTION_ROW = {
    "id": 10,
    "run_type": "ingest",
    "status": "succeeded",
    "data_source_slug": "congress-gov-api",
}

_STATUS_PARSE_ROW = {
    "id": 2,
    "parser_name": "senate_pdf",
    "status": "succeeded",
}

_STATUS_DS_ROW = {
    "id": 1,
    "slug": "congress-gov-api",
    "active": True,
}


def _runtime_status(
    ingestion_runs: list | None = None,
    parse_runs: list | None = None,
    data_sources: list | None = None,
    source_artifacts: list | None = None,
) -> dict:
    runs = ingestion_runs or []
    parses = parse_runs or []
    sources = data_sources or []
    artifacts = source_artifacts or []
    return {
        "ingestion_runs": runs,
        "parse_runs": parses,
        "data_sources": sources,
        "source_artifacts": artifacts,
        "summary": {
            "ingestion_run_count": len(runs),
            "parse_run_count": len(parses),
            "data_source_count": len(sources),
            "source_artifact_count": len(artifacts),
        },
    }


class TestSummarizeStatus:
    def test_summary_key_present(self):
        result = summarize_status(_runtime_status())
        assert "summary" in result

    def test_data_source_count_forwarded(self):
        status = _runtime_status(data_sources=[_STATUS_DS_ROW, _STATUS_DS_ROW])
        result = summarize_status(status)
        assert result["summary"]["data_source_count"] == 2

    def test_latest_ingestion_run_present(self):
        status = _runtime_status(ingestion_runs=[_STATUS_INGESTION_ROW])
        result = summarize_status(status)
        assert "latest_ingestion_run" in result

    def test_latest_artifact_present(self):
        artifact = {"id": 4, "artifact_kind": "pdf", "data_source_slug": "house-disclosures"}
        status = _runtime_status(source_artifacts=[artifact])
        result = summarize_status(status)
        assert result["latest_artifact"]["id"] == 4

    def test_latest_ingestion_run_fields(self):
        status = _runtime_status(ingestion_runs=[_STATUS_INGESTION_ROW])
        run = summarize_status(status)["latest_ingestion_run"]
        assert run["id"] == 10
        assert run["run_type"] == "ingest"
        assert run["status"] == "succeeded"
        assert run["data_source"] == "congress-gov-api"

    def test_latest_ingestion_run_none_when_empty(self):
        status = _runtime_status(ingestion_runs=[])
        run = summarize_status(status)["latest_ingestion_run"]
        assert run["id"] is None
        assert run["status"] is None

    def test_returns_first_ingestion_run_as_latest(self):
        older = {**_STATUS_INGESTION_ROW, "id": 5}
        newer = {**_STATUS_INGESTION_ROW, "id": 99}
        status = _runtime_status(ingestion_runs=[newer, older])
        result = summarize_status(status)
        assert result["latest_ingestion_run"]["id"] == 99

    def test_summary_counts_present(self):
        status = _runtime_status(
            ingestion_runs=[_STATUS_INGESTION_ROW],
            data_sources=[_STATUS_DS_ROW],
        )
        result = summarize_status(status)
        assert result["summary"]["ingestion_run_count"] == 1
        assert result["summary"]["data_source_count"] == 1

    def test_exact_top_level_keys(self):
        result = summarize_status(_runtime_status())
        assert set(result.keys()) == {"summary", "latest_ingestion_run", "latest_artifact"}

    def test_latest_ingestion_run_exact_keys(self):
        status = _runtime_status(ingestion_runs=[_STATUS_INGESTION_ROW])
        run = summarize_status(status)["latest_ingestion_run"]
        assert set(run.keys()) == {"id", "run_type", "status", "data_source"}

    def test_latest_artifact_exact_keys(self):
        artifact = {"id": 4, "artifact_kind": "pdf", "data_source_slug": "house-disclosures"}
        status = _runtime_status(source_artifacts=[artifact])
        art = summarize_status(status)["latest_artifact"]
        assert set(art.keys()) == {"id", "artifact_kind", "data_source"}

    def test_json_serializable(self):
        status = _runtime_status(
            ingestion_runs=[_STATUS_INGESTION_ROW],
            data_sources=[_STATUS_DS_ROW],
        )
        obj = json.loads(as_json(summarize_status(status)))
        assert obj["latest_ingestion_run"]["id"] == 10

    def test_pure_same_inputs_same_output(self):
        status = _runtime_status(
            ingestion_runs=[_STATUS_INGESTION_ROW],
            data_sources=[_STATUS_DS_ROW],
        )
        r1 = summarize_status(status)
        r2 = summarize_status(status)
        assert r1 == r2


# ---------------------------------------------------------------------------
# summarize_disclosure_artifact_ingest_result
# ---------------------------------------------------------------------------


def _artifact_ingest_result(
    discovered: int = 5,
    stored: int = 4,
    local_root: str | None = None,
) -> DisclosureArtifactIngestResult:
    return DisclosureArtifactIngestResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        chamber="senate",
        year=2024,
        filing_kind=None,
        local_root=Path(local_root) if local_root is not None else None,
        discovered_count=discovered,
        stored_count=stored,
        artifact_rows=(),
    )


class TestSummarizeDisclosureArtifactIngestResult:
    def test_run_id_present(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result())
        assert result["run_id"] == _RUN_ID

    def test_data_source_slug(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result())
        assert result["data_source"] == "congress-gov-api"

    def test_discovered_forwarded(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result(discovered=12))
        assert result["discovered"] == 12

    def test_stored_forwarded(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result(stored=9))
        assert result["stored"] == 9

    def test_local_root_absent_when_none(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result(local_root=None))
        assert "local_root" not in result

    def test_local_root_present_when_given(self):
        result = summarize_disclosure_artifact_ingest_result(
            _artifact_ingest_result(local_root="/data/disclosures/2024")
        )
        assert result["local_root"] == "/data/disclosures/2024"

    def test_local_root_coerced_to_str_from_path(self):
        r = DisclosureArtifactIngestResult(
            data_source=_DATA_SOURCE,
            run_id=_RUN_ID,
            chamber="house",
            year=2024,
            filing_kind="ptr",
            local_root=Path("/tmp/artifacts"),
            discovered_count=3,
            stored_count=3,
            artifact_rows=(),
        )
        result = summarize_disclosure_artifact_ingest_result(r)
        assert result["local_root"] == "/tmp/artifacts"

    def test_zero_counts(self):
        result = summarize_disclosure_artifact_ingest_result(
            _artifact_ingest_result(discovered=0, stored=0)
        )
        assert result["discovered"] == 0
        assert result["stored"] == 0

    def test_keys_without_local_root(self):
        result = summarize_disclosure_artifact_ingest_result(_artifact_ingest_result())
        assert set(result.keys()) == {"run_id", "data_source", "discovered", "stored"}

    def test_keys_with_local_root(self):
        result = summarize_disclosure_artifact_ingest_result(
            _artifact_ingest_result(local_root="/data")
        )
        assert set(result.keys()) == {"run_id", "data_source", "discovered", "stored", "local_root"}


# ---------------------------------------------------------------------------
# summarize_parse_disclosures_result
# ---------------------------------------------------------------------------


def _parse_disclosures_result(
    processed: int = 10,
    succeeded: int = 8,
    failed: int = 2,
    parse_sessions: tuple = (),
) -> DisclosureParseRuntimeResult:
    return DisclosureParseRuntimeResult(
        processed_count=processed,
        succeeded_count=succeeded,
        failed_count=failed,
        parse_sessions=parse_sessions,
        parsed_documents=(),
        failed_artifact_ids=(),
    )


class TestSummarizeParseDisclosuresResult:
    def test_processed_forwarded(self):
        result = summarize_parse_disclosures_result(_parse_disclosures_result(processed=12))
        assert result["processed"] == 12

    def test_succeeded_forwarded(self):
        result = summarize_parse_disclosures_result(_parse_disclosures_result(succeeded=9))
        assert result["succeeded"] == 9

    def test_failed_forwarded(self):
        result = summarize_parse_disclosures_result(_parse_disclosures_result(failed=3))
        assert result["failed"] == 3

    def test_zero_counts(self):
        result = summarize_parse_disclosures_result(
            _parse_disclosures_result(processed=0, succeeded=0, failed=0)
        )
        assert result["processed"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0

    def test_keys_are_compact(self):
        result = summarize_parse_disclosures_result(_parse_disclosures_result())
        assert set(result.keys()) == {"processed", "succeeded", "failed"}

    def test_parse_sessions_not_in_summary(self):
        result = summarize_parse_disclosures_result(_parse_disclosures_result())
        assert "parse_sessions" not in result

    def test_pure_same_inputs_same_output(self):
        r = _parse_disclosures_result(processed=5, succeeded=4, failed=1)
        assert summarize_parse_disclosures_result(r) == summarize_parse_disclosures_result(r)


# ---------------------------------------------------------------------------
# summarize_process_disclosures_result
# ---------------------------------------------------------------------------


def _process_disclosures_result(
    parse_processed: int = 10,
    parse_succeeded: int = 8,
    parse_failed: int = 2,
    transform_count: int = 6,
    skipped_transform_count: int = 0,
    load_tables: list | None = None,
) -> DisclosuresParseLoadResult:
    parse = DisclosureParseRuntimeResult(
        processed_count=parse_processed,
        succeeded_count=parse_succeeded,
        failed_count=parse_failed,
        parse_sessions=(),
        parsed_documents=(),
        failed_artifact_ids=(),
    )
    load = DisclosuresLoadRuntimeResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        load_summary=_load_summary(tables=load_tables or []),
        sidecars=(),
    )
    return DisclosuresParseLoadResult(
        parse_result=parse,
        transform_count=transform_count,
        skipped_transform_count=skipped_transform_count,
        skipped_sessions=(),
        load_result=load,
    )


class TestSummarizeProcessDisclosuresResult:
    def test_has_parse_key(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert "parse" in result

    def test_has_load_key(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert "load" in result

    def test_has_transformed_key(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert "transformed" in result

    def test_keys_are_exactly_parse_transformed_and_load(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert set(result.keys()) == {"parse", "transformed", "load"}

    def test_parse_processed_forwarded(self):
        result = summarize_process_disclosures_result(_process_disclosures_result(parse_processed=15))
        assert result["parse"]["processed"] == 15

    def test_parse_succeeded_forwarded(self):
        result = summarize_process_disclosures_result(_process_disclosures_result(parse_succeeded=7))
        assert result["parse"]["succeeded"] == 7

    def test_parse_failed_forwarded(self):
        result = summarize_process_disclosures_result(_process_disclosures_result(parse_failed=3))
        assert result["parse"]["failed"] == 3

    def test_load_run_id_forwarded(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert result["load"]["run_id"] == _RUN_ID

    def test_transform_count_forwarded(self):
        result = summarize_process_disclosures_result(
            _process_disclosures_result(transform_count=4)
        )
        assert result["transformed"] == 4

    def test_load_data_source_slug_forwarded(self):
        result = summarize_process_disclosures_result(_process_disclosures_result())
        assert result["load"]["data_source"] == "congress-gov-api"

    def test_load_counts_forwarded(self):
        tables = [TableWriteResult(table="financial_disclosure", inserted=5, updated=2)]
        result = summarize_process_disclosures_result(
            _process_disclosures_result(load_tables=tables)
        )
        assert result["load"]["counts"]["inserted"] == 5
        assert result["load"]["counts"]["updated"] == 2

    def test_zero_parse_counts(self):
        result = summarize_process_disclosures_result(
            _process_disclosures_result(parse_processed=0, parse_succeeded=0, parse_failed=0)
        )
        assert result["parse"]["processed"] == 0
        assert result["parse"]["succeeded"] == 0
        assert result["parse"]["failed"] == 0

    def test_pure_same_inputs_same_output(self):
        r = _process_disclosures_result(parse_processed=5)
        assert summarize_process_disclosures_result(r) == summarize_process_disclosures_result(r)


# ---------------------------------------------------------------------------
# summarize_disclosures_bundle_process_result
# ---------------------------------------------------------------------------


def _bundle_process_result(
    parse_processed: int = 10,
    parse_succeeded: int = 8,
    parse_failed: int = 2,
    transform_count: int = 6,
    load_tables: list | None = None,
) -> DisclosuresBundleProcessResult:
    from unittest.mock import MagicMock
    parse = DisclosureParseRuntimeResult(
        processed_count=parse_processed,
        succeeded_count=parse_succeeded,
        failed_count=parse_failed,
        parse_sessions=(),
        parsed_documents=(),
        failed_artifact_ids=(),
    )
    load = DisclosuresLoadRuntimeResult(
        data_source={"id": 1, "slug": "financial-disclosures"},
        run_id=42,
        load_summary=build_load_summary(
            load_tables or [],
            warn_error=WarnErrorSummary(),
            run_id=42,
        ),
        sidecars=(),
    )
    stage_result = MagicMock()
    return DisclosuresBundleProcessResult(
        stage_result=stage_result,
        parse_result=parse,
        transform_count=transform_count,
        skipped_transform_count=0,
        skipped_sessions=(),
        load_result=load,
    )


class TestSummarizeDisclosuresBundleProcessResult:
    def test_has_parse_key(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        assert "parse" in result

    def test_has_transformed_key(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        assert "transformed" in result

    def test_has_load_key(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        assert "load" in result

    def test_exact_keys(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        assert set(result.keys()) == {"parse", "transformed", "load"}

    def test_parse_counts_forwarded(self):
        result = summarize_disclosures_bundle_process_result(
            _bundle_process_result(parse_processed=12, parse_succeeded=10, parse_failed=2)
        )
        assert result["parse"]["processed"] == 12
        assert result["parse"]["succeeded"] == 10
        assert result["parse"]["failed"] == 2

    def test_transform_count_forwarded(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result(transform_count=4))
        assert result["transformed"] == 4

    def test_load_run_id_forwarded(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        assert result["load"]["run_id"] == 42

    def test_load_counts_forwarded(self):
        tables = [TableWriteResult(table="financial_disclosure", inserted=5, updated=2)]
        result = summarize_disclosures_bundle_process_result(_bundle_process_result(load_tables=tables))
        assert result["load"]["counts"]["inserted"] == 5
        assert result["load"]["counts"]["updated"] == 2

    def test_zero_counts(self):
        result = summarize_disclosures_bundle_process_result(
            _bundle_process_result(parse_processed=0, parse_succeeded=0, parse_failed=0, transform_count=0)
        )
        assert result["parse"]["processed"] == 0
        assert result["transformed"] == 0

    def test_json_serializable(self):
        result = summarize_disclosures_bundle_process_result(_bundle_process_result())
        obj = json.loads(as_json(result))
        assert obj["transformed"] == 6

    def test_pure_same_inputs_same_output(self):
        r = _bundle_process_result(parse_processed=5)
        r1 = summarize_disclosures_bundle_process_result(r)
        r2 = summarize_disclosures_bundle_process_result(r)
        assert r1 == r2


# ---------------------------------------------------------------------------
# summarize_local_oracle_run_result
# ---------------------------------------------------------------------------


def _congress_stage_summary() -> CongressStageSummary:
    return CongressStageSummary(
        run_id=1,
        source_slug="congress-gov-api",
        total_inserted=50,
        total_written=50,
        load_ok=True,
    )


def _local_oracle_run_result(
    snapshot_id: str = "2025-01-15",
    congress: CongressStageSummary | None = None,
    disclosures: dict | None = None,
    recompute: dict | None = None,
    publish: dict | None = None,
    verify: PublishVerifyResult | None = None,
    roundtrip: PublishRoundtripResult | None = None,
) -> LocalOracleRunResult:
    return LocalOracleRunResult(
        snapshot_id=snapshot_id,
        congress=congress or _congress_stage_summary(),
        disclosures=disclosures or {"run_id": 1, "source_slug": "financial-disclosures", "load_ok": True},
        recompute=recompute or {"run_id": 2, "rule_fires": 3, "evidence_cards": 1},
        publish=publish or {"run_id": 3, "snapshot_id": snapshot_id, "succeeded": True},
        verify=verify or _verify_result(),
        roundtrip=roundtrip or PublishRoundtripResult(stages=()),
    )


class TestSummarizeLocalOracleRunResult:
    def test_snapshot_id_forwarded(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result(snapshot_id="2026-01-01"))
        assert result["snapshot_id"] == "2026-01-01"

    def test_disclosures_key_present(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert "disclosures" in result

    def test_recompute_key_present(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert "recompute" in result

    def test_publish_key_present(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert "publish" in result

    def test_verify_key_present(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert "verify" in result

    def test_roundtrip_key_present(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert "roundtrip" in result

    def test_exact_keys(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert set(result.keys()) == {
            "snapshot_id",
            "congress",
            "disclosures",
            "recompute",
            "publish",
            "verify",
            "roundtrip",
        }

    def test_congress_stage_forwarded(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert result["congress"]["run_id"] == 1
        assert result["congress"]["source_slug"] == "congress-gov-api"
        assert result["congress"]["load_ok"] is True

    def test_disclosures_content_preserved(self):
        disclosures = {"run_id": 5, "source_slug": "financial-disclosures", "load_ok": True}
        result = summarize_local_oracle_run_result(_local_oracle_run_result(disclosures=disclosures))
        assert result["disclosures"]["run_id"] == 5
        assert result["disclosures"]["load_ok"] is True

    def test_recompute_rule_fires_preserved(self):
        recompute = {"run_id": 7, "rule_fires": 9, "evidence_cards": 4}
        result = summarize_local_oracle_run_result(_local_oracle_run_result(recompute=recompute))
        assert result["recompute"]["rule_fires"] == 9

    def test_publish_succeeded_preserved(self):
        publish = {"run_id": 3, "snapshot_id": "2025-01-15", "succeeded": False}
        result = summarize_local_oracle_run_result(_local_oracle_run_result(publish=publish))
        assert result["publish"]["succeeded"] is False

    def test_verify_summary_preserved(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert result["verify"]["ok"] is True
        assert result["verify"]["total_checked"] == 21

    def test_roundtrip_summary_preserved(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        assert result["roundtrip"]["ok"] is True
        assert result["roundtrip"]["total_checked"] == 0

    def test_json_serializable(self):
        result = summarize_local_oracle_run_result(_local_oracle_run_result())
        obj = json.loads(as_json(result))
        assert obj["snapshot_id"] == "2025-01-15"
        assert obj["recompute"]["rule_fires"] == 3
        assert obj["congress"]["load_ok"] is True
        assert obj["verify"]["ok"] is True
        assert obj["roundtrip"]["ok"] is True

    def test_pure_same_inputs_same_output(self):
        r = _local_oracle_run_result()
        r1 = summarize_local_oracle_run_result(r)
        r2 = summarize_local_oracle_run_result(r)
        assert r1 == r2


def _history_backfill_result() -> LocalHistoryBackfillResult:
    plan = HistoryBackfillPlan(
        congress=119,
        date_window=CongressDateWindow(
            congress=119,
            start_date=dt.date(2025, 1, 3),
            end_date=dt.date(2025, 1, 13),
            bounded_by_today=True,
        ),
        cadence="weekly:monday",
        targets=[
            HistoricalSnapshotTarget(
                congress=119,
                snapshot_date=dt.date(2025, 1, 6),
                snapshot_id="2025-01-06",
                publish_root=Path("/tmp/history/2025-01-06"),
            ),
            HistoricalSnapshotTarget(
                congress=119,
                snapshot_date=dt.date(2025, 1, 13),
                snapshot_id="2025-01-13",
                publish_root=Path("/tmp/history/2025-01-13"),
            ),
        ],
    )
    execution = HistoryBackfillExecutionResult(
        plan=plan,
        attempts=[
            HistoryBackfillAttempt(
                snapshot_id="2025-01-06",
                snapshot_date=dt.date(2025, 1, 6),
                publish_root=Path("/tmp/history/2025-01-06"),
                status="completed",
            ),
            HistoryBackfillAttempt(
                snapshot_id="2025-01-13",
                snapshot_date=dt.date(2025, 1, 13),
                publish_root=Path("/tmp/history/2025-01-13"),
                status="skipped_existing",
                reason="existing_manifest",
            ),
        ],
    )
    return LocalHistoryBackfillResult(
        execution=execution,
        snapshot_results={
            "2025-01-06": _local_oracle_run_result(snapshot_id="2025-01-06"),
        },
        aggregate_source_roots=[
            Path("/tmp/history/2025-01-06"),
            Path("/tmp/history/2025-01-13"),
        ],
        target_root=Path("/tmp/history"),
        overwrite=False,
        continue_on_error=True,
        aggregate=LocalHistoryAggregateSummary(
            latest_snapshot_id="2025-01-13",
            snapshot_count=2,
            member_history_count=5,
            target_root=Path("/tmp/history-aggregate"),
            verify=HistoryVerifyResult(
                stages=(
                    HistoryVerifyStageResult(stage="snapshot_index", checked=2, issues=()),
                )
            ),
        ),
    )


class TestSummarizeLocalHistoryBackfillResult:
    def test_includes_plan_and_execution_policy(self) -> None:
        result = summarize_local_history_backfill_result(_history_backfill_result())
        assert result["congress"] == 119
        assert result["cadence"] == "weekly:monday"
        assert result["target_root"] == "/tmp/history"
        assert result["overwrite"] is False
        assert result["continue_on_error"] is True

    def test_attempts_include_structural_reason_and_nested_oracle_summary(self) -> None:
        result = summarize_local_history_backfill_result(_history_backfill_result())
        assert result["attempts"][0]["ok"] is True
        assert result["attempts"][0]["oracle"]["snapshot_id"] == "2025-01-06"
        assert result["attempts"][1]["ok"] is None
        assert result["attempts"][1]["reason"] == "existing_manifest"

    def test_aggregate_summary_and_counts_forwarded(self) -> None:
        result = summarize_local_history_backfill_result(_history_backfill_result())
        assert result["planned_count"] == 2
        assert result["attempted_count"] == 2
        assert result["completed_count"] == 1
        assert result["aggregate_source_count"] == 2
        assert result["aggregate"]["member_history_count"] == 5
        assert result["aggregate"]["verify"]["ok"] is True


# ---------------------------------------------------------------------------
# summarize_publish_verify_result
# ---------------------------------------------------------------------------


def _verify_stage(
    stage: str = "manifest",
    checked: int = 5,
    issues: tuple[PublishVerifyIssue, ...] = (),
) -> PublishVerifyStageResult:
    return PublishVerifyStageResult(stage=stage, checked=checked, issues=issues)


def _verify_result(
    stages: tuple[PublishVerifyStageResult, ...] | None = None,
) -> PublishVerifyResult:
    if stages is None:
        stages = (
            _verify_stage("manifest", checked=3),
            _verify_stage("profiles", checked=10),
            _verify_stage("evidence", checked=7),
            _verify_stage("zip", checked=1),
        )
    return PublishVerifyResult(stages=stages)


class TestSummarizePublishVerifyResult:
    def test_ok_true_when_no_errors(self):
        result = summarize_publish_verify_result(_verify_result())
        assert result["ok"] is True

    def test_ok_false_when_error_present(self):
        issue = PublishVerifyIssue(stage="manifest", message="missing entry", severity="error")
        stage = _verify_stage("manifest", checked=1, issues=(issue,))
        result = summarize_publish_verify_result(PublishVerifyResult(stages=(stage,)))
        assert result["ok"] is False

    def test_total_checked_sums_stages(self):
        result = summarize_publish_verify_result(_verify_result())
        # 3 + 10 + 7 + 1 = 21
        assert result["total_checked"] == 21

    def test_total_errors_zero_when_clean(self):
        result = summarize_publish_verify_result(_verify_result())
        assert result["total_errors"] == 0

    def test_total_errors_counts_across_stages(self):
        err1 = PublishVerifyIssue(stage="profiles", message="bad hash", severity="error")
        err2 = PublishVerifyIssue(stage="evidence", message="missing file", severity="error")
        warn = PublishVerifyIssue(stage="zip", message="extra file", severity="warning")
        stages = (
            _verify_stage("profiles", checked=5, issues=(err1,)),
            _verify_stage("evidence", checked=3, issues=(err2,)),
            _verify_stage("zip", checked=1, issues=(warn,)),
        )
        result = summarize_publish_verify_result(PublishVerifyResult(stages=stages))
        assert result["total_errors"] == 2

    def test_total_warnings_counted(self):
        warn = PublishVerifyIssue(stage="manifest", message="extra file", severity="warning")
        stage = _verify_stage("manifest", checked=2, issues=(warn,))
        result = summarize_publish_verify_result(PublishVerifyResult(stages=(stage,)))
        assert result["total_warnings"] == 1

    def test_stages_list_length(self):
        result = summarize_publish_verify_result(_verify_result())
        assert len(result["stages"]) == 4

    def test_stage_entry_keys(self):
        result = summarize_publish_verify_result(_verify_result())
        entry = result["stages"][0]
        assert set(entry.keys()) == {"stage", "checked", "ok", "errors", "warnings"}

    def test_stage_names_forwarded(self):
        result = summarize_publish_verify_result(_verify_result())
        names = [s["stage"] for s in result["stages"]]
        assert names == ["manifest", "profiles", "evidence", "zip"]

    def test_stage_checked_forwarded(self):
        result = summarize_publish_verify_result(_verify_result())
        assert result["stages"][0]["checked"] == 3
        assert result["stages"][1]["checked"] == 10

    def test_stage_ok_false_when_error(self):
        err = PublishVerifyIssue(stage="evidence", message="bad hash", severity="error")
        stage = _verify_stage("evidence", checked=2, issues=(err,))
        result = summarize_publish_verify_result(PublishVerifyResult(stages=(stage,)))
        assert result["stages"][0]["ok"] is False

    def test_stage_ok_true_warning_only(self):
        warn = PublishVerifyIssue(stage="zip", message="extra file", severity="warning")
        stage = _verify_stage("zip", checked=1, issues=(warn,))
        result = summarize_publish_verify_result(PublishVerifyResult(stages=(stage,)))
        assert result["stages"][0]["ok"] is True

    def test_stage_errors_and_warnings_per_stage(self):
        err = PublishVerifyIssue(stage="profiles", message="bad hash", severity="error")
        warn = PublishVerifyIssue(stage="profiles", message="slow", severity="warning")
        stage = _verify_stage("profiles", checked=4, issues=(err, warn))
        result = summarize_publish_verify_result(PublishVerifyResult(stages=(stage,)))
        assert result["stages"][0]["errors"] == 1
        assert result["stages"][0]["warnings"] == 1

    def test_empty_stages(self):
        result = summarize_publish_verify_result(PublishVerifyResult(stages=()))
        assert result["ok"] is True
        assert result["total_checked"] == 0
        assert result["total_errors"] == 0
        assert result["stages"] == []

    def test_exact_top_level_keys(self):
        result = summarize_publish_verify_result(_verify_result())
        assert set(result.keys()) == {"ok", "total_checked", "total_errors", "total_warnings", "stages"}

    def test_json_serializable(self):
        result = summarize_publish_verify_result(_verify_result())
        obj = json.loads(as_json(result))
        assert obj["ok"] is True
        assert obj["total_checked"] == 21
        assert len(obj["stages"]) == 4

    def test_pure_same_inputs_same_output(self):
        v = _verify_result()
        assert summarize_publish_verify_result(v) == summarize_publish_verify_result(v)


# ---------------------------------------------------------------------------
# summarize_publish_roundtrip_result
# ---------------------------------------------------------------------------


def _roundtrip_stage(
    stage: str,
    checked: int = 0,
    issues: tuple[PublishRoundtripIssue, ...] = (),
) -> PublishRoundtripStageResult:
    return PublishRoundtripStageResult(stage=stage, checked=checked, issues=issues)


def _roundtrip_result() -> PublishRoundtripResult:
    return PublishRoundtripResult(
        stages=(
            _roundtrip_stage("snapshot", checked=1),
            _roundtrip_stage("profiles", checked=10),
            _roundtrip_stage("evidence", checked=7),
            _roundtrip_stage("zip", checked=4),
            _roundtrip_stage("homepage", checked=1),
            _roundtrip_stage("lookup", checked=1),
        )
    )


class TestSummarizePublishRoundtripResult:
    def test_ok_true_when_no_errors(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert result["ok"] is True

    def test_ok_false_when_error_present(self):
        err = PublishRoundtripIssue(stage="snapshot", message="missing row", severity="error")
        stage = _roundtrip_stage("snapshot", checked=1, issues=(err,))
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=(stage,)))
        assert result["ok"] is False

    def test_total_checked_sums_stages(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert result["total_checked"] == 24

    def test_total_errors_zero_when_clean(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert result["total_errors"] == 0

    def test_total_errors_counts_across_stages(self):
        err1 = PublishRoundtripIssue(stage="profiles", message="missing member", severity="error")
        err2 = PublishRoundtripIssue(stage="evidence", message="missing file", severity="error")
        warn = PublishRoundtripIssue(stage="zip", message="extra file", severity="warning")
        stages = (
            _roundtrip_stage("profiles", checked=5, issues=(err1,)),
            _roundtrip_stage("evidence", checked=3, issues=(err2,)),
            _roundtrip_stage("zip", checked=1, issues=(warn,)),
        )
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=stages))
        assert result["total_errors"] == 2

    def test_total_warnings_counted(self):
        warn = PublishRoundtripIssue(stage="homepage", message="empty feed", severity="warning")
        stage = _roundtrip_stage("homepage", checked=1, issues=(warn,))
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=(stage,)))
        assert result["total_warnings"] == 1

    def test_stages_list_length(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert len(result["stages"]) == 6

    def test_stage_entry_keys(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        entry = result["stages"][0]
        assert set(entry.keys()) == {"stage", "checked", "ok", "errors", "warnings"}

    def test_stage_names_forwarded(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        names = [s["stage"] for s in result["stages"]]
        assert names == ["snapshot", "profiles", "evidence", "zip", "homepage", "lookup"]

    def test_stage_checked_forwarded(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert result["stages"][0]["checked"] == 1
        assert result["stages"][1]["checked"] == 10

    def test_stage_ok_false_when_error(self):
        err = PublishRoundtripIssue(stage="evidence", message="bad hash", severity="error")
        stage = _roundtrip_stage("evidence", checked=2, issues=(err,))
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=(stage,)))
        assert result["stages"][0]["ok"] is False

    def test_stage_ok_true_warning_only(self):
        warn = PublishRoundtripIssue(stage="zip", message="extra file", severity="warning")
        stage = _roundtrip_stage("zip", checked=1, issues=(warn,))
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=(stage,)))
        assert result["stages"][0]["ok"] is True

    def test_stage_errors_and_warnings_per_stage(self):
        err = PublishRoundtripIssue(stage="profiles", message="bad hash", severity="error")
        warn = PublishRoundtripIssue(stage="profiles", message="slow", severity="warning")
        stage = _roundtrip_stage("profiles", checked=4, issues=(err, warn))
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=(stage,)))
        assert result["stages"][0]["errors"] == 1
        assert result["stages"][0]["warnings"] == 1

    def test_empty_stages(self):
        result = summarize_publish_roundtrip_result(PublishRoundtripResult(stages=()))
        assert result["ok"] is True
        assert result["total_checked"] == 0
        assert result["total_errors"] == 0
        assert result["stages"] == []

    def test_exact_top_level_keys(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        assert set(result.keys()) == {"ok", "total_checked", "total_errors", "total_warnings", "stages"}

    def test_json_serializable(self):
        result = summarize_publish_roundtrip_result(_roundtrip_result())
        obj = json.loads(as_json(result))
        assert obj["ok"] is True
        assert obj["total_checked"] == 24
        assert len(obj["stages"]) == 6

    def test_pure_same_inputs_same_output(self):
        r = _roundtrip_result()
        assert summarize_publish_roundtrip_result(r) == summarize_publish_roundtrip_result(r)


# ---------------------------------------------------------------------------
# summarize_history_verify_result
# ---------------------------------------------------------------------------


def _history_verify_stage(
    stage: str,
    checked: int = 0,
    issues: tuple[HistoryVerifyIssue, ...] = (),
) -> HistoryVerifyStageResult:
    return HistoryVerifyStageResult(stage=stage, checked=checked, issues=issues)


def _history_verify_result() -> HistoryVerifyResult:
    return HistoryVerifyResult(
        stages=(
            _history_verify_stage("snapshot_index", checked=5),
            _history_verify_stage("bootstrap", checked=2),
            _history_verify_stage("current_aggregates", checked=3),
            _history_verify_stage("snapshot_presets", checked=4),
            _history_verify_stage("members", checked=8),
            _history_verify_stage("member_pages", checked=1),
        )
    )


class TestSummarizeHistoryVerifyResult:
    def test_ok_true_when_no_errors(self):
        result = summarize_history_verify_result(_history_verify_result())
        assert result["ok"] is True

    def test_ok_false_when_error_present(self):
        err = HistoryVerifyIssue(stage="bootstrap", message="missing file", severity="error")
        stage = _history_verify_stage("bootstrap", checked=1, issues=(err,))
        result = summarize_history_verify_result(HistoryVerifyResult(stages=(stage,)))
        assert result["ok"] is False

    def test_total_checked_sums_stages(self):
        result = summarize_history_verify_result(_history_verify_result())
        assert result["total_checked"] == 23

    def test_stage_names_forwarded(self):
        result = summarize_history_verify_result(_history_verify_result())
        names = [s["stage"] for s in result["stages"]]
        assert names == [
            "snapshot_index",
            "bootstrap",
            "current_aggregates",
            "snapshot_presets",
            "members",
            "member_pages",
        ]

    def test_json_serializable(self):
        result = summarize_history_verify_result(_history_verify_result())
        obj = json.loads(as_json(result))
        assert obj["ok"] is True
        assert obj["total_checked"] == 23
