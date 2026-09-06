"""Builders for runtime result dataclasses.

Each builder mirrors one of the result types returned by runtime
commands.  Defaults produce a valid, minimal result so tests only
override what they care about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.db.load_report import LoadSummary, TableWriteResult
from src.pipeline.publish_pipeline import PublishResult
from src.pipeline.recompute_run import RecomputeRunResult
from src.pipeline.stages import PipelineResult
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_artifacts import DisclosureArtifactIngestResult
from src.runtime.disclosures_load_from_parse import DisclosuresParseLoadResult
from src.runtime.disclosures_parse import DisclosureParseRuntimeResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.recompute import RuntimeRecomputeResult
from tests.support.load import load_summary

# ---------------------------------------------------------------------------
# Shared defaults
# ---------------------------------------------------------------------------

DATA_SOURCE: dict[str, Any] = {"id": 1, "slug": "congress-gov-api", "name": "Congress.gov API"}
RUN_ID: int = 42


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def congress_result(
    ls: LoadSummary | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> CongressLoadResult:
    return CongressLoadResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        load_summary=ls or load_summary(),
    )


def disclosures_result(
    ls: LoadSummary | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> DisclosuresLoadRuntimeResult:
    return DisclosuresLoadRuntimeResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        load_summary=ls or load_summary(),
        sidecars=(),
    )


def recompute_result(
    rule_fires: list | None = None,
    evidence_cards: list | None = None,
    ls: LoadSummary | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> RuntimeRecomputeResult:
    inner = RecomputeRunResult(
        rule_fires=rule_fires or [],
        evidence_cards=evidence_cards or [],
        load_summary=ls,
    )
    return RuntimeRecomputeResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        recompute_result=inner,
    )


def publish_result(
    snapshot_id: str = "2024-06-01",
    planned: int = 10,
    written: int = 10,
    failures: list[str] | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> PublishRuntimeResult:
    pub = PublishResult(
        snapshot_id=snapshot_id,
        planned_count=planned,
        written_count=written,
        verification_failures=failures or [],
        pipeline_result=PipelineResult(),
    )
    return PublishRuntimeResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        snapshot_id=snapshot_id,
        publish_result=pub,
    )


def artifact_ingest_result(
    discovered: int = 5,
    stored: int = 4,
    local_root: str | None = None,
    chamber: str = "senate",
    year: int = 2024,
    filing_kind: str | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> DisclosureArtifactIngestResult:
    return DisclosureArtifactIngestResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        chamber=chamber,
        year=year,
        filing_kind=filing_kind,
        local_root=Path(local_root) if local_root is not None else None,
        discovered_count=discovered,
        stored_count=stored,
        artifact_rows=(),
    )


def parse_disclosures_result(
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
    )


def process_disclosures_result(
    parse_processed: int = 10,
    parse_succeeded: int = 8,
    parse_failed: int = 2,
    transform_count: int = 6,
    skipped_transform_count: int = 0,
    load_tables: list[TableWriteResult] | None = None,
    data_source: dict[str, Any] | None = None,
    run_id: int = RUN_ID,
) -> DisclosuresParseLoadResult:
    parse = DisclosureParseRuntimeResult(
        processed_count=parse_processed,
        succeeded_count=parse_succeeded,
        failed_count=parse_failed,
        parse_sessions=(),
        parsed_documents=(),
    )
    load = DisclosuresLoadRuntimeResult(
        data_source=data_source or DATA_SOURCE,
        run_id=run_id,
        load_summary=load_summary(tables=load_tables or []),
        sidecars=(),
    )
    return DisclosuresParseLoadResult(
        parse_result=parse,
        transform_count=transform_count,
        skipped_transform_count=skipped_transform_count,
        load_result=load,
    )
