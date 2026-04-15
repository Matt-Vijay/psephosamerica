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
from src.runtime.output import (
    as_json,
    summarize_disclosure_artifact_ingest_result,
    summarize_load_result,
    summarize_publish_result,
    summarize_recompute_result,
    summarize_status,
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
