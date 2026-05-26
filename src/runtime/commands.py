"""Operator command execution and CLI dispatch helpers."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from src.ingest.congress.archive import CongressArchive, manifest_from_existing_archive
from src.ingest.congress.archive_manifest import load_manifest as load_congress_archive_manifest
from src.ingest.congress.archive_manifest import write_manifest
from src.ingest.congress.archive_validate import validate_congress_archive_manifest
from src.parse.disclosures.transform import DisclosureTransformResult
from src.pipeline.publish_snapshot_run import ZipBundleInputs, _ontology_edge_from_row
from src.prediction.backtest import (
    PredictionBacktestPayload,
    REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
)
from src.prediction.dataset import PredictionEvalDatasetPayload
from src.prediction.eval_report import (
    PredictionEvalReportPayload,
    build_prediction_eval_report,
)
from src.prediction.window_plan import (
    PredictionEvalWindowPlanPayload,
    build_prediction_eval_window_plan,
)
from src.prediction.llm_semantics import (
    BillSemanticIndexPayload,
    OpenAIBillSemanticExtractor,
    bill_semantic_input_from_row,
    load_bill_semantic_payloads,
    materialize_bill_semantics,
)
from src.evidence.source_anchor_policy import (
    has_official_claim_source_anchor,
    is_official_source_url,
)
from src.query.published_rows import (
    fetch_bill_semantic_input_rows,
    fetch_all_ontology_edge_rows,
    fetch_vote_prediction_backtest_bill_signal_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
    fetch_vote_prediction_contribution_signal_rows,
    fetch_vote_prediction_statement_signal_rows,
)
from src.runtime.app import OpenPactRuntime, build_runtime, open_runtime_connection
from src.runtime.bill_semantics_cache import (
    bill_semantics_cache_failures as _prediction_backtest_bill_semantics_cache_failures,
)
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_model_names as _bill_semantics_index_model_names,
)
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_object as _bill_semantics_index_object,
)
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_payload as _bill_semantics_index_payload,
)
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_run_metadata_failures as _bill_semantics_index_run_metadata_failures,
)
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_sha256 as _bill_semantics_index_sha256,
)
from src.runtime.bootstrap import bootstrap_database, describe_bootstrap_plan
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_archive_materialize import (
    MaterializedCongressArchiveResult,
    materialize_congress_archive,
)
from src.runtime.congress_live_full import run_live_congress_load_full
from src.runtime.congress_options import (
    CongressLoadOptions,
    current_congress_for_date,
    resolve_congress_vote_coverage,
)
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.history_backfill import (
    build_history_backfill_report,
    check_history_backfill_inputs,
    LocalHistoryBackfillResult,
    plan_congress_history_backfill,
    run_local_history_backfill,
)
from src.runtime.history_verify import (
    verify_history_aggregate_local as _verify_local_history_aggregate,
)
from src.runtime.history_verify_types import HistoryVerifyResult
from src.runtime.json_artifacts import write_json_artifact as _atomic_write_json_artifact
from src.runtime.disclosures import DisclosuresLoadRuntimeResult, run_disclosures_load_runtime
from src.runtime.disclosures_artifacts import run_disclosure_artifact_ingest
from src.runtime.disclosures_bundle import DisclosuresBundle, load_disclosures_bundle
from src.runtime.disclosures_bundle_materialize import (
    MaterializedDisclosuresBundleResult,
    materialize_disclosures_bundle,
)
from src.runtime.disclosures_bundle_validate import validate_disclosures_bundle
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_load_from_parse import run_disclosures_parse_load_runtime
from src.runtime.disclosures_parse import run_disclosure_parse_runtime
from src.runtime.fec import FecBulkFilePaths, FecLocalLoadResult, run_fec_local_load_runtime
from src.runtime.fec_bulk_materialize import materialize_fec_bulk_files
from src.runtime.member_fec_crosswalk import (
    MemberFecCrosswalkLoadResult,
    load_member_fec_crosswalk_runtime,
)
from src.runtime.member_fec_crosswalk_materialize import (
    materialize_member_fec_crosswalk,
)
from src.runtime.public_statement_rows_materialize import (
    materialize_public_statement_rows,
)
from src.runtime.public_statement_rss_materialize import (
    materialize_public_statement_rss,
)
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.output import (
    summarize_disclosure_artifact_ingest_result,
    summarize_disclosures_bundle_process_result,
    summarize_fec_load_result,
    summarize_history_verify_result,
    summarize_local_history_backfill_result,
    summarize_load_result,
    summarize_local_oracle_run_result,
    summarize_member_fec_crosswalk_load_result,
    summarize_parse_disclosures_result,
    summarize_process_disclosures_result,
    summarize_publish_result,
    summarize_publish_roundtrip_result,
    summarize_publish_verify_result,
    summarize_recompute_result,
)
from src.runtime.paths import local_artifact_root
from src.runtime.prediction_backtest import (
    handle_prediction_backtest_command,
    prediction_backtest_source_state as _prediction_backtest_source_state_impl,
    run_prediction_backtest_command as _run_prediction_backtest_command,
    verify_prediction_backtest_command,
)
from src.runtime.prediction_operator_resume_run import (
    verify_prediction_operator_resume_run,
)
from src.runtime.prediction_input_inventory import (
    handle_prediction_input_inventory_command,
    prediction_input_inventory_source_state as _prediction_input_inventory_source_state_impl,
    run_prediction_input_inventory_command as _run_prediction_input_inventory_command,
    verify_prediction_input_inventory_command,
)
from src.runtime.prediction_source_url_audit import (
    run_prediction_source_url_audit_command,
    verify_prediction_source_url_audit_command,
)
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.publish_roundtrip import verify_roundtrip as _verify_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify import verify_local_publish as _verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime
from src.runtime.status_command import get_runtime_status
from src.runtime.zip_bundle import load_zip_bundle


_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _handle_verify_prediction_operator_resume_run(args: Any) -> dict[str, Any]:
    return verify_prediction_operator_resume_run(args)


def _emit_verification_summary(command: str, publish_root: Path, result: Any) -> None:
    if result.ok:
        return

    issues = [issue for issue in result.all_issues() if getattr(issue, "severity", None) == "error"]
    preview: list[str] = []
    for issue in issues[:3]:
        location = f" ({issue.path})" if getattr(issue, "path", None) else ""
        preview.append(f"[{issue.stage}]{location} {issue.message}")

    detail = "; ".join(preview) if preview else "no detailed issues captured"
    more = f"; +{len(issues) - 3} more" if len(issues) > 3 else ""
    sys.stderr.write(
        f"{command} failed for {publish_root}: {result.total_errors} error(s): {detail}{more}\n"
    )


def load_congress(
    ctx: RuntimeContext,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a live Congress load."""
    conn = open_connection(ctx)
    return run_live_congress_load_full(
        conn,
        ctx.settings,
        congress=options.congress,
        include_votes=options.include_votes,
        house_vote_year=options.house_vote_year,
        senate_session=options.senate_session,
    )


def load_congress_local(
    ctx: RuntimeContext,
    archive: Path,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a Congress load from a local archive bundle."""
    conn = open_connection(ctx)
    return run_congress_archive_load(conn, archive, options)


def load_fec_local(
    ctx: RuntimeContext,
    files: FecBulkFilePaths,
) -> FecLocalLoadResult:
    """Open a connection and load local FEC bulk files."""
    conn = open_connection(ctx)
    return run_fec_local_load_runtime(conn, files)


def load_member_fec_crosswalk_local(
    ctx: RuntimeContext,
    crosswalk: Path,
    *,
    member_terms: Path | None = None,
    source_url: str | None = None,
) -> MemberFecCrosswalkLoadResult:
    """Open a connection and load a local member-FEC candidate crosswalk."""
    conn = open_connection(ctx)
    return load_member_fec_crosswalk_runtime(
        conn,
        crosswalk,
        member_terms_path=member_terms,
        source_url=source_url,
    )


def load_disclosures(
    ctx: RuntimeContext,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult:
    """Open a connection and run the disclosures load."""
    conn = open_connection(ctx)
    return run_disclosures_load_runtime(conn, results)


def process_disclosures_local(
    ctx: RuntimeContext,
    bundle: DisclosuresBundle,
    *,
    local_root: Path | None = None,
) -> DisclosuresBundleProcessResult:
    """Open a connection and run the bundle-process disclosure pipeline."""
    conn = open_connection(ctx)
    return run_disclosures_bundle_process(conn, bundle, local_root=local_root)


def recompute_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    *,
    statement_rows: list[dict[str, Any]] | None = None,
    statement_rows_source: dict[str, Any] | None = None,
) -> RuntimeRecomputeResult:
    """Open a connection and run a full conflict-of-interest recompute."""
    conn = open_connection(ctx)
    return run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=ctx.taxonomy,
        issuer_sector_resolver=ctx.issuer_sector_resolver,
        contribution_sector_resolver=ctx.contribution_sector_resolver,
        statement_rows=statement_rows,
        statement_rows_source=statement_rows_source,
    )


def publish_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
) -> PublishRuntimeResult:
    """Open a connection and publish an immutable snapshot."""
    conn = open_connection(ctx)
    return run_publish_runtime(conn, snapshot_date, target_dir, zip_bundle_inputs)


def verify_publish_local(publish_root: Path) -> PublishVerifyResult:
    """Verify a local publish tree without a database connection."""
    result = _verify_local_publish(publish_root)
    _emit_verification_summary("verify-publish", publish_root, result)
    return result


def verify_publish_roundtrip_local(
    ctx: RuntimeContext,
    publish_root: Path,
) -> PublishRoundtripResult:
    """Verify a local publish tree using the DB as the source of truth."""
    conn = open_connection(ctx)
    result = _verify_roundtrip(conn, publish_root)
    _emit_verification_summary("verify-publish-roundtrip", publish_root, result)
    return result


def verify_history_aggregate_local(publish_root: Path) -> HistoryVerifyResult:
    """Verify a local history aggregate root without a database connection."""
    result = _verify_local_history_aggregate(publish_root)
    _emit_verification_summary("verify-history-aggregate", publish_root, result)
    return result


def run_oracle_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> LocalOracleRunResult:
    """Open a connection and run the full local oracle pipeline."""
    conn = open_connection(ctx)
    return run_oracle_local(conn, congress_archive, disclosures_bundle, options)


def run_history_backfill_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    *,
    congress: int,
    target_root: Path,
    aggregate_root: Path | None = None,
    chamber: str | None = None,
    limit: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    overwrite: bool = False,
    continue_on_error: bool = False,
    artifact_root: Path | None = None,
    input_readiness: Any | None = None,
) -> LocalHistoryBackfillResult:
    """Run the local oracle across a planned weekly Congress history window."""
    result = run_local_history_backfill(
        ctx,
        congress_archive,
        disclosures_bundle,
        congress=congress,
        target_root=target_root,
        aggregate_root=aggregate_root,
        chamber=chamber,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        overwrite=overwrite,
        continue_on_error=continue_on_error,
        artifact_root=artifact_root,
    )
    if input_readiness is not None:
        result = replace(result, input_readiness=input_readiness)
        if result.report_path is not None:
            _write_json_artifact(
                result.report_path,
                build_history_backfill_report(result).model_dump(mode="json"),
            )
    return result


def run_prediction_backtest_command(
    ctx: RuntimeContext,
    *,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    model: str = "baseline",
    bill_semantics_root: Path | None = None,
) -> PredictionBacktestPayload:
    """Compatibility wrapper for the extracted prediction backtest runner."""
    return _run_prediction_backtest_command(
        ctx,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        model=model,
        bill_semantics_root=bill_semantics_root,
    )


def run_prediction_eval_report_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    bill_semantics_root: Path | None = None,
) -> PredictionEvalReportPayload:
    """Compare baseline, ontology, and learned models on cutoff-safe vote windows."""
    conn = open_connection(ctx)
    training_feature_rows = fetch_vote_prediction_backtest_feature_rows(
        conn,
        training_feature_cutoff,
    )
    evaluation_feature_rows = fetch_vote_prediction_backtest_feature_rows(conn, feature_cutoff)
    training_label_rows = fetch_vote_prediction_backtest_label_rows(conn, train_start, train_end)
    evaluation_label_rows = fetch_vote_prediction_backtest_label_rows(conn, label_start, label_end)
    ontology_edges = [_ontology_edge_from_row(row) for row in fetch_all_ontology_edge_rows(conn)]
    bill_signal_rows = fetch_vote_prediction_backtest_bill_signal_rows(
        conn,
        feature_cutoff,
    )
    training_contribution_signal_rows = fetch_vote_prediction_contribution_signal_rows(
        conn,
        training_feature_cutoff,
    )
    evaluation_contribution_signal_rows = fetch_vote_prediction_contribution_signal_rows(
        conn,
        feature_cutoff,
    )
    training_statement_signal_rows = fetch_vote_prediction_statement_signal_rows(
        conn,
        training_feature_cutoff,
    )
    evaluation_statement_signal_rows = fetch_vote_prediction_statement_signal_rows(
        conn,
        feature_cutoff,
    )
    bill_semantics = (
        load_bill_semantic_payloads(bill_semantics_root)
        if bill_semantics_root is not None
        else None
    )
    return build_prediction_eval_report(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        training_feature_rows=training_feature_rows,
        evaluation_feature_rows=evaluation_feature_rows,
        training_label_rows=training_label_rows,
        evaluation_label_rows=evaluation_label_rows,
        ontology_edges=ontology_edges,
        bill_signal_rows=bill_signal_rows,
        bill_semantics=bill_semantics,
        training_contribution_signal_rows=training_contribution_signal_rows,
        evaluation_contribution_signal_rows=evaluation_contribution_signal_rows,
        training_statement_signal_rows=training_statement_signal_rows,
        evaluation_statement_signal_rows=evaluation_statement_signal_rows,
    )


def run_prediction_input_inventory_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
) -> Any:
    """Compatibility wrapper for the extracted prediction input inventory runner."""
    return _run_prediction_input_inventory_command(
        ctx,
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
    )


def _snapshot_date_or_today(snapshot_date: dt.date | None) -> dt.date:
    return snapshot_date if snapshot_date is not None else dt.date.today()


def _publish_target_or_default(target_dir: str | Path | None, runtime: OpenPactRuntime) -> Path:
    if target_dir is None:
        return runtime.publish_root
    return Path(target_dir)


def _artifact_root_or_default(local_root: str | Path | None) -> Path:
    if local_root is None:
        return local_artifact_root()
    return Path(local_root)


def _current_congress(today: dt.date | None = None) -> int:
    return current_congress_for_date(today)


def _disclosure_summary(result: Any) -> dict[str, Any]:
    summary = summarize_disclosure_artifact_ingest_result(result)
    summary.pop("local_root", None)
    return summary


def _load_house_disclosures(conn: Any, year: int, local_root: Path) -> dict[str, Any]:
    annual = run_disclosure_artifact_ingest(
        conn,
        chamber="house",
        year=year,
        filing_kind="annual",
        local_root=local_root,
    )
    ptr = run_disclosure_artifact_ingest(
        conn,
        chamber="house",
        year=year,
        filing_kind="ptr",
        local_root=local_root,
    )
    return {
        "annual": _disclosure_summary(annual),
        "ptr": _disclosure_summary(ptr),
        "discovered": annual.discovered_count + ptr.discovered_count,
        "stored": annual.stored_count + ptr.stored_count,
    }


def _load_senate_disclosures(conn: Any, year: int, local_root: Path) -> dict[str, Any]:
    return _disclosure_summary(
        run_disclosure_artifact_ingest(
            conn,
            chamber="senate",
            year=year,
            local_root=local_root,
        )
    )


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    ingestion_runs: list[dict[str, Any]] = status.get("ingestion_runs", [])
    parse_runs: list[dict[str, Any]] = status.get("parse_runs", [])
    source_artifacts: list[dict[str, Any]] = status.get("source_artifacts", [])
    data_sources: list[dict[str, Any]] = status.get("data_sources", [])

    latest_run = ingestion_runs[0] if ingestion_runs else None
    latest_parse = parse_runs[0] if parse_runs else None
    latest_artifact = source_artifacts[0] if source_artifacts else None

    return {
        "summary": status.get("summary", {}),
        "latest_ingestion_run": {
            "id": latest_run.get("id") if latest_run else None,
            "run_type": latest_run.get("run_type") if latest_run else None,
            "status": latest_run.get("status") if latest_run else None,
            "data_source": latest_run.get("data_source_slug") if latest_run else None,
        },
        "latest_parse_run": {
            "id": latest_parse.get("id") if latest_parse else None,
            "parser_name": latest_parse.get("parser_name") if latest_parse else None,
            "status": latest_parse.get("status") if latest_parse else None,
        },
        "latest_artifact": {
            "id": latest_artifact.get("id") if latest_artifact else None,
            "artifact_kind": latest_artifact.get("artifact_kind") if latest_artifact else None,
            "data_source": latest_artifact.get("data_source_slug") if latest_artifact else None,
        },
        "active_data_sources": [
            {
                "slug": row.get("slug"),
                "name": row.get("name"),
                "source_kind": row.get("source_kind"),
            }
            for row in data_sources
        ],
    }


def _publish_summary_ok(summary: dict[str, Any]) -> bool:
    return bool(summary.get("succeeded", True))


def _oracle_summary_ok(summary: dict[str, Any]) -> bool:
    congress_ok = bool(summary.get("congress", {}).get("load_ok", True))
    disclosures_ok = bool(summary.get("disclosures", {}).get("load_ok", True))
    publish_ok = bool(summary.get("publish", {}).get("succeeded", True))
    verify_ok = bool(summary.get("verify", {}).get("ok", True))
    roundtrip_ok = bool(summary.get("roundtrip", {}).get("ok", True))
    return congress_ok and disclosures_ok and publish_ok and verify_ok and roundtrip_ok


def _materialized_congress_archive_summary(
    result: MaterializedCongressArchiveResult,
    *,
    mode: str = "materialized",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "archive_root": str(result.archive_root),
        "manifest_path": str(result.manifest_path),
        "congress": result.congress,
        "include_votes": result.include_votes,
        "house_vote_year": result.house_vote_year,
        "senate_session": result.senate_session,
        "member_count": result.member_count,
        "committee_count": result.committee_count,
        "bill_count": result.bill_count,
        "member_detail_count": result.member_detail_count,
        "bill_detail_count": result.bill_detail_count,
        "cosponsor_file_count": result.cosponsor_file_count,
        "house_vote_count": result.house_vote_count,
        "senate_vote_count": result.senate_vote_count,
        "source_state": _congress_archive_source_state(
            congress=result.congress,
            include_votes=result.include_votes,
            member_count=result.member_count,
            committee_count=result.committee_count,
            bill_count=result.bill_count,
            member_detail_count=result.member_detail_count,
            bill_detail_count=result.bill_detail_count,
            cosponsor_file_count=result.cosponsor_file_count,
            house_vote_count=result.house_vote_count,
            senate_vote_count=result.senate_vote_count,
        ),
    }


def _materialized_disclosures_bundle_summary(
    result: MaterializedDisclosuresBundleResult,
    *,
    mode: str = "materialized",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "bundle_path": str(result.bundle_path),
        "artifact_root": str(result.artifact_root),
        "chamber": result.chamber,
        "years": list(result.years),
        "artifact_count": result.artifact_count,
        "house_count": result.house_count,
        "senate_count": result.senate_count,
    }


def _count_json_items(path: Path, key: str) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{path} is missing a list-valued {key!r} field")
    return len(items)


def _single_value_or_none(values: set[int]) -> int | None:
    if len(values) == 1:
        return next(iter(values))
    return None


def _reusable_congress_archive_path(archive_root: Path) -> Path:
    manifest_file = archive_root / "manifest.json"
    return manifest_file if manifest_file.is_file() else archive_root


def _should_reuse_existing_congress_archive(archive_root: Path) -> bool:
    if not archive_root.exists():
        return False
    if archive_root.is_dir():
        try:
            next(archive_root.iterdir())
        except StopIteration:
            return False
        return True
    return False


def _summarize_existing_congress_archive(
    archive_root: Path,
    *,
    congress: int,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> dict[str, Any]:
    manifest_file = archive_root / "manifest.json"
    manifest = (
        load_congress_archive_manifest(manifest_file)
        if manifest_file.is_file()
        else manifest_from_existing_archive(CongressArchive(archive_root, congress))
    )
    validation = validate_congress_archive_manifest(manifest)
    if not validation.valid:
        missing_labels = ", ".join(missing.label for missing in validation.missing)
        raise ValueError(f"congress archive references missing files: {missing_labels}")
    if manifest.congress != congress:
        raise ValueError(
            f"existing congress archive is for Congress {manifest.congress}, expected {congress}"
        )

    house_vote_years = {vote.year for vote in manifest.house_votes}
    senate_sessions = {vote.session_number for vote in manifest.senate_votes}
    if include_votes and house_vote_year is None and senate_session is None:
        coverage = resolve_congress_vote_coverage(congress)
        house_vote_year = coverage.house_vote_year
        senate_session = coverage.senate_session
    if house_vote_year is not None and house_vote_year not in house_vote_years:
        raise ValueError(
            f"existing congress archive is missing requested house vote year: {house_vote_year}"
        )
    if senate_session is not None and senate_session not in senate_sessions:
        raise ValueError(
            f"existing congress archive is missing requested senate session: {senate_session}"
        )
    member_count = _count_json_items(manifest.members.path, "members")
    committee_count = _count_json_items(manifest.committees.path, "committees")
    bill_count = _count_json_items(manifest.bills.path, "bills")
    return {
        "mode": "reused",
        "archive_root": str(archive_root),
        "manifest_path": str(manifest_file) if manifest_file.is_file() else None,
        "congress": manifest.congress,
        "include_votes": bool(manifest.house_votes or manifest.senate_votes),
        "house_vote_year": _single_value_or_none(house_vote_years),
        "senate_session": _single_value_or_none(senate_sessions),
        "member_count": member_count,
        "committee_count": committee_count,
        "bill_count": bill_count,
        "member_detail_count": len(manifest.member_details),
        "bill_detail_count": len(manifest.bill_details),
        "cosponsor_file_count": len(manifest.cosponsors),
        "house_vote_count": len(manifest.house_votes),
        "senate_vote_count": len(manifest.senate_votes),
        "source_state": _congress_archive_source_state(
            congress=manifest.congress,
            include_votes=bool(manifest.house_votes or manifest.senate_votes),
            member_count=member_count,
            committee_count=committee_count,
            bill_count=bill_count,
            member_detail_count=len(manifest.member_details),
            bill_detail_count=len(manifest.bill_details),
            cosponsor_file_count=len(manifest.cosponsors),
            house_vote_count=len(manifest.house_votes),
            senate_vote_count=len(manifest.senate_votes),
        ),
    }


def _congress_archive_source_state(
    *,
    congress: int,
    include_votes: bool,
    member_count: int,
    committee_count: int,
    bill_count: int,
    member_detail_count: int,
    bill_detail_count: int,
    cosponsor_file_count: int,
    house_vote_count: int,
    senate_vote_count: int,
) -> dict[str, Any]:
    vote_archive_file_count = house_vote_count + senate_vote_count
    source_family_ids = ["committee_membership", "congress_bill"]
    if vote_archive_file_count:
        source_family_ids.append("congress_vote")
    return {
        "congress": congress,
        "source_family_ids": source_family_ids,
        "source_family_count": len(source_family_ids),
        "member_count": member_count,
        "committee_count": committee_count,
        "bill_count": bill_count,
        "member_detail_count": member_detail_count,
        "bill_detail_count": bill_detail_count,
        "cosponsor_file_count": cosponsor_file_count,
        "house_vote_file_count": house_vote_count,
        "senate_vote_file_count": senate_vote_count,
        "vote_archive_file_count": vote_archive_file_count,
        "prediction_member_inputs_available": member_count > 0,
        "prediction_bill_inputs_available": bill_count > 0,
        "prediction_vote_inputs_requested": include_votes,
        "prediction_vote_inputs_available": vote_archive_file_count > 0,
    }


def _summarize_existing_disclosures_bundle(
    bundle_path: Path,
    *,
    artifact_root: Path,
    requested_years: list[int] | None,
    requested_chamber: str,
) -> tuple[DisclosuresBundle, dict[str, Any]]:
    bundle = load_disclosures_bundle(bundle_path)
    validate_disclosures_bundle(bundle)
    house_count = sum(entry.chamber == "house" for entry in bundle.artifacts)
    senate_count = sum(entry.chamber == "senate" for entry in bundle.artifacts)
    if house_count and senate_count:
        chamber = "both"
    elif house_count:
        chamber = "house"
    else:
        chamber = "senate"
    years = sorted({entry.filing_year for entry in bundle.artifacts})
    effective_requested_years = years if not requested_years else requested_years
    missing_years = sorted(set(effective_requested_years) - set(years))
    if missing_years:
        missing_years_text = ", ".join(str(year) for year in missing_years)
        raise ValueError(
            f"existing disclosures bundle is missing requested years: {missing_years_text}"
        )
    if requested_chamber == "house" and house_count == 0:
        raise ValueError("existing disclosures bundle is missing requested house filings")
    if requested_chamber == "senate" and senate_count == 0:
        raise ValueError("existing disclosures bundle is missing requested senate filings")
    if requested_chamber == "both" and (house_count == 0 or senate_count == 0):
        raise ValueError("existing disclosures bundle does not include both requested chambers")
    return (
        bundle,
        {
            "mode": "reused",
            "bundle_path": str(bundle_path),
            "artifact_root": str(artifact_root),
            "chamber": chamber,
            "years": effective_requested_years,
            "artifact_count": len(bundle.artifacts),
            "house_count": house_count,
            "senate_count": senate_count,
        },
    )


def _materialize_history_backfill_inputs(
    args: Any,
    *,
    load_bundle_for_replay: bool = False,
) -> tuple[
    RuntimeContext,
    Path,
    dict[str, Any],
    Path,
    dict[str, Any],
    DisclosuresBundle | None,
    Any,
]:
    runtime = build_runtime()
    congress_archive_root = Path(args.congress_archive_root)
    disclosures_bundle_path = Path(args.disclosures_bundle_path)
    disclosures_artifact_root = Path(args.disclosures_artifact_root)
    requested_years = list(args.disclosures_year) if args.disclosures_year is not None else None
    reuse_existing_inputs = bool(getattr(args, "reuse_existing_inputs", False))
    needs_congress_materialize = not (
        reuse_existing_inputs and _should_reuse_existing_congress_archive(congress_archive_root)
    )

    api_key = args.api_key or runtime.context.settings.congress_api_key
    if needs_congress_materialize and not api_key:
        raise ValueError("Congress.gov API key is required")

    if not needs_congress_materialize:
        congress_input_path = _reusable_congress_archive_path(congress_archive_root)
        congress_summary = _summarize_existing_congress_archive(
            congress_archive_root,
            congress=args.congress,
            include_votes=args.include_votes,
            house_vote_year=args.house_vote_year,
            senate_session=args.senate_session,
        )
    else:
        congress_result = materialize_congress_archive(
            api_key=api_key,
            archive_root=congress_archive_root,
            congress=args.congress,
            include_votes=args.include_votes,
            house_vote_year=args.house_vote_year,
            senate_session=args.senate_session,
            manifest_path=None,
        )
        congress_input_path = congress_result.manifest_path
        congress_summary = _materialized_congress_archive_summary(
            congress_result,
            mode="materialized",
        )

    if reuse_existing_inputs and disclosures_bundle_path.exists():
        reused_bundle, disclosures_summary = _summarize_existing_disclosures_bundle(
            disclosures_bundle_path,
            artifact_root=disclosures_artifact_root,
            requested_years=requested_years,
            requested_chamber=args.chamber,
        )
        disclosures_bundle = reused_bundle if load_bundle_for_replay else None
    else:
        if not requested_years:
            raise ValueError(
                "--disclosures-year is required unless --reuse-existing-inputs can infer years from an existing bundle"
            )
        disclosures_result = materialize_disclosures_bundle(
            years=requested_years,
            chamber=args.chamber,
            bundle_path=disclosures_bundle_path,
            artifact_root=disclosures_artifact_root,
        )
        disclosures_summary = _materialized_disclosures_bundle_summary(
            disclosures_result,
            mode="materialized",
        )
        disclosures_bundle = None

    readiness = check_history_backfill_inputs(
        congress_input_path,
        disclosures_bundle_path,
        artifact_root=disclosures_artifact_root,
        chamber=args.chamber,
        limit=args.limit,
    )
    if load_bundle_for_replay and readiness.ready_to_replay and disclosures_bundle is None:
        disclosures_bundle = load_disclosures_bundle(disclosures_bundle_path)
    return (
        runtime.context,
        congress_input_path,
        congress_summary,
        disclosures_bundle_path,
        disclosures_summary,
        disclosures_bundle,
        readiness,
    )


def _handle_bootstrap_db(args: Any) -> dict[str, Any]:
    plan = describe_bootstrap_plan()
    if args.dry_run:
        return {
            "ok": True,
            "command": "bootstrap-db",
            "dry_run": True,
            **plan,
        }

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    bootstrap_database(conn)
    return {"ok": True, "command": "bootstrap-db", "dry_run": False, **plan}


def _handle_status(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    return {
        "ok": True,
        "command": "status",
        **_compact_status(get_runtime_status(conn, limit=args.limit)),
    }


def _handle_load_congress(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    if issues:
        return _command_issue_result("load-congress", issues)

    runtime = build_runtime()
    ctx = runtime.context
    if args.api_key:
        ctx = replace(
            ctx, settings=ctx.settings.model_copy(update={"congress_api_key": args.api_key})
        )

    include_votes = (
        args.include_votes or args.house_vote_year is not None or args.senate_session is not None
    )
    options = CongressLoadOptions(
        congress=args.congress if args.congress is not None else _current_congress(),
        include_votes=include_votes,
        house_vote_year=args.house_vote_year,
        senate_session=args.senate_session,
    )
    result = load_congress(ctx, options)
    return {"ok": True, "command": "load-congress", **summarize_load_result(result)}


def _handle_load_fec_local(args: Any) -> dict[str, Any]:
    contribution_chunk_size = getattr(args, "contribution_chunk_size", 50_000)
    if type(contribution_chunk_size) is not int or contribution_chunk_size <= 0:
        issues = ["contribution_chunk_size must be a positive integer"]
        return {
            "ok": False,
            "command": "load-fec-local",
            "issues": issues,
            "issue_count": len(issues),
        }

    runtime = build_runtime()
    files = FecBulkFilePaths(
        committee_master=Path(args.committee_master),
        candidate_committee_linkage=Path(args.candidate_committee_linkage),
        individual_contributions=Path(args.individual_contributions),
        committee_source_url=args.committee_source_url or FecBulkFilePaths.committee_source_url,
        linkage_source_url=args.linkage_source_url or FecBulkFilePaths.linkage_source_url,
        contribution_source_url=(
            args.contribution_source_url or FecBulkFilePaths.contribution_source_url
        ),
        contribution_chunk_size=contribution_chunk_size,
    )
    result = load_fec_local(runtime.context, files)
    summary = summarize_fec_load_result(result)
    return {"ok": summary.get("ok", True), "command": "load-fec-local", **summary}


def _handle_verify_fec_inputs(args: Any) -> dict[str, Any]:
    files = {
        "committee_master": Path(args.committee_master),
        "candidate_committee_linkage": Path(args.candidate_committee_linkage),
        "individual_contributions": Path(args.individual_contributions),
    }
    crosswalk_raw = getattr(args, "member_fec_crosswalk", None)
    crosswalk_path = Path(crosswalk_raw) if crosswalk_raw else None
    if crosswalk_path is not None:
        files["member_fec_crosswalk"] = crosswalk_path

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    row_counts: dict[str, int | None] = {}
    artifact_sha256: dict[str, str | None] = {}
    for name, path in files.items():
        if not path.is_file():
            issues.append(f"{name}: file not found: {path}")
            row_counts[name] = None
            artifact_sha256[name] = None
            continue
        artifact_sha256[name] = _sha256_file_path(path)
        try:
            row_counts[name] = (
                _member_fec_crosswalk_row_count(path)
                if name == "member_fec_crosswalk"
                else _non_empty_line_count(path)
            )
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{name}: failed to inspect: {exc}")
            row_counts[name] = None

    if bool(getattr(args, "require_member_fec_crosswalk", False)):
        if crosswalk_path is None or row_counts.get("member_fec_crosswalk") is None:
            quality_gate_failures.append("member_fec_crosswalk_missing")

    minimums = {
        "committee_master": getattr(args, "min_committee_rows", None),
        "candidate_committee_linkage": getattr(args, "min_linkage_rows", None),
        "individual_contributions": getattr(args, "min_contribution_rows", None),
        "member_fec_crosswalk": getattr(args, "min_member_fec_rows", None),
    }
    minimum_arg_names = {
        "committee_master": "min_committee_rows",
        "candidate_committee_linkage": "min_linkage_rows",
        "individual_contributions": "min_contribution_rows",
        "member_fec_crosswalk": "min_member_fec_rows",
    }
    for name, minimum in minimums.items():
        if minimum is None:
            continue
        if not _is_non_negative_plain_int(minimum):
            issues.append(f"{minimum_arg_names[name]} must be a non-negative integer")
            continue
        count = row_counts.get(name)
        if count is None or count < minimum:
            quality_gate_failures.append(f"{name}_rows_below_min")

    source_state = {
        "paths": {name: str(path) for name, path in files.items()},
        "row_counts": row_counts,
        "require_member_fec_crosswalk": bool(getattr(args, "require_member_fec_crosswalk", False)),
    }
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-fec-inputs",
            "checked": len(files),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "row_counts": row_counts,
            "artifact_sha256": artifact_sha256,
            "run_metadata": {
                "command": "verify-fec-inputs",
                "verification_flags": {
                    "require_member_fec_crosswalk": bool(
                        getattr(args, "require_member_fec_crosswalk", False)
                    ),
                    "min_committee_rows": getattr(args, "min_committee_rows", None),
                    "min_linkage_rows": getattr(args, "min_linkage_rows", None),
                    "min_contribution_rows": getattr(
                        args,
                        "min_contribution_rows",
                        None,
                    ),
                    "min_member_fec_rows": getattr(args, "min_member_fec_rows", None),
                },
                "artifact_sha256": artifact_sha256,
                "source_state": source_state,
            },
        },
    )


def _handle_materialize_fec_bulk_files(args: Any) -> dict[str, Any]:
    cycle = getattr(args, "cycle", None)
    if type(cycle) is not int or cycle <= 0:
        return _command_issue_result(
            "materialize-fec-bulk-files",
            ["cycle must be a positive integer"],
        )

    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 60.0),
    )
    if timeout_issue is not None:
        return _command_issue_result("materialize-fec-bulk-files", [timeout_issue])

    result = materialize_fec_bulk_files(
        cycle=cycle,
        output_dir=Path(args.output_dir),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
        committee_url=getattr(args, "committee_url", None),
        linkage_url=getattr(args, "linkage_url", None),
        contribution_url=getattr(args, "contribution_url", None),
        filter_individual_contributions=bool(
            getattr(args, "filter_individual_contributions", True)
        ),
        member_fec_crosswalk=Path(args.member_fec_crosswalk)
        if getattr(args, "member_fec_crosswalk", None) is not None
        else None,
    )
    payload = asdict(result)
    summary = {
        "ok": True,
        "command": "materialize-fec-bulk-files",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-fec-bulk-files",
        payload,
    )
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _handle_materialize_member_fec_crosswalk(args: Any) -> dict[str, Any]:
    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 60.0),
    )
    if timeout_issue is not None:
        return _command_issue_result("materialize-member-fec-crosswalk", [timeout_issue])

    source_file = Path(args.source_file) if getattr(args, "source_file", None) is not None else None
    result = materialize_member_fec_crosswalk(
        output_path=Path(args.output),
        terms_output_path=Path(args.terms_output)
        if getattr(args, "terms_output", None) is not None
        else None,
        source_path=source_file,
        source_url=str(args.source_url),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
    )
    payload = asdict(result)
    summary = {
        "ok": True,
        "command": "materialize-member-fec-crosswalk",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-member-fec-crosswalk",
        payload,
    )
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _handle_materialize_public_statement_rows(args: Any) -> dict[str, Any]:
    result = materialize_public_statement_rows(
        input_path=Path(args.input),
        output_path=Path(args.output),
        taxonomy_path=Path(args.taxonomy),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
    )
    payload = asdict(result)
    summary = {
        "ok": result.row_count > 0,
        "command": "materialize-public-statement-rows",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-public-statement-rows",
        payload,
    )
    if result.row_count == 0:
        summary["quality_gate_failures"] = ["no_statement_rows_materialized"]
    else:
        summary["quality_gate_failures"] = []
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _handle_materialize_public_statement_rss(args: Any) -> dict[str, Any]:
    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 30.0),
    )
    issues = [timeout_issue] if timeout_issue is not None else []
    max_feeds = getattr(args, "max_feeds", None)
    max_items_per_feed = getattr(args, "max_items_per_feed", None)
    for key, value in (
        ("max_feeds", max_feeds),
        ("max_items_per_feed", max_items_per_feed),
    ):
        if not _is_optional_non_negative_plain_int(value):
            issues.append(f"{key} must be a non-negative integer")
    if issues:
        return _command_issue_result("materialize-public-statement-rss", issues)

    source_file = Path(args.source_file) if getattr(args, "source_file", None) is not None else None
    result = materialize_public_statement_rss(
        output_path=Path(args.output),
        source_path=source_file,
        source_url=str(args.source_url),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
        max_feeds=max_feeds,
        max_items_per_feed=max_items_per_feed,
    )
    payload = asdict(result)
    summary = {
        "ok": result.feed_count > 0 if result.dry_run else result.row_count > 0,
        "command": "materialize-public-statement-rss",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-public-statement-rss",
        payload,
    )
    if result.feed_count == 0:
        summary["quality_gate_failures"] = ["no_official_rss_feeds"]
    elif not result.dry_run and result.row_count == 0:
        summary["quality_gate_failures"] = ["no_statement_rows_materialized"]
    else:
        summary["quality_gate_failures"] = []
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _materialize_summary_run_metadata(
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    source_state_keys = (
        "cycle",
        "dry_run",
        "force",
        "row_count",
        "feed_count",
        "fetched_feed_count",
        "skipped_count",
        "member_count",
        "sector_count",
        "input_path",
        "source_path",
        "output_path",
        "output_dir",
    )
    source_state = {key: payload[key] for key in source_state_keys if key in payload}
    source_urls = _materialize_summary_source_urls(payload)
    if source_urls:
        source_state["source_urls"] = source_urls
    artifact_sha256 = {
        key: payload[key]
        for key in ("input_sha256", "output_sha256", "source_sha256")
        if isinstance(payload.get(key), str) and _is_sha256_hex(payload[key])
    }
    file_hashes = _materialize_summary_file_hashes(payload)
    if file_hashes:
        artifact_sha256["files"] = file_hashes
    return {
        "command": command,
        "source_state": source_state,
        "artifact_sha256": artifact_sha256,
    }


def _materialize_summary_source_urls(payload: dict[str, Any]) -> list[str]:
    urls: set[str] = set()
    source_url = payload.get("source_url")
    if isinstance(source_url, str) and source_url:
        urls.add(source_url)
    files = payload.get("files")
    if isinstance(files, list | tuple):
        for file_payload in files:
            if not isinstance(file_payload, dict):
                continue
            file_source_url = file_payload.get("source_url")
            if isinstance(file_source_url, str) and file_source_url:
                urls.add(file_source_url)
    return sorted(urls)


def _materialize_summary_file_hashes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    files = payload.get("files")
    if not isinstance(files, list | tuple):
        return []
    file_hashes: list[dict[str, Any]] = []
    for file_payload in files:
        if not isinstance(file_payload, dict):
            continue
        sha256 = file_payload.get("sha256")
        if not isinstance(sha256, str) or not _is_sha256_hex(sha256):
            continue
        file_hashes.append(
            {
                "kind": file_payload.get("kind"),
                "output_path": file_payload.get("output_path"),
                "sha256": sha256,
            }
        )
    return file_hashes


def _sha256_file_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _non_empty_line_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="latin-1", errors="ignore", newline="") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def _member_fec_crosswalk_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = set(reader.fieldnames or [])
        required = {"bioguide_id", "fec_candidate_id"}
        if not required <= fieldnames:
            missing = sorted(required - fieldnames)
            raise ValueError(f"missing required columns: {missing}")
        return sum(
            1
            for row in reader
            if str(row.get("bioguide_id") or "").strip()
            and str(row.get("fec_candidate_id") or "").strip()
        )


def _handle_load_member_fec_crosswalk_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    result = load_member_fec_crosswalk_local(
        runtime.context,
        Path(args.crosswalk),
        member_terms=Path(args.member_terms)
        if getattr(args, "member_terms", None) is not None
        else None,
        source_url=args.source_url,
    )
    summary = summarize_member_fec_crosswalk_load_result(result)
    return {
        "ok": summary.get("ok", True),
        "command": "load-member-fec-crosswalk-local",
        **summary,
    }


def _handle_load_disclosures(args: Any) -> dict[str, Any]:
    year = args.year if args.year is not None else dt.date.today().year
    if type(year) is not int or year <= 0:
        return _command_issue_result(
            "load-disclosures",
            ["year must be a positive integer"],
        )

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    artifact_root = _artifact_root_or_default(args.local_root)

    if args.chamber == "both":
        return {
            "ok": True,
            "command": "load-disclosures",
            "year": year,
            "local_root": str(artifact_root),
            "house": _load_house_disclosures(conn, year, artifact_root),
            "senate": _load_senate_disclosures(conn, year, artifact_root),
        }

    summary = (
        _load_house_disclosures(conn, year, artifact_root)
        if args.chamber == "house"
        else _load_senate_disclosures(conn, year, artifact_root)
    )
    return {
        "ok": True,
        "command": "load-disclosures",
        "year": year,
        "local_root": str(artifact_root),
        args.chamber: summary,
    }


def _handle_parse_disclosures(args: Any) -> dict[str, Any]:
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("parse-disclosures", [limit_issue])

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    chamber = None if args.chamber == "both" else args.chamber
    result = run_disclosure_parse_runtime(
        conn,
        local_root=_artifact_root_or_default(args.local_root),
        chamber=chamber,
        limit=args.limit,
    )
    return {
        "ok": True,
        "command": "parse-disclosures",
        **summarize_parse_disclosures_result(result),
    }


def _handle_process_disclosures(args: Any) -> dict[str, Any]:
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("process-disclosures", [limit_issue])

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    chamber = None if args.chamber == "both" else args.chamber
    result = run_disclosures_parse_load_runtime(
        conn,
        local_root=_artifact_root_or_default(args.local_root),
        chamber=chamber,
        limit=args.limit,
    )
    return {
        "ok": True,
        "command": "process-disclosures",
        **summarize_process_disclosures_result(result),
    }


def _handle_recompute(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    statement_rows_path = (
        Path(args.statement_rows) if getattr(args, "statement_rows", None) is not None else None
    )
    statement_rows = (
        _load_statement_rows(statement_rows_path) if statement_rows_path is not None else None
    )
    statement_rows_source = (
        _statement_rows_source_metadata(statement_rows_path, len(statement_rows))
        if statement_rows_path is not None and statement_rows is not None
        else None
    )
    snapshot_date = _snapshot_date_or_today(args.snapshot_date)
    result = (
        recompute_snapshot(
            runtime.context,
            snapshot_date,
            statement_rows=statement_rows,
            statement_rows_source=statement_rows_source,
        )
        if statement_rows is not None
        else recompute_snapshot(runtime.context, snapshot_date)
    )
    summary = summarize_recompute_result(result)
    if statement_rows is not None:
        summary["statement_rows_loaded"] = len(statement_rows)
        summary["statement_rows_source"] = statement_rows_source
    return {"ok": True, "command": "recompute", **summary}


def _load_statement_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"statement rows file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _load_statement_rows_jsonl(path)
    if suffix == ".json":
        return _load_statement_rows_json(path)
    if suffix == ".csv":
        return _load_statement_rows_csv(path)
    raise ValueError("statement rows must be a .json, .jsonl, or .csv file")


def _statement_rows_source_metadata(path: Path, row_count: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "row_count": row_count,
    }


def _handle_verify_public_statement_rows(args: Any) -> dict[str, Any]:
    path = Path(args.statement_rows)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        rows = _load_statement_rows(path)
    except Exception as exc:  # noqa: BLE001
        issues.append(str(exc))
    row_count = len(rows)
    min_rows = getattr(args, "min_rows", None)
    if min_rows is not None:
        if not _is_non_negative_plain_int(min_rows):
            issues.append("min_rows must be a non-negative integer")
        elif row_count < min_rows:
            quality_gate_failures.append("row_count_below_minimum")
    duplicate_source_ids = _duplicate_statement_source_ids(rows)
    if duplicate_source_ids:
        quality_gate_failures.append("duplicate_source_ids")
    member_count = len({str(row["member_bioguide_id"]) for row in rows})
    sector_count = len({str(row["sector"]) for row in rows})
    official_source_url_count = row_count if not issues else 0
    date_range = _statement_rows_date_range(rows)

    result: dict[str, Any] = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-public-statement-rows",
        "artifact": str(path),
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else None,
        "checked": 1 if path.is_file() else 0,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "row_count": row_count,
        "member_count": member_count,
        "sector_count": sector_count,
        "official_source_url_count": official_source_url_count,
        "duplicate_source_ids": duplicate_source_ids,
        "date_range": date_range,
        "verification_flags": {
            "min_rows": min_rows,
        },
    }
    result["run_metadata"] = {
        "command": "verify-public-statement-rows",
        "artifact_sha256": result["artifact_sha256"],
        "verification_flags": result["verification_flags"],
        "source_state": {
            "row_count": row_count,
            "member_count": member_count,
            "sector_count": sector_count,
            "official_source_url_count": official_source_url_count,
            "duplicate_source_id_count": len(duplicate_source_ids),
            "date_range": date_range,
        },
    }
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        result["output_sha256"] = _write_json_artifact(output, result)
        result["output"] = str(output)
    return result


def _statement_source_id(row: dict[str, Any]) -> str:
    for key in ("statement_id", "source_record_id", "source_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _duplicate_statement_source_ids(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        source_id = _statement_source_id(row)
        if source_id in seen:
            duplicates.add(source_id)
        seen.add(source_id)
    return sorted(duplicates)


def _statement_rows_date_range(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    dates = [
        row["statement_date"] for row in rows if isinstance(row.get("statement_date"), dt.date)
    ]
    return {
        "min": min(dates).isoformat() if dates else None,
        "max": max(dates).isoformat() if dates else None,
    }


def _load_statement_rows_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("statement_rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("statement rows JSON must be a list or object with statement_rows")
    return [_coerce_statement_row(row) for row in rows]


def _load_statement_rows_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"statement rows JSONL line {line_number} must be an object")
        rows.append(_coerce_statement_row(raw))
    return rows


def _load_statement_rows_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_coerce_statement_row(dict(row)) for row in csv.DictReader(handle)]


def _coerce_statement_row(row: object) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("statement row must be an object")
    coerced = dict(row)
    statement_date = coerced.get("statement_date")
    if isinstance(statement_date, str) and statement_date:
        coerced["statement_date"] = dt.date.fromisoformat(statement_date)
    _validate_statement_row(coerced)
    return coerced


def _validate_statement_row(row: dict[str, Any]) -> None:
    required_fields = (
        "member_bioguide_id",
        "sector",
        "statement_date",
        "statement_source_url",
    )
    missing = [field for field in required_fields if not row.get(field)]
    if not (row.get("statement_id") or row.get("source_record_id") or row.get("source_id")):
        missing.append("statement_id/source_record_id/source_id")
    if missing:
        raise ValueError(f"statement row missing required fields: {', '.join(missing)}")
    if not isinstance(row["statement_date"], dt.date):
        raise ValueError("statement row statement_date must be an ISO date")
    if not is_official_source_url("public_statement", str(row["statement_source_url"])):
        raise ValueError("statement row statement_source_url must be an official House/Senate URL")


def _handle_publish(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    zip_bundle_path = getattr(args, "zip_bundle", None)
    if zip_bundle_path is None:
        raise ValueError("publish requires --zip-bundle")

    zip_bundle_inputs = load_zip_bundle(Path(zip_bundle_path))
    result = publish_snapshot(
        runtime.context,
        _snapshot_date_or_today(args.snapshot_date),
        _publish_target_or_default(args.out_dir, runtime),
        zip_bundle_inputs,
    )
    summary = summarize_publish_result(result)
    return {"ok": _publish_summary_ok(summary), "command": "publish", **summary}


def _handle_load_congress_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("load-congress-local", issues)

    runtime = build_runtime()
    options = CongressLoadOptions(
        congress=args.congress,
        include_votes=False,
        house_vote_year=None,
        senate_session=None,
    )
    result = load_congress_local(runtime.context, Path(args.archive), options)
    return {"ok": True, "command": "load-congress-local", **summarize_load_result(result)}


def _handle_process_disclosures_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    bundle = load_disclosures_bundle(Path(args.bundle))
    result = process_disclosures_local(runtime.context, bundle)
    return {
        "ok": True,
        "command": "process-disclosures-local",
        **summarize_disclosures_bundle_process_result(result),
    }


def _handle_run_oracle_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("run-oracle-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-oracle-local", [limit_issue])

    runtime = build_runtime()
    configured_congress = args.congress if args.congress is not None else _current_congress()
    congress_source = "explicit-arg" if args.congress is not None else "current-date-default"
    congress_options = CongressOracleOptions(
        congress=configured_congress,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        congress_source=congress_source,
    )
    options = LocalOracleOptions(
        congress_options=congress_options,
        snapshot_date=args.snapshot_date,
        target_dir=Path(args.target_dir),
        snapshot_id=args.snapshot_id,
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
    )
    result = run_oracle_local_command(
        runtime.context,
        Path(args.congress_archive),
        load_disclosures_bundle(Path(args.disclosures_bundle)),
        options,
    )
    summary = summarize_local_oracle_run_result(result)
    return {"ok": _oracle_summary_ok(summary), "command": "run-oracle-local", **summary}


def _handle_run_history_backfill_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("run-history-backfill-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-history-backfill-local", [limit_issue])

    input_readiness = check_history_backfill_inputs(
        Path(args.congress_archive),
        Path(args.disclosures_bundle),
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        chamber=args.chamber,
        limit=args.limit,
    )
    if not input_readiness.ready_to_replay:
        plan = plan_congress_history_backfill(
            args.congress,
            target_root=Path(args.target_root),
            start_date=args.start_date,
            end_date=args.end_date,
        )
        return {
            "ok": False,
            "command": "run-history-backfill-local",
            "input_readiness": input_readiness.model_dump(mode="json"),
            "congress": plan.congress,
            "cadence": plan.cadence,
            "date_window": {
                "start_date": plan.date_window.start_date.isoformat(),
                "end_date": plan.date_window.end_date.isoformat(),
                "bounded_by_today": plan.date_window.bounded_by_today,
            },
            "planned_count": len(plan.targets),
            "attempted_count": 0,
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "remaining_count": len(plan.targets),
            "aggregate_source_count": 0,
            "attempts": [],
            "aggregate": None,
        }

    runtime = build_runtime()
    result = run_history_backfill_local_command(
        runtime.context,
        Path(args.congress_archive),
        load_disclosures_bundle(Path(args.disclosures_bundle)),
        congress=args.congress,
        target_root=Path(args.target_root),
        aggregate_root=Path(args.aggregate_root) if args.aggregate_root is not None else None,
        start_date=args.start_date,
        end_date=args.end_date,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        overwrite=args.overwrite,
        continue_on_error=args.continue_on_error,
        input_readiness=input_readiness,
    )
    summary = summarize_local_history_backfill_result(result)
    summary["input_readiness"] = input_readiness.model_dump(mode="json")
    return {"ok": result.ok, "command": "run-history-backfill-local", **summary}


def _handle_check_history_backfill_inputs(args: Any) -> dict[str, Any]:
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("check-history-backfill-inputs", [limit_issue])

    payload = check_history_backfill_inputs(
        Path(args.congress_archive),
        Path(args.disclosures_bundle),
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        chamber=getattr(args, "chamber", "both"),
        limit=getattr(args, "limit", None),
    )
    return {
        "ok": payload.ready_to_replay,
        "command": "check-history-backfill-inputs",
        **payload.model_dump(mode="json"),
    }


def _handle_write_congress_archive_manifest(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("write-congress-archive-manifest", issues)

    archive_root = Path(args.archive_root)
    manifest_path = (
        Path(args.manifest_path)
        if args.manifest_path is not None
        else archive_root / "manifest.json"
    )
    manifest = manifest_from_existing_archive(CongressArchive(archive_root, args.congress))
    validation = validate_congress_archive_manifest(manifest)
    if not validation.valid:
        missing_labels = ", ".join(missing.label for missing in validation.missing)
        raise ValueError(f"congress archive references missing files: {missing_labels}")
    written = write_manifest(manifest_path, manifest)
    return {
        "ok": True,
        "command": "write-congress-archive-manifest",
        "archive_root": str(archive_root),
        "manifest_path": str(written),
        "congress": manifest.congress,
        "member_detail_count": len(manifest.member_details),
        "bill_detail_count": len(manifest.bill_details),
        "cosponsor_count": len(manifest.cosponsors),
        "house_vote_count": len(manifest.house_votes),
        "senate_vote_count": len(manifest.senate_votes),
    }


def _handle_materialize_congress_archive(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    if issues:
        return _command_issue_result("materialize-congress-archive", issues)

    runtime = build_runtime()
    api_key = args.api_key or runtime.context.settings.congress_api_key
    if not api_key:
        raise ValueError("Congress.gov API key is required")
    result = materialize_congress_archive(
        api_key=api_key,
        archive_root=Path(args.archive_root),
        congress=args.congress,
        include_votes=args.include_votes,
        house_vote_year=args.house_vote_year,
        senate_session=args.senate_session,
        manifest_path=Path(args.manifest_path) if args.manifest_path is not None else None,
    )
    return {
        "ok": True,
        "command": "materialize-congress-archive",
        "archive_root": str(result.archive_root),
        "manifest_path": str(result.manifest_path),
        "congress": result.congress,
        "include_votes": result.include_votes,
        "house_vote_year": result.house_vote_year,
        "senate_session": result.senate_session,
        "member_count": result.member_count,
        "committee_count": result.committee_count,
        "bill_count": result.bill_count,
        "member_detail_count": result.member_detail_count,
        "bill_detail_count": result.bill_detail_count,
        "cosponsor_file_count": result.cosponsor_file_count,
        "house_vote_count": result.house_vote_count,
        "senate_vote_count": result.senate_vote_count,
        "source_state": _congress_archive_source_state(
            congress=result.congress,
            include_votes=result.include_votes,
            member_count=result.member_count,
            committee_count=result.committee_count,
            bill_count=result.bill_count,
            member_detail_count=result.member_detail_count,
            bill_detail_count=result.bill_detail_count,
            cosponsor_file_count=result.cosponsor_file_count,
            house_vote_count=result.house_vote_count,
            senate_vote_count=result.senate_vote_count,
        ),
    }


def _handle_materialize_history_backfill_inputs(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    issues.extend(
        _positive_int_sequence_issues(
            "disclosures_year",
            getattr(args, "disclosures_year", None),
        )
    )
    if issues:
        return _command_issue_result("materialize-history-backfill-inputs", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("materialize-history-backfill-inputs", [limit_issue])

    _, _, congress_summary, _, disclosures_summary, _, readiness = (
        _materialize_history_backfill_inputs(args)
    )
    return {
        "ok": readiness.ready_to_replay,
        "command": "materialize-history-backfill-inputs",
        "congress_archive": congress_summary,
        "disclosures_bundle": disclosures_summary,
        "input_readiness": readiness.model_dump(mode="json"),
    }


def _handle_run_history_launch_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    issues.extend(
        _positive_int_sequence_issues(
            "disclosures_year",
            getattr(args, "disclosures_year", None),
        )
    )
    if issues:
        return _command_issue_result("run-history-launch-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-history-launch-local", [limit_issue])

    (
        ctx,
        congress_input_path,
        congress_summary,
        _disclosures_bundle_path,
        disclosures_summary,
        disclosures_bundle,
        readiness,
    ) = _materialize_history_backfill_inputs(args, load_bundle_for_replay=True)
    base = {
        "command": "run-history-launch-local",
        "congress_archive": congress_summary,
        "disclosures_bundle": disclosures_summary,
        "input_readiness": readiness.model_dump(mode="json"),
    }
    if not readiness.ready_to_replay:
        return {
            "ok": False,
            **base,
            "history_backfill": None,
        }

    result = run_history_backfill_local_command(
        ctx,
        congress_input_path,
        disclosures_bundle
        if disclosures_bundle is not None
        else load_disclosures_bundle(Path(args.disclosures_bundle_path)),
        congress=args.congress,
        target_root=Path(args.target_root),
        aggregate_root=Path(args.aggregate_root) if args.aggregate_root is not None else None,
        start_date=args.start_date,
        end_date=args.end_date,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        artifact_root=Path(args.disclosures_artifact_root),
        overwrite=args.overwrite,
        continue_on_error=args.continue_on_error,
        input_readiness=readiness,
    )
    return {
        "ok": result.ok,
        **base,
        "history_backfill": summarize_local_history_backfill_result(result),
    }


def _handle_materialize_disclosures_bundle(args: Any) -> dict[str, Any]:
    year_issues = _positive_int_sequence_issues("year", getattr(args, "year", None))
    if year_issues:
        return _command_issue_result("materialize-disclosures-bundle", year_issues)

    result = materialize_disclosures_bundle(
        years=args.year,
        chamber=args.chamber,
        bundle_path=Path(args.bundle_path),
        artifact_root=Path(args.artifact_root),
    )
    return {
        "ok": True,
        "command": "materialize-disclosures-bundle",
        "bundle_path": str(result.bundle_path),
        "artifact_root": str(result.artifact_root),
        "chamber": result.chamber,
        "years": list(result.years),
        "artifact_count": result.artifact_count,
        "house_count": result.house_count,
        "senate_count": result.senate_count,
    }


def _handle_materialize_bill_semantics(args: Any) -> dict[str, Any]:
    if not _is_optional_non_negative_plain_int(getattr(args, "limit", None)):
        return _command_issue_result(
            "materialize-bill-semantics",
            ["limit must be a non-negative integer"],
        )

    runtime = build_runtime()
    conn = open_connection(runtime.context)
    targeting_requested = _materialize_bill_semantic_targeting_requested(args)
    target_bill_keys = _materialize_bill_semantic_target_keys(args)
    unsupported_target_bill_keys = _unsupported_bill_semantic_target_keys(target_bill_keys)
    feature_cutoff = getattr(args, "feature_cutoff", None)
    bill_rows = fetch_bill_semantic_input_rows(conn, feature_cutoff=feature_cutoff)
    matched_bill_keys: list[str] = []
    if targeting_requested:
        bill_rows_by_key = [(_materialize_bill_semantic_row_key(row), row) for row in bill_rows]
        target_key_set = set(target_bill_keys)
        bill_rows_by_key = [
            (bill_key, row)
            for bill_key, row in bill_rows_by_key
            if bill_key is not None and bill_key in target_key_set
        ]
        bill_rows = [row for _, row in bill_rows_by_key]
        matched_bill_keys = [bill_key for bill_key, _ in bill_rows_by_key]
    unmatched_bill_keys = sorted(set(target_bill_keys) - set(matched_bill_keys))
    quality_gate_failures: list[str] = []
    if getattr(args, "fail_on_unmatched_targets", False) and unmatched_bill_keys:
        quality_gate_failures.append("unmatched_target_bill_keys")
    if getattr(args, "dry_run", False):
        selected_bill_rows = bill_rows[: args.limit] if args.limit is not None else bill_rows
        plan_payload = _bill_semantic_materialization_plan(
            args=args,
            selected_bill_rows=selected_bill_rows,
            target_bill_keys=target_bill_keys,
            matched_bill_keys=matched_bill_keys,
        )
        plan_output = (
            Path(args.plan_output) if getattr(args, "plan_output", None) is not None else None
        )
        plan_output_sha256: str | None = None
        if plan_output is not None:
            plan_output_sha256 = _write_json_artifact(plan_output, plan_payload)
        return _attach_materialize_bill_semantics_summary_output(
            args,
            {
                "ok": not quality_gate_failures,
                "command": "materialize-bill-semantics",
                "dry_run": True,
                "quality_gate_failures": quality_gate_failures,
                "output_root": str(Path(args.output_root)),
                "model": args.model,
                "requested_count": len(selected_bill_rows),
                "written_count": 0,
                "cached_count": 0,
                "target_bill_keys": target_bill_keys,
                "matched_bill_keys": sorted(set(matched_bill_keys)),
                "unmatched_bill_keys": unmatched_bill_keys,
                "unsupported_target_bill_keys": unsupported_target_bill_keys,
                "plan_output": str(plan_output) if plan_output is not None else None,
                "plan_output_sha256": plan_output_sha256,
            },
            index_sha256=None,
        )
    if quality_gate_failures:
        return _attach_materialize_bill_semantics_summary_output(
            args,
            {
                "ok": False,
                "command": "materialize-bill-semantics",
                "dry_run": False,
                "quality_gate_failures": quality_gate_failures,
                "output_root": str(Path(args.output_root)),
                "model": args.model,
                "requested_count": 0,
                "written_count": 0,
                "cached_count": 0,
                "target_bill_keys": target_bill_keys,
                "matched_bill_keys": sorted(set(matched_bill_keys)),
                "unmatched_bill_keys": unmatched_bill_keys,
                "unsupported_target_bill_keys": unsupported_target_bill_keys,
                "index_path": None,
                "index_sha256": None,
            },
            index_sha256=None,
        )
    extractor = (
        OpenAIBillSemanticExtractor.from_env(model=args.model)
        if bill_rows
        else cast(OpenAIBillSemanticExtractor, _NoopBillSemanticExtractor())
    )
    result = materialize_bill_semantics(
        bill_rows=bill_rows,
        output_root=Path(args.output_root),
        extractor=extractor,
        limit=args.limit,
        overwrite=args.overwrite,
    )
    index_sha256 = hashlib.sha256(result.index_path.read_bytes()).hexdigest()
    return _attach_materialize_bill_semantics_summary_output(
        args,
        {
            "ok": True,
            "command": "materialize-bill-semantics",
            "dry_run": False,
            "quality_gate_failures": [],
            "output_root": str(result.output_root),
            "model": args.model,
            "requested_count": result.requested_count,
            "written_count": result.written_count,
            "cached_count": result.cached_count,
            "target_bill_keys": target_bill_keys,
            "matched_bill_keys": sorted(set(matched_bill_keys)),
            "unmatched_bill_keys": unmatched_bill_keys,
            "unsupported_target_bill_keys": unsupported_target_bill_keys,
            "index_path": str(result.index_path),
            "index_sha256": index_sha256,
        },
        index_sha256=index_sha256,
    )


def _attach_materialize_bill_semantics_summary_output(
    args: Any,
    summary: dict[str, Any],
    *,
    index_sha256: str | None,
) -> dict[str, Any]:
    source_report_metadata = _materialize_bill_semantics_source_report_metadata(args)
    summary["run_metadata"] = {
        "command": "materialize-bill-semantics",
        "dry_run": bool(summary.get("dry_run")),
        "model": str(getattr(args, "model", "")),
        "output_root": str(Path(args.output_root)),
        "feature_cutoff": (
            getattr(args, "feature_cutoff").isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "limit": getattr(args, "limit", None),
        "overwrite": bool(getattr(args, "overwrite", False)),
        "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
        "target_bill_keys": list(summary.get("target_bill_keys", [])),
        "matched_bill_keys": list(summary.get("matched_bill_keys", [])),
        "unmatched_bill_keys": list(summary.get("unmatched_bill_keys", [])),
        "index_sha256": index_sha256,
        "source_state": _materialize_bill_semantics_source_state(
            args=args,
            summary=summary,
            index_sha256=index_sha256,
            source_report_metadata=source_report_metadata,
        ),
    }
    if source_report_metadata:
        summary["run_metadata"]["source_report"] = source_report_metadata
    summary_output_arg = getattr(args, "summary_output", None)
    if summary_output_arg is None:
        return summary
    summary_output = Path(summary_output_arg)
    summary_output_sha256 = _write_json_artifact(summary_output, summary)
    summary["summary_output"] = str(summary_output)
    summary["summary_output_sha256"] = summary_output_sha256
    return summary


def _materialize_bill_semantics_source_state(
    *,
    args: Any,
    summary: dict[str, Any],
    index_sha256: str | None,
    source_report_metadata: dict[str, Any],
) -> dict[str, Any]:
    source_state: dict[str, Any] = {
        "dry_run": bool(summary.get("dry_run")),
        "model": str(getattr(args, "model", "")),
        "output_root": str(Path(args.output_root)),
        "feature_cutoff": (
            getattr(args, "feature_cutoff").isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "requested_count": _plain_int_or_zero(summary.get("requested_count")),
        "written_count": _plain_int_or_zero(summary.get("written_count")),
        "cached_count": _plain_int_or_zero(summary.get("cached_count")),
        "target_bill_count": len(list(summary.get("target_bill_keys", []))),
        "matched_bill_count": len(list(summary.get("matched_bill_keys", []))),
        "unmatched_bill_count": len(list(summary.get("unmatched_bill_keys", []))),
        "index_path": summary.get("index_path"),
        "index_sha256": index_sha256,
    }
    plan_output = summary.get("plan_output")
    if plan_output is not None:
        source_state["plan_output"] = str(plan_output)
    plan_output_sha256 = summary.get("plan_output_sha256")
    if plan_output_sha256 is not None:
        source_state["plan_output_sha256"] = str(plan_output_sha256)
    unsupported_target_bill_keys = _string_list(summary.get("unsupported_target_bill_keys"))
    if unsupported_target_bill_keys:
        source_state["unsupported_target_bill_count"] = len(unsupported_target_bill_keys)
        source_state["unsupported_target_bill_keys"] = unsupported_target_bill_keys
    if source_report_metadata:
        source_state["source_report"] = source_report_metadata
    return source_state


def _materialize_bill_semantics_source_report_metadata(args: Any) -> dict[str, Any]:
    missing_from_report = getattr(args, "missing_from_report", None)
    if not missing_from_report:
        return {}
    report_path = Path(str(missing_from_report))
    metadata: dict[str, Any] = {"path": str(report_path)}
    if report_path.is_file():
        metadata["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        report_metadata = _bill_semantic_plan_report_metadata(report_path)
        metadata["missing_bill_keys"] = report_metadata.get(
            "missing_from_report_missing_bill_keys",
            [],
        )
        metadata["cutoff_ineligible_bill_keys"] = report_metadata.get(
            "missing_from_report_cutoff_ineligible_bill_keys",
            [],
        )
        metadata["run_metadata"] = report_metadata.get("missing_from_report_run_metadata")
    return metadata


def _bill_semantic_materialization_plan(
    *,
    args: Any,
    selected_bill_rows: list[dict[str, Any]],
    target_bill_keys: list[str],
    matched_bill_keys: list[str],
) -> dict[str, Any]:
    matched_bills: list[dict[str, Any]] = []
    for row in selected_bill_rows:
        bill_input = bill_semantic_input_from_row(row)
        matched_bills.append(
            {
                **bill_input.model_dump(mode="json"),
                "source_anchor_count": len(bill_input.source_anchors),
            }
        )
    missing_from_report = getattr(args, "missing_from_report", None)
    missing_from_report_path = Path(str(missing_from_report)) if missing_from_report else None
    missing_from_report_metadata = (
        _bill_semantic_plan_report_metadata(missing_from_report_path)
        if missing_from_report_path is not None and missing_from_report_path.is_file()
        else {}
    )
    inputs = {
        "bill_keys": [str(key) for key in getattr(args, "bill_key", None) or []],
        "missing_from_report": (
            str(missing_from_report_path) if missing_from_report_path is not None else None
        ),
        "missing_from_report_sha256": (
            hashlib.sha256(missing_from_report_path.read_bytes()).hexdigest()
            if missing_from_report_path is not None and missing_from_report_path.is_file()
            else None
        ),
        **missing_from_report_metadata,
    }
    sorted_matched_bill_keys = sorted(set(matched_bill_keys))
    unmatched_bill_keys = sorted(set(target_bill_keys) - set(matched_bill_keys))
    unsupported_target_bill_keys = _unsupported_bill_semantic_target_keys(target_bill_keys)
    requested_count = len(selected_bill_rows)
    return {
        "command": "materialize-bill-semantics",
        "dry_run": True,
        "output_root": str(Path(args.output_root)),
        "model": args.model,
        "feature_cutoff": (
            args.feature_cutoff.isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "limit": args.limit,
        "overwrite": args.overwrite,
        "fail_on_unmatched_targets": getattr(args, "fail_on_unmatched_targets", False),
        "inputs": inputs,
        "run_metadata": {
            "command": "materialize-bill-semantics",
            "dry_run": True,
            "model": str(getattr(args, "model", "")),
            "output_root": str(Path(args.output_root)),
            "feature_cutoff": (
                getattr(args, "feature_cutoff").isoformat()
                if getattr(args, "feature_cutoff", None) is not None
                else None
            ),
            "limit": getattr(args, "limit", None),
            "overwrite": bool(getattr(args, "overwrite", False)),
            "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
            "source_state": _bill_semantics_plan_verify_source_state(
                feature_cutoff=(
                    getattr(args, "feature_cutoff").isoformat()
                    if getattr(args, "feature_cutoff", None) is not None
                    else None
                ),
                target_bill_keys=target_bill_keys,
                matched_bill_keys=sorted_matched_bill_keys,
                unmatched_bill_keys=unmatched_bill_keys,
                unsupported_target_bill_keys=unsupported_target_bill_keys,
                matched_bills=matched_bills,
                requested_count=requested_count,
                inputs=inputs,
            ),
        },
        "target_bill_keys": target_bill_keys,
        "matched_bill_keys": sorted_matched_bill_keys,
        "unmatched_bill_keys": unmatched_bill_keys,
        "unsupported_target_bill_keys": unsupported_target_bill_keys,
        "requested_count": requested_count,
        "matched_bills": matched_bills,
    }


class _NoopBillSemanticExtractor:
    def extract(self, bill_input: Any) -> Any:
        raise RuntimeError(f"unexpected semantic extraction for {bill_input!r}")


def _materialize_bill_semantic_targeting_requested(args: Any) -> bool:
    return (
        bool(getattr(args, "bill_key", None))
        or getattr(args, "missing_from_report", None) is not None
    )


def _materialize_bill_semantic_row_key(row: dict[str, Any]) -> str | None:
    congress = row.get("congress")
    bill_type = row.get("bill_type")
    bill_number = row.get("bill_number")
    if isinstance(congress, bool) or isinstance(bill_number, bool):
        return None
    if not isinstance(congress, int) or not isinstance(bill_number, int):
        return None
    if not isinstance(bill_type, str) or not bill_type.strip():
        return None
    return f"{congress}-{bill_type.strip().lower()}-{bill_number}"


def _materialize_bill_semantic_target_keys(args: Any) -> list[str]:
    explicit_keys = [str(key) for key in getattr(args, "bill_key", None) or []]
    report_path = getattr(args, "missing_from_report", None)
    report_keys = (
        _bill_semantic_missing_keys_from_report(Path(report_path))
        if report_path is not None
        else []
    )
    return sorted(set(explicit_keys + report_keys))


def _unsupported_bill_semantic_target_keys(bill_keys: list[str]) -> list[str]:
    return sorted(
        {bill_key for bill_key in bill_keys if not re.fullmatch(r"\d+-[a-z]+-\d+", bill_key)}
    )


def _bill_semantic_missing_keys_from_report(path: Path) -> list[str]:
    return _bill_semantic_report_keys_from_coverage(path, "missing_bill_keys")


def _bill_semantic_cutoff_ineligible_keys_from_report(path: Path) -> list[str]:
    return _bill_semantic_report_keys_from_coverage(path, "cutoff_ineligible_bill_keys")


def _bill_semantic_report_keys_from_coverage(path: Path, key_name: str) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("prediction eval report must be a JSON object")

    coverage_fields = [
        "bill_semantic_coverage",
        "training_bill_semantic_coverage",
        "evaluation_bill_semantic_coverage",
    ]
    keys: list[str] = []
    seen_coverage = False
    for field in coverage_fields:
        coverage = data.get(field)
        if coverage is None:
            continue
        if not isinstance(coverage, dict):
            raise ValueError(f"prediction eval report {field} must be a JSON object")
        seen_coverage = True
        field_keys = coverage.get(key_name, [])
        if not isinstance(field_keys, list):
            raise ValueError(f"prediction eval report {field}.{key_name} must be a list")
        keys.extend(str(key) for key in field_keys)
    if not seen_coverage:
        raise ValueError("prediction eval report missing bill semantic coverage")
    return sorted(set(keys))


def _bill_semantic_plan_report_metadata(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    run_metadata = data.get("run_metadata") if isinstance(data, dict) else None
    return {
        "missing_from_report_missing_bill_keys": _bill_semantic_missing_keys_from_report(path),
        "missing_from_report_cutoff_ineligible_bill_keys": (
            _bill_semantic_cutoff_ineligible_keys_from_report(path)
        ),
        "missing_from_report_run_metadata": (
            run_metadata if isinstance(run_metadata, dict) else None
        ),
    }


def _handle_verify_bill_semantics_plan(args: Any) -> dict[str, Any]:
    plan_path = Path(args.plan)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    checked = 0

    def failure_result(
        *,
        checked: int,
        issues: list[str],
        quality_gate_failures: list[str] | None = None,
    ) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-bill-semantics-plan",
                "plan": str(plan_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": quality_gate_failures or [],
                "quality_gate_failure_count": len(quality_gate_failures or []),
                "run_metadata": _bill_semantics_plan_verify_run_metadata(
                    args,
                    plan_path,
                ),
            },
        )

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"failed to load plan: {exc}"])
    if not isinstance(plan, dict):
        return failure_result(checked=0, issues=["plan must be an object"])
    checked += 1
    if plan.get("command") != "materialize-bill-semantics":
        issues.append("plan command must be materialize-bill-semantics")
    if plan.get("dry_run") is not True:
        issues.append("plan dry_run must be true")

    target_bill_keys = _string_list_from_plan(plan, "target_bill_keys", issues)
    matched_bill_keys = _string_list_from_plan(plan, "matched_bill_keys", issues)
    unmatched_bill_keys = _string_list_from_plan(plan, "unmatched_bill_keys", issues)
    unsupported_target_bill_keys = _string_list_from_plan(
        plan,
        "unsupported_target_bill_keys",
        issues,
        optional=True,
    )
    expected_unsupported = _unsupported_bill_semantic_target_keys(target_bill_keys)
    if unsupported_target_bill_keys != expected_unsupported:
        issues.append(
            "unsupported_target_bill_keys mismatch: "
            f"expected {expected_unsupported!r}, got {unsupported_target_bill_keys!r}"
        )
    expected_unmatched = sorted(set(target_bill_keys) - set(matched_bill_keys))
    if unmatched_bill_keys != expected_unmatched:
        issues.append(
            "unmatched_bill_keys mismatch: "
            f"expected {expected_unmatched!r}, got {unmatched_bill_keys!r}"
        )
    fail_on_unmatched_targets = bool(
        getattr(args, "fail_on_unmatched_targets", False)
        or plan.get("fail_on_unmatched_targets") is True
    )
    if fail_on_unmatched_targets and unmatched_bill_keys:
        quality_gate_failures.append("unmatched_target_bill_keys")

    matched_bills = plan.get("matched_bills")
    if not isinstance(matched_bills, list):
        issues.append("matched_bills must be a list")
        matched_bills = []
    else:
        require_matched_source_anchors = bool(
            getattr(args, "require_matched_source_anchors", False)
        )
        feature_cutoff = _date_from_iso_string(plan.get("feature_cutoff"))
        if _is_present_invalid_iso_date(plan.get("feature_cutoff")):
            issues.append("feature_cutoff must be an ISO date")
        _validate_bill_semantics_plan_matched_bills(
            matched_bills=matched_bills,
            matched_bill_keys=matched_bill_keys,
            issues=issues,
            quality_gate_failures=quality_gate_failures,
            require_matched_source_anchors=require_matched_source_anchors,
            feature_cutoff=feature_cutoff,
        )
    requested_count = plan.get("requested_count")
    if not _is_plain_int(requested_count) or requested_count < 0:
        issues.append("requested_count must be a non-negative integer")
    elif requested_count != len(matched_bills):
        issues.append(
            f"requested_count mismatch: expected {len(matched_bills)}, got {requested_count}"
        )

    inputs = plan.get("inputs")
    require_source_report = bool(getattr(args, "require_source_report", False))
    if inputs is None:
        if require_source_report:
            issues.append("inputs missing")
    else:
        if not isinstance(inputs, dict):
            issues.append("inputs must be an object")
        else:
            checked += _validate_bill_semantics_plan_inputs(
                inputs,
                issues,
                require_source_report=require_source_report,
            )
    plan_source_state = _bill_semantics_plan_verify_source_state(
        feature_cutoff=plan.get("feature_cutoff"),
        target_bill_keys=target_bill_keys,
        matched_bill_keys=matched_bill_keys,
        unmatched_bill_keys=unmatched_bill_keys,
        unsupported_target_bill_keys=unsupported_target_bill_keys,
        matched_bills=matched_bills,
        requested_count=requested_count if _is_plain_int(requested_count) else None,
        inputs=inputs if isinstance(inputs, dict) else None,
    )
    plan_run_metadata = plan.get("run_metadata")
    if plan_run_metadata is not None:
        if not isinstance(plan_run_metadata, dict):
            issues.append("plan run_metadata must be an object")
        else:
            checked += 1
            _validate_bill_semantics_plan_run_metadata(
                run_metadata=plan_run_metadata,
                expected_source_state=plan_source_state,
                issues=issues,
            )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-bill-semantics-plan",
            "plan": str(plan_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "target_bill_keys": target_bill_keys,
            "matched_bill_keys": matched_bill_keys,
            "unmatched_bill_keys": unmatched_bill_keys,
            "unsupported_target_bill_keys": unsupported_target_bill_keys,
            "fail_on_unmatched_targets": fail_on_unmatched_targets,
            "require_source_report": require_source_report,
            "matched_bill_count": len(matched_bills),
            "run_metadata": _bill_semantics_plan_verify_run_metadata(
                args,
                plan_path,
                source_state=plan_source_state,
            ),
        },
    )


def _string_list_from_plan(
    plan: dict[str, Any],
    key: str,
    issues: list[str],
    *,
    optional: bool = False,
) -> list[str]:
    value = plan.get(key)
    if value is None and optional:
        return []
    if not isinstance(value, list):
        issues.append(f"{key} must be a list")
        return []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        issues.append(f"{key} must contain only non-empty strings")
        return []
    return sorted(value)


def _validate_bill_semantics_plan_matched_bills(
    *,
    matched_bills: list[Any],
    matched_bill_keys: list[str],
    issues: list[str],
    quality_gate_failures: list[str],
    require_matched_source_anchors: bool = False,
    feature_cutoff: dt.date | None = None,
) -> None:
    seen_keys: list[str] = []
    missing_source_anchor = False
    future_available_at = False
    for index, item in enumerate(matched_bills):
        if not isinstance(item, dict):
            issues.append(f"matched_bills[{index}] must be an object")
            continue
        bill_key = item.get("bill_key")
        if bill_key is None:
            issues.append(f"matched_bills[{index}].bill_key is required")
            continue
        seen_keys.append(str(bill_key))
        if _is_present_invalid_iso_date(item.get("available_at")):
            issues.append(f"matched_bills[{index}].available_at must be an ISO date")
        available_at = _date_from_iso_string(item.get("available_at"))
        if (
            feature_cutoff is not None
            and available_at is not None
            and available_at > feature_cutoff
        ):
            future_available_at = True
        source_anchors = item.get("source_anchors")
        if source_anchors is not None and not isinstance(source_anchors, list):
            issues.append(f"matched_bills[{index}].source_anchors must be a list")
        elif require_matched_source_anchors:
            if not source_anchors:
                missing_source_anchor = True
            elif any(not _is_official_plan_source_anchor(anchor) for anchor in source_anchors):
                missing_source_anchor = True
        source_anchor_count = item.get("source_anchor_count")
        if source_anchor_count is not None:
            if not _is_plain_int(source_anchor_count) or source_anchor_count < 0:
                issues.append(f"matched_bills[{index}].source_anchor_count must be non-negative")
            elif isinstance(source_anchors, list) and source_anchor_count != len(source_anchors):
                issues.append(
                    f"matched_bills[{index}].source_anchor_count mismatch: "
                    f"expected {len(source_anchors)}, got {source_anchor_count}"
                )
        elif require_matched_source_anchors:
            missing_source_anchor = True
    unknown_keys = sorted(set(seen_keys) - set(matched_bill_keys))
    if unknown_keys:
        issues.append(f"matched_bills contains keys outside matched_bill_keys: {unknown_keys!r}")
    if missing_source_anchor:
        quality_gate_failures.append("matched_bill_source_anchors_missing")
    if future_available_at:
        quality_gate_failures.append("matched_bill_available_after_feature_cutoff")


def _date_from_iso_string(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _is_present_invalid_iso_date(value: Any) -> bool:
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        return True
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return True
    return False


def _is_official_plan_source_anchor(anchor: Any) -> bool:
    if not isinstance(anchor, dict):
        return False
    source_type = anchor.get("source_type")
    if not isinstance(source_type, str):
        return False
    url = anchor.get("url")
    if url is not None and not isinstance(url, str):
        return False
    return is_official_source_url(source_type, url)


def _validate_bill_semantics_plan_inputs(
    inputs: dict[str, Any],
    issues: list[str],
    *,
    require_source_report: bool = False,
) -> int:
    report_path_raw = inputs.get("missing_from_report")
    expected_report_sha = inputs.get("missing_from_report_sha256")
    if require_source_report:
        if not report_path_raw:
            issues.append("missing_from_report: missing path")
        if not expected_report_sha:
            issues.append("missing_from_report: missing sha256")
        if "missing_from_report_missing_bill_keys" not in inputs:
            issues.append("missing_from_report: missing bill keys missing")
        if "missing_from_report_run_metadata" not in inputs:
            issues.append("missing_from_report: run_metadata missing")
    if report_path_raw and not expected_report_sha:
        issues.append("missing_from_report: missing sha256")
        return 0
    if expected_report_sha and not report_path_raw:
        issues.append("missing_from_report: missing path")
        return 0
    if not report_path_raw and not expected_report_sha:
        return 0
    if not isinstance(expected_report_sha, str) or not _is_sha256_hex(expected_report_sha):
        issues.append("missing_from_report: sha256 invalid")
        return 0
    report_path = Path(str(report_path_raw))
    if not report_path.is_file():
        issues.append(f"missing_from_report: file not found: {report_path}")
        return 1
    actual_report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if actual_report_sha != expected_report_sha:
        issues.append(
            "missing_from_report: sha256 mismatch: "
            f"expected {expected_report_sha}, got {actual_report_sha}"
        )
    expected_missing_keys = inputs.get("missing_from_report_missing_bill_keys")
    if expected_missing_keys is not None:
        if not isinstance(expected_missing_keys, list):
            issues.append("missing_from_report: missing bill keys must be a list")
        else:
            actual_missing_keys = _bill_semantic_missing_keys_from_report(report_path)
            normalized_expected_missing_keys = sorted(str(key) for key in expected_missing_keys)
            if normalized_expected_missing_keys != actual_missing_keys:
                issues.append("missing_from_report: missing bill keys mismatch")
    expected_cutoff_ineligible_keys = inputs.get("missing_from_report_cutoff_ineligible_bill_keys")
    if expected_cutoff_ineligible_keys is not None:
        if not isinstance(expected_cutoff_ineligible_keys, list):
            issues.append("missing_from_report: cutoff-ineligible bill keys must be a list")
        else:
            actual_cutoff_ineligible_keys = _bill_semantic_cutoff_ineligible_keys_from_report(
                report_path
            )
            normalized_expected_cutoff_keys = sorted(
                str(key) for key in expected_cutoff_ineligible_keys
            )
            if normalized_expected_cutoff_keys != actual_cutoff_ineligible_keys:
                issues.append("missing_from_report: cutoff-ineligible bill keys mismatch")
    expected_run_metadata = inputs.get("missing_from_report_run_metadata")
    if expected_run_metadata is not None:
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        actual_run_metadata = (
            report_data.get("run_metadata") if isinstance(report_data, dict) else None
        )
        if expected_run_metadata != actual_run_metadata:
            issues.append("missing_from_report: run_metadata mismatch")
    return 1


def _validate_bill_semantics_plan_run_metadata(
    *,
    run_metadata: dict[Any, Any],
    expected_source_state: dict[str, Any],
    issues: list[str],
) -> None:
    if run_metadata.get("command") != "materialize-bill-semantics":
        issues.append("plan run_metadata mismatch: command")
    if run_metadata.get("dry_run") is not True:
        issues.append("plan run_metadata mismatch: dry_run")
    source_state = run_metadata.get("source_state")
    if source_state is None:
        issues.append("plan run_metadata source_state missing")
        return
    if not isinstance(source_state, dict):
        issues.append("plan run_metadata source_state must be an object")
        return
    invalid_count_keys = _bill_semantics_plan_invalid_source_state_count_keys(source_state)
    issues.extend(
        f"plan run_metadata source_state {issue}"
        for issue in _bill_semantics_plan_source_state_count_issues(
            source_state,
            invalid_count_keys=invalid_count_keys,
        )
    )
    for key in sorted(set(source_state) - set(expected_source_state)):
        issues.append(f"plan run_metadata source_state unexpected: {key}")
    for key, expected_value in expected_source_state.items():
        if key in invalid_count_keys:
            continue
        if source_state.get(key) != expected_value:
            issues.append(f"plan run_metadata source_state mismatch: {key}")


def _bill_semantics_plan_invalid_source_state_count_keys(
    source_state: dict[str, Any],
) -> set[str]:
    keys = (
        "target_bill_count",
        "matched_bill_count",
        "unmatched_bill_count",
        "unsupported_target_bill_count",
        "matched_source_anchor_count",
        "matched_bill_source_anchor_count",
        "requested_count",
    )
    return {
        key
        for key in keys
        if key in source_state and not _is_non_negative_plain_int(source_state.get(key))
    }


def _bill_semantics_plan_source_state_count_issues(
    source_state: dict[str, Any],
    *,
    invalid_count_keys: set[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    invalid_keys = (
        invalid_count_keys
        if invalid_count_keys is not None
        else _bill_semantics_plan_invalid_source_state_count_keys(source_state)
    )
    for key in (
        "target_bill_count",
        "matched_bill_count",
        "unmatched_bill_count",
        "unsupported_target_bill_count",
        "matched_source_anchor_count",
        "matched_bill_source_anchor_count",
        "requested_count",
    ):
        if key in source_state and not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif key in invalid_keys:
            issues.append(f"{key} must be a non-negative integer")
    return issues


def _handle_verify_bill_semantics(args: Any) -> dict[str, Any]:
    root = Path(args.root)
    index_path = root / "index.json"
    required_model_names = _required_string_list(getattr(args, "require_model_name", None))
    index_sha256 = (
        hashlib.sha256(index_path.read_bytes()).hexdigest() if index_path.is_file() else None
    )
    try:
        payloads = load_bill_semantic_payloads(root)
    except Exception as exc:  # noqa: BLE001
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-bill-semantics",
                "root": str(root),
                "index_path": str(index_path),
                "index_sha256": index_sha256,
                "bill_count": 0,
                "issues": [str(exc)],
                "issue_count": 1,
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _bill_semantics_verify_run_metadata(
                    args,
                    root,
                    index_sha256,
                    source_state=_bill_semantics_unavailable_source_state(
                        root=root,
                        index_path=index_path,
                        index_sha256=index_sha256,
                    ),
                ),
            },
        )
    index_payload = _bill_semantics_index_payload(root)
    index_object = _bill_semantics_index_object(root)
    model_names = sorted(
        {payload.model_name for payload in payloads if payload.model_name is not None}
    )
    unofficial_source_anchor_count = _bill_semantics_unofficial_source_anchor_count(payloads)
    quality_gate_failures = [
        f"missing_model_name:{model_name}"
        for model_name in required_model_names
        if model_name not in model_names
    ]
    if index_payload.source_inputs_sha256 is not None and not _is_sha256_hex(
        index_payload.source_inputs_sha256
    ):
        quality_gate_failures.append("source_inputs_sha256_invalid")
    elif bool(getattr(args, "require_source_inputs_sha256", False)):
        if index_payload.source_inputs_sha256 is None:
            quality_gate_failures.append("source_inputs_sha256_missing")
        if "source_bill_count" not in index_object:
            quality_gate_failures.append("source_bill_count_missing")
        if "source_bill_keys" not in index_object:
            quality_gate_failures.append("source_bill_keys_missing")
        quality_gate_failures.extend(
            _bill_semantics_index_run_metadata_failures(
                index_payload=index_payload,
                index_object=index_object,
            )
        )
        if unofficial_source_anchor_count:
            quality_gate_failures.append("unofficial_source_anchors")
    return _attach_optional_verification_output(
        args,
        {
            "ok": not quality_gate_failures,
            "command": "verify-bill-semantics",
            "root": str(root),
            "index_path": str(index_path),
            "index_sha256": index_sha256,
            "bill_count": len(payloads),
            "bill_keys": [payload.bill_key for payload in payloads],
            "model_names": model_names,
            "required_model_names": required_model_names,
            "source_bill_count": index_payload.source_bill_count,
            "source_bill_keys": list(index_payload.source_bill_keys),
            "source_inputs_sha256": index_payload.source_inputs_sha256,
            "unofficial_source_anchor_count": unofficial_source_anchor_count,
            "issues": [],
            "issue_count": 0,
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "run_metadata": _bill_semantics_verify_run_metadata(
                args,
                root,
                index_sha256,
                source_state=_bill_semantics_verify_source_state(
                    payloads=payloads,
                    index_payload=index_payload,
                    unofficial_source_anchor_count=unofficial_source_anchor_count,
                ),
            ),
        },
    )


def _bill_semantics_unofficial_source_anchor_count(
    payloads: list[Any],
) -> int:
    return sum(
        1
        for payload in payloads
        for anchor in payload.source_anchors
        if not is_official_source_url(anchor.source_type, anchor.url)
    )


def _handle_plan_history_backfill(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("plan-history-backfill", issues)

    plan = plan_congress_history_backfill(
        args.congress,
        target_root=Path(args.target_root),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return {
        "ok": True,
        "command": "plan-history-backfill",
        "congress": plan.congress,
        "cadence": plan.cadence,
        "date_window": {
            "start_date": plan.date_window.start_date.isoformat(),
            "end_date": plan.date_window.end_date.isoformat(),
            "bounded_by_today": plan.date_window.bounded_by_today,
        },
        "snapshot_count": len(plan.targets),
        "targets": [
            {
                "snapshot_id": target.snapshot_id,
                "snapshot_date": target.snapshot_date.isoformat(),
                "publish_root": str(target.publish_root),
            }
            for target in plan.targets
        ],
    }


def _handle_aggregate_history(args: Any) -> dict[str, Any]:
    from src.pipeline.history_aggregate_run import write_history_aggregate

    result = write_history_aggregate(
        [Path(root) for root in args.source_root],
        Path(args.target_root),
    )
    verify_result = verify_history_aggregate_local(Path(args.target_root))
    return {
        "ok": verify_result.ok,
        "command": "aggregate-history",
        "latest_snapshot_id": result.latest_snapshot_id,
        "snapshot_count": len(result.snapshot_index.snapshots),
        "member_history_count": result.member_history_count,
        "target_root": str(result.target_root),
        "verify": summarize_history_verify_result(verify_result),
    }


def _handle_verify_publish(args: Any) -> dict[str, Any]:
    result = verify_publish_local(Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-publish",
        **summarize_publish_verify_result(result),
    }


def _handle_verify_publish_roundtrip(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    result = verify_publish_roundtrip_local(runtime.context, Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-publish-roundtrip",
        "roundtrip": summarize_publish_roundtrip_result(result),
    }


def _handle_verify_history_aggregate(args: Any) -> dict[str, Any]:
    result = verify_history_aggregate_local(Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-history-aggregate",
        **summarize_history_verify_result(result),
    }


def _handle_prediction_backtest(args: Any) -> dict[str, Any]:
    return handle_prediction_backtest_command(args)


def _handle_prediction_input_inventory(args: Any) -> dict[str, Any]:
    return handle_prediction_input_inventory_command(args)


def _handle_verify_prediction_input_inventory(args: Any) -> dict[str, Any]:
    return verify_prediction_input_inventory_command(args)


def _prediction_input_inventory_source_state(payload: Any) -> dict[str, Any]:
    return _prediction_input_inventory_source_state_impl(payload)


def _handle_prediction_eval_report(args: Any) -> dict[str, Any]:
    manifest_output_arg = getattr(args, "manifest_output", None)
    if manifest_output_arg is not None and (args.output is None or args.dataset_output is None):
        raise ValueError("manifest-output requires --output and --dataset-output")
    threshold_issues = [
        *_prediction_window_date_issues(args),
        *_prediction_eval_invalid_coverage_threshold_issues(args),
    ]
    if threshold_issues:
        return {
            "ok": False,
            "command": "prediction-eval-report",
            "issues": threshold_issues,
            "issue_count": len(threshold_issues),
            "quality_gate_failures": [],
            "quality_gate_failure_count": 0,
            "output": getattr(args, "output", None),
            "output_sha256": None,
            "dataset_output": getattr(args, "dataset_output", None),
            "dataset_output_sha256": None,
            "manifest_output": manifest_output_arg,
            "manifest_output_sha256": None,
        }
    runtime = build_runtime()
    bill_semantics_root = (
        Path(args.bill_semantics_root) if args.bill_semantics_root is not None else None
    )
    bill_semantics_index_sha256 = _bill_semantics_index_sha256(bill_semantics_root)
    bill_semantics_model_names = _bill_semantics_index_model_names(bill_semantics_root)
    congress_archive_manifest = _congress_archive_manifest_metadata(
        getattr(args, "congress_archive_manifest", None)
    )
    result = run_prediction_eval_report_command(
        runtime.context,
        training_feature_cutoff=args.training_feature_cutoff,
        train_start=args.train_start,
        train_end=args.train_end,
        feature_cutoff=args.feature_cutoff,
        label_start=args.label_start,
        label_end=args.label_end,
        bill_semantics_root=bill_semantics_root,
    )
    source_state = _prediction_eval_source_state(result)
    run_metadata = {
        "command": "prediction-eval-report",
        **_prediction_window_run_metadata(args),
        "bill_semantics_root": (
            str(bill_semantics_root) if bill_semantics_root is not None else None
        ),
        "bill_semantics_index_sha256": bill_semantics_index_sha256,
        "bill_semantics_model_names": bill_semantics_model_names,
        "thresholds": _prediction_eval_thresholds(args),
        "source_state": source_state,
    }
    if congress_archive_manifest is not None:
        run_metadata["congress_archive_manifest"] = congress_archive_manifest
    payload = result.model_dump(mode="json")
    payload["run_metadata"] = run_metadata
    output = Path(args.output) if args.output is not None else None
    output_sha256: str | None = None
    if output is not None:
        output_sha256 = _write_json_artifact(output, payload)
    dataset_output = Path(args.dataset_output) if args.dataset_output is not None else None
    dataset_output_sha256: str | None = None
    if dataset_output is not None:
        dataset_payload = result.dataset.model_dump(mode="json")
        dataset_payload["run_metadata"] = run_metadata
        dataset_output_sha256 = _write_json_artifact(
            dataset_output,
            dataset_payload,
        )
    strict_readiness = bool(getattr(args, "strict_readiness", False))
    quality_gate_failures = _prediction_eval_quality_gate_failures(
        result,
        args,
        bill_semantics_model_names=bill_semantics_model_names,
    )
    readiness_ok = result.readiness.status == "ready" if strict_readiness else result.readiness.ok
    ok = readiness_ok and not quality_gate_failures
    failure_groups = getattr(result, "failure_groups", [])
    backfill_recommendations = getattr(result, "backfill_recommendations", [])
    readiness_warning_reasons = list(result.readiness.warning_reasons)
    if len(bill_semantics_model_names) > 1:
        readiness_warning_reasons.append("mixed_bill_semantics_models")
    summary = {
        "ok": ok,
        "command": "prediction-eval-report",
        "strict_readiness": strict_readiness,
        "source_state": source_state,
        "quality_gate_failures": quality_gate_failures,
        "readiness_status": result.readiness.status,
        "readiness_blocking_reasons": list(result.readiness.blocking_reasons),
        "readiness_warning_reasons": readiness_warning_reasons,
        "training_example_count": result.training_example_count,
        "evaluation_label_count": result.evaluation_label_count,
        "models": [model.model_dump(mode="json") for model in result.models],
        "dataset_training_examples": result.dataset.training.label_count,
        "dataset_evaluation_examples": result.dataset.evaluation.label_count,
        "dataset_feature_count": len(result.dataset.feature_names),
        "bill_semantic_required_count": result.bill_semantic_coverage.required_bill_count,
        "bill_semantic_covered_count": result.bill_semantic_coverage.covered_bill_count,
        "bill_semantic_missing_count": result.bill_semantic_coverage.missing_bill_count,
        "bill_semantic_cutoff_ineligible_count": (
            getattr(result.bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "bill_semantic_missing_keys": list(result.bill_semantic_coverage.missing_bill_keys),
        "bill_semantic_cutoff_ineligible_keys": list(
            getattr(result.bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "bill_semantic_coverage_rate": result.bill_semantic_coverage.coverage_rate,
        "bill_metadata_required_count": result.bill_metadata_coverage.required_bill_count,
        "bill_metadata_loaded_count": result.bill_metadata_coverage.loaded_bill_count,
        "bill_metadata_missing_count": result.bill_metadata_coverage.missing_bill_count,
        "bill_metadata_cutoff_ineligible_count": (
            getattr(result.bill_metadata_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "bill_metadata_missing_keys": list(result.bill_metadata_coverage.missing_bill_keys),
        "bill_metadata_cutoff_ineligible_keys": list(
            getattr(result.bill_metadata_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "bill_metadata_coverage_rate": result.bill_metadata_coverage.coverage_rate,
        "training_bill_semantic_required_count": (
            result.training_bill_semantic_coverage.required_bill_count
        ),
        "training_bill_semantic_covered_count": (
            result.training_bill_semantic_coverage.covered_bill_count
        ),
        "training_bill_semantic_missing_count": (
            result.training_bill_semantic_coverage.missing_bill_count
        ),
        "training_bill_semantic_cutoff_ineligible_count": (
            getattr(result.training_bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "training_bill_semantic_missing_keys": list(
            result.training_bill_semantic_coverage.missing_bill_keys
        ),
        "training_bill_semantic_cutoff_ineligible_keys": list(
            getattr(result.training_bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "training_bill_semantic_coverage_rate": (
            result.training_bill_semantic_coverage.coverage_rate
        ),
        "evaluation_bill_semantic_required_count": (
            result.evaluation_bill_semantic_coverage.required_bill_count
        ),
        "evaluation_bill_semantic_covered_count": (
            result.evaluation_bill_semantic_coverage.covered_bill_count
        ),
        "evaluation_bill_semantic_missing_count": (
            result.evaluation_bill_semantic_coverage.missing_bill_count
        ),
        "evaluation_bill_semantic_cutoff_ineligible_count": (
            getattr(result.evaluation_bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "evaluation_bill_semantic_missing_keys": list(
            result.evaluation_bill_semantic_coverage.missing_bill_keys
        ),
        "evaluation_bill_semantic_cutoff_ineligible_keys": list(
            getattr(result.evaluation_bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "evaluation_bill_semantic_coverage_rate": (
            result.evaluation_bill_semantic_coverage.coverage_rate
        ),
        "training_model_ready_rate": result.data_quality.training.model_ready_rate,
        "evaluation_model_ready_rate": result.data_quality.evaluation.model_ready_rate,
        "evaluation_source_url_coverage_rate": (
            result.data_quality.evaluation.source_url_coverage_rate
        ),
        "training_feature_source_coverage_count": len(result.training_feature_source_coverage),
        "training_feature_source_sourced_prediction_count": (
            _feature_source_sourced_prediction_count(result.training_feature_source_coverage)
        ),
        "training_feature_source_prediction_count": _feature_source_prediction_count(
            result.training_feature_source_coverage
        ),
        "training_feature_source_coverage_rate": _feature_source_coverage_rate(
            result.training_feature_source_coverage
        ),
        "training_feature_source_url_sourced_prediction_count": (
            _feature_source_url_sourced_prediction_count(result.training_feature_source_coverage)
        ),
        "training_feature_source_url_anchor_count": _feature_source_url_anchor_count(
            result.training_feature_source_coverage
        ),
        "training_feature_source_url_coverage_rate": _feature_source_url_coverage_rate(
            result.training_feature_source_coverage
        ),
        "training_feature_official_source_sourced_prediction_count": (
            _feature_source_official_source_sourced_prediction_count(
                result.training_feature_source_coverage
            )
        ),
        "training_feature_official_source_anchor_count": (
            _feature_source_official_source_anchor_count(result.training_feature_source_coverage)
        ),
        "training_feature_official_source_coverage_rate": (
            _feature_source_official_source_coverage_rate(result.training_feature_source_coverage)
        ),
        "evaluation_feature_source_coverage_count": len(result.evaluation_feature_source_coverage),
        "evaluation_feature_source_sourced_prediction_count": (
            _feature_source_sourced_prediction_count(result.evaluation_feature_source_coverage)
        ),
        "evaluation_feature_source_prediction_count": _feature_source_prediction_count(
            result.evaluation_feature_source_coverage
        ),
        "evaluation_feature_source_coverage_rate": _feature_source_coverage_rate(
            result.evaluation_feature_source_coverage
        ),
        "evaluation_feature_source_url_sourced_prediction_count": (
            _feature_source_url_sourced_prediction_count(result.evaluation_feature_source_coverage)
        ),
        "evaluation_feature_source_url_anchor_count": _feature_source_url_anchor_count(
            result.evaluation_feature_source_coverage
        ),
        "evaluation_feature_source_url_coverage_rate": _feature_source_url_coverage_rate(
            result.evaluation_feature_source_coverage
        ),
        "evaluation_feature_official_source_sourced_prediction_count": (
            _feature_source_official_source_sourced_prediction_count(
                result.evaluation_feature_source_coverage
            )
        ),
        "evaluation_feature_official_source_anchor_count": (
            _feature_source_official_source_anchor_count(result.evaluation_feature_source_coverage)
        ),
        "evaluation_feature_official_source_coverage_rate": (
            _feature_source_official_source_coverage_rate(result.evaluation_feature_source_coverage)
        ),
        "feature_source_coverage_count": len(result.feature_source_coverage),
        "cutoff_audit": _prediction_eval_cutoff_audit_summary(result.cutoff_audit),
        "bill_semantics_root": (
            str(bill_semantics_root) if bill_semantics_root is not None else None
        ),
        "bill_semantics_index_sha256": bill_semantics_index_sha256,
        "bill_semantics_model_names": bill_semantics_model_names,
        "learned_model_signal_count": len(result.learned_model.signal_names),
        "learned_model_intercept": getattr(result.learned_model, "intercept", None),
        "top_learned_signal_coefficients": _top_learned_signal_coefficients(result),
        "unavailable_signal_counts": dict(result.unavailable_signal_counts),
        "failure_case_count": len(result.top_failure_cases),
        "failure_group_count": len(failure_groups),
        "top_failure_groups": [group.model_dump(mode="json") for group in failure_groups[:10]],
        "backfill_recommendation_count": len(backfill_recommendations),
        "top_backfill_recommendations": [
            recommendation.model_dump(mode="json")
            for recommendation in backfill_recommendations[:10]
        ],
        "output": str(output) if output is not None else None,
        "output_sha256": output_sha256,
        "dataset_output": str(dataset_output) if dataset_output is not None else None,
        "dataset_output_sha256": dataset_output_sha256,
    }
    if congress_archive_manifest is not None:
        summary["congress_archive_manifest"] = congress_archive_manifest
    manifest_output = (
        Path(args.manifest_output) if getattr(args, "manifest_output", None) is not None else None
    )
    manifest_output_sha256: str | None = None
    if manifest_output is not None:
        manifest_output_sha256 = _write_json_artifact(
            manifest_output,
            _prediction_eval_manifest(summary, args),
        )
    summary["manifest_output"] = str(manifest_output) if manifest_output is not None else None
    summary["manifest_output_sha256"] = manifest_output_sha256
    return summary


def run_prediction_eval_window_plan_command(
    *,
    start_label_year: int,
    end_label_year: int,
    train_years: int = 2,
    label_years: int = 1,
    max_feature_cutoff: dt.date | None = None,
    output_dir: str = "out",
    bill_semantics_root: str | None = None,
    congress_archive_manifest: str | None = None,
    plan_artifact_path: str | None = None,
) -> PredictionEvalWindowPlanPayload:
    return build_prediction_eval_window_plan(
        start_label_year=start_label_year,
        end_label_year=end_label_year,
        train_years=train_years,
        label_years=label_years,
        max_feature_cutoff=max_feature_cutoff,
        output_dir=output_dir,
        bill_semantics_root=bill_semantics_root,
        congress_archive_manifest=congress_archive_manifest,
        plan_artifact_path=plan_artifact_path,
    )


def _handle_prediction_eval_window_plan(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("start_label_year", "end_label_year", "train_years", "label_years"),
    )
    if issues:
        return _command_issue_result("prediction-eval-window-plan", issues)

    result = run_prediction_eval_window_plan_command(
        start_label_year=args.start_label_year,
        end_label_year=args.end_label_year,
        train_years=args.train_years,
        label_years=args.label_years,
        max_feature_cutoff=args.max_feature_cutoff,
        output_dir=args.output_dir,
        bill_semantics_root=args.bill_semantics_root,
        congress_archive_manifest=getattr(args, "congress_archive_manifest", None),
        plan_artifact_path=args.output,
    )
    run_metadata = {
        "command": "prediction-eval-window-plan",
        "start_label_year": args.start_label_year,
        "end_label_year": args.end_label_year,
        "train_years": args.train_years,
        "label_years": args.label_years,
        "max_feature_cutoff": (
            args.max_feature_cutoff.isoformat() if args.max_feature_cutoff is not None else None
        ),
        "output_dir": args.output_dir,
        "bill_semantics_root": args.bill_semantics_root,
        "congress_archive_manifest": getattr(args, "congress_archive_manifest", None),
    }
    payload = result.model_dump(mode="json")
    payload["command"] = "prediction-eval-window-plan"
    payload["run_metadata"] = run_metadata
    output = Path(args.output) if args.output is not None else None
    output_sha256: str | None = None
    if output is not None:
        output_sha256 = _write_json_artifact(output, payload)
    return {
        "ok": True,
        "command": "prediction-eval-window-plan",
        "plan_version": result.plan_version,
        "window_count": result.window_count,
        "start_label_year": result.start_label_year,
        "end_label_year": result.end_label_year,
        "train_years": result.train_years,
        "label_years": result.label_years,
        "max_feature_cutoff": (
            result.max_feature_cutoff.isoformat() if result.max_feature_cutoff is not None else None
        ),
        "windows": [window.model_dump(mode="json") for window in result.windows],
        "summary_command": result.summary_command,
        "verify_summary_command": result.verify_summary_command,
        "verify_run_command": result.verify_run_command,
        "run_metadata": run_metadata,
        "output": str(output) if output is not None else None,
        "output_sha256": output_sha256,
    }


def _handle_verify_prediction_eval_window_plan(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-plan",
                "artifact": str(artifact_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_plan_verify_run_metadata(
                    args,
                    artifact_path,
                ),
            },
        )

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(artifact, dict):
        return failure_result(issues=["artifact must be an object"])

    issues: list[str] = []
    if artifact.get("command") not in (None, "prediction-eval-window-plan"):
        issues.append("artifact command must be prediction-eval-window-plan")
    plan_payload = {
        key: value for key, value in artifact.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"invalid window plan: {exc}"])

    if bool(getattr(args, "require_run_metadata", False)):
        _validate_prediction_eval_window_plan_run_metadata(
            artifact=artifact,
            plan=plan,
            issues=issues,
        )

    quality_gate_failures: list[str] = []
    min_window_count = getattr(args, "min_window_count", None)
    if min_window_count is not None:
        if not _is_plain_int(min_window_count):
            issues.append("min_window_count must be an integer")
        elif min_window_count < 0:
            issues.append("min_window_count must be a non-negative integer")
        elif plan.window_count < min_window_count:
            quality_gate_failures.append("window_count_below_minimum")

    require_plan_congress_archive_manifest = bool(
        _prediction_eval_window_plan_run_metadata_value(
            artifact,
            "congress_archive_manifest",
        )
    )
    command_issues = _prediction_eval_window_plan_command_issues(
        plan,
        require_congress_archive_manifest=require_plan_congress_archive_manifest,
    )
    if bool(getattr(args, "require_commands", False)):
        issues.extend(command_issues)

    source_state = _prediction_eval_window_plan_source_state(plan, command_issues)
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-eval-window-plan",
            "artifact": str(artifact_path),
            "checked": 1 + plan.window_count,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "plan_version": plan.plan_version,
            "window_count": plan.window_count,
            "start_label_year": plan.start_label_year,
            "end_label_year": plan.end_label_year,
            "min_window_count": min_window_count,
            "command_issue_count": len(command_issues),
            "command_issues": command_issues,
            "run_metadata": _prediction_eval_window_plan_verify_run_metadata(
                args,
                artifact_path,
                source_state=source_state,
            ),
        },
    )


def _prediction_eval_window_plan_run_metadata_value(
    artifact: dict[str, Any],
    key: str,
) -> Any:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None
    return run_metadata.get(key)


def _validate_prediction_eval_window_plan_run_metadata(
    *,
    artifact: dict[str, Any],
    plan: PredictionEvalWindowPlanPayload,
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append("run_metadata missing")
        return
    expected = {
        "command": "prediction-eval-window-plan",
        "start_label_year": plan.start_label_year,
        "end_label_year": plan.end_label_year,
        "train_years": plan.train_years,
        "label_years": plan.label_years,
        "max_feature_cutoff": (
            plan.max_feature_cutoff.isoformat() if plan.max_feature_cutoff is not None else None
        ),
    }
    for key, expected_value in expected.items():
        if run_metadata.get(key) != expected_value:
            issues.append(f"run_metadata {key} mismatch")
    for key in ("output_dir", "bill_semantics_root", "congress_archive_manifest"):
        if key not in run_metadata:
            issues.append(f"run_metadata {key} missing")


def _prediction_eval_window_plan_command_issues(
    plan: PredictionEvalWindowPlanPayload,
    *,
    require_congress_archive_manifest: bool = False,
) -> list[str]:
    issues: list[str] = []
    verifier_output_paths: list[tuple[str, str]] = []
    if not plan.summary_command.startswith(
        "python3 -m src.runtime.main prediction-eval-window-summary "
    ):
        issues.append("summary_command unsupported")
    if plan.window_count and "--report " not in plan.summary_command:
        issues.append("summary_command missing --report")
    if "--require-report-run-metadata" not in plan.summary_command:
        issues.append("summary_command missing --require-report-run-metadata")
    if "--require-non-overlapping-label-windows" not in plan.summary_command:
        issues.append("summary_command missing --require-non-overlapping-label-windows")
    if "--output " not in plan.summary_command:
        issues.append("summary_command missing --output")
    if not plan.verify_summary_command.startswith(
        "python3 -m src.runtime.main verify-prediction-eval-window-summary "
    ):
        issues.append("verify_summary_command unsupported")
    if not plan.verify_run_command.startswith(
        "python3 -m src.runtime.main verify-prediction-eval-window-run "
    ):
        issues.append("verify_run_command unsupported")
    for fragment in (
        "--artifact ",
        "--plan ",
        "--require-plan-match",
        "--require-run-metadata",
        "--require-current-report-hashes",
        "--require-non-overlapping-label-windows",
        f"--min-window-count {plan.window_count}",
        "--output ",
    ):
        if fragment not in plan.verify_summary_command:
            issues.append(f"verify_summary_command missing {fragment.strip()}")
    for fragment in (
        "--plan ",
        "--summary-verify ",
        "--require-window-verifiers",
        "--require-summary-verify",
        "--output ",
    ):
        if fragment not in plan.verify_run_command:
            issues.append(f"verify_run_command missing {fragment.strip()}")
    for index, window in enumerate(plan.windows):
        eval_command = window.eval_report_command
        if not eval_command.startswith("python3 -m src.runtime.main prediction-eval-report "):
            issues.append(f"windows[{index}].eval_report_command unsupported")
        inventory_command = window.input_inventory_command
        if not inventory_command.startswith(
            "python3 -m src.runtime.main prediction-input-inventory "
        ):
            issues.append(f"windows[{index}].input_inventory_command unsupported")
        inventory_verify_command = window.input_inventory_verify_command
        if not inventory_verify_command.startswith(
            "python3 -m src.runtime.main verify-prediction-input-inventory "
        ):
            issues.append(f"windows[{index}].input_inventory_verify_command unsupported")
        else:
            verifier_output_paths.append(
                (
                    f"windows[{index}].input_inventory_verify_command",
                    _option_value_from_command(inventory_verify_command, "--output") or "",
                )
            )
        verify_command = window.eval_manifest_verify_command
        if not verify_command.startswith(
            "python3 -m src.runtime.main verify-prediction-eval-manifest "
        ):
            issues.append(f"windows[{index}].eval_manifest_verify_command unsupported")
        else:
            verifier_output_paths.append(
                (
                    f"windows[{index}].eval_manifest_verify_command",
                    _option_value_from_command(verify_command, "--output") or "",
                )
            )
        expected_fragments = _prediction_eval_window_expected_command_fragments(window)
        for fragment in expected_fragments:
            if fragment not in eval_command:
                issues.append(f"windows[{index}].eval_report_command missing {fragment}")
            if fragment not in inventory_command:
                issues.append(f"windows[{index}].input_inventory_command missing {fragment}")
        if require_congress_archive_manifest:
            if "--congress-archive-manifest " not in eval_command:
                issues.append(
                    f"windows[{index}].eval_report_command missing --congress-archive-manifest"
                )
            if "--congress-archive-manifest " not in inventory_command:
                issues.append(
                    f"windows[{index}].input_inventory_command missing --congress-archive-manifest"
                )
        if "--dataset-output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --dataset-output")
        if "--manifest-output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --manifest-output")
        if "--output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --output")
        if "--output " not in inventory_command:
            issues.append(f"windows[{index}].input_inventory_command missing --output")
        for fragment in (
            "--artifact ",
            "--require-run-metadata",
            *(
                ("--require-congress-archive-manifest",)
                if require_congress_archive_manifest
                else ()
            ),
            "--require-portable-jurisdiction-ids",
            "--require-portable-body-ids",
            "--require-portable-session-ids",
            "--require-source-family congress_vote",
            "--require-source-family congress_bill",
            "--min-training-labels 1",
            "--min-evaluation-labels 1",
            "--min-training-label-official-source-url-coverage-rate 1",
            "--min-evaluation-label-official-source-url-coverage-rate 1",
            "--min-bill-official-source-url-coverage-rate 1",
            "--min-bill-sponsor-availability-rate 1",
            "--min-ontology-official-source-anchor-coverage-rate 1",
            "--min-fec-contributions 1",
            "--min-member-attributed-fec-contributions 1",
            "--min-members-with-fec-candidate-id 1",
            "--min-public-statement-signals 1",
            "--min-members-with-public-statement-signals 1",
            "--output ",
        ):
            if fragment not in inventory_verify_command:
                issues.append(
                    f"windows[{index}].input_inventory_verify_command missing {fragment.strip()}"
                )
        for fragment in (
            "--manifest ",
            "--require-artifact-run-metadata",
            *(
                ("--require-congress-archive-manifest",)
                if require_congress_archive_manifest
                else ()
            ),
            "--require-model-name member_vote_rate_baseline",
            "--require-model-name ontology_signal_model",
            "--require-model-name learned_signal_logistic",
            "--require-ontology-feature-signals",
            "--min-training-feature-source-url-coverage-rate 1",
            "--min-training-feature-official-source-coverage-rate 1",
            "--min-evaluation-feature-source-url-coverage-rate 1",
            "--min-evaluation-feature-official-source-coverage-rate 1",
            "--min-evaluation-source-url-coverage-rate 1",
            "--require-fail-on-unknown-bill-semantic-availability",
            "--require-fail-on-unknown-bill-signal-availability",
            "--require-fail-on-unknown-ontology-edge-availability",
            "--require-fail-on-unknown-contribution-signal-availability",
            "--require-fail-on-unknown-statement-signal-availability",
            "--output ",
        ):
            if fragment not in verify_command:
                issues.append(
                    f"windows[{index}].eval_manifest_verify_command missing {fragment.strip()}"
                )
    seen_verifier_outputs: dict[str, str] = {}
    for label, output_path in verifier_output_paths:
        if not output_path:
            issues.append(f"{label} missing --output value")
            continue
        previous = seen_verifier_outputs.get(output_path)
        if previous is None:
            seen_verifier_outputs[output_path] = label
        else:
            issues.append(f"{label} duplicates verifier output: {previous}")
    return issues


def _prediction_eval_window_expected_command_fragments(
    window: Any,
) -> list[str]:
    return [
        f"--training-feature-cutoff {window.training_feature_cutoff.isoformat()}",
        f"--train-start {window.train_start.isoformat()}",
        f"--train-end {window.train_end.isoformat()}",
        f"--feature-cutoff {window.feature_cutoff.isoformat()}",
        f"--label-start {window.label_start.isoformat()}",
        f"--label-end {window.label_end.isoformat()}",
    ]


def _prediction_eval_window_plan_source_state(
    plan: PredictionEvalWindowPlanPayload,
    command_issues: list[str],
) -> dict[str, Any]:
    return {
        "plan_version": plan.plan_version,
        "window_count": plan.window_count,
        "window_ids": [window.window_id for window in plan.windows],
        "first_feature_cutoff": (
            plan.windows[0].feature_cutoff.isoformat() if plan.windows else None
        ),
        "last_feature_cutoff": (
            plan.windows[-1].feature_cutoff.isoformat() if plan.windows else None
        ),
        "first_label_start": (plan.windows[0].label_start.isoformat() if plan.windows else None),
        "last_label_end": plan.windows[-1].label_end.isoformat() if plan.windows else None,
        "command_issue_count": len(command_issues),
        "has_summary_command": bool(plan.summary_command),
        "has_verify_summary_command": bool(plan.verify_summary_command),
        "has_verify_run_command": bool(plan.verify_run_command),
        "requires_training_labels": all(
            "--min-training-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        "requires_evaluation_labels": all(
            "--min-evaluation-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
    }


def _prediction_eval_window_plan_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-plan",
        "artifact": str(artifact_path),
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_commands": bool(getattr(args, "require_commands", False)),
            "min_window_count": getattr(args, "min_window_count", None),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _handle_prediction_eval_window_summary(args: Any) -> dict[str, Any]:
    report_paths = [Path(path) for path in getattr(args, "report", [])]
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    reports: list[tuple[Path, dict[str, Any], PredictionEvalReportPayload]] = []

    if not report_paths:
        issues.append("at least one --report is required")
    if len({str(path) for path in report_paths}) != len(report_paths):
        issues.append("report paths must be unique")

    for path in report_paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{path}: failed to load report: {exc}")
            continue
        if not isinstance(raw, dict):
            issues.append(f"{path}: report must be an object")
            continue
        try:
            report = PredictionEvalReportPayload.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{path}: invalid prediction eval report: {exc}")
            continue
        reports.append((path, raw, report))

    if bool(getattr(args, "require_report_run_metadata", False)):
        for path, raw, report in reports:
            _validate_prediction_eval_window_summary_report_metadata(
                path=path,
                raw=raw,
                report=report,
                issues=issues,
            )

    if bool(getattr(args, "require_ready_reports", False)):
        for path, _, report in reports:
            if report.readiness.status != "ready" or not report.readiness.ok:
                quality_gate_failures.append(f"{path}: report_not_ready")

    sorted_reports = sorted(reports, key=lambda item: (item[2].label_start, item[2].label_end))
    if bool(getattr(args, "require_non_overlapping_label_windows", False)):
        for previous, current in zip(sorted_reports, sorted_reports[1:]):
            previous_report = previous[2]
            current_report = current[2]
            if previous_report.label_end >= current_report.label_start:
                issues.append(
                    "label windows overlap: "
                    f"{previous[0]} {previous_report.label_start.isoformat()}.."
                    f"{previous_report.label_end.isoformat()} and "
                    f"{current[0]} {current_report.label_start.isoformat()}.."
                    f"{current_report.label_end.isoformat()}"
                )

    windows = [
        _prediction_eval_window_summary_window(path, report) for path, _, report in sorted_reports
    ]
    model_summaries = _prediction_eval_window_summary_models([report for _, _, report in reports])
    source_state = {
        "report_count": len(reports),
        "report_paths": [str(path) for path, _, _ in reports],
        "window_count": len(windows),
        "total_evaluation_label_count": sum(
            report.evaluation_label_count for _, _, report in reports
        ),
        "model_names": sorted(model_summaries),
    }
    result: dict[str, Any] = {
        "ok": bool(reports) and not issues and not quality_gate_failures,
        "command": "prediction-eval-window-summary",
        "report_count": len(reports),
        "checked": len(report_paths),
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "window_count": len(windows),
        "windows": windows,
        "total_evaluation_label_count": source_state["total_evaluation_label_count"],
        "model_count": len(model_summaries),
        "model_names": sorted(model_summaries),
        "models": [model_summaries[name] for name in sorted(model_summaries)],
        "run_metadata": _prediction_eval_window_summary_run_metadata(
            args,
            report_paths,
            source_state=source_state,
        ),
    }
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


def _validate_prediction_eval_window_summary_report_metadata(
    *,
    path: Path,
    raw: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    run_metadata = raw.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append(f"{path}: run_metadata missing")
        return
    if run_metadata.get("command") != "prediction-eval-report":
        issues.append(f"{path}: run_metadata command mismatch")
    for key, expected in {
        "training_feature_cutoff": report.training_feature_cutoff.isoformat(),
        "train_start": report.train_start.isoformat(),
        "train_end": report.train_end.isoformat(),
        "feature_cutoff": report.feature_cutoff.isoformat(),
        "label_start": report.label_start.isoformat(),
        "label_end": report.label_end.isoformat(),
    }.items():
        if run_metadata.get(key) != expected:
            issues.append(f"{path}: run_metadata {key} mismatch")


def _prediction_eval_window_summary_window(
    path: Path,
    report: PredictionEvalReportPayload,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "training_feature_cutoff": report.training_feature_cutoff.isoformat(),
        "train_start": report.train_start.isoformat(),
        "train_end": report.train_end.isoformat(),
        "feature_cutoff": report.feature_cutoff.isoformat(),
        "label_start": report.label_start.isoformat(),
        "label_end": report.label_end.isoformat(),
        "readiness_status": report.readiness.status,
        "training_example_count": report.training_example_count,
        "evaluation_label_count": report.evaluation_label_count,
        "model_names": [model.model_name for model in report.models],
    }


def _prediction_eval_window_summary_models(
    reports: list[PredictionEvalReportPayload],
) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    brier_weights: dict[str, int] = {}
    brier_weighted_sums: dict[str, float] = {}
    log_loss_weights: dict[str, int] = {}
    log_loss_weighted_sums: dict[str, float] = {}
    for report in reports:
        for model in report.models:
            metrics = model.metrics
            item = totals.setdefault(
                model.model_name,
                {
                    "model_name": model.model_name,
                    "window_count": 0,
                    "label_count": 0,
                    "evaluated_count": 0,
                    "correct_count": 0,
                    "skipped_count": 0,
                    "accuracy": None,
                    "coverage_rate": None,
                    "weighted_brier_score": None,
                    "weighted_log_loss": None,
                    "brier_score_window_count": 0,
                    "log_loss_window_count": 0,
                    "skip_reason_counts": {},
                },
            )
            item["window_count"] += 1
            item["label_count"] += metrics.label_count
            item["evaluated_count"] += metrics.evaluated_count
            item["correct_count"] += metrics.correct_count
            item["skipped_count"] += metrics.skipped_count
            skip_counts = cast(dict[str, int], item["skip_reason_counts"])
            for reason, count in model.skip_reason_counts.items():
                skip_counts[reason] = skip_counts.get(reason, 0) + count
            if metrics.brier_score is not None and metrics.evaluated_count:
                brier_weights[model.model_name] = (
                    brier_weights.get(model.model_name, 0) + metrics.evaluated_count
                )
                brier_weighted_sums[model.model_name] = brier_weighted_sums.get(
                    model.model_name,
                    0.0,
                ) + (metrics.brier_score * metrics.evaluated_count)
                item["brier_score_window_count"] += 1
            if metrics.log_loss is not None and metrics.evaluated_count:
                log_loss_weights[model.model_name] = (
                    log_loss_weights.get(model.model_name, 0) + metrics.evaluated_count
                )
                log_loss_weighted_sums[model.model_name] = log_loss_weighted_sums.get(
                    model.model_name,
                    0.0,
                ) + (metrics.log_loss * metrics.evaluated_count)
                item["log_loss_window_count"] += 1
    for model_name, item in totals.items():
        evaluated_count = int(item["evaluated_count"])
        label_count = int(item["label_count"])
        correct_count = int(item["correct_count"])
        item["accuracy"] = correct_count / evaluated_count if evaluated_count else None
        item["coverage_rate"] = evaluated_count / label_count if label_count else None
        if brier_weights.get(model_name):
            item["weighted_brier_score"] = (
                brier_weighted_sums[model_name] / brier_weights[model_name]
            )
        if log_loss_weights.get(model_name):
            item["weighted_log_loss"] = (
                log_loss_weighted_sums[model_name] / log_loss_weights[model_name]
            )
    return totals


def _prediction_eval_window_summary_run_metadata(
    args: Any,
    report_paths: list[Path],
    *,
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": "prediction-eval-window-summary",
        "report_paths": [str(path) for path in report_paths],
        "report_sha256": {str(path): _file_sha256(path) for path in report_paths},
        "verification_flags": {
            "require_report_run_metadata": bool(
                getattr(args, "require_report_run_metadata", False)
            ),
            "require_ready_reports": bool(getattr(args, "require_ready_reports", False)),
            "require_non_overlapping_label_windows": bool(
                getattr(args, "require_non_overlapping_label_windows", False)
            ),
        },
        "source_state": source_state,
    }


def _handle_verify_prediction_eval_window_summary(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-summary",
                "artifact": str(artifact_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_summary_verify_run_metadata(
                    args,
                    artifact_path,
                ),
            },
        )

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(artifact, dict):
        return failure_result(issues=["artifact must be an object"])

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if artifact.get("command") != "prediction-eval-window-summary":
        issues.append("artifact command must be prediction-eval-window-summary")

    windows = artifact.get("windows")
    if not isinstance(windows, list):
        issues.append("windows must be a list")
        windows = []
    models = artifact.get("models")
    if not isinstance(models, list):
        issues.append("models must be a list")
        models = []

    _validate_prediction_eval_window_summary_counts(
        artifact=artifact,
        windows=windows,
        models=models,
        issues=issues,
    )
    if bool(getattr(args, "require_run_metadata", False)):
        _validate_prediction_eval_window_summary_run_metadata(
            artifact=artifact,
            issues=issues,
        )
    if bool(getattr(args, "require_current_report_hashes", False)):
        _validate_prediction_eval_window_summary_current_hashes(
            artifact=artifact,
            windows=windows,
            issues=issues,
        )
    if bool(getattr(args, "require_non_overlapping_label_windows", False)):
        _validate_prediction_eval_window_summary_non_overlapping_windows(
            windows,
            issues=issues,
        )
    plan_path_raw = getattr(args, "plan", None)
    require_plan_match = bool(getattr(args, "require_plan_match", False))
    plan_state: dict[str, Any] | None = None
    if require_plan_match and plan_path_raw is None:
        issues.append("plan required for require_plan_match")
    if plan_path_raw is not None:
        plan_state = _validate_prediction_eval_window_summary_plan_match(
            plan_path=Path(plan_path_raw),
            windows=windows,
            issues=issues,
            require_match=require_plan_match,
        )

    min_window_count = getattr(args, "min_window_count", None)
    if min_window_count is not None:
        if not _is_plain_int(min_window_count):
            issues.append("min_window_count must be an integer")
        elif min_window_count < 0:
            issues.append("min_window_count must be a non-negative integer")
        elif len(windows) < min_window_count:
            quality_gate_failures.append("window_count_below_minimum")
    min_total_labels = getattr(args, "min_total_evaluation_labels", None)
    total_labels = _plain_int_or_zero(artifact.get("total_evaluation_label_count"))
    if min_total_labels is not None:
        if not _is_plain_int(min_total_labels):
            issues.append("min_total_evaluation_labels must be an integer")
        elif min_total_labels < 0:
            issues.append("min_total_evaluation_labels must be a non-negative integer")
        elif total_labels < min_total_labels:
            quality_gate_failures.append("total_evaluation_label_count_below_minimum")

    source_state = _prediction_eval_window_summary_verify_source_state(
        artifact=artifact,
        windows=windows,
        models=models,
        plan_state=plan_state,
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-eval-window-summary",
            "artifact": str(artifact_path),
            "checked": 1 + len(windows) + len(models),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "window_count": len(windows),
            "model_count": len(models),
            "total_evaluation_label_count": total_labels,
            "plan": plan_path_raw,
            "plan_matched": plan_state.get("matched") if plan_state is not None else None,
            "run_metadata": _prediction_eval_window_summary_verify_run_metadata(
                args,
                artifact_path,
                source_state=source_state,
            ),
        },
    )


def _validate_prediction_eval_window_summary_counts(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    models: list[Any],
    issues: list[str],
) -> None:
    if not _is_plain_int(artifact.get("window_count")):
        issues.append("window_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("window_count")):
        issues.append("window_count must be a non-negative integer")
    elif artifact["window_count"] != len(windows):
        issues.append("window_count mismatch")
    if not _is_plain_int(artifact.get("report_count")):
        issues.append("report_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("report_count")):
        issues.append("report_count must be a non-negative integer")
    elif artifact["report_count"] != len(windows):
        issues.append("report_count mismatch")
    if not _is_plain_int(artifact.get("model_count")):
        issues.append("model_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("model_count")):
        issues.append("model_count must be a non-negative integer")
    elif artifact["model_count"] != len(models):
        issues.append("model_count mismatch")
    declared_total = artifact.get("total_evaluation_label_count")
    if not _is_plain_int(declared_total):
        issues.append("total_evaluation_label_count must be an integer")
    elif not _is_non_negative_plain_int(declared_total):
        issues.append("total_evaluation_label_count must be a non-negative integer")
    else:
        window_total = sum(
            window.get("evaluation_label_count")
            for window in windows
            if isinstance(window, dict)
            and _is_non_negative_plain_int(window.get("evaluation_label_count"))
        )
        if declared_total != window_total:
            issues.append("total_evaluation_label_count mismatch")
    model_names = artifact.get("model_names")
    if not isinstance(model_names, list) or not all(isinstance(name, str) for name in model_names):
        issues.append("model_names must be a string list")
    else:
        actual_model_names = sorted(
            str(model.get("model_name"))
            for model in models
            if isinstance(model, dict) and isinstance(model.get("model_name"), str)
        )
        if model_names != actual_model_names:
            issues.append("model_names mismatch")
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            issues.append(f"windows[{index}] must be an object")
            continue
        for key in ("path", "label_start", "label_end"):
            if not isinstance(window.get(key), str) or not window.get(key):
                issues.append(f"windows[{index}].{key} missing")
        _validate_prediction_eval_window_summary_window_dates(window, index=index, issues=issues)
        sha_raw = window.get("sha256")
        if sha_raw is None or sha_raw == "":
            issues.append(f"windows[{index}].sha256 missing")
        elif not isinstance(sha_raw, str) or not _is_sha256_hex(sha_raw):
            issues.append(f"windows[{index}].sha256 invalid")
        if not _is_plain_int(window.get("evaluation_label_count")):
            issues.append(f"windows[{index}].evaluation_label_count must be an integer")
        elif not _is_non_negative_plain_int(window.get("evaluation_label_count")):
            issues.append(f"windows[{index}].evaluation_label_count must be a non-negative integer")
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            issues.append(f"models[{index}] must be an object")
            continue
        _validate_prediction_eval_window_summary_model(model, index=index, issues=issues)


def _validate_prediction_eval_window_summary_window_dates(
    window: dict[str, Any],
    *,
    index: int,
    issues: list[str],
) -> None:
    parsed: dict[str, dt.date] = {}
    for key in (
        "training_feature_cutoff",
        "train_start",
        "train_end",
        "feature_cutoff",
        "label_start",
        "label_end",
    ):
        value = window.get(key)
        if not isinstance(value, str) or not value:
            issues.append(f"windows[{index}].{key} missing")
            continue
        try:
            parsed[key] = dt.date.fromisoformat(value)
        except ValueError:
            issues.append(f"windows[{index}].{key} must be an ISO date")
    if {"train_start", "train_end"} <= parsed.keys() and parsed["train_start"] > parsed[
        "train_end"
    ]:
        issues.append(f"windows[{index}].train_start must be on or before train_end")
    if {"label_start", "label_end"} <= parsed.keys() and parsed["label_start"] > parsed[
        "label_end"
    ]:
        issues.append(f"windows[{index}].label_start must be on or before label_end")
    if {"training_feature_cutoff", "train_start"} <= parsed.keys() and parsed[
        "training_feature_cutoff"
    ] >= parsed["train_start"]:
        issues.append(f"windows[{index}].training_feature_cutoff must be before train_start")
    if {"train_end", "feature_cutoff"} <= parsed.keys() and parsed["train_end"] > parsed[
        "feature_cutoff"
    ]:
        issues.append(f"windows[{index}].train_end must be on or before feature_cutoff")
    if {"feature_cutoff", "label_start"} <= parsed.keys() and parsed["feature_cutoff"] >= parsed[
        "label_start"
    ]:
        issues.append(f"windows[{index}].feature_cutoff must be before label_start")


def _validate_prediction_eval_window_summary_model(
    model: dict[str, Any],
    *,
    index: int,
    issues: list[str],
) -> None:
    if not isinstance(model.get("model_name"), str) or not model.get("model_name"):
        issues.append(f"models[{index}].model_name missing")
    for key in (
        "window_count",
        "label_count",
        "evaluated_count",
        "correct_count",
        "skipped_count",
    ):
        if not _is_plain_int(model.get(key)):
            issues.append(f"models[{index}].{key} must be an integer")
        elif not _is_non_negative_plain_int(model.get(key)):
            issues.append(f"models[{index}].{key} must be a non-negative integer")
    if (
        _is_non_negative_plain_int(model.get("evaluated_count"))
        and _is_non_negative_plain_int(model.get("skipped_count"))
        and _is_non_negative_plain_int(model.get("label_count"))
        and model["evaluated_count"] + model["skipped_count"] != model["label_count"]
    ):
        issues.append(f"models[{index}].label_count mismatch")
    if (
        _is_non_negative_plain_int(model.get("correct_count"))
        and _is_non_negative_plain_int(model.get("evaluated_count"))
        and model["correct_count"] > model["evaluated_count"]
    ):
        issues.append(f"models[{index}].correct_count exceeds evaluated_count")
    if _is_non_negative_plain_int(model.get("evaluated_count")):
        expected_accuracy = (
            model["correct_count"] / model["evaluated_count"]
            if _is_non_negative_plain_int(model.get("correct_count")) and model["evaluated_count"]
            else None
        )
        if not _prediction_eval_optional_rate_valid(model.get("accuracy")):
            issues.append(f"models[{index}].accuracy must be a number between 0 and 1")
        elif not _numeric_or_none_matches(model.get("accuracy"), expected_accuracy):
            issues.append(f"models[{index}].accuracy mismatch")
    if _is_non_negative_plain_int(model.get("label_count")):
        expected_coverage = (
            model["evaluated_count"] / model["label_count"]
            if _is_non_negative_plain_int(model.get("evaluated_count")) and model["label_count"]
            else None
        )
        if not _prediction_eval_optional_rate_valid(model.get("coverage_rate")):
            issues.append(f"models[{index}].coverage_rate must be a number between 0 and 1")
        elif not _numeric_or_none_matches(model.get("coverage_rate"), expected_coverage):
            issues.append(f"models[{index}].coverage_rate mismatch")


def _prediction_eval_optional_rate_valid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return 0 <= value <= 1


def _validate_prediction_eval_window_summary_run_metadata(
    *,
    artifact: dict[str, Any],
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append("run_metadata missing")
        return
    if run_metadata.get("command") != "prediction-eval-window-summary":
        issues.append("run_metadata command mismatch")
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        issues.append("run_metadata source_state missing")
        return
    expected_source_state_keys = {
        "model_names",
        "report_count",
        "report_paths",
        "window_count",
        "total_evaluation_label_count",
    }
    for key in sorted(set(source_state) - expected_source_state_keys):
        issues.append(f"run_metadata source_state unexpected: {key}")
    for key in ("report_count", "window_count", "total_evaluation_label_count"):
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"run_metadata source_state {key} must be an integer")
            continue
        if not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"run_metadata source_state {key} must be a non-negative integer")
            continue
        if source_state.get(key) != artifact.get(key):
            issues.append(f"run_metadata source_state {key} mismatch")


def _validate_prediction_eval_window_summary_current_hashes(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    metadata_hashes = run_metadata.get("report_sha256") if isinstance(run_metadata, dict) else None
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            continue
        path_raw = window.get("path")
        sha_raw = window.get("sha256")
        if not isinstance(path_raw, str) or not path_raw:
            continue
        path = Path(path_raw)
        try:
            current_sha = _file_sha256(path)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"windows[{index}].path unreadable: {exc}")
            continue
        if isinstance(sha_raw, str) and _is_sha256_hex(sha_raw) and current_sha != sha_raw:
            issues.append(f"windows[{index}].sha256 mismatch")
        if isinstance(metadata_hashes, dict) and path_raw in metadata_hashes:
            metadata_sha = metadata_hashes.get(path_raw)
            if not isinstance(metadata_sha, str) or not _is_sha256_hex(metadata_sha):
                issues.append(f"run_metadata report_sha256 invalid: {path_raw}")
            elif metadata_sha != current_sha:
                issues.append(f"run_metadata report_sha256 mismatch: {path_raw}")


def _validate_prediction_eval_window_summary_non_overlapping_windows(
    windows: list[Any],
    *,
    issues: list[str],
) -> None:
    parsed_windows: list[tuple[str, dt.date, dt.date]] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            continue
        try:
            label_start = dt.date.fromisoformat(str(window.get("label_start")))
            label_end = dt.date.fromisoformat(str(window.get("label_end")))
        except ValueError:
            issues.append(f"windows[{index}].label window invalid")
            continue
        if label_start > label_end:
            issues.append(f"windows[{index}].label window reversed")
            continue
        parsed_windows.append((str(window.get("path")), label_start, label_end))
    for previous, current in zip(
        sorted(parsed_windows, key=lambda item: (item[1], item[2])),
        sorted(parsed_windows, key=lambda item: (item[1], item[2]))[1:],
    ):
        if previous[2] >= current[1]:
            issues.append(
                "label windows overlap: "
                f"{previous[0]} {previous[1].isoformat()}..{previous[2].isoformat()} "
                f"and {current[0]} {current[1].isoformat()}..{current[2].isoformat()}"
            )


def _validate_prediction_eval_window_summary_plan_match(
    *,
    plan_path: Path,
    windows: list[Any],
    issues: list[str],
    require_match: bool,
) -> dict[str, Any]:
    try:
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"plan failed to load: {exc}")
        return {"path": str(plan_path), "matched": False, "issue": "load_failed"}
    if not isinstance(raw_plan, dict):
        issues.append("plan must be an object")
        return {"path": str(plan_path), "matched": False, "issue": "not_object"}
    plan_payload = {
        key: value for key, value in raw_plan.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"invalid plan: {exc}")
        return {"path": str(plan_path), "matched": False, "issue": "invalid_plan"}
    expected_paths = _prediction_eval_window_plan_expected_report_paths(plan)
    actual_paths = [
        window.get("path")
        for window in windows
        if isinstance(window, dict) and isinstance(window.get("path"), str)
    ]
    plan_issues: list[str] = []
    if actual_paths != expected_paths:
        plan_issues.append("summary report paths do not match plan")
    expected_windows = [
        {
            "path": path,
            "feature_cutoff": window.feature_cutoff.isoformat(),
            "label_start": window.label_start.isoformat(),
            "label_end": window.label_end.isoformat(),
        }
        for path, window in zip(expected_paths, plan.windows)
    ]
    actual_windows = [
        {
            "path": window.get("path"),
            "feature_cutoff": window.get("feature_cutoff"),
            "label_start": window.get("label_start"),
            "label_end": window.get("label_end"),
        }
        for window in windows
        if isinstance(window, dict)
    ]
    if actual_windows != expected_windows:
        plan_issues.append("summary windows do not match plan")
    if require_match:
        issues.extend(plan_issues)
    return {
        "path": str(plan_path),
        "sha256": _file_sha256(plan_path),
        "matched": not plan_issues,
        "issue_count": len(plan_issues),
        "issues": plan_issues,
        "expected_report_paths": expected_paths,
        "actual_report_paths": actual_paths,
    }


def _prediction_eval_window_plan_expected_report_paths(
    plan: PredictionEvalWindowPlanPayload,
) -> list[str]:
    paths: list[str] = []
    for window in plan.windows:
        parsed = _option_value_from_command(window.eval_report_command, "--output")
        paths.append(parsed if parsed is not None else "")
    return paths


def _option_value_from_command(command: str, option: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for index, part in enumerate(parts):
        if part == option and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _option_values_from_command(command: str, option: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    values: list[str] = []
    for index, part in enumerate(parts):
        if part == option and index + 1 < len(parts):
            values.append(parts[index + 1])
    return values


def _int_option_value_from_command(command: str, option: str) -> int | None:
    raw = _option_value_from_command(command, option)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_option_value_from_command(command: str, option: str) -> float | None:
    raw = _option_value_from_command(command, option)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _prediction_eval_window_summary_verify_source_state(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    models: list[Any],
    plan_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_state: dict[str, Any] = {
        "report_count": artifact.get("report_count"),
        "window_count": len(windows),
        "total_evaluation_label_count": artifact.get("total_evaluation_label_count"),
        "model_names": [
            model.get("model_name")
            for model in models
            if isinstance(model, dict) and isinstance(model.get("model_name"), str)
        ],
        "window_paths": [
            window.get("path")
            for window in windows
            if isinstance(window, dict) and isinstance(window.get("path"), str)
        ],
        "windows": [
            _prediction_eval_window_summary_source_state_window(window)
            for window in windows
            if isinstance(window, dict)
        ],
    }
    if plan_state is not None:
        source_state["plan"] = plan_state
    return source_state


def _prediction_eval_window_summary_source_state_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": window.get("path"),
        "training_feature_cutoff": window.get("training_feature_cutoff"),
        "train_start": window.get("train_start"),
        "train_end": window.get("train_end"),
        "feature_cutoff": window.get("feature_cutoff"),
        "label_start": window.get("label_start"),
        "label_end": window.get("label_end"),
        "evaluation_label_count": window.get("evaluation_label_count"),
    }


def _prediction_eval_window_summary_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-summary",
        "artifact": str(artifact_path),
        "artifact_sha256": _file_sha256(artifact_path) if artifact_path.exists() else None,
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_current_report_hashes": bool(
                getattr(args, "require_current_report_hashes", False)
            ),
            "require_non_overlapping_label_windows": bool(
                getattr(args, "require_non_overlapping_label_windows", False)
            ),
            "plan": getattr(args, "plan", None),
            "require_plan_match": bool(getattr(args, "require_plan_match", False)),
            "min_window_count": getattr(args, "min_window_count", None),
            "min_total_evaluation_labels": getattr(
                args,
                "min_total_evaluation_labels",
                None,
            ),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _handle_verify_prediction_eval_window_run(args: Any) -> dict[str, Any]:
    plan_path = Path(args.plan)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-run",
                "plan": str(plan_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_run_verify_run_metadata(args, plan_path),
            },
        )

    try:
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load plan: {exc}"])
    if not isinstance(raw_plan, dict):
        return failure_result(issues=["plan must be an object"])
    plan_payload = {
        key: value for key, value in raw_plan.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"invalid window plan: {exc}"])

    issues: list[str] = []
    congress_archive_manifest, congress_archive_manifest_issues = (
        _prediction_eval_window_run_plan_congress_archive_manifest(raw_plan)
    )
    issues.extend(congress_archive_manifest_issues)
    expected_window_verifiers = _prediction_eval_window_run_expected_window_verifiers(
        plan,
        congress_archive_manifest=congress_archive_manifest,
    )
    expected_summary_verify = _prediction_eval_window_run_expected_summary_verify(
        args,
        plan,
        plan_path=plan_path,
        issues=issues,
    )
    verifier_results = _prediction_eval_window_run_verifier_results(
        expected_window_verifiers,
        require=bool(getattr(args, "require_window_verifiers", False)),
        issues=issues,
    )
    summary_result = None
    if expected_summary_verify is not None:
        summary_result = _prediction_eval_window_run_verifier_result(
            expected_summary_verify,
            require=bool(getattr(args, "require_summary_verify", False)),
            issues=issues,
        )
    elif bool(getattr(args, "require_summary_verify", False)):
        issues.append("summary verifier path missing")

    loaded_verifier_count = sum(1 for item in verifier_results if item.get("loaded"))
    if summary_result is not None and summary_result.get("loaded"):
        loaded_verifier_count += 1
    missing_verifier_count = sum(1 for item in verifier_results if item.get("missing"))
    if summary_result is not None and summary_result.get("missing"):
        missing_verifier_count += 1
    failing_verifier_count = sum(1 for item in verifier_results if item.get("status") == "failed")
    if summary_result is not None and summary_result.get("status") == "failed":
        failing_verifier_count += 1
    source_state = _prediction_eval_window_run_verify_source_state(
        plan=plan,
        expected_window_verifiers=expected_window_verifiers,
        expected_summary_verify=expected_summary_verify,
        verifier_results=verifier_results,
        summary_result=summary_result,
        loaded_verifier_count=loaded_verifier_count,
        missing_verifier_count=missing_verifier_count,
        failing_verifier_count=failing_verifier_count,
        congress_archive_manifest=congress_archive_manifest,
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues,
            "command": "verify-prediction-eval-window-run",
            "plan": str(plan_path),
            "plan_sha256": _file_sha256(plan_path),
            "summary_verify": (
                str(expected_summary_verify["path"])
                if expected_summary_verify is not None
                else None
            ),
            "checked": len(verifier_results) + (1 if summary_result is not None else 0),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": [],
            "quality_gate_failure_count": 0,
            "window_count": plan.window_count,
            "expected_window_verifier_count": len(expected_window_verifiers),
            "loaded_verifier_count": loaded_verifier_count,
            "missing_verifier_count": missing_verifier_count,
            "failing_verifier_count": failing_verifier_count,
            "window_verifiers": verifier_results,
            "summary_verifier": summary_result,
            "run_metadata": _prediction_eval_window_run_verify_run_metadata(
                args,
                plan_path,
                source_state=source_state,
            ),
        },
    )


def _prediction_eval_window_run_expected_window_verifiers(
    plan: PredictionEvalWindowPlanPayload,
    *,
    congress_archive_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    verifiers: list[dict[str, Any]] = []
    for window in plan.windows:
        for kind, command, expected_command in (
            (
                "input_inventory",
                window.input_inventory_verify_command,
                "verify-prediction-input-inventory",
            ),
            (
                "eval_manifest",
                window.eval_manifest_verify_command,
                "verify-prediction-eval-manifest",
            ),
        ):
            output = _option_value_from_command(command, "--output")
            verifiers.append(
                {
                    "kind": kind,
                    "window_id": window.window_id,
                    "path": Path(output) if output is not None else None,
                    "expected_command": expected_command,
                    "expected_congress_archive_manifest": congress_archive_manifest,
                    "expected_verification_flags": (
                        _prediction_eval_window_run_expected_verification_flags(
                            kind=kind,
                            command=command,
                        )
                    ),
                }
            )
    return verifiers


def _prediction_eval_window_run_expected_verification_flags(
    *,
    kind: str,
    command: str,
) -> dict[str, Any]:
    if kind == "input_inventory":
        return {
            "require_run_metadata": "--require-run-metadata" in command,
            "require_congress_archive_manifest": ("--require-congress-archive-manifest" in command),
            "require_portable_jurisdiction_ids": ("--require-portable-jurisdiction-ids" in command),
            "require_portable_body_ids": "--require-portable-body-ids" in command,
            "require_portable_session_ids": "--require-portable-session-ids" in command,
            "require_source_families": _option_values_from_command(
                command,
                "--require-source-family",
            ),
            "min_training_labels": _int_option_value_from_command(
                command,
                "--min-training-labels",
            ),
            "min_evaluation_labels": _int_option_value_from_command(
                command,
                "--min-evaluation-labels",
            ),
            "min_training_feature_vote_history_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-vote-history-source-coverage-rate",
                )
            ),
            "min_evaluation_feature_vote_history_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-vote-history-source-coverage-rate",
                )
            ),
            "min_training_label_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-label-official-source-url-coverage-rate",
                )
            ),
            "min_evaluation_label_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-label-official-source-url-coverage-rate",
                )
            ),
            "min_bill_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-bill-official-source-url-coverage-rate",
                )
            ),
            "min_bill_sponsor_availability_rate": _float_option_value_from_command(
                command,
                "--min-bill-sponsor-availability-rate",
            ),
            "min_ontology_official_source_anchor_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-ontology-official-source-anchor-coverage-rate",
                )
            ),
            "min_fec_contributions": _int_option_value_from_command(
                command,
                "--min-fec-contributions",
            ),
            "min_member_attributed_fec_contributions": _int_option_value_from_command(
                command,
                "--min-member-attributed-fec-contributions",
            ),
            "min_members_with_fec_candidate_id": _int_option_value_from_command(
                command,
                "--min-members-with-fec-candidate-id",
            ),
            "min_public_statement_signals": _int_option_value_from_command(
                command,
                "--min-public-statement-signals",
            ),
            "min_members_with_public_statement_signals": _int_option_value_from_command(
                command,
                "--min-members-with-public-statement-signals",
            ),
        }
    if kind == "eval_manifest":
        return {
            "require_artifact_run_metadata": "--require-artifact-run-metadata" in command,
            "require_congress_archive_manifest": ("--require-congress-archive-manifest" in command),
            "require_model_names": _option_values_from_command(
                command,
                "--require-model-name",
            ),
            "require_ontology_feature_signals": ("--require-ontology-feature-signals" in command),
            "min_training_feature_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-source-url-coverage-rate",
                )
            ),
            "min_training_feature_official_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-official-source-coverage-rate",
                )
            ),
            "min_evaluation_feature_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-source-url-coverage-rate",
                )
            ),
            "min_evaluation_feature_official_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-official-source-coverage-rate",
                )
            ),
            "min_evaluation_source_url_coverage_rate": _float_option_value_from_command(
                command,
                "--min-evaluation-source-url-coverage-rate",
            ),
            "require_fail_on_unknown_bill_semantic_availability": (
                "--require-fail-on-unknown-bill-semantic-availability" in command
            ),
            "require_fail_on_unknown_bill_signal_availability": (
                "--require-fail-on-unknown-bill-signal-availability" in command
            ),
            "require_fail_on_unknown_ontology_edge_availability": (
                "--require-fail-on-unknown-ontology-edge-availability" in command
            ),
            "require_fail_on_unknown_contribution_signal_availability": (
                "--require-fail-on-unknown-contribution-signal-availability" in command
            ),
            "require_fail_on_unknown_statement_signal_availability": (
                "--require-fail-on-unknown-statement-signal-availability" in command
            ),
        }
    return {}


def _prediction_eval_window_run_plan_congress_archive_manifest(
    raw_plan: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    run_metadata = raw_plan.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None, []
    congress_archive_manifest = run_metadata.get("congress_archive_manifest")
    if congress_archive_manifest is None:
        return None, []
    if isinstance(congress_archive_manifest, dict):
        return congress_archive_manifest, []
    if isinstance(congress_archive_manifest, str) and congress_archive_manifest:
        manifest_path = Path(congress_archive_manifest)
        if not manifest_path.is_file():
            return None, [
                f"plan congress_archive_manifest file not found: {manifest_path}",
            ]
        return _congress_archive_manifest_metadata(manifest_path), []
    return None, ["plan congress_archive_manifest must be a path string or metadata object"]


def _prediction_eval_window_run_expected_summary_verify(
    args: Any,
    plan: PredictionEvalWindowPlanPayload,
    *,
    plan_path: Path,
    issues: list[str],
) -> dict[str, Any] | None:
    derived = _option_value_from_command(plan.verify_summary_command, "--output")
    provided = getattr(args, "summary_verify", None)
    path_raw = provided if provided is not None else derived
    if path_raw is None:
        return None
    if (
        bool(getattr(args, "require_summary_verify", False))
        and provided is not None
        and derived is not None
        and provided != derived
    ):
        issues.append("summary_verify does not match plan verify_summary_command output")
    return {
        "kind": "summary",
        "window_id": None,
        "path": Path(path_raw),
        "expected_command": "verify-prediction-eval-window-summary",
        "require_plan_matched": True,
        "expected_plan": str(plan_path),
        "expected_plan_sha256": _file_sha256(plan_path),
        "expected_windows": _prediction_eval_window_run_expected_summary_windows(plan),
    }


def _prediction_eval_window_run_expected_summary_windows(
    plan: PredictionEvalWindowPlanPayload,
) -> list[dict[str, Any]]:
    report_paths = _prediction_eval_window_plan_expected_report_paths(plan)
    return [
        {
            "path": path,
            "training_feature_cutoff": window.training_feature_cutoff.isoformat(),
            "train_start": window.train_start.isoformat(),
            "train_end": window.train_end.isoformat(),
            "feature_cutoff": window.feature_cutoff.isoformat(),
            "label_start": window.label_start.isoformat(),
            "label_end": window.label_end.isoformat(),
        }
        for path, window in zip(report_paths, plan.windows)
    ]


def _prediction_eval_window_run_verifier_results(
    expected_verifiers: list[dict[str, Any]],
    *,
    require: bool,
    issues: list[str],
) -> list[dict[str, Any]]:
    return [
        _prediction_eval_window_run_verifier_result(verifier, require=require, issues=issues)
        for verifier in expected_verifiers
    ]


def _prediction_eval_window_run_verifier_result(
    expected_verifier: dict[str, Any],
    *,
    require: bool,
    issues: list[str],
) -> dict[str, Any]:
    path = expected_verifier.get("path")
    path_text = str(path) if path is not None else None
    result = {
        "kind": expected_verifier.get("kind"),
        "window_id": expected_verifier.get("window_id"),
        "path": path_text,
        "expected_command": expected_verifier.get("expected_command"),
        "loaded": False,
        "missing": False,
        "status": "unchecked",
        "ok": None,
        "issue_count": None,
        "quality_gate_failure_count": None,
        "sha256": None,
    }
    if not isinstance(path, Path):
        result["missing"] = True
        result["status"] = "missing"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(expected_verifier, "output missing")
            )
        return result
    if not path.exists():
        result["missing"] = True
        result["status"] = "missing"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"artifact missing: {path}",
                )
            )
        return result
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        result["sha256"] = _file_sha256(path)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"failed to load artifact: {exc}",
                )
            )
        return result
    if not isinstance(artifact, dict):
        result["status"] = "failed"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "artifact must be an object",
                )
            )
        return result
    result["loaded"] = True
    result["ok"] = artifact.get("ok")
    issue_count = artifact.get("issue_count")
    quality_gate_failure_count = artifact.get("quality_gate_failure_count")
    result["issue_count"] = issue_count if _is_plain_int(issue_count) else None
    result["quality_gate_failure_count"] = (
        quality_gate_failure_count if _is_plain_int(quality_gate_failure_count) else None
    )
    verifier_issues = _prediction_eval_window_run_artifact_issues(expected_verifier, artifact)
    if verifier_issues:
        result["status"] = "failed"
        if require:
            issues.extend(verifier_issues)
    else:
        result["status"] = "passed"
    return result


def _prediction_eval_window_run_artifact_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    expected_command = expected_verifier.get("expected_command")
    if artifact.get("command") != expected_command:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                f"command mismatch: expected {expected_command}",
            )
        )
    if artifact.get("ok") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(expected_verifier, "ok is not true")
        )
    issue_count = artifact.get("issue_count")
    if not _is_plain_int(issue_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "issue_count must be an integer",
            )
        )
    elif not _is_non_negative_plain_int(issue_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "issue_count must be a non-negative integer",
            )
        )
    elif issue_count != 0:
        issues.append(
            _prediction_eval_window_run_verifier_issue(expected_verifier, "issue_count is not zero")
        )
    quality_gate_failure_count = artifact.get("quality_gate_failure_count")
    if not _is_plain_int(quality_gate_failure_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count must be an integer",
            )
        )
    elif not _is_non_negative_plain_int(quality_gate_failure_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count must be a non-negative integer",
            )
        )
    elif quality_gate_failure_count != 0:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count is not zero",
            )
        )
    if expected_verifier.get("require_plan_matched") and artifact.get("plan_matched") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "plan_matched is not true",
            )
        )
    if expected_verifier.get("require_plan_matched"):
        issues.extend(
            _prediction_eval_window_run_summary_plan_metadata_issues(
                expected_verifier,
                artifact,
            )
        )
    issues.extend(
        _prediction_eval_window_run_verification_flags_issues(
            expected_verifier,
            artifact,
        )
    )
    issues.extend(
        _prediction_eval_window_run_congress_archive_manifest_issues(
            expected_verifier,
            artifact,
        )
    )
    return issues


def _prediction_eval_window_run_verification_flags_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    expected_flags = expected_verifier.get("expected_verification_flags")
    if not isinstance(expected_flags, dict) or not expected_flags:
        return []
    run_metadata = artifact.get("run_metadata")
    verification_flags = (
        run_metadata.get("verification_flags") if isinstance(run_metadata, dict) else None
    )
    if not isinstance(verification_flags, dict):
        return [
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        ]
    issues: list[str] = []
    for key, expected_value in expected_flags.items():
        actual_value = verification_flags.get(key)
        if actual_value != expected_value:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"run_metadata verification_flags mismatch: {key}",
                )
            )
    return issues


def _prediction_eval_window_run_congress_archive_manifest_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    expected = expected_verifier.get("expected_congress_archive_manifest")
    if expected is None:
        return []
    issues: list[str] = []
    run_metadata = artifact.get("run_metadata")
    verification_flags = (
        run_metadata.get("verification_flags") if isinstance(run_metadata, dict) else None
    )
    if not isinstance(verification_flags, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        )
    elif verification_flags.get("require_congress_archive_manifest") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata require_congress_archive_manifest is not true",
            )
        )
    source_artifact_path = _prediction_eval_window_run_verified_source_artifact_path(
        expected_verifier,
        artifact,
    )
    if source_artifact_path is None:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "verified source artifact path missing",
            )
        )
        return issues
    try:
        source_artifact = json.loads(source_artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                f"failed to load verified source artifact: {exc}",
            )
        )
        return issues
    if not isinstance(source_artifact, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "verified source artifact must be an object",
            )
        )
        return issues
    actual = _prediction_eval_window_run_source_congress_archive_manifest(
        expected_verifier,
        source_artifact,
    )
    if not isinstance(actual, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "congress_archive_manifest missing",
            )
        )
        return issues
    for key in ("path", "sha256"):
        if actual.get(key) != expected.get(key):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"congress_archive_manifest {key} mismatch",
                )
            )
    return issues


def _prediction_eval_window_run_verified_source_artifact_path(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> Path | None:
    if expected_verifier.get("kind") == "input_inventory":
        path = artifact.get("artifact")
    elif expected_verifier.get("kind") == "eval_manifest":
        path = artifact.get("manifest")
    else:
        path = None
    if not isinstance(path, str) or not path:
        return None
    return Path(path)


def _prediction_eval_window_run_source_congress_archive_manifest(
    expected_verifier: dict[str, Any],
    source_artifact: dict[str, Any],
) -> Any:
    if expected_verifier.get("kind") == "input_inventory":
        run_metadata = source_artifact.get("run_metadata")
        if isinstance(run_metadata, dict):
            return run_metadata.get("congress_archive_manifest")
        return None
    if expected_verifier.get("kind") == "eval_manifest":
        inputs = source_artifact.get("inputs")
        if isinstance(inputs, dict):
            return inputs.get("congress_archive_manifest")
        return None
    return None


def _prediction_eval_window_run_summary_plan_metadata_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return [
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata missing",
            )
        ]
    verification_flags = run_metadata.get("verification_flags")
    if not isinstance(verification_flags, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        )
    else:
        if verification_flags.get("plan") != expected_verifier.get("expected_plan"):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata plan mismatch",
                )
            )
        for flag in (
            "require_plan_match",
            "require_current_report_hashes",
            "require_non_overlapping_label_windows",
        ):
            if verification_flags.get(flag) is not True:
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        f"run_metadata {flag} is not true",
                    )
                )
    source_state = run_metadata.get("source_state")
    plan_state = source_state.get("plan") if isinstance(source_state, dict) else None
    if not isinstance(plan_state, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata source_state plan missing",
            )
        )
    else:
        plan_sha256 = plan_state.get("sha256")
        if not isinstance(plan_sha256, str) or not _is_sha256_hex(plan_sha256):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata source_state plan sha256 invalid",
                )
            )
        elif plan_sha256 != expected_verifier.get("expected_plan_sha256"):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata source_state plan sha256 mismatch",
                )
            )
        expected_windows = expected_verifier.get("expected_windows")
        actual_windows = source_state.get("windows") if isinstance(source_state, dict) else None
        if expected_windows is not None:
            if not isinstance(actual_windows, list):
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        "run_metadata source_state windows missing",
                    )
                )
            elif [
                _prediction_eval_window_run_summary_window_projection(window)
                for window in actual_windows
                if isinstance(window, dict)
            ] != expected_windows:
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        "run_metadata source_state windows mismatch",
                    )
                )
    return issues


def _prediction_eval_window_run_summary_window_projection(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": window.get("path"),
        "training_feature_cutoff": window.get("training_feature_cutoff"),
        "train_start": window.get("train_start"),
        "train_end": window.get("train_end"),
        "feature_cutoff": window.get("feature_cutoff"),
        "label_start": window.get("label_start"),
        "label_end": window.get("label_end"),
    }


def _prediction_eval_window_run_verifier_issue(
    expected_verifier: dict[str, Any],
    message: str,
) -> str:
    kind = expected_verifier.get("kind")
    window_id = expected_verifier.get("window_id")
    if isinstance(window_id, str) and window_id:
        return f"{kind} verifier {window_id}: {message}"
    return f"{kind} verifier: {message}"


def _prediction_eval_window_run_verify_source_state(
    *,
    plan: PredictionEvalWindowPlanPayload,
    expected_window_verifiers: list[dict[str, Any]],
    expected_summary_verify: dict[str, Any] | None,
    verifier_results: list[dict[str, Any]],
    summary_result: dict[str, Any] | None,
    loaded_verifier_count: int,
    missing_verifier_count: int,
    failing_verifier_count: int,
    congress_archive_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_results = [result for result in verifier_results if result.get("loaded")]
    if summary_result is not None and summary_result.get("loaded"):
        loaded_results.append(summary_result)
    expected_verifier_hash_count = len(expected_window_verifiers) + (
        1 if expected_summary_verify is not None else 0
    )
    verifier_sha256 = {
        str(result["path"]): result["sha256"]
        for result in loaded_results
        if isinstance(result.get("path"), str) and isinstance(result.get("sha256"), str)
    }
    strict_flags = _prediction_eval_window_run_strict_flag_state(expected_window_verifiers)
    return {
        "plan_version": plan.plan_version,
        "window_count": plan.window_count,
        "window_ids": [window.window_id for window in plan.windows],
        "expected_window_verifier_paths": [
            str(verifier["path"])
            for verifier in expected_window_verifiers
            if verifier.get("path") is not None
        ],
        "expected_summary_verify_path": (
            str(expected_summary_verify["path"]) if expected_summary_verify is not None else None
        ),
        "loaded_verifier_count": loaded_verifier_count,
        "missing_verifier_count": missing_verifier_count,
        "failing_verifier_count": failing_verifier_count,
        "expected_verifier_hash_count": expected_verifier_hash_count,
        "verifier_hash_count": len(verifier_sha256),
        "verifier_hash_coverage_complete": (len(verifier_sha256) == expected_verifier_hash_count),
        "verifier_sha256": verifier_sha256,
        "requires_training_labels": all(
            "--min-training-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        "requires_evaluation_labels": all(
            "--min-evaluation-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        **strict_flags,
        "congress_archive_manifest": congress_archive_manifest,
    }


def _prediction_eval_window_run_strict_flag_state(
    expected_window_verifiers: list[dict[str, Any]],
) -> dict[str, bool]:
    input_flags = [
        flags
        for verifier in expected_window_verifiers
        if verifier.get("kind") == "input_inventory"
        and isinstance((flags := verifier.get("expected_verification_flags")), dict)
    ]
    eval_flags = [
        flags
        for verifier in expected_window_verifiers
        if verifier.get("kind") == "eval_manifest"
        and isinstance((flags := verifier.get("expected_verification_flags")), dict)
    ]
    input_portable = all(
        flags.get("require_portable_jurisdiction_ids") is True
        and flags.get("require_portable_body_ids") is True
        and flags.get("require_portable_session_ids") is True
        for flags in input_flags
    )
    input_source_families = all(
        flags.get("require_source_families") == ["congress_vote", "congress_bill"]
        for flags in input_flags
    )
    input_official_thresholds = all(
        flags.get("min_training_label_official_source_url_coverage_rate") is not None
        and flags.get("min_evaluation_label_official_source_url_coverage_rate") is not None
        and flags.get("min_bill_official_source_url_coverage_rate") is not None
        and flags.get("min_bill_sponsor_availability_rate") is not None
        and flags.get("min_ontology_official_source_anchor_coverage_rate") is not None
        for flags in input_flags
    )
    input_optional_evidence = all(
        flags.get("min_fec_contributions") == 1
        and flags.get("min_member_attributed_fec_contributions") == 1
        and flags.get("min_members_with_fec_candidate_id") == 1
        and flags.get("min_public_statement_signals") == 1
        and flags.get("min_members_with_public_statement_signals") == 1
        for flags in input_flags
    )
    eval_model_suite = all(
        flags.get("require_model_names")
        == [
            "member_vote_rate_baseline",
            "ontology_signal_model",
            "learned_signal_logistic",
        ]
        for flags in eval_flags
    )
    eval_official_thresholds = all(
        flags.get("min_training_feature_source_url_coverage_rate") is not None
        and flags.get("min_training_feature_official_source_coverage_rate") is not None
        and flags.get("min_evaluation_feature_source_url_coverage_rate") is not None
        and flags.get("min_evaluation_feature_official_source_coverage_rate") is not None
        and flags.get("min_evaluation_source_url_coverage_rate") is not None
        for flags in eval_flags
    )
    eval_unknown_availability = all(
        flags.get("require_fail_on_unknown_bill_semantic_availability") is True
        and flags.get("require_fail_on_unknown_bill_signal_availability") is True
        and flags.get("require_fail_on_unknown_ontology_edge_availability") is True
        and flags.get("require_fail_on_unknown_contribution_signal_availability") is True
        and flags.get("require_fail_on_unknown_statement_signal_availability") is True
        for flags in eval_flags
    )
    eval_ontology_features = all(
        flags.get("require_ontology_feature_signals") is True for flags in eval_flags
    )
    return {
        "requires_input_inventory_portable_ids": bool(input_flags) and input_portable,
        "requires_input_inventory_congress_source_families": (
            bool(input_flags) and input_source_families
        ),
        "requires_input_inventory_official_source_thresholds": (
            bool(input_flags) and input_official_thresholds
        ),
        "requires_input_inventory_optional_evidence": (
            bool(input_flags) and input_optional_evidence
        ),
        "requires_eval_manifest_model_suite": bool(eval_flags) and eval_model_suite,
        "requires_eval_manifest_official_source_thresholds": (
            bool(eval_flags) and eval_official_thresholds
        ),
        "requires_eval_manifest_unknown_availability_failures": (
            bool(eval_flags) and eval_unknown_availability
        ),
        "requires_eval_manifest_ontology_feature_signals": (
            bool(eval_flags) and eval_ontology_features
        ),
    }


def _prediction_eval_window_run_verify_run_metadata(
    args: Any,
    plan_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-run",
        "plan": str(plan_path),
        "plan_sha256": _file_sha256(plan_path) if plan_path.exists() else None,
        "verification_flags": {
            "summary_verify": getattr(args, "summary_verify", None),
            "require_window_verifiers": bool(getattr(args, "require_window_verifiers", False)),
            "require_summary_verify": bool(getattr(args, "require_summary_verify", False)),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _numeric_or_none_matches(actual: Any, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return (
        isinstance(actual, int | float)
        and not isinstance(actual, bool)
        and abs(actual - expected) <= 1e-9
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_artifact(path: Path, payload: Any) -> str:
    return _atomic_write_json_artifact(path, payload)


def _write_text_artifact(path: Path, text: str) -> str:
    return _write_bytes_artifact(path, text.encode("utf-8"))


def _write_bytes_artifact(path: Path, encoded: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(encoded)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return hashlib.sha256(encoded).hexdigest()


def _prediction_window_run_metadata(args: Any) -> dict[str, str]:
    return {
        "training_feature_cutoff": args.training_feature_cutoff.isoformat(),
        "train_start": args.train_start.isoformat(),
        "train_end": args.train_end.isoformat(),
        "feature_cutoff": args.feature_cutoff.isoformat(),
        "label_start": args.label_start.isoformat(),
        "label_end": args.label_end.isoformat(),
    }


def _congress_archive_manifest_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    manifest_path = Path(str(value))
    return {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def _validate_congress_archive_manifest_metadata(
    *,
    label: str,
    manifest: Any,
    issues: list[str],
    require_manifest: bool,
) -> None:
    if manifest is None:
        if require_manifest:
            issues.append(f"{label} missing")
        return
    if not isinstance(manifest, dict):
        issues.append(f"{label} must be an object")
        return
    manifest_path_raw = manifest.get("path")
    expected_sha = manifest.get("sha256")
    if not isinstance(manifest_path_raw, str) or not manifest_path_raw:
        issues.append(f"{label} path missing")
        return
    if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
        issues.append(f"{label} sha256 invalid")
        return
    manifest_path = Path(manifest_path_raw)
    if not manifest_path.is_file():
        issues.append(f"{label} file not found: {manifest_path}")
        return
    actual_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        issues.append(f"{label} sha256 mismatch: expected {expected_sha}, got {actual_sha}")


def _prediction_backtest_run_metadata(args: Any) -> dict[str, str]:
    return {
        "feature_cutoff": args.feature_cutoff.isoformat(),
        "label_start": args.label_start.isoformat(),
        "label_end": args.label_end.isoformat(),
    }


def _prediction_eval_manifest(summary: dict[str, Any], args: Any) -> dict[str, Any]:
    run_metadata = {
        "command": "prediction-eval-report",
        **_prediction_window_run_metadata(args),
        "bill_semantics_root": summary["bill_semantics_root"],
        "bill_semantics_index_sha256": summary["bill_semantics_index_sha256"],
        "bill_semantics_model_names": summary["bill_semantics_model_names"],
        "thresholds": _prediction_eval_thresholds(args),
        "source_state": summary["source_state"],
    }
    if "congress_archive_manifest" in summary:
        run_metadata["congress_archive_manifest"] = summary["congress_archive_manifest"]
    inputs = {
        "bill_semantics_root": summary["bill_semantics_root"],
        "bill_semantics_index_sha256": summary["bill_semantics_index_sha256"],
        "bill_semantics_model_names": summary["bill_semantics_model_names"],
    }
    if "congress_archive_manifest" in summary:
        inputs["congress_archive_manifest"] = summary["congress_archive_manifest"]
    return {
        "command": "prediction-eval-report",
        "run_metadata": run_metadata,
        "windows": {
            "training_feature_cutoff": args.training_feature_cutoff.isoformat(),
            "train_start": args.train_start.isoformat(),
            "train_end": args.train_end.isoformat(),
            "feature_cutoff": args.feature_cutoff.isoformat(),
            "label_start": args.label_start.isoformat(),
            "label_end": args.label_end.isoformat(),
        },
        "inputs": inputs,
        "artifacts": {
            "report": {
                "path": summary["output"],
                "sha256": summary["output_sha256"],
            },
            "dataset": {
                "path": summary["dataset_output"],
                "sha256": summary["dataset_output_sha256"],
            },
        },
        "source_state": summary["source_state"],
        "quality": {
            "ok": summary["ok"],
            "strict_readiness": summary["strict_readiness"],
            "thresholds": _prediction_eval_thresholds(args),
            "readiness_status": summary["readiness_status"],
            "readiness_blocking_reasons": summary["readiness_blocking_reasons"],
            "readiness_warning_reasons": summary["readiness_warning_reasons"],
            "quality_gate_failures": summary["quality_gate_failures"],
        },
        "coverage": {
            "training_example_count": summary["training_example_count"],
            "evaluation_label_count": summary["evaluation_label_count"],
            "dataset_feature_count": summary["dataset_feature_count"],
            "bill_semantic_coverage_rate": summary["bill_semantic_coverage_rate"],
            "bill_metadata_coverage_rate": summary["bill_metadata_coverage_rate"],
            "training_feature_source_coverage_rate": summary[
                "training_feature_source_coverage_rate"
            ],
            "training_feature_source_url_coverage_rate": summary[
                "training_feature_source_url_coverage_rate"
            ],
            "training_feature_official_source_coverage_rate": summary[
                "training_feature_official_source_coverage_rate"
            ],
            "evaluation_feature_source_coverage_rate": summary[
                "evaluation_feature_source_coverage_rate"
            ],
            "evaluation_feature_source_url_coverage_rate": summary[
                "evaluation_feature_source_url_coverage_rate"
            ],
            "evaluation_feature_official_source_coverage_rate": summary[
                "evaluation_feature_official_source_coverage_rate"
            ],
            "evaluation_source_url_coverage_rate": summary["evaluation_source_url_coverage_rate"],
        },
        "learned_model": {
            "signal_count": summary["learned_model_signal_count"],
            "intercept": summary["learned_model_intercept"],
            "top_signal_coefficients": summary["top_learned_signal_coefficients"],
        },
        "failure_analysis": {
            "failure_case_count": summary["failure_case_count"],
            "failure_group_count": summary["failure_group_count"],
            "backfill_recommendation_count": summary["backfill_recommendation_count"],
            "top_failure_groups": summary["top_failure_groups"],
            "top_backfill_recommendations": summary["top_backfill_recommendations"],
        },
    }


def _top_learned_signal_coefficients(result: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    coefficients = list(getattr(result, "learned_signal_coefficients", []))
    coefficients.sort(key=lambda item: abs(float(getattr(item, "coefficient", 0.0))), reverse=True)
    return [
        cast(dict[str, Any], coefficient.model_dump(mode="json"))
        if hasattr(coefficient, "model_dump")
        else {
            "signal_name": getattr(coefficient, "signal_name"),
            "coefficient": getattr(coefficient, "coefficient"),
        }
        for coefficient in coefficients[:limit]
    ]


def _prediction_eval_source_state(
    payload: PredictionEvalReportPayload,
) -> dict[str, Any]:
    source_state = {
        "training_example_count": payload.training_example_count,
        "evaluation_label_count": payload.evaluation_label_count,
        "model_count": len(payload.models),
        "comparison_count": len(getattr(payload, "comparisons", [])),
        "dataset_training_examples": payload.dataset.training.label_count,
        "dataset_evaluation_examples": payload.dataset.evaluation.label_count,
        "dataset_feature_count": len(payload.dataset.feature_names),
        "bill_semantic_required_count": (payload.bill_semantic_coverage.required_bill_count),
        "bill_semantic_covered_count": payload.bill_semantic_coverage.covered_bill_count,
        "bill_semantic_missing_count": payload.bill_semantic_coverage.missing_bill_count,
        "bill_semantic_cutoff_ineligible_count": (
            getattr(payload.bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "bill_semantic_required_keys": list(payload.bill_semantic_coverage.required_bill_keys),
        "bill_semantic_covered_keys": list(payload.bill_semantic_coverage.covered_bill_keys),
        "bill_semantic_missing_keys": list(payload.bill_semantic_coverage.missing_bill_keys),
        "bill_semantic_cutoff_ineligible_keys": list(
            getattr(payload.bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "bill_metadata_required_count": (payload.bill_metadata_coverage.required_bill_count),
        "bill_metadata_loaded_count": payload.bill_metadata_coverage.loaded_bill_count,
        "bill_metadata_missing_count": payload.bill_metadata_coverage.missing_bill_count,
        "bill_metadata_cutoff_ineligible_count": (
            getattr(payload.bill_metadata_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "bill_metadata_required_keys": list(payload.bill_metadata_coverage.required_bill_keys),
        "bill_metadata_loaded_keys": list(payload.bill_metadata_coverage.loaded_bill_keys),
        "bill_metadata_missing_keys": list(payload.bill_metadata_coverage.missing_bill_keys),
        "bill_metadata_cutoff_ineligible_keys": list(
            getattr(payload.bill_metadata_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "training_bill_semantic_required_count": (
            payload.training_bill_semantic_coverage.required_bill_count
        ),
        "training_bill_semantic_covered_count": (
            payload.training_bill_semantic_coverage.covered_bill_count
        ),
        "training_bill_semantic_missing_count": (
            payload.training_bill_semantic_coverage.missing_bill_count
        ),
        "training_bill_semantic_cutoff_ineligible_count": (
            getattr(payload.training_bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "training_bill_semantic_required_keys": list(
            payload.training_bill_semantic_coverage.required_bill_keys
        ),
        "training_bill_semantic_covered_keys": list(
            payload.training_bill_semantic_coverage.covered_bill_keys
        ),
        "training_bill_semantic_missing_keys": list(
            payload.training_bill_semantic_coverage.missing_bill_keys
        ),
        "training_bill_semantic_cutoff_ineligible_keys": list(
            getattr(payload.training_bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "evaluation_bill_semantic_required_count": (
            payload.evaluation_bill_semantic_coverage.required_bill_count
        ),
        "evaluation_bill_semantic_covered_count": (
            payload.evaluation_bill_semantic_coverage.covered_bill_count
        ),
        "evaluation_bill_semantic_missing_count": (
            payload.evaluation_bill_semantic_coverage.missing_bill_count
        ),
        "evaluation_bill_semantic_cutoff_ineligible_count": (
            getattr(payload.evaluation_bill_semantic_coverage, "cutoff_ineligible_bill_count", 0)
        ),
        "evaluation_bill_semantic_required_keys": list(
            payload.evaluation_bill_semantic_coverage.required_bill_keys
        ),
        "evaluation_bill_semantic_covered_keys": list(
            payload.evaluation_bill_semantic_coverage.covered_bill_keys
        ),
        "evaluation_bill_semantic_missing_keys": list(
            payload.evaluation_bill_semantic_coverage.missing_bill_keys
        ),
        "evaluation_bill_semantic_cutoff_ineligible_keys": list(
            getattr(payload.evaluation_bill_semantic_coverage, "cutoff_ineligible_bill_keys", [])
        ),
        "cutoff_audit": _prediction_eval_cutoff_audit_summary(payload.cutoff_audit),
        "training_feature_source_coverage_count": len(payload.training_feature_source_coverage),
        **_prediction_eval_feature_source_state(
            "training",
            payload.training_feature_source_coverage,
        ),
        "evaluation_feature_source_coverage_count": len(payload.evaluation_feature_source_coverage),
        **_prediction_eval_feature_source_state(
            "evaluation",
            payload.evaluation_feature_source_coverage,
        ),
        "feature_source_coverage_count": len(payload.feature_source_coverage),
        "learned_model_signal_count": len(payload.learned_model.signal_names),
        "unavailable_signal_kind_count": len(payload.unavailable_signal_counts),
        "unavailable_signal_total_count": sum(payload.unavailable_signal_counts.values()),
        "failure_case_count": len(payload.top_failure_cases),
        "failure_group_count": len(payload.failure_groups),
        "backfill_recommendation_count": len(payload.backfill_recommendations),
    }
    jurisdiction_count = _prediction_eval_jurisdiction_count(payload)
    implemented_jurisdiction_count = _prediction_eval_implemented_jurisdiction_count(payload)
    portable_jurisdiction_count = _prediction_eval_portable_jurisdiction_count(payload)
    legislative_body_count = _prediction_eval_legislative_body_count(payload)
    legislative_session_count = _prediction_eval_legislative_session_count(payload)
    jurisdiction_ids = _prediction_eval_jurisdiction_ids(payload)
    implemented_jurisdiction_ids = _prediction_eval_implemented_jurisdiction_ids(payload)
    portable_jurisdiction_ids = _prediction_eval_portable_jurisdiction_ids(payload)
    legislative_body_ids = _prediction_eval_legislative_body_ids(payload)
    legislative_session_ids = _prediction_eval_legislative_session_ids(payload)
    source_family_ids = _prediction_eval_source_family_ids(payload)
    if jurisdiction_count:
        source_state["jurisdiction_count"] = jurisdiction_count
        source_state["jurisdiction_ids"] = jurisdiction_ids
    if implemented_jurisdiction_count:
        source_state["implemented_jurisdiction_count"] = implemented_jurisdiction_count
        source_state["implemented_jurisdiction_ids"] = implemented_jurisdiction_ids
    if portable_jurisdiction_count:
        source_state["portable_jurisdiction_count"] = portable_jurisdiction_count
        source_state["portable_jurisdiction_ids"] = portable_jurisdiction_ids
    if legislative_body_count:
        source_state["legislative_body_count"] = legislative_body_count
        source_state["legislative_body_ids"] = legislative_body_ids
    if legislative_session_count:
        source_state["legislative_session_count"] = legislative_session_count
        source_state["legislative_session_ids"] = legislative_session_ids
    if source_family_ids:
        source_state["source_family_count"] = len(source_family_ids)
        source_state["source_family_ids"] = source_family_ids
    return source_state


def _prediction_eval_jurisdiction_count(payload: PredictionEvalReportPayload) -> int:
    return len(_prediction_eval_jurisdiction_ids(payload))


def _prediction_eval_jurisdiction_ids(payload: PredictionEvalReportPayload) -> list[str]:
    return sorted(
        {comparison.jurisdiction_id for comparison in getattr(payload, "comparisons", [])}
    )


def _prediction_eval_implemented_jurisdiction_count(payload: PredictionEvalReportPayload) -> int:
    return len(_prediction_eval_implemented_jurisdiction_ids(payload))


def _prediction_eval_implemented_jurisdiction_ids(
    payload: PredictionEvalReportPayload,
) -> list[str]:
    return sorted(
        {
            comparison.jurisdiction_id
            for comparison in getattr(payload, "comparisons", [])
            if comparison.jurisdiction_id == "us_congress"
        }
    )


def _prediction_eval_portable_jurisdiction_count(payload: PredictionEvalReportPayload) -> int:
    return len(_prediction_eval_portable_jurisdiction_ids(payload))


def _prediction_eval_portable_jurisdiction_ids(
    payload: PredictionEvalReportPayload,
) -> list[str]:
    return sorted(
        {
            comparison.jurisdiction_id
            for comparison in getattr(payload, "comparisons", [])
            if comparison.jurisdiction_id != "us_congress"
        }
    )


def _prediction_eval_legislative_body_count(payload: PredictionEvalReportPayload) -> int:
    return len(_prediction_eval_legislative_body_ids(payload))


def _prediction_eval_legislative_body_ids(payload: PredictionEvalReportPayload) -> list[str]:
    return sorted(
        {
            f"{comparison.jurisdiction_id}:{_prediction_eval_legislative_body_id(comparison)}"
            for comparison in getattr(payload, "comparisons", [])
        }
    )


def _prediction_eval_legislative_session_count(payload: PredictionEvalReportPayload) -> int:
    return len(_prediction_eval_legislative_session_ids(payload))


def _prediction_eval_legislative_session_ids(payload: PredictionEvalReportPayload) -> list[str]:
    return sorted(
        {
            (
                f"{comparison.jurisdiction_id}:"
                f"{_prediction_eval_legislative_body_id(comparison)}:"
                f"{comparison.legislative_session_id}"
            )
            for comparison in getattr(payload, "comparisons", [])
        }
    )


def _prediction_eval_legislative_body_id(comparison: Any) -> str:
    if comparison.legislative_body_id:
        return str(comparison.legislative_body_id)
    if comparison.jurisdiction_id == "us_congress":
        return f"us_congress_{comparison.chamber}"
    return str(comparison.chamber)


def _prediction_eval_source_family_ids(payload: PredictionEvalReportPayload) -> list[str]:
    families: set[str] = set()
    for comparison in getattr(payload, "comparisons", []):
        if comparison.source_url:
            source_type = (
                "vote_event" if comparison.jurisdiction_id == "us_congress" else "legislative_vote"
            )
            families.add(_prediction_eval_source_family_id(source_type))
        for anchors_by_signal in comparison.feature_source_anchors_by_model.values():
            for anchors in anchors_by_signal.values():
                for anchor in anchors:
                    families.add(
                        _prediction_eval_source_family_id(
                            str(anchor.source_type),
                            comparison=comparison,
                        )
                    )
    return sorted(families)


def _prediction_eval_source_family_id(
    source_type: str,
    *,
    comparison: Any | None = None,
) -> str:
    source_type = source_type.strip()
    if source_type == "legislative_vote":
        return "legislative_vote"
    if source_type in {"vote_event", "congress_vote"}:
        jurisdiction_id = getattr(comparison, "jurisdiction_id", None)
        return "congress_vote" if jurisdiction_id in (None, "us_congress") else "legislative_vote"
    return source_type


def _prediction_eval_feature_source_state(
    prefix: str,
    source_coverage: list[Any],
) -> dict[str, int | float | None]:
    return {
        f"{prefix}_feature_source_prediction_count": _feature_source_prediction_count(
            source_coverage
        ),
        f"{prefix}_feature_source_sourced_prediction_count": (
            _feature_source_sourced_prediction_count(source_coverage)
        ),
        f"{prefix}_feature_source_anchor_count": _feature_source_anchor_count(source_coverage),
        f"{prefix}_feature_source_coverage_rate": _feature_source_coverage_rate(source_coverage),
        f"{prefix}_feature_source_url_sourced_prediction_count": (
            _feature_source_url_sourced_prediction_count(source_coverage)
        ),
        f"{prefix}_feature_source_url_anchor_count": _feature_source_url_anchor_count(
            source_coverage
        ),
        f"{prefix}_feature_source_url_coverage_rate": _feature_source_url_coverage_rate(
            source_coverage
        ),
        f"{prefix}_feature_official_source_sourced_prediction_count": (
            _feature_source_official_source_sourced_prediction_count(source_coverage)
        ),
        f"{prefix}_feature_official_source_anchor_count": (
            _feature_source_official_source_anchor_count(source_coverage)
        ),
        f"{prefix}_feature_official_source_coverage_rate": (
            _feature_source_official_source_coverage_rate(source_coverage)
        ),
    }


def _prediction_eval_thresholds(args: Any) -> dict[str, Any]:
    return {
        "min_training_examples": getattr(args, "min_training_examples", None),
        "min_evaluation_examples": getattr(args, "min_evaluation_examples", None),
        "min_bill_semantic_coverage_rate": getattr(
            args,
            "min_bill_semantic_coverage_rate",
            None,
        ),
        "min_bill_metadata_coverage_rate": getattr(
            args,
            "min_bill_metadata_coverage_rate",
            None,
        ),
        "min_training_bill_semantic_coverage_rate": getattr(
            args,
            "min_training_bill_semantic_coverage_rate",
            None,
        ),
        "min_evaluation_bill_semantic_coverage_rate": getattr(
            args,
            "min_evaluation_bill_semantic_coverage_rate",
            None,
        ),
        "min_training_feature_source_coverage_rate": getattr(
            args,
            "min_training_feature_source_coverage_rate",
            None,
        ),
        "min_evaluation_feature_source_coverage_rate": getattr(
            args,
            "min_evaluation_feature_source_coverage_rate",
            None,
        ),
        "min_training_feature_source_url_coverage_rate": getattr(
            args,
            "min_training_feature_source_url_coverage_rate",
            None,
        ),
        "min_training_feature_official_source_coverage_rate": getattr(
            args,
            "min_training_feature_official_source_coverage_rate",
            None,
        ),
        "min_evaluation_feature_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_feature_source_url_coverage_rate",
            None,
        ),
        "min_evaluation_feature_official_source_coverage_rate": getattr(
            args,
            "min_evaluation_feature_official_source_coverage_rate",
            None,
        ),
        "min_evaluation_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_source_url_coverage_rate",
            None,
        ),
        "fail_on_unknown_bill_semantic_availability": bool(
            getattr(args, "fail_on_unknown_bill_semantic_availability", False)
        ),
        "fail_on_unknown_bill_signal_availability": bool(
            getattr(args, "fail_on_unknown_bill_signal_availability", False)
        ),
        "fail_on_unknown_ontology_edge_availability": bool(
            getattr(args, "fail_on_unknown_ontology_edge_availability", False)
        ),
        "fail_on_unknown_contribution_signal_availability": bool(
            getattr(args, "fail_on_unknown_contribution_signal_availability", False)
        ),
        "fail_on_unknown_statement_signal_availability": bool(
            getattr(args, "fail_on_unknown_statement_signal_availability", False)
        ),
        "fail_on_mixed_bill_semantics_models": bool(
            getattr(args, "fail_on_mixed_bill_semantics_models", False)
        ),
    }


def _prediction_eval_invalid_coverage_threshold_issues(args: Any) -> list[str]:
    issues: list[str] = []
    for arg_name in _PREDICTION_EVAL_INT_THRESHOLD_ARGS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            issues.append(f"{arg_name} must be a non-negative integer")
    for arg_name in _PREDICTION_EVAL_RATE_THRESHOLD_ARGS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 <= float(value) <= 1
        ):
            issues.append(f"{arg_name} must be a number between 0 and 1")
    return issues


_PREDICTION_EVAL_INT_THRESHOLD_ARGS = (
    "min_training_examples",
    "min_evaluation_examples",
)


_PREDICTION_EVAL_RATE_THRESHOLD_ARGS = (
    "min_bill_semantic_coverage_rate",
    "min_bill_metadata_coverage_rate",
    "min_training_bill_semantic_coverage_rate",
    "min_evaluation_bill_semantic_coverage_rate",
    "min_training_feature_source_coverage_rate",
    "min_evaluation_feature_source_coverage_rate",
    "min_training_feature_source_url_coverage_rate",
    "min_training_feature_official_source_coverage_rate",
    "min_evaluation_feature_source_url_coverage_rate",
    "min_evaluation_feature_official_source_coverage_rate",
    "min_evaluation_source_url_coverage_rate",
)


def _handle_verify_prediction_eval_manifest(args: Any) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    checked = 0

    def failure_result(*, checked: int, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-manifest",
                "manifest": str(manifest_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "run_metadata": _prediction_eval_manifest_verify_run_metadata(
                    args,
                    manifest_path,
                ),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
            },
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"failed to load manifest: {exc}"])
    if not isinstance(manifest, dict):
        return failure_result(checked=0, issues=["manifest must be an object"])
    if manifest.get("command") != "prediction-eval-report":
        issues.append("manifest command must be prediction-eval-report")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        return failure_result(
            checked=0,
            issues=["manifest artifacts must be an object"],
        )
    require_artifact_run_metadata = bool(getattr(args, "require_artifact_run_metadata", False))
    if require_artifact_run_metadata:
        if not isinstance(manifest.get("windows"), dict):
            issues.append("manifest windows missing")
        if not isinstance(manifest.get("inputs"), dict):
            issues.append("manifest inputs missing")
        quality = manifest.get("quality")
        if not isinstance(quality, dict) or not isinstance(
            quality.get("thresholds"),
            dict,
        ):
            issues.append("manifest quality thresholds missing")
        if "source_state" not in manifest:
            issues.append("manifest source_state missing")
    derived_source_state: dict[str, Any] | None = None
    if not isinstance(manifest.get("source_state"), dict):
        report_payload = _load_prediction_eval_report_from_artifacts(artifacts)
        if report_payload is not None:
            derived_source_state = _prediction_eval_source_state(report_payload)
    _validate_prediction_eval_manifest_run_metadata(
        manifest=manifest,
        issues=issues,
        require_run_metadata=require_artifact_run_metadata,
        expected_source_state=derived_source_state,
    )
    for required_name in ("report", "dataset"):
        if required_name not in artifacts or artifacts[required_name] is None:
            issues.append(f"{required_name}: missing artifact entry")
    for name, artifact in artifacts.items():
        if artifact is None:
            continue
        checked += 1
        if not isinstance(artifact, dict):
            issues.append(f"{name}: artifact must be an object")
            continue
        artifact_path_raw = artifact.get("path")
        expected_sha = artifact.get("sha256")
        if not artifact_path_raw:
            issues.append(f"{name}: missing path")
            continue
        if expected_sha is None or expected_sha == "":
            issues.append(f"{name}: missing sha256")
            continue
        if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
            issues.append(f"{name}: invalid sha256")
            continue
        artifact_path = Path(str(artifact_path_raw))
        if not artifact_path.is_file():
            issues.append(f"{name}: file not found: {artifact_path}")
            continue
        actual_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            issues.append(f"{name}: sha256 mismatch: expected {expected_sha}, got {actual_sha}")
        _validate_prediction_eval_artifact_schema(
            name=name,
            artifact_path=artifact_path,
            issues=issues,
        )
        _validate_prediction_eval_artifact_run_metadata(
            name=name,
            artifact_path=artifact_path,
            manifest=manifest,
            issues=issues,
            require_run_metadata=require_artifact_run_metadata,
            expected_source_state=derived_source_state,
        )
    inputs = manifest.get("inputs")
    if isinstance(inputs, dict):
        _validate_prediction_eval_manifest_inputs_shape(inputs=inputs, issues=issues)
        _validate_congress_archive_manifest_metadata(
            label="manifest inputs congress_archive_manifest",
            manifest=inputs.get("congress_archive_manifest"),
            issues=issues,
            require_manifest=bool(getattr(args, "require_congress_archive_manifest", False)),
        )
    elif bool(getattr(args, "require_congress_archive_manifest", False)):
        issues.append("manifest inputs congress_archive_manifest missing")
    _validate_prediction_eval_manifest_payload_consistency(
        manifest=manifest,
        artifacts=artifacts,
        issues=issues,
    )
    invalid_coverage_thresholds = _prediction_eval_manifest_invalid_coverage_thresholds(
        args=args,
        issues=issues,
    )
    report_payload = _load_prediction_eval_report_from_artifacts(artifacts)
    source_family_ids = (
        _prediction_eval_source_family_ids(report_payload) if report_payload is not None else []
    )
    required_source_family_ids = _required_source_family_ids(
        getattr(args, "require_source_families", None),
        issues=issues,
    )
    missing_required_source_family_ids = [
        source_family_id
        for source_family_id in required_source_family_ids
        if source_family_id not in source_family_ids
    ]
    quality_gate_failures.extend(
        f"missing_required_source_family:{source_family_id}"
        for source_family_id in missing_required_source_family_ids
    )
    model_names = _prediction_eval_manifest_model_names(artifacts)
    required_model_names = _required_string_list(getattr(args, "require_model_name", None))
    required_bill_semantics_model_names = _required_string_list(
        getattr(args, "require_bill_semantics_model_name", None)
    )
    require_bill_semantics_source_inputs_sha256 = bool(
        getattr(args, "require_bill_semantics_source_inputs_sha256", False)
    )
    require_bill_semantics_cache = (
        bool(getattr(args, "require_bill_semantics_cache", False))
        or bool(required_bill_semantics_model_names)
        or require_bill_semantics_source_inputs_sha256
    )
    quality_gate_failures.extend(
        _prediction_eval_manifest_required_model_failures(
            model_names=model_names,
            required_model_names=required_model_names,
        )
    )
    quality_gate_failures.extend(
        _prediction_eval_manifest_coverage_threshold_failures(
            manifest=manifest,
            args=args,
            invalid_thresholds=invalid_coverage_thresholds,
        )
    )
    quality_gate_failures.extend(
        _prediction_eval_manifest_required_threshold_failures(
            manifest=manifest,
            args=args,
        )
    )
    quality_gate_failures.extend(
        _prediction_eval_manifest_failure_analysis_failures(
            manifest=manifest,
            require_failure_analysis=bool(getattr(args, "require_failure_analysis", False)),
            require_backfill_recommendations=bool(
                getattr(args, "require_backfill_recommendations", False)
            ),
        )
    )
    ontology_feature_signal_state = _prediction_eval_manifest_ontology_feature_signal_state(
        artifacts
    )
    if (
        bool(getattr(args, "require_ontology_feature_signals", False))
        and ontology_feature_signal_state["missing_signal_names"]
    ):
        quality_gate_failures.append("missing_ontology_feature_signals")
    if (
        bool(getattr(args, "require_ontology_feature_signals", False))
        and not ontology_feature_signal_state["missing_signal_names"]
        and ontology_feature_signal_state["missing_source_anchor_signal_names"]
    ):
        quality_gate_failures.append("missing_ontology_feature_source_anchors")
    if (
        bool(getattr(args, "require_ontology_feature_signals", False))
        and not ontology_feature_signal_state["missing_signal_names"]
        and not ontology_feature_signal_state["missing_source_anchor_signal_names"]
        and ontology_feature_signal_state["missing_learned_signal_names"]
    ):
        quality_gate_failures.append("missing_learned_ontology_feature_signals")
    if isinstance(inputs, dict):
        bill_semantics_root = inputs.get("bill_semantics_root")
        has_bill_semantics_root = (
            isinstance(bill_semantics_root, str)
            and bill_semantics_root == bill_semantics_root.strip()
            and bool(bill_semantics_root.strip())
        )
        expected_bill_semantics_index_sha = inputs.get("bill_semantics_index_sha256")
        if require_bill_semantics_cache and not _manifest_has_bill_semantics_cache(inputs):
            quality_gate_failures.append("bill_semantics_cache_missing")
        if has_bill_semantics_root and (
            expected_bill_semantics_index_sha is None or expected_bill_semantics_index_sha == ""
        ):
            issues.append("bill-semantics index: missing sha256")
        elif expected_bill_semantics_index_sha and bill_semantics_root in {None, ""}:
            issues.append("bill-semantics index: missing root")
        elif has_bill_semantics_root and expected_bill_semantics_index_sha:
            checked += 1
            bill_semantics_index = Path(str(bill_semantics_root)) / "index.json"
            if not isinstance(
                expected_bill_semantics_index_sha,
                str,
            ) or not _is_sha256_hex(expected_bill_semantics_index_sha):
                issues.append("bill-semantics index: invalid sha256")
            elif not bill_semantics_index.is_file():
                issues.append(f"bill-semantics index: file not found: {bill_semantics_index}")
            else:
                actual_bill_semantics_index_sha = hashlib.sha256(
                    bill_semantics_index.read_bytes()
                ).hexdigest()
                if actual_bill_semantics_index_sha != expected_bill_semantics_index_sha:
                    issues.append(
                        "bill-semantics index: sha256 mismatch: "
                        f"expected {expected_bill_semantics_index_sha}, "
                        f"got {actual_bill_semantics_index_sha}"
                    )
                expected_model_names = inputs.get("bill_semantics_model_names")
                if expected_model_names is not None:
                    if _prediction_eval_manifest_input_model_names_are_valid(expected_model_names):
                        actual_model_names = _bill_semantics_index_model_names(
                            Path(str(bill_semantics_root))
                        )
                        normalized_expected_model_names = sorted(
                            str(model_name) for model_name in expected_model_names
                        )
                        if normalized_expected_model_names != actual_model_names:
                            issues.append(
                                "bill-semantics index: model_names mismatch: "
                                f"expected {normalized_expected_model_names!r}, "
                                f"got {actual_model_names!r}"
                            )
                if require_bill_semantics_cache:
                    quality_gate_failures.extend(
                        _prediction_backtest_bill_semantics_cache_failures(
                            run_metadata={
                                "bill_semantics_root": bill_semantics_root,
                                "bill_semantics_index_sha256": (expected_bill_semantics_index_sha),
                            },
                            required_model_names=required_bill_semantics_model_names,
                            require_source_inputs_sha256=(
                                require_bill_semantics_source_inputs_sha256
                            ),
                            issues=issues,
                        )
                    )
    elif require_bill_semantics_cache:
        quality_gate_failures.append("bill_semantics_cache_missing")
    if bool(getattr(args, "require_ready_quality", False)):
        quality_gate_failures.extend(_prediction_eval_manifest_ready_quality_failures(manifest))
    failure_analysis = manifest.get("failure_analysis")
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-eval-manifest",
            "manifest": str(manifest_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "model_names": model_names,
            "required_model_names": required_model_names,
            "source_family_ids": source_family_ids,
            "required_source_family_ids": required_source_family_ids,
            "missing_required_source_family_ids": missing_required_source_family_ids,
            "ontology_feature_signal_state": ontology_feature_signal_state,
            "failure_case_count": _manifest_failure_analysis_int(
                failure_analysis,
                "failure_case_count",
            ),
            "failure_group_count": _manifest_failure_analysis_int(
                failure_analysis,
                "failure_group_count",
            ),
            "backfill_recommendation_count": _manifest_failure_analysis_int(
                failure_analysis,
                "backfill_recommendation_count",
            ),
            "run_metadata": _prediction_eval_manifest_verify_run_metadata(
                args,
                manifest_path,
                artifacts=artifacts,
                source_state=_prediction_eval_manifest_verify_source_state(
                    manifest=manifest,
                    artifacts=artifacts,
                    ontology_feature_signal_state=ontology_feature_signal_state,
                ),
            ),
        },
    )


def _manifest_has_bill_semantics_cache(inputs: dict[Any, Any]) -> bool:
    root = inputs.get("bill_semantics_root")
    index_sha = inputs.get("bill_semantics_index_sha256")
    return bool(
        isinstance(root, str)
        and root == root.strip()
        and root.strip()
        and isinstance(index_sha, str)
        and _is_sha256_hex(index_sha)
    )


def _validate_prediction_eval_manifest_inputs_shape(
    *,
    inputs: dict[Any, Any],
    issues: list[str],
) -> None:
    input_model_names = inputs.get("bill_semantics_model_names")
    if "bill_semantics_model_names" in inputs and not isinstance(input_model_names, list):
        _append_issue_once(issues, "manifest inputs bill_semantics_model_names must be a list")
    elif isinstance(input_model_names, list) and not all(
        isinstance(model_name, str) for model_name in input_model_names
    ):
        _append_issue_once(
            issues,
            "manifest inputs bill_semantics_model_names must be a string list",
        )
    elif isinstance(input_model_names, list) and _string_list_has_empty(input_model_names):
        _append_issue_once(
            issues,
            "manifest inputs bill_semantics_model_names must be non-empty",
        )
    elif isinstance(input_model_names, list) and _string_list_has_untrimmed(input_model_names):
        _append_issue_once(
            issues,
            "manifest inputs bill_semantics_model_names must be trimmed",
        )
    elif isinstance(input_model_names, list) and _string_list_has_duplicates(input_model_names):
        _append_issue_once(
            issues,
            "manifest inputs bill_semantics_model_names must be unique",
        )
    input_index_sha_issue = _prediction_eval_optional_sha256_issue(
        "manifest inputs bill_semantics_index_sha256",
        inputs.get("bill_semantics_index_sha256"),
    )
    if input_index_sha_issue is not None:
        _append_issue_once(issues, input_index_sha_issue)
    input_root_issue = _prediction_eval_optional_path_string_issue(
        "manifest inputs bill_semantics_root",
        inputs.get("bill_semantics_root"),
    )
    if input_root_issue is not None:
        _append_issue_once(issues, input_root_issue)


def _prediction_eval_manifest_input_model_names_are_valid(value: object) -> bool:
    if not isinstance(value, list):
        return False
    if not all(isinstance(model_name, str) for model_name in value):
        return False
    if _string_list_has_empty(value):
        return False
    if _string_list_has_untrimmed(value):
        return False
    return not _string_list_has_duplicates(value)


def _prediction_eval_manifest_model_names(artifacts: dict[Any, Any]) -> list[str]:
    report = _load_prediction_eval_report_from_artifacts(artifacts)
    if report is None:
        return []
    return sorted({model.model_name for model in report.models})


def _required_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _required_model_names(
    value: Any,
    *,
    issues: list[str] | None,
    label: str,
) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    valid: list[str] = []
    malformed = False
    for item in values:
        if not isinstance(item, str):
            malformed = True
            continue
        if not item or item != item.strip():
            malformed = True
            continue
        valid.append(item)
    if len(set(valid)) != len(valid):
        malformed = True
    if malformed and issues is not None:
        issues.append(f"{label} must contain unique trimmed non-empty strings")
    return sorted(set(valid))


def _required_source_family_ids(value: Any, *, issues: list[str] | None) -> list[str]:
    source_family_ids = sorted(set(_required_string_list(value)))
    malformed = [
        source_family_id
        for source_family_id in source_family_ids
        if _SOURCE_FAMILY_ID_RE.fullmatch(source_family_id) is None
    ]
    if malformed and issues is not None:
        issues.append("require_source_families must contain normalized source family ids")
    return [
        source_family_id
        for source_family_id in source_family_ids
        if source_family_id not in malformed
    ]


def _prediction_eval_manifest_required_model_failures(
    *,
    model_names: list[str],
    required_model_names: list[str],
) -> list[str]:
    present = set(model_names)
    return [
        f"missing_model_name:{model_name}"
        for model_name in required_model_names
        if model_name not in present
    ]


def _prediction_eval_manifest_ontology_feature_signal_state(
    artifacts: dict[Any, Any],
) -> dict[str, Any]:
    dataset = _load_prediction_eval_dataset_from_artifacts(artifacts)
    report = _load_prediction_eval_report_from_artifacts(artifacts)
    learned_signal_names: set[str] = (
        set(report.learned_model.signal_names) if report is not None else set()
    )
    if dataset is not None:
        feature_names = set(dataset.feature_names)
        training_missing_source_anchor_signal_names = (
            _prediction_eval_split_missing_source_anchor_signal_names(
                dataset.training,
                REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
            )
        )
        evaluation_missing_source_anchor_signal_names = (
            _prediction_eval_split_missing_source_anchor_signal_names(
                dataset.evaluation,
                REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
            )
        )
        source = "dataset"
    elif report is not None:
        feature_names = set(report.dataset.feature_names)
        training_missing_source_anchor_signal_names = (
            _prediction_eval_split_missing_source_anchor_signal_names(
                report.dataset.training,
                REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
            )
        )
        evaluation_missing_source_anchor_signal_names = (
            _prediction_eval_split_missing_source_anchor_signal_names(
                report.dataset.evaluation,
                REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
            )
        )
        source = "report.dataset"
    else:
        feature_names = set()
        training_missing_source_anchor_signal_names = set()
        evaluation_missing_source_anchor_signal_names = set()
        source = None
    required = sorted(REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES)
    missing = [signal_name for signal_name in required if signal_name not in feature_names]
    present = [signal_name for signal_name in required if signal_name in feature_names]
    required_present = {signal_name for signal_name in required if signal_name in feature_names}
    training_source_backed_signal_names = (
        required_present - training_missing_source_anchor_signal_names
    )
    evaluation_source_backed_signal_names = (
        required_present - evaluation_missing_source_anchor_signal_names
    )
    source_backed_signal_names = (
        training_source_backed_signal_names & evaluation_source_backed_signal_names
    )
    training_source_backed = [
        signal_name
        for signal_name in required
        if signal_name in training_source_backed_signal_names
    ]
    evaluation_source_backed = [
        signal_name
        for signal_name in required
        if signal_name in evaluation_source_backed_signal_names
    ]
    source_backed = [
        signal_name for signal_name in required if signal_name in source_backed_signal_names
    ]
    learned = [signal_name for signal_name in required if signal_name in learned_signal_names]
    missing_training_source_anchors = [
        signal_name
        for signal_name in required
        if signal_name in training_missing_source_anchor_signal_names
    ]
    missing_evaluation_source_anchors = [
        signal_name
        for signal_name in required
        if signal_name in evaluation_missing_source_anchor_signal_names
    ]
    missing_source_anchors = [
        signal_name
        for signal_name in required
        if signal_name in training_missing_source_anchor_signal_names
        or signal_name in evaluation_missing_source_anchor_signal_names
    ]
    missing_learned = [
        signal_name
        for signal_name in required
        if signal_name in feature_names and signal_name not in learned_signal_names
    ]
    return {
        "source": source,
        "required_signal_names": required,
        "present_signal_names": present,
        "missing_signal_names": missing,
        "training_source_backed_signal_names": training_source_backed,
        "evaluation_source_backed_signal_names": evaluation_source_backed,
        "source_backed_signal_names": source_backed,
        "missing_training_source_anchor_signal_names": missing_training_source_anchors,
        "missing_evaluation_source_anchor_signal_names": missing_evaluation_source_anchors,
        "missing_source_anchor_signal_names": missing_source_anchors,
        "learned_signal_names": learned,
        "missing_learned_signal_names": missing_learned,
        "feature_name_count": len(feature_names),
    }


def _prediction_eval_split_missing_source_anchor_signal_names(
    split: Any,
    required_signal_names: set[str],
) -> set[str]:
    missing: set[str] = set()
    for example in getattr(split, "examples", []):
        feature_signals = getattr(example, "features", {})
        feature_source_anchors = getattr(example, "feature_source_anchors", {})
        for signal_name in required_signal_names:
            if signal_name not in feature_signals:
                continue
            if not _ontology_signal_requires_source_anchor(
                signal_name,
                feature_signals.get(signal_name),
            ):
                continue
            if not has_official_claim_source_anchor(feature_source_anchors.get(signal_name, [])):
                missing.add(signal_name)
    return missing


def _ontology_signal_requires_source_anchor(signal_name: str, value: float | None) -> bool:
    if value is None:
        return False
    if signal_name == "sponsor_cosponsor_alignment":
        return abs(value - 0.5) > 1e-9
    return abs(value) > 1e-9


def _prediction_eval_split_source_backed_signal_names(
    split: Any,
) -> set[str]:
    return {
        signal_name
        for example in getattr(split, "examples", [])
        for signal_name, anchors in example.feature_source_anchors.items()
        if has_official_claim_source_anchor(anchors)
    }


_PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS = (
    ("min_bill_semantic_coverage_rate", "bill_semantic_coverage_rate"),
    ("min_bill_metadata_coverage_rate", "bill_metadata_coverage_rate"),
    (
        "min_training_feature_source_coverage_rate",
        "training_feature_source_coverage_rate",
    ),
    (
        "min_evaluation_feature_source_coverage_rate",
        "evaluation_feature_source_coverage_rate",
    ),
    (
        "min_training_feature_source_url_coverage_rate",
        "training_feature_source_url_coverage_rate",
    ),
    (
        "min_training_feature_official_source_coverage_rate",
        "training_feature_official_source_coverage_rate",
    ),
    (
        "min_evaluation_feature_source_url_coverage_rate",
        "evaluation_feature_source_url_coverage_rate",
    ),
    (
        "min_evaluation_feature_official_source_coverage_rate",
        "evaluation_feature_official_source_coverage_rate",
    ),
    ("min_evaluation_source_url_coverage_rate", "evaluation_source_url_coverage_rate"),
)


def _prediction_eval_manifest_coverage_threshold_failures(
    *,
    manifest: dict[str, Any],
    args: Any,
    invalid_thresholds: set[str] | None = None,
) -> list[str]:
    thresholds: list[tuple[str, float]] = []
    invalid_thresholds = invalid_thresholds or set()
    for arg_name, coverage_key in _PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS:
        if arg_name in invalid_thresholds:
            continue
        minimum = getattr(args, arg_name, None)
        if minimum is not None:
            thresholds.append((coverage_key, float(minimum)))
    if not thresholds:
        return []
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        return [f"coverage_missing:{coverage_key}" for coverage_key, _ in thresholds]
    failures: list[str] = []
    for coverage_key, minimum in thresholds:
        value = coverage.get(coverage_key)
        if not isinstance(value, int | float):
            failures.append(f"coverage_missing:{coverage_key}")
        elif float(value) < minimum:
            failures.append(f"coverage_below_min:{coverage_key}")
    return failures


def _prediction_eval_manifest_invalid_coverage_thresholds(
    *,
    args: Any,
    issues: list[str],
) -> set[str]:
    invalid: set[str] = set()
    for arg_name, _ in _PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 <= float(value) <= 1
        ):
            invalid.add(arg_name)
            issues.append(f"{arg_name} must be a number between 0 and 1")
    return invalid


_PREDICTION_EVAL_MANIFEST_REQUIRED_BOOL_THRESHOLDS = (
    (
        "require_fail_on_unknown_bill_semantic_availability",
        "fail_on_unknown_bill_semantic_availability",
    ),
    (
        "require_fail_on_unknown_bill_signal_availability",
        "fail_on_unknown_bill_signal_availability",
    ),
    (
        "require_fail_on_unknown_ontology_edge_availability",
        "fail_on_unknown_ontology_edge_availability",
    ),
    (
        "require_fail_on_unknown_contribution_signal_availability",
        "fail_on_unknown_contribution_signal_availability",
    ),
    (
        "require_fail_on_unknown_statement_signal_availability",
        "fail_on_unknown_statement_signal_availability",
    ),
)


def _prediction_eval_manifest_required_threshold_failures(
    *,
    manifest: dict[str, Any],
    args: Any,
) -> list[str]:
    required_thresholds = [
        threshold_name
        for arg_name, threshold_name in _PREDICTION_EVAL_MANIFEST_REQUIRED_BOOL_THRESHOLDS
        if bool(getattr(args, arg_name, False))
    ]
    if not required_thresholds:
        return []
    quality = manifest.get("quality")
    thresholds = quality.get("thresholds") if isinstance(quality, dict) else None
    if not isinstance(thresholds, dict):
        return [
            f"required_threshold_missing:{threshold_name}" for threshold_name in required_thresholds
        ]
    return [
        f"required_threshold_missing:{threshold_name}"
        for threshold_name in required_thresholds
        if thresholds.get(threshold_name) is not True
    ]


_PREDICTION_EVAL_MANIFEST_FAILURE_ANALYSIS_KEYS = (
    "failure_case_count",
    "failure_group_count",
    "backfill_recommendation_count",
    "top_failure_groups",
    "top_backfill_recommendations",
)


def _prediction_eval_manifest_failure_analysis_failures(
    *,
    manifest: dict[str, Any],
    require_failure_analysis: bool,
    require_backfill_recommendations: bool,
) -> list[str]:
    if not require_failure_analysis and not require_backfill_recommendations:
        return []
    failure_analysis = manifest.get("failure_analysis")
    if not isinstance(failure_analysis, dict):
        return ["failure_analysis_missing"]

    failures: list[str] = []
    if require_failure_analysis:
        for key in _PREDICTION_EVAL_MANIFEST_FAILURE_ANALYSIS_KEYS:
            if key not in failure_analysis:
                failures.append(f"failure_analysis_missing:{key}")
        for key in (
            "failure_case_count",
            "failure_group_count",
            "backfill_recommendation_count",
        ):
            if key in failure_analysis and not _is_plain_int(failure_analysis.get(key)):
                failures.append(f"failure_analysis_invalid:{key}")
            elif key in failure_analysis and not _is_non_negative_plain_int(
                failure_analysis.get(key)
            ):
                failures.append(f"failure_analysis_invalid:{key}")
        for key in ("top_failure_groups", "top_backfill_recommendations"):
            if key in failure_analysis and not isinstance(failure_analysis.get(key), list):
                failures.append(f"failure_analysis_invalid:{key}")
        top_failure_groups = failure_analysis.get("top_failure_groups")
        if isinstance(top_failure_groups, list):
            failures.extend(
                _prediction_eval_manifest_failure_group_sample_failures(top_failure_groups)
            )

    if require_backfill_recommendations:
        recommendation_count = failure_analysis.get("backfill_recommendation_count")
        recommendations = failure_analysis.get("top_backfill_recommendations")
        if (
            not _is_plain_int(recommendation_count)
            or recommendation_count <= 0
            or not isinstance(recommendations, list)
            or not recommendations
        ):
            failures.append("backfill_recommendations_missing")
    recommendations = failure_analysis.get("top_backfill_recommendations")
    if isinstance(recommendations, list):
        failures.extend(
            _prediction_eval_manifest_backfill_recommendation_shape_failures(recommendations)
        )
        failures.extend(
            _prediction_eval_manifest_cutoff_ineligible_backfill_failures(
                manifest=manifest,
                recommendations=recommendations,
            )
        )
    return failures


def _prediction_eval_manifest_backfill_recommendation_shape_failures(
    recommendations: list[Any],
) -> list[str]:
    failures: list[str] = []
    for index, recommendation in enumerate(recommendations):
        if not isinstance(recommendation, dict):
            failures.append(f"failure_analysis_invalid:top_backfill_recommendations[{index}]")
            continue
        prefix = f"failure_analysis_invalid:top_backfill_recommendations[{index}]"
        for key in ("action", "reason"):
            value = recommendation.get(key)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                failures.append(f"{prefix}.{key}")
        for key in ("priority_score", "affected_case_count"):
            if not _is_non_negative_plain_int(recommendation.get(key)):
                failures.append(f"{prefix}.{key}")
        unavailable_signal_counts = recommendation.get("unavailable_signal_counts")
        if not isinstance(unavailable_signal_counts, dict) or any(
            not isinstance(key, str)
            or not key.strip()
            or key != key.strip()
            or not _is_non_negative_plain_int(value)
            for key, value in (
                unavailable_signal_counts.items()
                if isinstance(unavailable_signal_counts, dict)
                else ()
            )
        ):
            failures.append(f"{prefix}.unavailable_signal_counts")
        sample_vote_event_ids = recommendation.get("sample_vote_event_ids")
        if sample_vote_event_ids is not None and (
            not isinstance(sample_vote_event_ids, list)
            or any(not _is_non_negative_plain_int(value) for value in sample_vote_event_ids)
        ):
            failures.append(f"{prefix}.sample_vote_event_ids")
        sample_source_family_ids = recommendation.get("sample_source_family_ids")
        if sample_source_family_ids is not None and not _source_family_id_list(
            sample_source_family_ids
        ):
            failures.append(f"{prefix}.sample_source_family_ids")
        sample_cases = recommendation.get("sample_cases")
        if sample_cases is not None:
            failures.extend(
                _prediction_eval_manifest_sample_case_shape_failures(
                    sample_cases,
                    prefix=prefix,
                )
            )
            if (
                isinstance(sample_vote_event_ids, list)
                and all(_is_non_negative_plain_int(value) for value in sample_vote_event_ids)
                and isinstance(sample_cases, list)
            ):
                sample_case_vote_event_ids = [
                    sample_case.get("vote_event_id")
                    for sample_case in sample_cases
                    if isinstance(sample_case, dict)
                    and _is_non_negative_plain_int(sample_case.get("vote_event_id"))
                ]
                if (
                    sample_case_vote_event_ids
                    and sample_case_vote_event_ids
                    != sample_vote_event_ids[: len(sample_case_vote_event_ids)]
                ):
                    failures.append(
                        f"failure_analysis_mismatch:top_backfill_recommendations[{index}].sample_cases"
                    )
        missing_bill_keys = recommendation.get("missing_bill_keys")
        if missing_bill_keys is not None and not _non_empty_string_list(missing_bill_keys):
            failures.append(f"{prefix}.missing_bill_keys")
    return failures


def _prediction_eval_manifest_sample_case_shape_failures(
    sample_cases: Any,
    *,
    prefix: str,
) -> list[str]:
    if not isinstance(sample_cases, list):
        return [f"{prefix}.sample_cases"]
    failures: list[str] = []
    for case_index, sample_case in enumerate(sample_cases):
        case_prefix = f"{prefix}.sample_cases[{case_index}]"
        if not isinstance(sample_case, dict):
            failures.append(case_prefix)
            continue
        vote_event_id = sample_case.get("vote_event_id")
        if vote_event_id is not None and not _is_non_negative_plain_int(vote_event_id):
            failures.append(f"{case_prefix}.vote_event_id")
        for key in (
            "event_key",
            "jurisdiction_id",
            "legislative_body_id",
            "legislative_session_id",
        ):
            value = sample_case.get(key)
            if value is not None and (
                not isinstance(value, str) or not value.strip() or value != value.strip()
            ):
                failures.append(f"{case_prefix}.{key}")
        for key in ("bill_key", "bill_context_key", "member_bioguide_id"):
            value = sample_case.get(key)
            if value is not None and (
                not isinstance(value, str) or not value.strip() or value != value.strip()
            ):
                failures.append(f"{case_prefix}.{key}")
        jurisdiction_id = sample_case.get("jurisdiction_id")
        if (
            isinstance(jurisdiction_id, str)
            and jurisdiction_id
            and jurisdiction_id != "us_congress"
        ):
            for key in ("legislative_body_id", "legislative_session_id"):
                value = sample_case.get(key)
                if not isinstance(value, str) or not value.strip() or value != value.strip():
                    failures.append(f"{case_prefix}.{key}")
    return failures


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item and item.strip() == item for item in value
    )


def _prediction_eval_manifest_cutoff_ineligible_backfill_failures(
    *,
    manifest: dict[str, Any],
    recommendations: list[Any],
) -> list[str]:
    source_state = manifest.get("source_state")
    if not isinstance(source_state, dict):
        return []
    cutoff_keys_by_action = {
        "materialize_missing_bill_semantics": set(
            _string_list(source_state.get("bill_semantic_cutoff_ineligible_keys"))
        )
        | set(_string_list(source_state.get("training_bill_semantic_cutoff_ineligible_keys")))
        | set(_string_list(source_state.get("evaluation_bill_semantic_cutoff_ineligible_keys"))),
        "timestamp_bill_semantic_availability": set(
            _string_list(source_state.get("bill_semantic_cutoff_ineligible_keys"))
        )
        | set(_string_list(source_state.get("training_bill_semantic_cutoff_ineligible_keys")))
        | set(_string_list(source_state.get("evaluation_bill_semantic_cutoff_ineligible_keys"))),
        "load_missing_bill_metadata": set(
            _string_list(source_state.get("bill_metadata_cutoff_ineligible_keys"))
        ),
    }
    failures: list[str] = []
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        action = recommendation.get("action")
        if not isinstance(action, str):
            continue
        cutoff_keys = cutoff_keys_by_action.get(action, set())
        if not cutoff_keys:
            continue
        for bill_key in _string_list(recommendation.get("missing_bill_keys")):
            if bill_key in cutoff_keys:
                failures.append(
                    f"failure_analysis_cutoff_ineligible_backfill_key:{action}:{bill_key}"
                )
    return sorted(set(failures))


def _prediction_eval_manifest_failure_group_sample_failures(
    top_failure_groups: list[Any],
) -> list[str]:
    failures: list[str] = []
    for group_index, group in enumerate(top_failure_groups):
        prefix = f"top_failure_groups[{group_index}]"
        if not isinstance(group, dict):
            failures.append(f"failure_analysis_invalid:{prefix}")
            continue
        sample_vote_event_ids = group.get("sample_vote_event_ids")
        if sample_vote_event_ids is None:
            continue
        if not isinstance(sample_vote_event_ids, list) or any(
            not _is_non_negative_plain_int(value) for value in sample_vote_event_ids
        ):
            failures.append(f"failure_analysis_invalid:{prefix}.sample_vote_event_ids")
            continue
        if not sample_vote_event_ids:
            continue
        sample_cases = group.get("sample_cases")
        if not isinstance(sample_cases, list) or not sample_cases:
            failures.append(f"failure_analysis_invalid:{prefix}.sample_cases")
            continue
        sample_case_ids: list[int] = []
        invalid_sample_case = False
        failures.extend(
            _prediction_eval_manifest_sample_case_shape_failures(
                sample_cases,
                prefix=f"failure_analysis_invalid:{prefix}",
            )
        )
        for case_index, sample_case in enumerate(sample_cases):
            if not isinstance(sample_case, dict):
                failures.append(f"failure_analysis_invalid:{prefix}.sample_cases[{case_index}]")
                invalid_sample_case = True
                continue
            vote_event_id = sample_case.get("vote_event_id")
            if not _is_non_negative_plain_int(vote_event_id):
                failures.append(
                    f"failure_analysis_invalid:{prefix}.sample_cases[{case_index}].vote_event_id"
                )
                invalid_sample_case = True
                continue
            sample_case_ids.append(vote_event_id)
        if (
            not invalid_sample_case
            and sample_case_ids != sample_vote_event_ids[: len(sample_case_ids)]
        ):
            failures.append(f"failure_analysis_mismatch:{prefix}.sample_cases")
    return failures


def _manifest_failure_analysis_int(value: Any, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    count = value.get(key)
    return count if _is_plain_int(count) else None


def _validate_prediction_eval_artifact_schema(
    *,
    name: str,
    artifact_path: Path,
    issues: list[str],
) -> None:
    try:
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"{name}: failed to load artifact JSON: {exc}")
        return
    if name == "report":
        try:
            PredictionEvalReportPayload.model_validate(artifact_payload)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{name}: artifact schema: {exc}")
    elif name == "dataset":
        try:
            PredictionEvalDatasetPayload.model_validate(artifact_payload)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{name}: artifact schema: {exc}")


def _validate_prediction_eval_manifest_payload_consistency(
    *,
    manifest: dict[str, Any],
    artifacts: dict[Any, Any],
    issues: list[str],
) -> None:
    report = _load_prediction_eval_report_from_artifacts(artifacts)
    dataset = _load_prediction_eval_dataset_from_artifacts(artifacts)
    if report is None:
        return
    if dataset is not None and dataset.model_dump(mode="json") != report.dataset.model_dump(
        mode="json"
    ):
        issues.append("dataset artifact mismatch: report.dataset")
    _validate_prediction_eval_manifest_windows(
        manifest=manifest,
        report=report,
        dataset=dataset,
        issues=issues,
    )
    _validate_prediction_eval_manifest_coverage(
        manifest=manifest,
        report=report,
        dataset=dataset,
        issues=issues,
    )
    _validate_prediction_eval_manifest_quality(
        manifest=manifest,
        report=report,
        issues=issues,
    )
    _validate_prediction_eval_manifest_source_state(
        manifest=manifest,
        report=report,
        issues=issues,
    )
    _validate_prediction_eval_manifest_learned_model(
        manifest=manifest,
        report=report,
        issues=issues,
    )
    _validate_prediction_eval_manifest_failure_analysis(
        manifest=manifest,
        report=report,
        issues=issues,
    )


def _load_prediction_eval_report_from_artifacts(
    artifacts: dict[Any, Any],
) -> PredictionEvalReportPayload | None:
    payload = _load_prediction_eval_artifact_json(artifacts, "report")
    if payload is None:
        return None
    try:
        return PredictionEvalReportPayload.model_validate(payload)
    except Exception:  # noqa: BLE001
        return None


def _load_prediction_eval_dataset_from_artifacts(
    artifacts: dict[Any, Any],
) -> PredictionEvalDatasetPayload | None:
    payload = _load_prediction_eval_artifact_json(artifacts, "dataset")
    if payload is None:
        return None
    try:
        return PredictionEvalDatasetPayload.model_validate(payload)
    except Exception:  # noqa: BLE001
        return None


def _load_prediction_eval_artifact_json(
    artifacts: dict[Any, Any],
    name: str,
) -> Any | None:
    artifact = artifacts.get(name)
    if not isinstance(artifact, dict):
        return None
    artifact_path_raw = artifact.get("path")
    if not artifact_path_raw:
        return None
    artifact_path = Path(str(artifact_path_raw))
    if not artifact_path.is_file():
        return None
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _validate_prediction_eval_manifest_windows(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    dataset: PredictionEvalDatasetPayload | None,
    issues: list[str],
) -> None:
    windows = manifest.get("windows")
    if not isinstance(windows, dict):
        return
    expected = {
        "training_feature_cutoff": report.training_feature_cutoff.isoformat(),
        "train_start": report.train_start.isoformat(),
        "train_end": report.train_end.isoformat(),
        "feature_cutoff": report.feature_cutoff.isoformat(),
        "label_start": report.label_start.isoformat(),
        "label_end": report.label_end.isoformat(),
    }
    if dataset is not None:
        dataset_expected = {
            "training_feature_cutoff": dataset.training_feature_cutoff.isoformat(),
            "train_start": dataset.train_start.isoformat(),
            "train_end": dataset.train_end.isoformat(),
            "feature_cutoff": dataset.feature_cutoff.isoformat(),
            "label_start": dataset.label_start.isoformat(),
            "label_end": dataset.label_end.isoformat(),
        }
        if dataset_expected != expected:
            issues.append("dataset artifact mismatch: windows")
    for key, expected_value in expected.items():
        if windows.get(key) != expected_value:
            issues.append(f"windows mismatch: {key}")


def _validate_prediction_eval_manifest_coverage(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    dataset: PredictionEvalDatasetPayload | None,
    issues: list[str],
) -> None:
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        return
    expected = {
        "training_example_count": report.training_example_count,
        "evaluation_label_count": report.evaluation_label_count,
        "dataset_feature_count": len(dataset.feature_names)
        if dataset is not None
        else len(report.dataset.feature_names),
        "bill_semantic_coverage_rate": report.bill_semantic_coverage.coverage_rate,
        "bill_metadata_coverage_rate": report.bill_metadata_coverage.coverage_rate,
        "training_feature_source_coverage_rate": _feature_source_coverage_rate(
            report.training_feature_source_coverage
        ),
        "training_feature_source_url_coverage_rate": _feature_source_url_coverage_rate(
            report.training_feature_source_coverage
        ),
        "training_feature_official_source_coverage_rate": (
            _feature_source_official_source_coverage_rate(report.training_feature_source_coverage)
        ),
        "evaluation_feature_source_coverage_rate": _feature_source_coverage_rate(
            report.evaluation_feature_source_coverage
        ),
        "evaluation_feature_source_url_coverage_rate": _feature_source_url_coverage_rate(
            report.evaluation_feature_source_coverage
        ),
        "evaluation_feature_official_source_coverage_rate": (
            _feature_source_official_source_coverage_rate(report.evaluation_feature_source_coverage)
        ),
        "evaluation_source_url_coverage_rate": (
            report.data_quality.evaluation.source_url_coverage_rate
        ),
    }
    for key, expected_value in expected.items():
        if key not in coverage:
            continue
        actual_value = coverage.get(key)
        if key.endswith("_coverage_rate") and not _prediction_eval_source_state_rate_valid(
            actual_value
        ):
            issues.append(f"coverage rate invalid: {key}")
            continue
        if _json_scalar_type_mismatch(actual_value, expected_value):
            issues.append(f"coverage mismatch: {key}")
        elif actual_value != expected_value:
            issues.append(f"coverage mismatch: {key}")


def _validate_prediction_eval_manifest_quality(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return
    thresholds = quality.get("thresholds")
    if thresholds is None:
        thresholds = {}
    if not isinstance(thresholds, dict):
        issues.append("quality thresholds must be an object")
        thresholds = {}
    thresholds = _validated_prediction_eval_manifest_thresholds(
        thresholds=thresholds,
        issues=issues,
    )
    strict_readiness = quality.get("strict_readiness", False)
    if not isinstance(strict_readiness, bool):
        issues.append("quality strict_readiness must be boolean")
        strict_readiness = bool(strict_readiness)
    inputs = manifest.get("inputs")
    bill_semantics_model_names: list[str] = []
    if isinstance(inputs, dict) and isinstance(inputs.get("bill_semantics_model_names"), list):
        bill_semantics_model_names = sorted(
            str(model_name) for model_name in inputs["bill_semantics_model_names"]
        )
    expected_quality_gate_failures = _prediction_eval_quality_gate_failures(
        report,
        SimpleNamespace(**thresholds),
        bill_semantics_model_names=bill_semantics_model_names,
    )
    expected_readiness_ok = (
        report.readiness.status == "ready" if strict_readiness else report.readiness.ok
    )
    expected_ok = expected_readiness_ok and not expected_quality_gate_failures
    expected_warning_reasons = list(report.readiness.warning_reasons)
    if len(bill_semantics_model_names) > 1:
        expected_warning_reasons.append("mixed_bill_semantics_models")
    expected = {
        "ok": expected_ok,
        "strict_readiness": strict_readiness,
        "readiness_status": report.readiness.status,
        "readiness_blocking_reasons": list(report.readiness.blocking_reasons),
        "readiness_warning_reasons": expected_warning_reasons,
        "quality_gate_failures": expected_quality_gate_failures,
    }
    for key, expected_value in expected.items():
        if key in quality and quality.get(key) != expected_value:
            issues.append(f"quality mismatch: {key}")


_PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS = {
    "min_training_examples",
    "min_evaluation_examples",
}

_PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS = {
    "min_bill_semantic_coverage_rate",
    "min_bill_metadata_coverage_rate",
    "min_training_bill_semantic_coverage_rate",
    "min_evaluation_bill_semantic_coverage_rate",
    "min_training_feature_source_coverage_rate",
    "min_evaluation_feature_source_coverage_rate",
    "min_training_feature_source_url_coverage_rate",
    "min_training_feature_official_source_coverage_rate",
    "min_evaluation_feature_source_url_coverage_rate",
    "min_evaluation_feature_official_source_coverage_rate",
    "min_evaluation_source_url_coverage_rate",
}

_PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS = {
    "fail_on_unknown_bill_semantic_availability",
    "fail_on_unknown_bill_signal_availability",
    "fail_on_unknown_ontology_edge_availability",
    "fail_on_unknown_contribution_signal_availability",
    "fail_on_unknown_statement_signal_availability",
    "fail_on_mixed_bill_semantics_models",
}

_PREDICTION_EVAL_MANIFEST_THRESHOLD_KEYS = (
    _PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS
    | _PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS
    | _PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS
)


_PREDICTION_EVAL_CUTOFF_AUDIT_KEYS = (
    "training_feature_row_count",
    "evaluation_feature_row_count",
    "training_label_row_count",
    "evaluation_label_row_count",
    "ontology_edge_count",
    "cutoff_ontology_edge_count",
    "unknown_availability_ontology_edge_count",
    "excluded_future_ontology_edge_count",
    "bill_signal_row_count",
    "cutoff_bill_signal_row_count",
    "unknown_availability_bill_signal_row_count",
    "excluded_future_bill_signal_row_count",
    "bill_semantic_count",
    "cutoff_training_bill_semantic_count",
    "excluded_future_training_bill_semantic_count",
    "cutoff_evaluation_bill_semantic_count",
    "excluded_future_evaluation_bill_semantic_count",
    "unknown_availability_bill_semantic_count",
    "training_contribution_signal_row_count",
    "cutoff_training_contribution_signal_row_count",
    "unknown_availability_training_contribution_signal_row_count",
    "excluded_future_training_contribution_signal_row_count",
    "evaluation_contribution_signal_row_count",
    "cutoff_evaluation_contribution_signal_row_count",
    "unknown_availability_evaluation_contribution_signal_row_count",
    "excluded_future_evaluation_contribution_signal_row_count",
    "training_statement_signal_row_count",
    "cutoff_training_statement_signal_row_count",
    "unknown_availability_training_statement_signal_row_count",
    "excluded_future_training_statement_signal_row_count",
    "evaluation_statement_signal_row_count",
    "cutoff_evaluation_statement_signal_row_count",
    "unknown_availability_evaluation_statement_signal_row_count",
    "excluded_future_evaluation_statement_signal_row_count",
)


def _validated_prediction_eval_manifest_thresholds(
    *,
    thresholds: dict[Any, Any],
    issues: list[str],
    issue_prefix: str = "quality threshold",
    invalid_keys: set[str] | None = None,
) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in thresholds.items():
        key_str = str(key)
        if key_str not in _PREDICTION_EVAL_MANIFEST_THRESHOLD_KEYS:
            issues.append(f"{issue_prefix} unknown: {key_str}")
            if invalid_keys is not None:
                invalid_keys.add(key_str)
            continue
        if value is None:
            validated[key_str] = None
            continue
        if key_str in _PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append(f"{issue_prefix} invalid: {key_str}")
                if invalid_keys is not None:
                    invalid_keys.add(key_str)
                continue
        elif key_str in _PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not 0 <= float(value) <= 1
            ):
                issues.append(f"{issue_prefix} invalid: {key_str}")
                if invalid_keys is not None:
                    invalid_keys.add(key_str)
                continue
        elif key_str in _PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS and not isinstance(
            value,
            bool,
        ):
            issues.append(f"{issue_prefix} invalid: {key_str}")
            if invalid_keys is not None:
                invalid_keys.add(key_str)
            continue
        validated[key_str] = value
    return validated


def _validate_prediction_eval_manifest_source_state(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    source_state = manifest.get("source_state")
    if source_state is None:
        return
    invalid_source_state_keys: set[str] = set()
    if isinstance(source_state, dict):
        invalid_source_state_keys = _validate_prediction_eval_source_state_shape(
            source_state=source_state,
            label="manifest source_state",
            issues=issues,
        )
    else:
        issues.append("manifest source_state must be an object")
        return
    expected = _prediction_eval_source_state(report)
    mismatch_issues = _mapping_mismatch_key_issues(
        label="manifest source_state mismatch",
        actual=source_state,
        expected=expected,
        ignored_keys=invalid_source_state_keys,
    )
    if mismatch_issues:
        issues.append("manifest source_state mismatch")
        issues.extend(mismatch_issues)


def _validate_prediction_eval_source_state_shape(
    *,
    source_state: dict[str, Any],
    label: str,
    issues: list[str],
) -> set[str]:
    invalid_keys: set[str] = set()
    source_state_index_sha_issue = _prediction_eval_optional_sha256_issue(
        f"{label} bill_semantics_index_sha256",
        source_state.get("bill_semantics_index_sha256"),
    )
    if source_state_index_sha_issue is not None:
        issues.append(source_state_index_sha_issue)
        invalid_keys.add("bill_semantics_index_sha256")
    source_state_root_issue = _prediction_eval_optional_path_string_issue(
        f"{label} bill_semantics_root",
        source_state.get("bill_semantics_root"),
    )
    if source_state_root_issue is not None:
        issues.append(source_state_root_issue)
        invalid_keys.add("bill_semantics_root")
    source_state_model_names = source_state.get("bill_semantics_model_names")
    if "bill_semantics_model_names" in source_state and not isinstance(
        source_state_model_names,
        list,
    ):
        issues.append(f"{label} bill_semantics_model_names must be a list")
        invalid_keys.add("bill_semantics_model_names")
    elif isinstance(source_state_model_names, list) and not all(
        isinstance(model_name, str) for model_name in source_state_model_names
    ):
        issues.append(f"{label} bill_semantics_model_names must be a string list")
        invalid_keys.add("bill_semantics_model_names")
    elif isinstance(source_state_model_names, list) and _string_list_has_empty(
        source_state_model_names
    ):
        issues.append(f"{label} bill_semantics_model_names must be non-empty")
        invalid_keys.add("bill_semantics_model_names")
    elif isinstance(source_state_model_names, list) and _string_list_has_untrimmed(
        source_state_model_names
    ):
        issues.append(f"{label} bill_semantics_model_names must be trimmed")
        invalid_keys.add("bill_semantics_model_names")
    elif isinstance(source_state_model_names, list) and _string_list_has_duplicates(
        source_state_model_names
    ):
        issues.append(f"{label} bill_semantics_model_names must be unique")
        invalid_keys.add("bill_semantics_model_names")
    cutoff_audit = source_state.get("cutoff_audit")
    if "cutoff_audit" in source_state:
        if not isinstance(cutoff_audit, dict):
            issues.append(f"{label} cutoff_audit must be an object")
            invalid_keys.add("cutoff_audit")
        elif any(not _is_non_negative_plain_int(value) for value in cutoff_audit.values()):
            issues.append(f"{label} cutoff_audit counts must be non-negative integers")
            invalid_keys.add("cutoff_audit")
        else:
            missing_keys = sorted(set(_PREDICTION_EVAL_CUTOFF_AUDIT_KEYS) - set(cutoff_audit))
            unexpected_keys = sorted(set(cutoff_audit) - set(_PREDICTION_EVAL_CUTOFF_AUDIT_KEYS))
            if missing_keys:
                issues.append(f"{label} cutoff_audit missing keys: {', '.join(missing_keys)}")
                invalid_keys.add("cutoff_audit")
            if unexpected_keys:
                issues.append(f"{label} cutoff_audit unexpected keys: {', '.join(unexpected_keys)}")
                invalid_keys.add("cutoff_audit")
            if not missing_keys and not unexpected_keys:
                partition_issues = _prediction_eval_cutoff_audit_partition_issues(cutoff_audit)
                if partition_issues:
                    issues.extend(
                        f"{label} cutoff_audit partition invalid: {issue}"
                        for issue in partition_issues
                    )
                    invalid_keys.add("cutoff_audit")
    for list_key in (
        "jurisdiction_ids",
        "implemented_jurisdiction_ids",
        "portable_jurisdiction_ids",
        "legislative_body_ids",
        "legislative_session_ids",
        "source_family_ids",
        "bill_semantic_required_keys",
        "bill_semantic_covered_keys",
        "bill_semantic_missing_keys",
        "bill_metadata_required_keys",
        "bill_metadata_loaded_keys",
        "bill_metadata_missing_keys",
        "training_bill_semantic_required_keys",
        "training_bill_semantic_covered_keys",
        "training_bill_semantic_missing_keys",
        "evaluation_bill_semantic_required_keys",
        "evaluation_bill_semantic_covered_keys",
        "evaluation_bill_semantic_missing_keys",
    ):
        value = source_state.get(list_key)
        if list_key not in source_state:
            continue
        if not isinstance(value, list):
            issues.append(f"{label} {list_key} must be a list")
            invalid_keys.add(list_key)
        elif not all(isinstance(item, str) for item in value):
            issues.append(f"{label} {list_key} must be a string list")
            invalid_keys.add(list_key)
        elif _string_list_has_empty(value):
            issues.append(f"{label} {list_key} must be non-empty")
            invalid_keys.add(list_key)
        elif _string_list_has_untrimmed(value):
            issues.append(f"{label} {list_key} must be trimmed")
            invalid_keys.add(list_key)
        elif _string_list_has_duplicates(value):
            issues.append(f"{label} {list_key} must be unique")
            invalid_keys.add(list_key)
        elif value != sorted(value):
            issues.append(f"{label} {list_key} must be sorted")
            invalid_keys.add(list_key)
        elif list_key == "source_family_ids" and not _source_family_id_list(value):
            issues.append(f"{label} {list_key} must be normalized ids")
            invalid_keys.add(list_key)
    invalid_keys.update(
        _prediction_eval_source_state_scope_invariant_keys(
            source_state=source_state,
            label=label,
            issues=issues,
        )
    )
    for key, value in source_state.items():
        key_str = str(key)
        if _prediction_eval_source_state_count_key(key_str) and not _is_non_negative_plain_int(
            value
        ):
            issues.append(f"{label} count invalid: {key_str}")
            invalid_keys.add(key_str)
        if key_str.endswith("_coverage_rate") and not _prediction_eval_source_state_rate_valid(
            value
        ):
            issues.append(f"{label} coverage rate invalid: {key_str}")
            invalid_keys.add(key_str)
    return invalid_keys


def _prediction_eval_source_state_scoped_id_keys(
    *,
    source_state: dict[str, Any],
    label: str,
    issues: list[str],
) -> set[str]:
    invalid_keys: set[str] = set()
    jurisdiction_ids_value = source_state.get("jurisdiction_ids")
    legislative_body_ids_value = source_state.get("legislative_body_ids")
    legislative_session_ids_value = source_state.get("legislative_session_ids")
    jurisdiction_ids = (
        set(jurisdiction_ids_value) if isinstance(jurisdiction_ids_value, list) else set()
    )
    legislative_body_ids = (
        set(legislative_body_ids_value) if isinstance(legislative_body_ids_value, list) else set()
    )
    if isinstance(legislative_body_ids_value, list) and all(
        isinstance(item, str) for item in legislative_body_ids_value
    ):
        invalid_body_id = False
        for item in legislative_body_ids_value:
            parts = item.split(":")
            if len(parts) != 2 or parts[0] not in jurisdiction_ids or not parts[1]:
                invalid_body_id = True
                break
        if invalid_body_id:
            issues.append(f"{label} invalid scoped ids: legislative_body_ids")
            invalid_keys.add("legislative_body_ids")
    if isinstance(legislative_session_ids_value, list) and all(
        isinstance(item, str) for item in legislative_session_ids_value
    ):
        invalid_session_id = False
        for item in legislative_session_ids_value:
            parts = item.split(":")
            if (
                len(parts) != 3
                or parts[0] not in jurisdiction_ids
                or f"{parts[0]}:{parts[1]}" not in legislative_body_ids
                or not parts[2]
            ):
                invalid_session_id = True
                break
        if invalid_session_id:
            issues.append(f"{label} invalid scoped ids: legislative_session_ids")
            invalid_keys.add("legislative_session_ids")
    return invalid_keys


def _source_family_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None for item in value
    )


def _prediction_eval_source_state_scope_invariant_keys(
    *,
    source_state: dict[str, Any],
    label: str,
    issues: list[str],
) -> set[str]:
    invalid_keys: set[str] = set()
    for count_key, ids_key in (
        ("jurisdiction_count", "jurisdiction_ids"),
        ("implemented_jurisdiction_count", "implemented_jurisdiction_ids"),
        ("portable_jurisdiction_count", "portable_jurisdiction_ids"),
        ("legislative_body_count", "legislative_body_ids"),
        ("legislative_session_count", "legislative_session_ids"),
        ("source_family_count", "source_family_ids"),
    ):
        count_value = source_state.get(count_key)
        ids_value = source_state.get(ids_key)
        if not _is_non_negative_plain_int(count_value) or not isinstance(ids_value, list):
            continue
        if count_value != len(ids_value):
            issues.append(f"{label} {count_key} must match {ids_key}")
    jurisdiction_ids = source_state.get("jurisdiction_ids")
    implemented_ids = source_state.get("implemented_jurisdiction_ids")
    portable_ids = source_state.get("portable_jurisdiction_ids")
    if (
        isinstance(jurisdiction_ids, list)
        and isinstance(implemented_ids, list)
        and isinstance(portable_ids, list)
        and all(isinstance(item, str) for item in jurisdiction_ids)
        and all(isinstance(item, str) for item in implemented_ids)
        and all(isinstance(item, str) for item in portable_ids)
    ):
        implemented_set = set(implemented_ids)
        portable_set = set(portable_ids)
        if implemented_set | portable_set != set(jurisdiction_ids):
            issues.append(f"{label} jurisdiction status ids must cover jurisdiction_ids")
        if implemented_set & portable_set:
            issues.append(f"{label} jurisdiction status ids must not overlap")
    invalid_keys.update(
        _prediction_eval_source_state_scoped_id_keys(
            source_state=source_state,
            label=label,
            issues=issues,
        )
    )
    return invalid_keys


def _prediction_eval_source_state_count_key(key: str) -> bool:
    return key.endswith("_count") or key in {
        "training_example_count",
        "dataset_training_examples",
        "dataset_evaluation_examples",
    }


def _prediction_eval_source_state_rate_valid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return 0 <= value <= 1


def _prediction_eval_cutoff_audit_partition_issues(
    cutoff_audit: dict[Any, Any],
) -> list[str]:
    issues: list[str] = []
    for total_key, cutoff_key, unknown_key, future_key in (
        (
            "ontology_edge_count",
            "cutoff_ontology_edge_count",
            "unknown_availability_ontology_edge_count",
            "excluded_future_ontology_edge_count",
        ),
        (
            "bill_signal_row_count",
            "cutoff_bill_signal_row_count",
            "unknown_availability_bill_signal_row_count",
            "excluded_future_bill_signal_row_count",
        ),
        (
            "training_contribution_signal_row_count",
            "cutoff_training_contribution_signal_row_count",
            "unknown_availability_training_contribution_signal_row_count",
            "excluded_future_training_contribution_signal_row_count",
        ),
        (
            "evaluation_contribution_signal_row_count",
            "cutoff_evaluation_contribution_signal_row_count",
            "unknown_availability_evaluation_contribution_signal_row_count",
            "excluded_future_evaluation_contribution_signal_row_count",
        ),
        (
            "training_statement_signal_row_count",
            "cutoff_training_statement_signal_row_count",
            "unknown_availability_training_statement_signal_row_count",
            "excluded_future_training_statement_signal_row_count",
        ),
        (
            "evaluation_statement_signal_row_count",
            "cutoff_evaluation_statement_signal_row_count",
            "unknown_availability_evaluation_statement_signal_row_count",
            "excluded_future_evaluation_statement_signal_row_count",
        ),
    ):
        if not _prediction_cutoff_partition_values_valid(
            cutoff_audit,
            total_key=total_key,
            cutoff_key=cutoff_key,
            unknown_key=unknown_key,
            future_key=future_key,
        ):
            issues.append(str(total_key))
    return issues


def _prediction_cutoff_partition_values_valid(
    values_by_key: dict[Any, Any],
    *,
    total_key: str,
    cutoff_key: str,
    unknown_key: str,
    future_key: str,
) -> bool:
    values = [
        values_by_key.get(total_key),
        values_by_key.get(cutoff_key),
        values_by_key.get(unknown_key),
        values_by_key.get(future_key),
    ]
    if all(value is None for value in values):
        return True
    if not all(_is_non_negative_plain_int(value) for value in values):
        return False
    total, cutoff, unknown, future = (int(value) for value in values)
    return cutoff + unknown + future == total


def _mapping_mismatch_key_issues(
    *,
    label: str,
    actual: object,
    expected: dict[str, Any],
    ignored_keys: set[str] | None = None,
) -> list[str]:
    if not isinstance(actual, dict):
        return [f"{label}: not an object"]
    issues: list[str] = []
    ignored_keys = ignored_keys or set()
    for key in sorted(set(str(item) for item in actual) | set(expected)):
        if key in ignored_keys:
            continue
        actual_value = actual.get(key)
        expected_value = expected.get(key)
        if _json_scalar_type_mismatch(actual_value, expected_value):
            issues.append(f"{label}: {key}")
        elif actual_value != expected_value:
            issues.append(f"{label}: {key}")
    return issues


def _prediction_eval_run_metadata_matches_expected(
    *,
    run_metadata: dict[str, Any],
    expected: dict[str, Any],
    ignored_run_metadata_keys: set[str],
    ignored_source_state_keys: set[str],
    ignored_threshold_keys: set[str],
    ignore_source_state_value: bool = False,
    ignore_threshold_value: bool = False,
) -> bool:
    if (
        not ignored_run_metadata_keys
        and not ignored_source_state_keys
        and not ignored_threshold_keys
        and not ignore_source_state_value
        and not ignore_threshold_value
    ):
        return run_metadata == expected
    if set(run_metadata) - ignored_run_metadata_keys != set(expected) - ignored_run_metadata_keys:
        return False
    for key, expected_value in expected.items():
        if key in ignored_run_metadata_keys:
            continue
        actual_value = run_metadata.get(key)
        if key == "source_state" and ignore_source_state_value:
            continue
        if key == "thresholds" and ignore_threshold_value:
            continue
        elif (
            key == "source_state"
            and isinstance(actual_value, dict)
            and isinstance(expected_value, dict)
        ):
            comparable_actual = {
                source_key: source_value
                for source_key, source_value in actual_value.items()
                if source_key not in ignored_source_state_keys
            }
            comparable_expected = {
                source_key: source_value
                for source_key, source_value in expected_value.items()
                if source_key not in ignored_source_state_keys
            }
            if comparable_actual != comparable_expected:
                return False
        elif (
            key == "thresholds"
            and ignored_threshold_keys
            and isinstance(actual_value, dict)
            and isinstance(expected_value, dict)
        ):
            comparable_actual = {
                threshold_key: threshold_value
                for threshold_key, threshold_value in actual_value.items()
                if str(threshold_key) not in ignored_threshold_keys
            }
            comparable_expected = {
                threshold_key: threshold_value
                for threshold_key, threshold_value in expected_value.items()
                if str(threshold_key) not in ignored_threshold_keys
            }
            if comparable_actual != comparable_expected:
                return False
        elif actual_value != expected_value:
            return False
    return True


def _json_scalar_type_mismatch(actual: object, expected: object) -> bool:
    if type(expected) is int:
        return type(actual) is not int
    if type(expected) is float:
        return type(actual) not in (int, float) or isinstance(actual, bool)
    if isinstance(expected, str | bool) or expected is None:
        return type(actual) is not type(expected)
    return False


def _is_plain_int(value: object) -> bool:
    return type(value) is int


def _plain_int_or_zero(value: object) -> int:
    return cast(int, value) if _is_plain_int(value) else 0


def _validate_prediction_eval_manifest_learned_model(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    learned_model = manifest.get("learned_model")
    if not isinstance(learned_model, dict):
        return
    expected = {
        "signal_count": len(report.learned_model.signal_names),
        "intercept": report.learned_model.intercept,
        "top_signal_coefficients": _top_learned_signal_coefficients(report),
    }
    for key, expected_value in expected.items():
        if key not in learned_model:
            continue
        actual_value = learned_model.get(key)
        if _json_scalar_type_mismatch(actual_value, expected_value):
            issues.append(f"learned_model mismatch: {key}")
        elif actual_value != expected_value:
            issues.append(f"learned_model mismatch: {key}")


def _validate_prediction_eval_manifest_failure_analysis(
    *,
    manifest: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    failure_analysis = manifest.get("failure_analysis")
    if not isinstance(failure_analysis, dict):
        return
    expected = {
        "failure_case_count": len(report.top_failure_cases),
        "failure_group_count": len(report.failure_groups),
        "backfill_recommendation_count": len(report.backfill_recommendations),
        "top_failure_groups": [
            group.model_dump(mode="json") for group in report.failure_groups[:10]
        ],
        "top_backfill_recommendations": [
            recommendation.model_dump(mode="json")
            for recommendation in report.backfill_recommendations[:10]
        ],
    }
    for key, expected_value in expected.items():
        if key not in failure_analysis:
            continue
        actual_value = failure_analysis.get(key)
        if key in {
            "failure_case_count",
            "failure_group_count",
            "backfill_recommendation_count",
        } and not _is_non_negative_plain_int(actual_value):
            continue
        if _json_scalar_type_mismatch(actual_value, expected_value):
            issues.append(f"failure_analysis mismatch: {key}")
        elif actual_value != expected_value:
            issues.append(f"failure_analysis mismatch: {key}")


def _handle_prediction_source_url_audit(args: Any) -> dict[str, Any]:
    return run_prediction_source_url_audit_command(args)


def _handle_verify_prediction_source_url_audit(args: Any) -> dict[str, Any]:
    return verify_prediction_source_url_audit_command(args)


def _handle_verify_prediction_backtest(args: Any) -> dict[str, Any]:
    return verify_prediction_backtest_command(args)


def _handle_verify_prediction_benchmark(args: Any) -> dict[str, Any]:
    bill_semantics_root = getattr(args, "bill_semantics_root", None)
    preflight_issues: list[str] = []
    required_bill_semantics_model_names = _required_model_names(
        getattr(args, "require_bill_semantics_model_name", None),
        issues=preflight_issues,
        label="require_bill_semantics_model_name",
    )
    require_bill_semantics_source_inputs_sha256 = bool(
        getattr(args, "require_bill_semantics_source_inputs_sha256", False)
    )
    require_bill_semantics_cache = (
        bool(getattr(args, "require_bill_semantics_cache", False))
        or bool(required_bill_semantics_model_names)
        or require_bill_semantics_source_inputs_sha256
    )
    source_url_audit = getattr(args, "source_url_audit", None)
    eval_window_run_verify = getattr(args, "eval_window_run_verify", None)
    require_source_url_audit = (
        bool(getattr(args, "require_source_url_audit", False))
        or bool(getattr(args, "require_source_url_audit_no_gaps", False))
        or bool(getattr(args, "require_source_url_audit_no_official_source_gaps", False))
        or bool(getattr(args, "require_source_url_audit_no_portable_context_gaps", False))
    )
    require_eval_window_run_verify = bool(getattr(args, "require_eval_window_run_verify", False))
    components = {
        "inventory": _handle_verify_prediction_input_inventory(
            SimpleNamespace(
                artifact=args.inventory,
                require_run_metadata=True,
                require_congress_archive_manifest=bool(
                    getattr(args, "require_inventory_congress_archive_manifest", False)
                ),
                require_clean_inventory=bool(getattr(args, "require_clean_inventory", False)),
                require_portable_jurisdiction_ids=bool(
                    getattr(args, "require_portable_jurisdiction_ids", False)
                ),
                require_portable_body_ids=bool(getattr(args, "require_portable_body_ids", False)),
                require_portable_session_ids=bool(
                    getattr(args, "require_portable_session_ids", False)
                ),
                require_source_families=list(getattr(args, "require_source_families", None) or []),
                min_training_labels=getattr(args, "min_training_labels", None),
                min_evaluation_labels=getattr(args, "min_evaluation_labels", None),
                min_training_feature_vote_history_source_coverage_rate=getattr(
                    args,
                    "min_training_feature_vote_history_source_coverage_rate",
                    None,
                ),
                min_evaluation_feature_vote_history_source_coverage_rate=getattr(
                    args,
                    "min_evaluation_feature_vote_history_source_coverage_rate",
                    None,
                ),
                min_training_label_source_url_coverage_rate=getattr(
                    args,
                    "min_training_label_source_url_coverage_rate",
                    None,
                ),
                min_evaluation_label_source_url_coverage_rate=getattr(
                    args,
                    "min_evaluation_label_source_url_coverage_rate",
                    None,
                ),
                min_training_label_official_source_url_coverage_rate=getattr(
                    args,
                    "min_training_label_official_source_url_coverage_rate",
                    None,
                ),
                min_evaluation_label_official_source_url_coverage_rate=getattr(
                    args,
                    "min_evaluation_label_official_source_url_coverage_rate",
                    None,
                ),
                min_bill_source_url_coverage_rate=getattr(
                    args,
                    "min_bill_source_url_coverage_rate",
                    None,
                ),
                min_bill_official_source_url_coverage_rate=getattr(
                    args,
                    "min_bill_official_source_url_coverage_rate",
                    None,
                ),
                min_bill_sponsor_availability_rate=getattr(
                    args,
                    "min_bill_sponsor_availability_rate",
                    None,
                ),
                require_no_bill_sponsor_introduced_date_fallbacks=bool(
                    getattr(args, "require_no_bill_sponsor_introduced_date_fallbacks", False)
                ),
                min_ontology_source_anchor_coverage_rate=getattr(
                    args,
                    "min_ontology_source_anchor_coverage_rate",
                    None,
                ),
                min_ontology_official_source_anchor_coverage_rate=getattr(
                    args,
                    "min_ontology_official_source_anchor_coverage_rate",
                    None,
                ),
                min_fec_member_attribution_rate=getattr(
                    args,
                    "min_fec_member_attribution_rate",
                    None,
                ),
                min_fec_contributions=getattr(args, "min_fec_contributions", None),
                min_member_attributed_fec_contributions=getattr(
                    args,
                    "min_member_attributed_fec_contributions",
                    None,
                ),
                min_members_with_fec_candidate_id=getattr(
                    args,
                    "min_members_with_fec_candidate_id",
                    None,
                ),
                min_public_statement_signals=getattr(
                    args,
                    "min_public_statement_signals",
                    None,
                ),
                min_members_with_public_statement_signals=getattr(
                    args,
                    "min_members_with_public_statement_signals",
                    None,
                ),
            )
        ),
        "backtest": _handle_verify_prediction_backtest(
            SimpleNamespace(
                artifact=args.backtest,
                require_run_metadata=True,
                require_evaluated_predictions=bool(
                    getattr(args, "require_evaluated_backtest", False)
                ),
                require_prediction_source_urls=bool(
                    getattr(args, "require_backtest_source_urls", False)
                ),
                require_official_prediction_source_urls=bool(
                    getattr(args, "require_backtest_official_source_urls", False)
                ),
                require_congress_archive_manifest=bool(
                    getattr(args, "require_backtest_congress_archive_manifest", False)
                ),
                require_model_name=getattr(args, "require_backtest_model_name", None),
                require_ontology_feature_signals=bool(
                    getattr(args, "require_backtest_ontology_feature_signals", False)
                ),
                require_source_families=list(
                    getattr(args, "require_backtest_source_families", None) or []
                ),
                require_bill_semantics_cache=require_bill_semantics_cache,
                require_bill_semantics_model_name=required_bill_semantics_model_names,
                require_bill_semantics_source_inputs_sha256=(
                    require_bill_semantics_source_inputs_sha256
                ),
            )
        ),
        "eval_manifest": _handle_verify_prediction_eval_manifest(
            SimpleNamespace(
                manifest=args.eval_manifest,
                require_artifact_run_metadata=True,
                require_congress_archive_manifest=bool(
                    getattr(args, "require_eval_congress_archive_manifest", False)
                ),
                require_ready_quality=bool(getattr(args, "require_ready_quality", False)),
                require_failure_analysis=bool(
                    getattr(args, "require_eval_failure_analysis", False)
                ),
                require_backfill_recommendations=bool(
                    getattr(args, "require_eval_backfill_recommendations", False)
                ),
                require_fail_on_unknown_bill_semantic_availability=bool(
                    getattr(
                        args,
                        "require_eval_fail_on_unknown_bill_semantic_availability",
                        False,
                    )
                ),
                require_fail_on_unknown_bill_signal_availability=bool(
                    getattr(
                        args,
                        "require_eval_fail_on_unknown_bill_signal_availability",
                        False,
                    )
                ),
                require_fail_on_unknown_ontology_edge_availability=bool(
                    getattr(
                        args,
                        "require_eval_fail_on_unknown_ontology_edge_availability",
                        False,
                    )
                ),
                require_fail_on_unknown_contribution_signal_availability=bool(
                    getattr(
                        args,
                        "require_eval_fail_on_unknown_contribution_signal_availability",
                        False,
                    )
                ),
                require_fail_on_unknown_statement_signal_availability=bool(
                    getattr(
                        args,
                        "require_eval_fail_on_unknown_statement_signal_availability",
                        False,
                    )
                ),
                require_model_name=getattr(args, "require_eval_model_name", None),
                require_ontology_feature_signals=bool(
                    getattr(args, "require_eval_ontology_feature_signals", False)
                ),
                require_source_families=list(
                    getattr(args, "require_eval_source_families", None) or []
                ),
                min_bill_semantic_coverage_rate=getattr(
                    args,
                    "min_bill_semantic_coverage_rate",
                    None,
                ),
                min_bill_metadata_coverage_rate=getattr(
                    args,
                    "min_bill_metadata_coverage_rate",
                    None,
                ),
                min_training_feature_source_coverage_rate=getattr(
                    args,
                    "min_training_feature_source_coverage_rate",
                    None,
                ),
                min_evaluation_feature_source_coverage_rate=getattr(
                    args,
                    "min_evaluation_feature_source_coverage_rate",
                    None,
                ),
                min_training_feature_source_url_coverage_rate=getattr(
                    args,
                    "min_training_feature_source_url_coverage_rate",
                    None,
                ),
                min_training_feature_official_source_coverage_rate=getattr(
                    args,
                    "min_training_feature_official_source_coverage_rate",
                    None,
                ),
                min_evaluation_feature_source_url_coverage_rate=getattr(
                    args,
                    "min_evaluation_feature_source_url_coverage_rate",
                    None,
                ),
                min_evaluation_feature_official_source_coverage_rate=getattr(
                    args,
                    "min_evaluation_feature_official_source_coverage_rate",
                    None,
                ),
                min_evaluation_source_url_coverage_rate=getattr(
                    args,
                    "min_evaluation_source_url_coverage_rate",
                    None,
                ),
                require_bill_semantics_cache=require_bill_semantics_cache,
                require_bill_semantics_model_name=required_bill_semantics_model_names,
                require_bill_semantics_source_inputs_sha256=(
                    require_bill_semantics_source_inputs_sha256
                ),
            )
        ),
        "bill_semantics_plan": _handle_verify_bill_semantics_plan(
            SimpleNamespace(
                plan=args.bill_semantics_plan,
                fail_on_unmatched_targets=bool(getattr(args, "fail_on_unmatched_targets", False)),
                require_source_report=True,
                require_matched_source_anchors=bool(
                    getattr(args, "require_bill_semantics_plan_source_anchors", False)
                ),
            )
        ),
    }
    if source_url_audit is not None:
        components["source_url_audit"] = _handle_verify_prediction_source_url_audit(
            SimpleNamespace(
                artifact=source_url_audit,
                require_run_metadata=True,
                require_no_gaps=bool(getattr(args, "require_source_url_audit_no_gaps", False)),
                require_no_official_source_gaps=bool(
                    getattr(
                        args,
                        "require_source_url_audit_no_official_source_gaps",
                        False,
                    )
                ),
                require_no_portable_context_gaps=bool(
                    getattr(
                        args,
                        "require_source_url_audit_no_portable_context_gaps",
                        False,
                    )
                ),
            )
        )
    elif require_source_url_audit:
        components["source_url_audit"] = _missing_prediction_source_url_audit_result()
    if eval_window_run_verify is not None:
        components["eval_window_run"] = _prediction_benchmark_eval_window_run_verify_result(
            Path(eval_window_run_verify)
        )
    elif require_eval_window_run_verify:
        components["eval_window_run"] = _missing_prediction_eval_window_run_verify_result()
    if bill_semantics_root is not None:
        components["bill_semantics_cache"] = _handle_verify_bill_semantics(
            SimpleNamespace(
                root=bill_semantics_root,
                output=None,
                require_model_name=required_bill_semantics_model_names,
                require_source_inputs_sha256=bool(require_bill_semantics_source_inputs_sha256),
            )
        )
    elif require_bill_semantics_cache:
        components["bill_semantics_cache"] = _missing_bill_semantics_cache_result(
            required_bill_semantics_model_names,
            require_source_inputs_sha256=require_bill_semantics_source_inputs_sha256,
        )
    issues: list[str] = []
    issues.extend(preflight_issues)
    quality_gate_failures: list[str] = []
    checked = 0
    for name, result in components.items():
        component_checked = result.get("checked")
        if component_checked is None:
            component_checked = 0
        if not _is_plain_int(component_checked):
            issues.append(f"{name}.checked must be an integer")
        elif not _is_non_negative_plain_int(component_checked):
            issues.append(f"{name}.checked must be a non-negative integer")
        else:
            checked += component_checked
        issues.extend(f"{name}: {issue}" for issue in result.get("issues", []))
        quality_gate_failures.extend(
            f"{name}: {failure}" for failure in result.get("quality_gate_failures", [])
        )
    issues.extend(
        _prediction_benchmark_bundle_consistency_issues(
            inventory_path=Path(args.inventory),
            backtest_path=Path(args.backtest),
            eval_manifest_path=Path(args.eval_manifest),
            bill_semantics_plan_path=Path(args.bill_semantics_plan),
            source_url_audit_path=Path(source_url_audit) if source_url_audit is not None else None,
        )
    )
    eval_backfill_recommendations = _prediction_benchmark_backfill_recommendations(
        Path(args.eval_manifest)
    )
    backfill_recommendations = _coalesce_prediction_benchmark_backfill_recommendations(
        [
            *_prediction_benchmark_inventory_backfill_recommendations(
                components.get("inventory"),
                Path(args.inventory),
            ),
            *_prediction_benchmark_backtest_backfill_recommendations(
                components.get("backtest"),
                Path(args.backtest),
            ),
            *_prediction_benchmark_eval_manifest_backfill_recommendations(
                components.get("eval_manifest"),
                Path(args.eval_manifest),
            ),
            *_prediction_benchmark_source_url_audit_backfill_recommendations(
                components.get("source_url_audit"),
                Path(source_url_audit) if source_url_audit is not None else None,
            ),
            *eval_backfill_recommendations,
        ]
    )
    backfill_plan = _prediction_benchmark_backfill_plan(
        inventory_path=Path(args.inventory),
        eval_manifest_path=Path(args.eval_manifest),
        bill_semantics_plan_path=Path(args.bill_semantics_plan),
        recommendations=backfill_recommendations,
        semantic_model_name=(
            required_bill_semantics_model_names[0]
            if required_bill_semantics_model_names
            else "gpt-5.5"
        ),
    )
    backfill_plan_missing_runtime_requirements: list[str] = []
    backfill_plan_missing_runtime_requirements_by_step: list[dict[str, Any]] = []
    backfill_plan_runtime_readiness_by_step: list[dict[str, Any]] = []
    if bool(getattr(args, "check_backfill_runtime_requirements", False)):
        (
            backfill_plan_missing_runtime_requirements,
            backfill_plan_missing_runtime_requirements_by_step,
            backfill_plan_runtime_readiness_by_step,
        ) = _prediction_benchmark_backfill_plan_missing_runtime_requirements(backfill_plan)
        if backfill_plan_missing_runtime_requirements:
            quality_gate_failures.append("backfill_plan: runtime_requirements_missing")
    artifacts = {
        "inventory": _artifact_reference(Path(args.inventory)),
        "backtest": _artifact_reference(Path(args.backtest)),
        "eval_manifest": _artifact_reference(Path(args.eval_manifest)),
        "bill_semantics_plan": _artifact_reference(Path(args.bill_semantics_plan)),
    }
    if source_url_audit is not None:
        artifacts["source_url_audit"] = _artifact_reference(Path(source_url_audit))
    if eval_window_run_verify is not None:
        artifacts["eval_window_run_verify"] = _artifact_reference(Path(eval_window_run_verify))
    if bill_semantics_root is not None:
        artifacts["bill_semantics_cache_index"] = _artifact_reference(
            Path(bill_semantics_root) / "index.json"
        )
    result = {
        "ok": (
            all(bool(result.get("ok")) for result in components.values())
            and not issues
            and not quality_gate_failures
        ),
        "command": "verify-prediction-benchmark",
        "artifacts": artifacts,
        "component_count": len(components),
        "checked": checked,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "backfill_recommendation_count": len(backfill_recommendations),
        "backfill_recommendations": backfill_recommendations,
        "backfill_plan": backfill_plan,
        "backfill_plan_missing_runtime_requirements": (backfill_plan_missing_runtime_requirements),
        "backfill_plan_missing_runtime_requirements_by_step": (
            backfill_plan_missing_runtime_requirements_by_step
        ),
        "backfill_plan_runtime_readiness_by_step": (backfill_plan_runtime_readiness_by_step),
        "components": components,
    }
    result["run_metadata"] = _prediction_benchmark_run_metadata(args, result)
    backfill_plan_output = (
        Path(args.backfill_plan_output)
        if getattr(args, "backfill_plan_output", None) is not None
        else None
    )
    if backfill_plan_output is not None:
        backfill_plan_artifact = _prediction_benchmark_backfill_plan_artifact(
            args=args,
            result=result,
        )
        backfill_plan_output_sha256 = _write_json_artifact(
            backfill_plan_output,
            backfill_plan_artifact,
        )
        result["backfill_plan_output"] = str(backfill_plan_output)
        result["backfill_plan_output_sha256"] = backfill_plan_output_sha256
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


_DEFAULT_RUNTIME_ENV_REQUIREMENTS = (
    "OPENPACT_POSTGRES_DSN",
    "OPENPACT_CONGRESS_API_KEY",
    "OPENAI_API_KEY",
)


def _handle_runtime_env_preflight(args: Any) -> dict[str, Any]:
    required_env = _required_string_list(getattr(args, "require_env", None))
    if not required_env:
        required_env = list(_DEFAULT_RUNTIME_ENV_REQUIREMENTS)
    dotenv_path = Path(args.dotenv) if getattr(args, "dotenv", None) is not None else None
    dotenv_keys, dotenv_issues = _read_dotenv_key_presence(dotenv_path)
    checks = [
        _runtime_env_check(name, dotenv_keys=dotenv_keys)
        for name in sorted(dict.fromkeys(required_env))
    ]
    missing_env = [check["name"] for check in checks if not bool(check["visible_to_process"])]
    dotenv_only = [
        check["name"]
        for check in checks
        if bool(check["present_in_dotenv"]) and not bool(check["visible_to_process"])
    ]
    next_actions_by_env = _runtime_env_preflight_next_actions_by_env(
        checks,
        dotenv_path=dotenv_path,
    )
    next_actions = sorted(
        {action for actions in next_actions_by_env.values() for action in actions}
    )
    result = {
        "ok": not missing_env and not dotenv_issues,
        "command": "runtime-env-preflight",
        "checked": len(checks),
        "checks": checks,
        "missing_env": missing_env,
        "missing_env_count": len(missing_env),
        "next_actions": next_actions,
        "next_actions_by_env": next_actions_by_env,
        "dotenv": {
            "path": str(dotenv_path) if dotenv_path is not None else None,
            "exists": dotenv_path.is_file() if dotenv_path is not None else None,
            "dotenv_only": dotenv_only,
            "dotenv_only_count": len(dotenv_only),
            "create_from_template_action": None,
        },
        "issues": dotenv_issues,
        "issue_count": len(dotenv_issues),
        "run_metadata": {
            "command": "runtime-env-preflight",
            "source_state": {
                "checked_env": [check["name"] for check in checks],
                "missing_env": missing_env,
                "dotenv_only": dotenv_only,
                "next_actions": next_actions,
            },
        },
    }
    template_output = (
        Path(args.template_output) if getattr(args, "template_output", None) is not None else None
    )
    if template_output is not None:
        template_sha256 = _write_runtime_env_template(template_output, required_env)
        result["template_output"] = str(template_output)
        result["template_output_sha256"] = template_sha256
        create_dotenv_action = _runtime_env_create_dotenv_from_template_action(
            dotenv_path=dotenv_path,
            template_output=template_output,
            missing_env=missing_env,
        )
        if create_dotenv_action is not None:
            result["dotenv"]["create_from_template_action"] = create_dotenv_action
            result["next_actions"] = sorted(
                {*_string_list(result.get("next_actions")), create_dotenv_action}
            )
            result["run_metadata"]["source_state"]["next_actions"] = result["next_actions"]
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


def _write_runtime_env_template(path: Path, required_env: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# OpenPact runtime environment template.",
        "# Fill values locally; do not commit secrets.",
        *[f"{name}=" for name in sorted(dict.fromkeys(required_env))],
        "",
    ]
    encoded = "\n".join(lines).encode("utf-8")
    return _write_bytes_artifact(path, encoded)


def _runtime_env_check(
    name: str,
    *,
    dotenv_keys: set[str],
) -> dict[str, Any]:
    value = os.environ.get(name)
    visible = value is not None and value != ""
    present_in_dotenv = name in dotenv_keys
    if visible:
        source = "process"
    elif present_in_dotenv:
        source = "dotenv_only"
    else:
        source = "absent"
    return {
        "name": name,
        "visible_to_process": visible,
        "present_in_dotenv": present_in_dotenv,
        "source": source,
    }


def _runtime_env_preflight_next_actions_by_env(
    checks: list[dict[str, Any]],
    *,
    dotenv_path: Path | None,
) -> dict[str, list[str]]:
    actions_by_env: dict[str, list[str]] = {}
    dotenv_action = f"source_dotenv:{dotenv_path}" if dotenv_path is not None else None
    for check in checks:
        name = str(check.get("name"))
        if bool(check.get("visible_to_process")):
            continue
        if bool(check.get("present_in_dotenv")) and dotenv_action is not None:
            actions_by_env[name] = [dotenv_action]
        else:
            actions_by_env[name] = [f"set_env:{name}"]
    return actions_by_env


def _runtime_env_create_dotenv_from_template_action(
    *,
    dotenv_path: Path | None,
    template_output: Path,
    missing_env: list[str],
) -> str | None:
    if dotenv_path is None or dotenv_path.is_file() or not missing_env:
        return None
    return f"create_dotenv_from_template:{dotenv_path}:{template_output}"


def _handle_verify_runtime_env_preflight(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-runtime-env-preflight",
                "artifact": str(artifact_path),
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _runtime_env_preflight_verify_run_metadata(
                    args,
                    artifact_path=artifact_path,
                ),
            },
        )

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(payload, dict):
        return failure_result(issues=["artifact must be an object"])

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if payload.get("command") != "runtime-env-preflight":
        issues.append("artifact_command_mismatch")
    issues.extend(_runtime_env_preflight_count_issues(payload))

    checks = payload.get("checks")
    checks_list = checks if isinstance(checks, list) else []
    if not isinstance(checks, list):
        issues.append("checks_missing")
    if _is_non_negative_plain_int(payload.get("checked")) and payload.get("checked") != len(
        checks_list
    ):
        issues.append("checked_count_mismatch")

    missing_env = _string_list(payload.get("missing_env"))
    if _is_non_negative_plain_int(payload.get("missing_env_count")) and payload.get(
        "missing_env_count"
    ) != len(missing_env):
        issues.append("missing_env_count_mismatch")

    dotenv = payload.get("dotenv")
    dotenv_only: list[str] = []
    if isinstance(dotenv, dict):
        dotenv_only = _string_list(dotenv.get("dotenv_only"))
        if _is_non_negative_plain_int(dotenv.get("dotenv_only_count")) and dotenv.get(
            "dotenv_only_count"
        ) != len(dotenv_only):
            issues.append("dotenv_only_count_mismatch")
    else:
        issues.append("dotenv_missing")

    artifact_issues = _string_list(payload.get("issues"))
    if _is_non_negative_plain_int(payload.get("issue_count")) and payload.get("issue_count") != len(
        artifact_issues
    ):
        issues.append("issue_count_mismatch")

    next_actions = _string_list(payload.get("next_actions"))
    next_actions_by_env = _runtime_env_next_actions_by_env_summary(
        payload.get("next_actions_by_env")
    )
    if bool(getattr(args, "require_next_actions", False)) and missing_env:
        for name in missing_env:
            if name not in next_actions_by_env and not any(
                action.endswith(f":{name}") for action in next_actions
            ):
                quality_gate_failures.append(f"missing_next_action:{name}")
        if not next_actions:
            quality_gate_failures.append("missing_next_actions")

    template_sha256 = None
    template_output_sha256_invalid = False
    template_output = payload.get("template_output")
    template_output_sha256 = payload.get("template_output_sha256")
    if bool(getattr(args, "require_template_output", False)):
        if not isinstance(template_output, str) or not template_output:
            quality_gate_failures.append("template_output_missing")
        elif not Path(template_output).is_file():
            quality_gate_failures.append("template_output_file_missing")
        else:
            template_sha256 = hashlib.sha256(Path(template_output).read_bytes()).hexdigest()
            if not isinstance(template_output_sha256, str) or not _is_sha256_hex(
                template_output_sha256
            ):
                template_output_sha256_invalid = True
                quality_gate_failures.append("template_output_sha256_invalid")
            elif template_sha256 != template_output_sha256:
                quality_gate_failures.append("template_output_sha256_mismatch")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _runtime_env_preflight_secret_literal_failures(payload)
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "checked": len(checks_list),
        "missing_env_count": len(missing_env),
        "next_action_count": len(next_actions),
        "template_output_present": isinstance(template_output, str) and bool(template_output),
        "template_output_sha256_invalid": template_output_sha256_invalid,
        "secret_literal_count": len(secret_failures),
    }
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-runtime-env-preflight",
        "artifact": str(artifact_path),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "checked": 1,
        "missing_env": missing_env,
        "missing_env_count": len(missing_env),
        "template_output": template_output,
        "template_output_sha256": template_sha256,
        "template_output_sha256_invalid": template_output_sha256_invalid,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "run_metadata": _runtime_env_preflight_verify_run_metadata(
            args,
            artifact_path=artifact_path,
            source_state=source_state,
        ),
    }
    return _attach_optional_verification_output(args, result)


def _runtime_env_preflight_count_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in ("checked", "missing_env_count", "issue_count"):
        if not _is_plain_int(payload.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(payload.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    dotenv = payload.get("dotenv")
    if isinstance(dotenv, dict) and not _is_plain_int(dotenv.get("dotenv_only_count")):
        issues.append("dotenv.dotenv_only_count must be an integer")
    elif isinstance(dotenv, dict) and not _is_non_negative_plain_int(
        dotenv.get("dotenv_only_count")
    ):
        issues.append("dotenv.dotenv_only_count must be a non-negative integer")
    return issues


def _runtime_env_preflight_secret_literal_failures(
    payload: dict[str, Any],
) -> list[str]:
    serialized = json.dumps(payload, sort_keys=True)
    failures: list[str] = []
    if "sk-" in serialized:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", serialized):
        failures.append("secret_literal:postgres_dsn")
    return failures


def _runtime_env_preflight_verify_run_metadata(
    args: Any,
    *,
    artifact_path: Path,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-runtime-env-preflight",
        "verification_flags": {
            "require_next_actions": bool(getattr(args, "require_next_actions", False)),
            "require_template_output": bool(getattr(args, "require_template_output", False)),
            "require_no_secret_literals": bool(getattr(args, "require_no_secret_literals", False)),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _read_dotenv_key_presence(path: Path | None) -> tuple[set[str], list[str]]:
    if path is None:
        return set(), []
    if not path.is_file():
        return set(), [f"dotenv file not found: {path}"]
    keys: set[str] = set()
    issues: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        return set(), [f"failed to read dotenv file: {exc}"]
    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            issues.append(f"dotenv line {index}: missing '='")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            issues.append(f"dotenv line {index}: missing key")
            continue
        if value.strip():
            keys.add(key)
    return keys, issues


def _handle_prediction_offline_readiness_summary(args: Any) -> dict[str, Any]:
    source_specs = {
        "benchmark_verify": Path(args.benchmark_verify),
        "backfill_runtime_verify": Path(args.backfill_runtime_verify),
    }
    optional_specs = {
        "backfill_plan": getattr(args, "backfill_plan", None),
        "env_preflight": getattr(args, "env_preflight", None),
        "env_preflight_verify": getattr(args, "env_preflight_verify", None),
        "congress_load_summary": getattr(args, "congress_load_summary", None),
        "fec_inputs_verify": getattr(args, "fec_inputs_verify", None),
        "public_statement_rows_verify": getattr(args, "public_statement_rows_verify", None),
    }
    for name, path in optional_specs.items():
        if path is not None:
            source_specs[name] = Path(path)

    source_artifacts: dict[str, dict[str, Any]] = {}
    loaded_payloads: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for name, path in source_specs.items():
        artifact, payload, artifact_issues = _load_prediction_readiness_artifact(path)
        source_artifacts[name] = artifact
        loaded_payloads[name] = payload
        issues.extend(f"{name}: {issue}" for issue in artifact_issues)

    benchmark = _prediction_readiness_benchmark_summary(loaded_payloads.get("benchmark_verify", {}))
    backfill_plan_steps = _prediction_readiness_backfill_plan_steps(
        loaded_payloads.get("backfill_plan", {})
    )
    runtime = _prediction_readiness_runtime_summary(
        loaded_payloads.get("backfill_runtime_verify", {}),
        backfill_plan_steps=backfill_plan_steps,
    )
    env_preflight = _prediction_readiness_env_preflight_summary(
        loaded_payloads.get("env_preflight", {})
    )
    env_preflight_verify = _prediction_readiness_env_preflight_verify_summary(
        loaded_payloads.get("env_preflight_verify", {})
    )
    env_consistency = _prediction_readiness_env_consistency(
        runtime=runtime,
        env_preflight=env_preflight,
    )
    local_inputs = _prediction_readiness_local_input_summaries(loaded_payloads)
    require_congress_load_summary = bool(getattr(args, "require_congress_load_summary", False))

    blockers = [
        *[f"benchmark:{failure}" for failure in benchmark["quality_gate_failures"]],
        *[f"benchmark_issue:{issue}" for issue in benchmark["issues"]],
        *[f"runtime:{failure}" for failure in runtime["quality_gate_failures"]],
        *[
            f"runtime_requirement:{requirement}"
            for requirement in runtime["missing_runtime_requirements"]
        ],
    ]
    for name, summary in local_inputs.items():
        if summary["present"] and not summary["ok"]:
            blockers.append(f"local_input:{name}")
    congress_load_requirement_blockers = _prediction_readiness_congress_load_requirement_blockers(
        local_inputs.get("congress_load", {}),
        required=require_congress_load_summary,
    )
    blockers.extend(congress_load_requirement_blockers)
    if issues:
        blockers.extend(f"source_artifact:{issue}" for issue in issues)
    for issue in _string_list(env_preflight.get("issues")):
        blockers.append(f"env_preflight_issue:{issue}")
    for issue in _string_list(env_preflight_verify.get("issues")):
        blockers.append(f"env_preflight_verify_issue:{issue}")
    for failure in _string_list(env_preflight_verify.get("quality_gate_failures")):
        blockers.append(f"env_preflight_verify:{failure}")
    if env_consistency["mismatch"]:
        blockers.append("env_preflight:missing_env_mismatch")
    require_eval_window_run = bool(getattr(args, "require_eval_window_run", False))
    if require_eval_window_run:
        eval_window_run = benchmark.get("eval_window_run")
        if not isinstance(eval_window_run, dict) or not bool(eval_window_run.get("present")):
            blockers.append("eval_window_run:missing")
        elif not bool(eval_window_run.get("ok")):
            blockers.append("eval_window_run:not_ok")

    congress_load_requirement_ok = not congress_load_requirement_blockers
    offline_input_ok = (
        all(summary["ok"] for summary in local_inputs.values() if summary["present"])
        and congress_load_requirement_ok
    )
    next_live_actions = _prediction_readiness_next_live_actions(
        benchmark=benchmark,
        runtime=runtime,
    )
    if congress_load_requirement_blockers:
        next_live_actions.append("load_congress_prediction_inputs")
        next_live_actions = sorted(dict.fromkeys(next_live_actions))
    if require_eval_window_run:
        eval_window_run = benchmark.get("eval_window_run")
        if not isinstance(eval_window_run, dict) or not bool(eval_window_run.get("ok")):
            next_live_actions.append("verify_prediction_eval_window_run")
            next_live_actions = sorted(dict.fromkeys(next_live_actions))
    blocker_summary = _prediction_readiness_blocker_summary(
        blockers=blockers,
        benchmark=benchmark,
    )
    backfill_plan_summary: dict[str, Any] = {
        "present": bool(backfill_plan_steps),
        "step_count": len(backfill_plan_steps),
    }
    backfill_plan_source_state = _prediction_readiness_backfill_plan_source_state(
        loaded_payloads.get("backfill_plan", {})
    )
    if backfill_plan_source_state:
        backfill_plan_summary["source_state"] = backfill_plan_source_state
    result: dict[str, Any] = {
        "ok": not blockers,
        "command": "prediction-offline-readiness-summary",
        "offline_input_ok": offline_input_ok,
        "congress_load_required": require_congress_load_summary,
        "live_ready": bool(benchmark["ok"]) and bool(runtime["ok"]),
        "source_artifacts": source_artifacts,
        "local_inputs": local_inputs,
        "benchmark": benchmark,
        "backfill_plan": backfill_plan_summary,
        "env_preflight": env_preflight,
        "env_preflight_verify": env_preflight_verify,
        "env_consistency": env_consistency,
        "runtime": runtime,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "blocker_summary": blocker_summary,
        "next_live_actions": next_live_actions,
        "next_actions_by_kind": _prediction_readiness_next_actions_by_kind(next_live_actions),
        "issues": issues,
        "issue_count": len(issues),
    }
    resume_script_output = (
        Path(args.resume_script_output)
        if getattr(args, "resume_script_output", None) is not None
        else None
    )
    if resume_script_output is not None:
        resume_script_output_sha256 = _write_prediction_resume_script(
            resume_script_output,
            result["runtime"]["operator_resume_batches"],
        )
        result["resume_script_output"] = str(resume_script_output)
        result["resume_script_output_sha256"] = resume_script_output_sha256
    else:
        result["resume_script_output"] = None
        result["resume_script_output_sha256"] = None
    result["run_metadata"] = _prediction_offline_readiness_run_metadata(result)
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


def _write_prediction_resume_script(
    path: Path,
    batches: list[dict[str, Any]],
) -> str:
    encoded = _prediction_resume_script_bytes(batches)
    digest = _write_bytes_artifact(path, encoded)
    path.chmod(0o755)
    return digest


def _handle_verify_prediction_offline_readiness_summary(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-offline-readiness-summary",
                "artifact": str(artifact_path),
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_offline_readiness_verify_run_metadata(
                    args,
                    artifact_path=artifact_path,
                ),
            },
        )

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(payload, dict):
        return failure_result(issues=["artifact must be an object"])

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if payload.get("command") != "prediction-offline-readiness-summary":
        issues.append("artifact_command_mismatch")
    issues.extend(_prediction_offline_readiness_count_issues(payload))
    issues.extend(_prediction_offline_readiness_congress_requirement_issues(payload))

    blockers = _string_list(payload.get("blockers"))
    blocker_count = len(blockers)
    declared_blocker_count = (
        payload.get("blocker_count")
        if _is_non_negative_plain_int(payload.get("blocker_count"))
        else None
    )
    if declared_blocker_count is not None and declared_blocker_count != blocker_count:
        issues.append("blocker_count_mismatch")
    declared_blocker_summary_total = _readiness_blocker_summary_total(payload)
    if (
        declared_blocker_summary_total is not None
        and declared_blocker_summary_total != blocker_count
    ):
        issues.append("blocker_summary_total_mismatch")

    artifact_issues = _string_list(payload.get("issues"))
    declared_issue_count = (
        payload.get("issue_count")
        if _is_non_negative_plain_int(payload.get("issue_count"))
        else None
    )
    if declared_issue_count is not None and declared_issue_count != len(artifact_issues):
        issues.append("issue_count_mismatch")
    source_artifact_state = _prediction_offline_readiness_source_artifact_state(
        payload,
        issues=issues,
    )

    run_metadata = payload.get("run_metadata")
    if bool(getattr(args, "require_run_metadata", False)):
        if not isinstance(run_metadata, dict):
            quality_gate_failures.append("run_metadata_missing")
        else:
            _validate_prediction_offline_readiness_run_metadata(
                payload,
                run_metadata=run_metadata,
                issues=issues,
                quality_gate_failures=quality_gate_failures,
            )

    env_preflight_verify = _prediction_readiness_env_preflight_verify_summary(
        payload.get("env_preflight_verify", {})
        if isinstance(payload.get("env_preflight_verify"), dict)
        else {}
    )
    if bool(getattr(args, "require_env_preflight_verify", False)):
        if not bool(env_preflight_verify.get("present")):
            quality_gate_failures.append("env_preflight_verify_missing")
        elif not bool(env_preflight_verify.get("ok")):
            quality_gate_failures.append("env_preflight_verify_not_ok")
    benchmark = payload.get("benchmark") if isinstance(payload.get("benchmark"), dict) else {}
    eval_window_run = _prediction_readiness_eval_window_run_summary(
        benchmark.get("eval_window_run")
    )
    if bool(getattr(args, "require_eval_window_run", False)):
        if not bool(eval_window_run.get("present")):
            quality_gate_failures.append("eval_window_run_missing")
        elif not bool(eval_window_run.get("ok")):
            quality_gate_failures.append("eval_window_run_not_ok")

    script_state = _prediction_offline_readiness_resume_script_state(
        payload,
        require_resume_script_match=bool(getattr(args, "require_resume_script_match", False)),
        quality_gate_failures=quality_gate_failures,
    )

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _prediction_offline_readiness_secret_literal_failures(payload)
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "blocker_count": blocker_count,
        "issue_count": len(artifact_issues),
        "operator_resume_batch_count": len(_prediction_resume_script_batches(payload)),
        "source_artifact_count": source_artifact_state["count"],
        "source_artifact_mismatch_count": source_artifact_state["mismatch_count"],
        "source_artifact_invalid_count": source_artifact_state["invalid_count"],
        "script_matches_expected": script_state["matches_expected"],
        "secret_literal_count": len(secret_failures),
        "env_preflight_verify_present": bool(env_preflight_verify.get("present")),
        "env_preflight_verify_ok": bool(env_preflight_verify.get("ok")),
    }
    if bool(getattr(args, "require_eval_window_run", False)) or bool(
        eval_window_run.get("present")
    ):
        source_state["eval_window_run_present"] = bool(eval_window_run.get("present"))
        source_state["eval_window_run_ok"] = bool(eval_window_run.get("ok"))
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-offline-readiness-summary",
        "artifact": str(artifact_path),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "blocker_count": blocker_count,
        "issue_count": len(issues),
        "issues": issues,
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "resume_script": script_state["path"],
        "resume_script_sha256": script_state["sha256"],
        "expected_resume_script_sha256": script_state["expected_sha256"],
        "summary_resume_script_sha256": payload.get("resume_script_output_sha256"),
        "script_matches_expected": script_state["matches_expected"],
        "env_preflight_verify": env_preflight_verify,
        "eval_window_run": eval_window_run,
        "run_metadata": _prediction_offline_readiness_verify_run_metadata(
            args,
            artifact_path=artifact_path,
            source_state=source_state,
        ),
    }
    return _attach_optional_verification_output(args, result)


def _readiness_blocker_summary_total(payload: dict[str, Any]) -> int | None:
    blocker_summary = payload.get("blocker_summary")
    if not isinstance(blocker_summary, dict):
        return None
    total = blocker_summary.get("total")
    return total if _is_non_negative_plain_int(total) else None


def _prediction_offline_readiness_count_issues(
    payload: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in ("blocker_count", "issue_count"):
        if not _is_plain_int(payload.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(payload.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    blocker_summary = payload.get("blocker_summary")
    if isinstance(blocker_summary, dict) and not _is_plain_int(blocker_summary.get("total")):
        issues.append("blocker_summary.total must be an integer")
    elif isinstance(blocker_summary, dict) and not _is_non_negative_plain_int(
        blocker_summary.get("total")
    ):
        issues.append("blocker_summary.total must be a non-negative integer")
    benchmark = payload.get("benchmark")
    if isinstance(benchmark, dict):
        for key in ("component_count", "quality_gate_failure_count"):
            if not _is_plain_int(benchmark.get(key)):
                issues.append(f"benchmark.{key} must be an integer")
            elif not _is_non_negative_plain_int(benchmark.get(key)):
                issues.append(f"benchmark.{key} must be a non-negative integer")
    env_preflight = payload.get("env_preflight")
    if isinstance(env_preflight, dict) and not _is_plain_int(env_preflight.get("issue_count")):
        issues.append("env_preflight.issue_count must be an integer")
    elif isinstance(env_preflight, dict) and not _is_non_negative_plain_int(
        env_preflight.get("issue_count")
    ):
        issues.append("env_preflight.issue_count must be a non-negative integer")
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        for key in ("runtime_ready_step_count", "runtime_blocked_step_count"):
            if not _is_plain_int(runtime.get(key)):
                issues.append(f"runtime.{key} must be an integer")
            elif not _is_non_negative_plain_int(runtime.get(key)):
                issues.append(f"runtime.{key} must be a non-negative integer")
    env_preflight_verify = payload.get("env_preflight_verify")
    if isinstance(env_preflight_verify, dict) and bool(env_preflight_verify.get("present")):
        for key in ("issue_count", "quality_gate_failure_count"):
            if not _is_plain_int(env_preflight_verify.get(key)):
                issues.append(f"env_preflight_verify.{key} must be an integer")
            elif not _is_non_negative_plain_int(env_preflight_verify.get(key)):
                issues.append(f"env_preflight_verify.{key} must be a non-negative integer")
    return issues


def _prediction_offline_readiness_congress_requirement_issues(
    payload: dict[str, Any],
) -> list[str]:
    if not bool(payload.get("congress_load_required")):
        return []
    local_inputs = payload.get("local_inputs")
    congress_load = local_inputs.get("congress_load") if isinstance(local_inputs, dict) else {}
    expected_blockers = _prediction_readiness_congress_load_requirement_blockers(
        congress_load if isinstance(congress_load, dict) else {},
        required=True,
    )
    blockers = set(_string_list(payload.get("blockers")))
    issues = [
        f"missing_required_congress_load_blocker:{blocker}"
        for blocker in expected_blockers
        if blocker not in blockers
    ]
    if expected_blockers and bool(payload.get("offline_input_ok")):
        issues.append("offline_input_ok_mismatch:congress_load_required")
    if expected_blockers and bool(payload.get("ok")):
        issues.append("ok_mismatch:congress_load_required")
    return issues


def _prediction_offline_readiness_source_artifact_state(
    payload: dict[str, Any],
    *,
    issues: list[str],
) -> dict[str, int]:
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        return {"count": 0, "mismatch_count": 0, "invalid_count": 0}
    mismatch_count = 0
    invalid_count = 0
    for name, artifact in source_artifacts.items():
        label = str(name)
        if not isinstance(artifact, dict):
            issues.append(f"source_artifact_invalid:{label}")
            invalid_count += 1
            continue
        path_raw = artifact.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            issues.append(f"source_artifact_path_missing:{label}")
            invalid_count += 1
            continue
        path = Path(path_raw)
        if not path.is_file():
            issues.append(f"source_artifact_missing:{label}")
            mismatch_count += 1
            continue
        expected_sha256 = artifact.get("sha256")
        if not isinstance(expected_sha256, str) or not _is_sha256_hex(expected_sha256):
            issues.append(f"source_artifact_sha256_invalid:{label}")
            invalid_count += 1
            continue
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 != actual_sha256:
            issues.append(f"source_artifact_sha256_mismatch:{label}")
            mismatch_count += 1
    return {
        "count": len(source_artifacts),
        "mismatch_count": mismatch_count,
        "invalid_count": invalid_count,
    }


def _validate_prediction_offline_readiness_run_metadata(
    payload: dict[str, Any],
    *,
    run_metadata: dict[str, Any],
    issues: list[str],
    quality_gate_failures: list[str],
) -> None:
    if run_metadata.get("command") != "prediction-offline-readiness-summary":
        issues.append("run_metadata_command_mismatch")
    expected_artifact_sha256 = _prediction_offline_readiness_source_artifact_sha256(payload)
    run_metadata_hashes = run_metadata.get("source_artifact_sha256")
    invalid_hash_found = False
    if isinstance(run_metadata_hashes, dict):
        for key, value in run_metadata_hashes.items():
            if value is None:
                continue
            if not isinstance(key, str) or not isinstance(value, str) or not _is_sha256_hex(value):
                issues.append(f"run_metadata_source_artifact_sha256_invalid:{key}")
                invalid_hash_found = True
    if (
        not invalid_hash_found
        and run_metadata.get("source_artifact_sha256") != expected_artifact_sha256
    ):
        issues.append("run_metadata_source_artifact_sha256_mismatch")
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        quality_gate_failures.append("run_metadata_source_state_missing")
        return
    issues.extend(
        f"run_metadata.source_state.{issue}"
        for issue in _prediction_offline_readiness_source_state_count_issues(source_state)
    )
    invalid_source_state_keys = _prediction_offline_readiness_invalid_source_state_count_keys(
        source_state
    )
    issues.extend(_prediction_offline_readiness_benchmark_scope_shape_issues(payload))
    expected = _prediction_offline_readiness_expected_source_state(payload)
    for key in sorted(set(source_state) - set(expected)):
        issues.append(f"run_metadata_source_state_unexpected:{key}")
    for key, expected_value in expected.items():
        if key in invalid_source_state_keys:
            continue
        if source_state.get(key) != expected_value:
            issues.append(f"run_metadata_source_state_mismatch:{key}")


def _prediction_offline_readiness_expected_source_state(
    payload: dict[str, Any],
) -> dict[str, Any]:
    benchmark = payload.get("benchmark") if isinstance(payload.get("benchmark"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    env_preflight = (
        payload.get("env_preflight") if isinstance(payload.get("env_preflight"), dict) else {}
    )
    expected = {
        "offline_input_ok": bool(payload.get("offline_input_ok")),
        "live_ready": bool(payload.get("live_ready")),
        "blocker_count": _plain_int_or_zero(payload.get("blocker_count")),
        "blocker_summary": payload.get("blocker_summary"),
        "benchmark_component_count": _plain_int_or_zero(benchmark.get("component_count")),
        "benchmark_quality_gate_failure_count": _plain_int_or_zero(
            benchmark.get("quality_gate_failure_count")
        ),
        "missing_runtime_requirements": _string_list(runtime.get("missing_runtime_requirements")),
        "env_preflight_missing_env": _string_list(env_preflight.get("missing_env")),
        "env_preflight_issue_count": _plain_int_or_zero(env_preflight.get("issue_count")),
        "env_consistency": payload.get("env_consistency"),
        "runtime_ready_step_count": _plain_int_or_zero(runtime.get("runtime_ready_step_count")),
        "runtime_blocked_step_count": _plain_int_or_zero(runtime.get("runtime_blocked_step_count")),
    }
    if "congress_load_required" in payload:
        expected["congress_load_required"] = bool(payload.get("congress_load_required"))
    if _plain_int_or_zero(benchmark.get("jurisdiction_count")):
        expected["benchmark_jurisdiction_count"] = _plain_int_or_zero(
            benchmark.get("jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark.get("jurisdiction_ids")):
        expected["benchmark_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark.get("jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark.get("implemented_jurisdiction_count")):
        expected["benchmark_implemented_jurisdiction_count"] = _plain_int_or_zero(
            benchmark.get("implemented_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark.get("implemented_jurisdiction_ids")):
        expected["benchmark_implemented_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark.get("implemented_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark.get("portable_jurisdiction_count")):
        expected["benchmark_portable_jurisdiction_count"] = _plain_int_or_zero(
            benchmark.get("portable_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark.get("portable_jurisdiction_ids")):
        expected["benchmark_portable_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark.get("portable_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark.get("legislative_body_count")):
        expected["benchmark_legislative_body_count"] = _plain_int_or_zero(
            benchmark.get("legislative_body_count")
        )
    if _sorted_string_list_or_empty(benchmark.get("legislative_body_ids")):
        expected["benchmark_legislative_body_ids"] = _sorted_string_list_or_empty(
            benchmark.get("legislative_body_ids")
        )
    if _plain_int_or_zero(benchmark.get("legislative_session_count")):
        expected["benchmark_legislative_session_count"] = _plain_int_or_zero(
            benchmark.get("legislative_session_count")
        )
    if _sorted_string_list_or_empty(benchmark.get("legislative_session_ids")):
        expected["benchmark_legislative_session_ids"] = _sorted_string_list_or_empty(
            benchmark.get("legislative_session_ids")
        )
    benchmark_source_state = _prediction_readiness_benchmark_source_state(benchmark)
    if _plain_int_or_zero(benchmark_source_state.get("jurisdiction_count")):
        expected["benchmark_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("jurisdiction_ids")):
        expected["benchmark_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("implemented_jurisdiction_count")):
        expected["benchmark_implemented_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("implemented_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("implemented_jurisdiction_ids")):
        expected["benchmark_implemented_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("implemented_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("portable_jurisdiction_count")):
        expected["benchmark_portable_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("portable_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("portable_jurisdiction_ids")):
        expected["benchmark_portable_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("portable_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("legislative_body_count")):
        expected["benchmark_legislative_body_count"] = _plain_int_or_zero(
            benchmark_source_state.get("legislative_body_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("legislative_body_ids")):
        expected["benchmark_legislative_body_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("legislative_body_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("legislative_session_count")):
        expected["benchmark_legislative_session_count"] = _plain_int_or_zero(
            benchmark_source_state.get("legislative_session_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("legislative_session_ids")):
        expected["benchmark_legislative_session_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("legislative_session_ids")
        )
    expected.update(
        _prediction_readiness_benchmark_backtest_source_coverage(benchmark_source_state)
    )
    expected.update(
        _prediction_readiness_benchmark_inventory_feature_source_coverage(benchmark_source_state)
    )
    expected.update(
        _prediction_readiness_benchmark_inventory_source_coverage(benchmark_source_state)
    )
    expected.update(_prediction_readiness_benchmark_source_url_audit_gaps(benchmark_source_state))
    expected.update(_prediction_readiness_benchmark_eval_cutoff_audit(benchmark_source_state))
    expected.update(_prediction_readiness_backfill_plan_source_state_for_run_metadata(payload))
    eval_window_run = benchmark.get("eval_window_run")
    if isinstance(eval_window_run, dict) and bool(eval_window_run.get("present")):
        expected["eval_window_run"] = _prediction_readiness_eval_window_run_source_state(
            eval_window_run
        )
    env_preflight_verify = payload.get("env_preflight_verify")
    if isinstance(env_preflight_verify, dict) and bool(env_preflight_verify.get("present")):
        expected["env_preflight_verify_issue_count"] = _plain_int_or_zero(
            env_preflight_verify.get("issue_count")
        )
        expected["env_preflight_verify_quality_gate_failure_count"] = _plain_int_or_zero(
            env_preflight_verify.get("quality_gate_failure_count")
        )
    local_inputs = payload.get("local_inputs")
    if isinstance(local_inputs, dict):
        congress_load = local_inputs.get("congress_load")
        if isinstance(congress_load, dict):
            congress_source_state = congress_load.get("source_state")
            if isinstance(congress_source_state, dict):
                expected.update(
                    _prediction_readiness_congress_load_source_state(congress_source_state)
                )
    return expected


def _prediction_offline_readiness_benchmark_scope_shape_issues(
    payload: dict[str, Any],
) -> list[str]:
    benchmark = payload.get("benchmark") if isinstance(payload.get("benchmark"), dict) else {}
    source_states: list[dict[str, Any]] = []
    if isinstance(benchmark, dict):
        source_states.append(benchmark)
        source_state = _prediction_readiness_benchmark_source_state(benchmark)
        if source_state:
            source_states.append(source_state)
    issues: list[str] = []
    for source_state in source_states:
        for key in (
            "jurisdiction_ids",
            "implemented_jurisdiction_ids",
            "portable_jurisdiction_ids",
            "legislative_body_ids",
            "legislative_session_ids",
        ):
            if key in source_state and not _sorted_string_list_or_empty(source_state.get(key)):
                issues.append(f"benchmark_source_state_invalid:{key}")
        issues.extend(_prediction_offline_readiness_benchmark_scoped_id_issues(source_state))
    issues.extend(_prediction_offline_readiness_source_url_audit_flag_issues(benchmark))
    return sorted(set(issues))


def _prediction_offline_readiness_source_url_audit_flag_issues(
    benchmark: dict[str, Any],
) -> list[str]:
    components = benchmark.get("components")
    if not isinstance(components, dict) or "source_url_audit" not in components:
        return []
    source_url_audit = components.get("source_url_audit")
    if not isinstance(source_url_audit, dict):
        return ["benchmark_source_url_audit_invalid"]
    run_metadata = source_url_audit.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return ["benchmark_source_url_audit_run_metadata_missing"]
    verification_flags = run_metadata.get("verification_flags")
    if not isinstance(verification_flags, dict):
        return ["benchmark_source_url_audit_verification_flags_missing"]
    if not isinstance(verification_flags.get("fail_on_gaps"), bool):
        return ["benchmark_source_url_audit_verification_flags_invalid:fail_on_gaps"]
    return []


def _prediction_offline_readiness_benchmark_scoped_id_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    jurisdiction_ids_value = source_state.get("jurisdiction_ids")
    legislative_body_ids_value = source_state.get("legislative_body_ids")
    legislative_session_ids_value = source_state.get("legislative_session_ids")
    if not (
        _sorted_string_list_or_empty(jurisdiction_ids_value)
        and isinstance(legislative_body_ids_value, list)
        and all(isinstance(item, str) for item in legislative_body_ids_value)
    ):
        return issues
    jurisdiction_ids = set(jurisdiction_ids_value)
    legislative_body_ids = set(legislative_body_ids_value)
    if any(
        len(parts := item.split(":")) != 2 or parts[0] not in jurisdiction_ids or not parts[1]
        for item in legislative_body_ids_value
    ):
        issues.append("benchmark_source_state_invalid:legislative_body_ids")
    if isinstance(legislative_session_ids_value, list) and all(
        isinstance(item, str) for item in legislative_session_ids_value
    ):
        if any(
            len(parts := item.split(":")) != 3
            or parts[0] not in jurisdiction_ids
            or f"{parts[0]}:{parts[1]}" not in legislative_body_ids
            or not parts[2]
            for item in legislative_session_ids_value
        ):
            issues.append("benchmark_source_state_invalid:legislative_session_ids")
    return issues


def _prediction_offline_readiness_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "blocker_count",
        "benchmark_component_count",
        "benchmark_quality_gate_failure_count",
        "benchmark_jurisdiction_count",
        "benchmark_implemented_jurisdiction_count",
        "benchmark_portable_jurisdiction_count",
        "benchmark_legislative_body_count",
        "benchmark_legislative_session_count",
        *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS,
        *_OFFLINE_READINESS_BENCHMARK_EVAL_CUTOFF_AUDIT_KEYS,
        "env_preflight_issue_count",
        "runtime_ready_step_count",
        "runtime_blocked_step_count",
        "backfill_plan_sample_case_count",
        "congress_load_member_row_count",
        "congress_load_member_term_row_count",
        "congress_load_bill_row_count",
        "congress_load_bill_sponsor_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    ):
        if key in source_state:
            issues.extend(
                _prediction_offline_readiness_source_state_count_issue(
                    key,
                    source_state.get(key),
                )
            )
    for key in _BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS:
        if key in source_state:
            value = source_state.get(key)
            if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1:
                issues.append(f"{key} must be a number between 0 and 1")
    for key in _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS:
        if key in source_state:
            value = source_state.get(key)
            if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1:
                issues.append(f"{key} must be a number between 0 and 1")
    for key in _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS:
        if key in source_state:
            value = source_state.get(key)
            if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1:
                issues.append(f"{key} must be a number between 0 and 1")
    if "backfill_plan_sample_vote_event_ids" in source_state:
        sample_vote_event_ids = source_state.get("backfill_plan_sample_vote_event_ids")
        if not isinstance(sample_vote_event_ids, list):
            issues.append("backfill_plan_sample_vote_event_ids must be a list")
        elif any(not _is_non_negative_plain_int(value) for value in sample_vote_event_ids):
            issues.append("backfill_plan_sample_vote_event_ids must be a non-negative integer list")
    if "source_url_audit_sample_vote_event_ids" in source_state:
        sample_vote_event_ids = source_state.get("source_url_audit_sample_vote_event_ids")
        if not isinstance(sample_vote_event_ids, list):
            issues.append("source_url_audit_sample_vote_event_ids must be a list")
        elif any(not _is_non_negative_plain_int(value) for value in sample_vote_event_ids):
            issues.append(
                "source_url_audit_sample_vote_event_ids must be a non-negative integer list"
            )
    for key in _PREDICTION_OFFLINE_READINESS_SOURCE_STATE_STRING_LIST_KEYS:
        if key in source_state and not _sorted_string_list_or_empty(source_state.get(key)):
            issues.append(f"{key} must be a sorted string list")
        elif (
            key in source_state
            and key.endswith("source_family_ids")
            and not _source_family_id_list(source_state.get(key))
        ):
            issues.append(f"{key} must contain normalized source family ids")
    issues.extend(
        _prediction_offline_readiness_sample_scoped_id_issues(
            source_state,
            prefix="backfill_plan",
        )
    )
    issues.extend(
        _prediction_offline_readiness_sample_scoped_id_issues(
            source_state,
            prefix="source_url_audit",
        )
    )
    for key in (
        "congress_load_required",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
    ):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"{key} must be a boolean")
    eval_window_run = source_state.get("eval_window_run")
    if isinstance(eval_window_run, dict):
        for key in (
            "checked",
            "window_count",
            "missing_verifier_count",
            "failing_verifier_count",
            "stale_field_count",
        ):
            if key in eval_window_run:
                issues.extend(
                    _prediction_offline_readiness_source_state_count_issue(
                        f"eval_window_run.{key}",
                        eval_window_run.get(key),
                    )
                )
        issues.extend(
            _prediction_offline_readiness_eval_window_run_archive_manifest_issues(eval_window_run)
        )
        issues.extend(_prediction_offline_readiness_eval_window_run_gate_issues(eval_window_run))
    for key in (
        "env_preflight_verify_issue_count",
        "env_preflight_verify_quality_gate_failure_count",
        "congress_load_member_row_count",
        "congress_load_member_term_row_count",
        "congress_load_bill_row_count",
        "congress_load_bill_sponsor_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    ):
        if key in source_state:
            issues.extend(
                _prediction_offline_readiness_source_state_count_issue(
                    key,
                    source_state.get(key),
                )
            )
    return issues


def _prediction_offline_readiness_sample_scoped_id_issues(
    source_state: dict[str, Any],
    *,
    prefix: str,
) -> list[str]:
    jurisdiction_key = f"{prefix}_sample_jurisdiction_ids"
    body_key = f"{prefix}_sample_legislative_body_ids"
    session_key = f"{prefix}_sample_legislative_session_ids"
    if not any(key in source_state for key in (jurisdiction_key, body_key, session_key)):
        return []
    jurisdiction_ids = source_state.get(jurisdiction_key)
    body_ids = source_state.get(body_key)
    session_ids = source_state.get(session_key)
    if not _sorted_string_list_or_empty(jurisdiction_ids):
        return []
    jurisdiction_id_set = set(jurisdiction_ids)
    issues: list[str] = []
    body_id_set: set[str] = set()
    if body_ids is not None and _sorted_string_list_or_empty(body_ids):
        body_id_set = set(body_ids)
        if any(
            len(parts := item.split(":")) != 2
            or parts[0] not in jurisdiction_id_set
            or not parts[1]
            for item in body_ids
        ):
            issues.append(f"{body_key} must contain scoped ids")
    if session_ids is not None and _sorted_string_list_or_empty(session_ids):
        if any(
            len(parts := item.split(":")) != 3
            or parts[0] not in jurisdiction_id_set
            or f"{parts[0]}:{parts[1]}" not in body_id_set
            or not parts[2]
            for item in session_ids
        ):
            issues.append(f"{session_key} must contain scoped ids")
    return issues


def _prediction_offline_readiness_source_state_count_issue(
    key: str,
    value: object,
) -> list[str]:
    if not _is_plain_int(value):
        return [f"{key} must be an integer"]
    if value < 0:
        return [f"{key} must be a non-negative integer"]
    return []


def _prediction_offline_readiness_invalid_source_state_count_keys(
    source_state: dict[str, Any],
) -> set[str]:
    invalid_keys: set[str] = set()
    for key in (
        "blocker_count",
        "benchmark_component_count",
        "benchmark_quality_gate_failure_count",
        "benchmark_jurisdiction_count",
        "benchmark_implemented_jurisdiction_count",
        "benchmark_portable_jurisdiction_count",
        "benchmark_legislative_body_count",
        "benchmark_legislative_session_count",
        *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
        *_BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS,
        *_OFFLINE_READINESS_BENCHMARK_EVAL_CUTOFF_AUDIT_KEYS,
        "env_preflight_issue_count",
        "runtime_ready_step_count",
        "runtime_blocked_step_count",
        "env_preflight_verify_issue_count",
        "env_preflight_verify_quality_gate_failure_count",
        "backfill_plan_sample_case_count",
        "congress_load_member_row_count",
        "congress_load_member_term_row_count",
        "congress_load_bill_row_count",
        "congress_load_bill_sponsor_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    ):
        if key in source_state and not _is_non_negative_plain_int(source_state.get(key)):
            invalid_keys.add(key)
    for key in _BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS:
        value = source_state.get(key)
        if key in source_state and (
            not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1
        ):
            invalid_keys.add(key)
    for key in _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS:
        value = source_state.get(key)
        if key in source_state and (
            not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1
        ):
            invalid_keys.add(key)
    for key in _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS:
        value = source_state.get(key)
        if key in source_state and (
            not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1
        ):
            invalid_keys.add(key)
    sample_vote_event_ids = source_state.get("backfill_plan_sample_vote_event_ids")
    if "backfill_plan_sample_vote_event_ids" in source_state and (
        not isinstance(sample_vote_event_ids, list)
        or any(not _is_non_negative_plain_int(value) for value in sample_vote_event_ids)
    ):
        invalid_keys.add("backfill_plan_sample_vote_event_ids")
    source_url_audit_vote_event_ids = source_state.get("source_url_audit_sample_vote_event_ids")
    if "source_url_audit_sample_vote_event_ids" in source_state and (
        not isinstance(source_url_audit_vote_event_ids, list)
        or any(not _is_non_negative_plain_int(value) for value in source_url_audit_vote_event_ids)
    ):
        invalid_keys.add("source_url_audit_sample_vote_event_ids")
    for key in _PREDICTION_OFFLINE_READINESS_SOURCE_STATE_STRING_LIST_KEYS:
        if key in source_state and not _sorted_string_list_or_empty(source_state.get(key)):
            invalid_keys.add(key)
    for key in (
        "congress_load_required",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
    ):
        if key in source_state and not isinstance(source_state.get(key), bool):
            invalid_keys.add(key)
    eval_window_run = source_state.get("eval_window_run")
    if isinstance(eval_window_run, dict):
        for key in (
            "checked",
            "window_count",
            "missing_verifier_count",
            "failing_verifier_count",
            "stale_field_count",
        ):
            if key in eval_window_run and not _is_non_negative_plain_int(eval_window_run.get(key)):
                invalid_keys.add("eval_window_run")
                break
        if _prediction_offline_readiness_eval_window_run_archive_manifest_issues(
            eval_window_run
        ) or _prediction_offline_readiness_eval_window_run_gate_issues(eval_window_run):
            invalid_keys.add("eval_window_run")
    return invalid_keys


def _prediction_offline_readiness_eval_window_run_gate_issues(
    eval_window_run: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key, _ in _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS:
        if key in eval_window_run and not isinstance(eval_window_run.get(key), bool):
            issues.append(f"eval_window_run.{key} must be a boolean")
    return issues


def _prediction_offline_readiness_eval_window_run_archive_manifest_issues(
    eval_window_run: dict[str, Any],
) -> list[str]:
    if "congress_archive_manifest" not in eval_window_run:
        return []
    archive_manifest = eval_window_run.get("congress_archive_manifest")
    if not isinstance(archive_manifest, dict):
        return ["eval_window_run.congress_archive_manifest must be an object"]
    path = archive_manifest.get("path")
    sha256 = archive_manifest.get("sha256")
    issues: list[str] = []
    if not isinstance(path, str) or not path:
        issues.append("eval_window_run.congress_archive_manifest.path must be a string")
    if not isinstance(sha256, str) or not _is_sha256_hex(sha256):
        issues.append("eval_window_run.congress_archive_manifest.sha256 invalid")
    return issues


def _is_non_negative_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_optional_non_negative_plain_int(value: object) -> bool:
    return value is None or _is_non_negative_plain_int(value)


def _optional_limit_issue(value: object) -> str | None:
    if _is_optional_non_negative_plain_int(value):
        return None
    return "limit must be a non-negative integer"


def _positive_int_arg_issues(args: Any, names: tuple[str, ...]) -> list[str]:
    issues: list[str] = []
    for name in names:
        value = getattr(args, name, None)
        if value is None:
            continue
        if type(value) is not int or value <= 0:
            issues.append(f"{name} must be a positive integer")
    return issues


def _positive_int_sequence_issues(name: str, values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list | tuple):
        return [f"{name} must be a sequence of positive integers"]
    issues: list[str] = []
    for index, value in enumerate(values):
        if type(value) is not int or value <= 0:
            issues.append(f"{name}[{index}] must be a positive integer")
    return issues


def _positive_finite_timeout(value: object) -> tuple[float, str | None]:
    if isinstance(value, bool):
        return 0.0, "timeout must be a positive finite number"
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return 0.0, "timeout must be a positive finite number"
    if not math.isfinite(timeout) or timeout <= 0.0:
        return 0.0, "timeout must be a positive finite number"
    return timeout, None


def _prediction_window_date_issues(args: Any) -> list[str]:
    issues: list[str] = []
    if args.train_start > args.train_end:
        issues.append("train_start must be on or before train_end")
    if args.label_start > args.label_end:
        issues.append("label_start must be on or before label_end")
    if args.training_feature_cutoff >= args.train_start:
        issues.append("training_feature_cutoff must be before train_start")
    if args.train_end > args.feature_cutoff:
        issues.append("train_end must be on or before feature_cutoff")
    if args.feature_cutoff >= args.label_start:
        issues.append("feature_cutoff must be before label_start")
    return issues


def _command_issue_result(command: str, issues: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "issues": issues,
        "issue_count": len(issues),
    }


def _prediction_offline_readiness_source_artifact_sha256(
    payload: dict[str, Any],
) -> dict[str, str | None]:
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        return {}
    result: dict[str, str | None] = {}
    for name, artifact in source_artifacts.items():
        if not isinstance(artifact, dict):
            continue
        sha256 = artifact.get("sha256")
        if sha256 is None:
            result[str(name)] = None
        elif isinstance(sha256, str) and _is_sha256_hex(sha256):
            result[str(name)] = sha256
    return result


def _prediction_offline_readiness_resume_script_state(
    payload: dict[str, Any],
    *,
    require_resume_script_match: bool,
    quality_gate_failures: list[str],
) -> dict[str, Any]:
    script_path_raw = payload.get("resume_script_output")
    script_path = Path(script_path_raw) if isinstance(script_path_raw, str) else None
    expected_bytes = _prediction_resume_script_bytes(_prediction_resume_script_batches(payload))
    expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
    if script_path is None:
        if require_resume_script_match:
            quality_gate_failures.append("resume_script_missing")
        return {
            "path": None,
            "sha256": None,
            "expected_sha256": expected_sha256,
            "matches_expected": False,
        }
    try:
        actual_bytes = script_path.read_bytes()
    except Exception:  # noqa: BLE001
        if require_resume_script_match:
            quality_gate_failures.append("resume_script_file_missing")
        return {
            "path": str(script_path),
            "sha256": None,
            "expected_sha256": expected_sha256,
            "matches_expected": False,
        }
    actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
    if require_resume_script_match and payload.get("resume_script_output_sha256") != actual_sha256:
        quality_gate_failures.append("resume_script_sha256_mismatch")
    matches_expected = actual_bytes == expected_bytes
    if require_resume_script_match and not matches_expected:
        quality_gate_failures.append("script_content_mismatch")
    return {
        "path": str(script_path),
        "sha256": actual_sha256,
        "expected_sha256": expected_sha256,
        "matches_expected": matches_expected,
    }


def _prediction_offline_readiness_secret_literal_failures(
    payload: dict[str, Any],
) -> list[str]:
    serialized = json.dumps(payload, sort_keys=True)
    failures: list[str] = []
    if "sk-" in serialized:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", serialized):
        failures.append("secret_literal:postgres_dsn")
    return sorted(set(failures))


def _prediction_offline_readiness_verify_run_metadata(
    args: Any,
    *,
    artifact_path: Path,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-offline-readiness-summary",
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_env_preflight_verify": bool(
                getattr(args, "require_env_preflight_verify", False)
            ),
            "require_eval_window_run": bool(getattr(args, "require_eval_window_run", False)),
            "require_resume_script_match": bool(
                getattr(args, "require_resume_script_match", False)
            ),
            "require_no_secret_literals": bool(getattr(args, "require_no_secret_literals", False)),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_resume_script_bytes(batches: list[dict[str, Any]]) -> bytes:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated by prediction-offline-readiness-summary.",
        "# Fill required environment variables locally before running a phase.",
        "# Commands are derived from the verified prediction backfill plan.",
    ]
    for batch in batches:
        phase = _plain_int_or_zero(batch.get("phase"))
        missing_requirements = _string_list(batch.get("missing_requirements"))
        actions = _string_list(batch.get("actions"))
        commands = _string_list(batch.get("commands"))
        source_requirements = _string_list(batch.get("source_requirements"))
        source_artifacts = _string_list(batch.get("source_artifacts"))
        additional_reasons = _string_list(batch.get("additional_reasons"))
        lines.extend(
            [
                "",
                f"# Phase {phase}",
                "# Missing requirements: "
                + (", ".join(missing_requirements) if missing_requirements else "none"),
                "# Actions: " + (", ".join(actions) if actions else "none"),
            ]
        )
        if source_requirements:
            lines.append("# Source requirements:")
            lines.extend(f"#   - {requirement}" for requirement in source_requirements)
        if source_artifacts:
            lines.append("# Source artifacts:")
            lines.extend(f"#   - {artifact}" for artifact in source_artifacts)
        if additional_reasons:
            lines.append("# Additional reasons:")
            lines.extend(f"#   - {reason}" for reason in additional_reasons)
        env_requirements = _prediction_resume_script_env_requirements(missing_requirements)
        for env_name in env_requirements:
            lines.append(f': "${{{env_name}:?Set {env_name} before phase {phase}}}"')
        if not commands:
            lines.append("# No commands for this phase.")
            continue
        lines.extend(commands)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _handle_verify_prediction_resume_script(args: Any) -> dict[str, Any]:
    readiness_path = Path(args.readiness_summary)
    script_path = Path(args.script)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-resume-script",
                "readiness_summary": str(readiness_path),
                "script": str(script_path),
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_resume_script_verify_run_metadata(
                    args,
                    readiness_path=readiness_path,
                    script_path=script_path,
                ),
            },
        )

    try:
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load readiness summary: {exc}"])
    if not isinstance(readiness, dict):
        return failure_result(issues=["readiness summary must be an object"])
    try:
        actual_bytes = script_path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load resume script: {exc}"])

    batches = _prediction_resume_script_batches(readiness)
    expected_bytes = _prediction_resume_script_bytes(batches)
    script_text = actual_bytes.decode("utf-8", errors="replace")
    actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
    expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
    expected_summary_sha256 = readiness.get("resume_script_output_sha256")
    expected_summary_path = readiness.get("resume_script_output")

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if readiness.get("command") != "prediction-offline-readiness-summary":
        issues.append("readiness_summary_command_mismatch")
    runtime = readiness.get("runtime")
    if not isinstance(runtime, dict) or not isinstance(
        runtime.get("operator_resume_batches"), list
    ):
        issues.append("operator_resume_batches_missing")
    elif isinstance(runtime.get("operator_resume_batches"), list):
        issues.extend(
            _prediction_resume_script_batch_contract_issues(runtime["operator_resume_batches"])
        )
    if isinstance(expected_summary_path, str) and expected_summary_path:
        if Path(expected_summary_path).expanduser() != script_path.expanduser():
            issues.append("script_path_mismatch")
    if expected_summary_sha256 is not None and (
        not isinstance(expected_summary_sha256, str) or not _is_sha256_hex(expected_summary_sha256)
    ):
        issues.append("summary_script_sha256_invalid")
    if (
        isinstance(expected_summary_sha256, str)
        and _is_sha256_hex(expected_summary_sha256)
        and actual_sha256 != expected_summary_sha256
    ):
        issues.append("script_sha256_mismatch")
    if actual_bytes != expected_bytes:
        quality_gate_failures.append("script_content_mismatch")
    readiness_verify = _prediction_resume_script_readiness_verify_state(
        readiness_path=readiness_path,
        verify_path_arg=getattr(args, "readiness_summary_verify", None),
        issues=issues,
        quality_gate_failures=quality_gate_failures,
    )

    if bool(getattr(args, "require_env_guards", False)):
        quality_gate_failures.extend(
            _prediction_resume_script_missing_env_guard_failures(
                script_text=script_text,
                batches=batches,
            )
        )
    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _prediction_resume_script_secret_literal_failures(script_text)
        quality_gate_failures.extend(secret_failures)
    unsafe_failures: list[str] = []
    if bool(getattr(args, "require_safe_commands", False)):
        unsafe_failures = _prediction_resume_script_unsafe_command_failures(
            script_text=script_text,
            batches=batches,
        )
        quality_gate_failures.extend(unsafe_failures)

    phase_count = len(batches)
    command_count = sum(len(_string_list(batch.get("commands"))) for batch in batches)
    env_guard_count = sum(
        len(
            _prediction_resume_script_env_requirements(
                _string_list(batch.get("missing_requirements"))
            )
        )
        for batch in batches
    )
    source_state = {
        "phase_count": phase_count,
        "command_count": command_count,
        "env_guard_count": env_guard_count,
        "readiness_summary_verify_present": bool(readiness_verify["present"]),
        "readiness_summary_verify_ok": bool(readiness_verify["ok"]),
        "script_matches_expected": actual_bytes == expected_bytes,
        "secret_literal_count": len(secret_failures),
        "unsafe_command_count": len(unsafe_failures),
    }
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-resume-script",
        "readiness_summary": str(readiness_path),
        "script": str(script_path),
        "phase_count": phase_count,
        "command_count": command_count,
        "script_sha256": actual_sha256,
        "expected_script_sha256": expected_sha256,
        "summary_script_sha256": expected_summary_sha256,
        "script_matches_expected": actual_bytes == expected_bytes,
        "readiness_summary_verify": readiness_verify,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "run_metadata": _prediction_resume_script_verify_run_metadata(
            args,
            readiness_path=readiness_path,
            script_path=script_path,
            source_state=source_state,
        ),
    }
    return _attach_optional_verification_output(args, result)


def _prediction_resume_script_batches(
    readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    runtime = readiness.get("runtime")
    if not isinstance(runtime, dict):
        return []
    return [
        batch for batch in runtime.get("operator_resume_batches", []) if isinstance(batch, dict)
    ]


def _prediction_resume_script_batch_contract_issues(
    batches: list[Any],
) -> list[str]:
    issues: list[str] = []
    count_fields = {
        "requirement_count": "missing_requirements",
        "action_count": "actions",
        "command_count": "commands",
    }
    for index, batch in enumerate(batches):
        prefix = f"operator_resume_batches[{index}]"
        if not isinstance(batch, dict):
            issues.append(f"{prefix} must be an object")
            continue
        if not _is_plain_int(batch.get("phase")):
            issues.append(f"{prefix}.phase must be an integer")
        source_requirements = batch.get("source_requirements")
        if source_requirements is not None and not _prediction_resume_script_clean_string_list(
            source_requirements
        ):
            issues.append(f"{prefix}.source_requirements must be a non-empty string list")
        for count_key, list_key in count_fields.items():
            if count_key not in batch:
                continue
            count_raw = batch.get(count_key)
            if not _is_plain_int(count_raw):
                issues.append(f"{prefix}.{count_key} must be an integer")
                continue
            if not _is_non_negative_plain_int(count_raw):
                issues.append(f"{prefix}.{count_key} must be a non-negative integer")
                continue
            list_raw = batch.get(list_key)
            expected_count = len(list_raw) if isinstance(list_raw, list) else 0
            if count_raw != expected_count:
                issues.append(f"{prefix}.{count_key}_mismatch")
    return issues


def _prediction_resume_script_clean_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() == item and bool(item) for item in value)
    )


def _prediction_resume_script_readiness_verify_state(
    *,
    readiness_path: Path,
    verify_path_arg: Any,
    issues: list[str],
    quality_gate_failures: list[str],
) -> dict[str, Any]:
    if verify_path_arg is None:
        return {
            "present": False,
            "ok": False,
            "path": None,
            "artifact": None,
            "artifact_sha256": None,
        }
    verify_path = Path(verify_path_arg)
    try:
        payload = json.loads(verify_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"readiness_summary_verify_load_failed: {exc}")
        return {
            "present": True,
            "ok": False,
            "path": str(verify_path),
            "artifact": None,
            "artifact_sha256": None,
        }
    if not isinstance(payload, dict):
        issues.append("readiness_summary_verify_must_be_object")
        return {
            "present": True,
            "ok": False,
            "path": str(verify_path),
            "artifact": None,
            "artifact_sha256": None,
        }
    if payload.get("command") != "verify-prediction-offline-readiness-summary":
        issues.append("readiness_summary_verify_command_mismatch")
    recorded_artifact = payload.get("artifact")
    if isinstance(recorded_artifact, str) and Path(recorded_artifact) != readiness_path:
        issues.append("readiness_summary_verify_artifact_path_mismatch")
    actual_readiness_sha256 = hashlib.sha256(readiness_path.read_bytes()).hexdigest()
    recorded_sha256 = payload.get("artifact_sha256")
    if not isinstance(recorded_sha256, str) or not _is_sha256_hex(recorded_sha256):
        issues.append("readiness_summary_verify_artifact_sha256_invalid")
    elif recorded_sha256 != actual_readiness_sha256:
        issues.append("readiness_summary_verify_artifact_sha256_mismatch")
    if not bool(payload.get("ok")):
        quality_gate_failures.append("readiness_summary_verify_not_ok")
    return {
        "present": True,
        "ok": bool(payload.get("ok")),
        "path": str(verify_path),
        "artifact": recorded_artifact,
        "artifact_sha256": recorded_sha256,
    }


def _prediction_resume_script_missing_env_guard_failures(
    *,
    script_text: str,
    batches: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for batch in batches:
        phase = _plain_int_or_zero(batch.get("phase"))
        env_names = _prediction_resume_script_env_requirements(
            _string_list(batch.get("missing_requirements"))
        )
        for env_name in env_names:
            expected = f': "${{{env_name}:?Set {env_name} before phase {phase}}}"'
            if expected not in script_text:
                failures.append(f"missing_env_guard:phase_{phase}:{env_name}")
    return failures


def _prediction_resume_script_secret_literal_failures(script_text: str) -> list[str]:
    failures: list[str] = []
    assignment_pattern = re.compile(
        r"^\s*(OPENAI_API_KEY|OPENPACT_POSTGRES_DSN|OPENPACT_CONGRESS_API_KEY)="
        r"([^#\s].*)$"
    )
    for line in script_text.splitlines():
        assignment_match = assignment_pattern.match(line)
        if assignment_match is not None:
            failures.append(f"secret_literal:{assignment_match.group(1)}")
        elif "sk-" in line:
            failures.append("secret_literal:openai_token")
        elif re.search(r"\bpostgres(?:ql)?://\S+", line):
            failures.append("secret_literal:postgres_dsn")
    return sorted(set(failures))


def _prediction_resume_script_unsafe_command_failures(
    *,
    script_text: str,
    batches: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith("#")
            or line == "set -euo pipefail"
            or line == "#!/usr/bin/env bash"
            or line.startswith(': "${')
            or line == "# No commands for this phase."
        ):
            continue
        if line.startswith("python3 -m src.runtime.main "):
            continue
        failures.append(f"unsafe_command:{line}")
    for batch in batches:
        actions = _string_list(batch.get("actions"))
        for command in _string_list(batch.get("commands")):
            if not command.startswith("python3 -m src.runtime.main "):
                continue
            if any(
                _prediction_backfill_suggested_command_supported(
                    action=action,
                    command=command,
                )
                for action in actions
            ):
                continue
            action_label = ",".join(actions) if actions else "unknown"
            failures.append(f"unsupported_action_command:{action_label}:{command}")
    return failures


def _prediction_resume_script_verify_run_metadata(
    args: Any,
    *,
    readiness_path: Path,
    script_path: Path,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-resume-script",
        "verification_flags": {
            "require_env_guards": bool(getattr(args, "require_env_guards", False)),
            "readiness_summary_verify": getattr(
                args,
                "readiness_summary_verify",
                None,
            ),
            "require_no_secret_literals": bool(getattr(args, "require_no_secret_literals", False)),
            "require_safe_commands": bool(getattr(args, "require_safe_commands", False)),
        },
        "artifact_sha256": {
            "readiness_summary": hashlib.sha256(readiness_path.read_bytes()).hexdigest()
            if readiness_path.is_file()
            else None,
            "script": hashlib.sha256(script_path.read_bytes()).hexdigest()
            if script_path.is_file()
            else None,
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_resume_script_env_requirements(
    requirements: list[str],
) -> list[str]:
    return sorted(
        {
            requirement.removeprefix("env:")
            for requirement in requirements
            if requirement.startswith("env:") and requirement.removeprefix("env:")
        }
    )


def _handle_verify_prediction_operator_handoff(args: Any) -> dict[str, Any]:
    env_path = Path(args.env_preflight_verify)
    readiness_verify_path = Path(args.readiness_summary_verify)
    resume_verify_path = Path(args.resume_script_verify)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    env_verify = _load_operator_handoff_payload(
        env_path,
        label="env_preflight_verify",
        expected_command="verify-runtime-env-preflight",
        issues=issues,
    )
    readiness_verify = _load_operator_handoff_payload(
        readiness_verify_path,
        label="readiness_summary_verify",
        expected_command="verify-prediction-offline-readiness-summary",
        issues=issues,
    )
    resume_verify = _load_operator_handoff_payload(
        resume_verify_path,
        label="resume_script_verify",
        expected_command="verify-prediction-resume-script",
        issues=issues,
    )

    for label, payload in (
        ("env_preflight_verify", env_verify),
        ("readiness_summary_verify", readiness_verify),
        ("resume_script_verify", resume_verify),
    ):
        if payload and not bool(payload.get("ok")):
            quality_gate_failures.append(f"{label}_not_ok")

    readiness_summary = _operator_handoff_load_readiness_summary(
        readiness_verify,
        issues=issues,
    )
    readiness_source_matches = _operator_handoff_check_env_preflight_source(
        readiness_summary=readiness_summary,
        env_path=env_path,
        issues=issues,
    )
    resume_links_readiness = _operator_handoff_check_resume_links(
        resume_verify=resume_verify,
        readiness_verify_path=readiness_verify_path,
        readiness_verify=readiness_verify,
        issues=issues,
    )
    issues.extend(_operator_handoff_resume_batch_contract_issues(readiness_summary))

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_handoff_secret_literal_failures(
            [env_verify, readiness_verify, resume_verify, readiness_summary]
        )
        quality_gate_failures.extend(secret_failures)

    chain = {
        "env_preflight_verify_ok": bool(env_verify.get("ok")),
        "readiness_summary_verify_ok": bool(readiness_verify.get("ok")),
        "resume_script_verify_ok": bool(resume_verify.get("ok")),
        "readiness_source_env_preflight_verify_matches": readiness_source_matches,
        "resume_links_readiness_summary_verify": resume_links_readiness,
    }
    operator_plan = _operator_handoff_plan(readiness_summary)
    source_state = {
        "verifier_count": 3,
        "all_verifiers_ok": all(
            bool(payload.get("ok")) for payload in (env_verify, readiness_verify, resume_verify)
        ),
        "chain_issue_count": len(issues),
        "operator_resume_batch_count": operator_plan["operator_resume_batch_count"],
        "next_live_action_count": len(operator_plan["next_live_actions"]),
        "secret_literal_count": len(secret_failures),
    }
    eval_window_run = _prediction_readiness_eval_window_run_summary(
        operator_plan.get("eval_window_run")
    )
    source_state.update(_operator_eval_window_run_label_gate_source_state(eval_window_run))
    source_state.update(_operator_eval_window_run_archive_source_state(eval_window_run))
    source_state.update(_operator_congress_load_source_state(operator_plan.get("congress_load")))
    source_state.update(_operator_cutoff_audit_source_state(operator_plan.get("cutoff_audit")))
    source_state.update(
        _operator_source_url_audit_source_state(operator_plan.get("source_url_audit"))
    )
    artifact_sha256 = {
        "env_preflight_verify": _optional_file_sha256(env_path),
        "readiness_summary_verify": _optional_file_sha256(readiness_verify_path),
        "resume_script_verify": _optional_file_sha256(resume_verify_path),
    }
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-handoff",
        "env_preflight_verify": str(env_path),
        "readiness_summary_verify": str(readiness_verify_path),
        "resume_script_verify": str(resume_verify_path),
        "artifact_sha256": artifact_sha256,
        "chain": chain,
        "operator_plan": operator_plan,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "run_metadata": {
            "command": "verify-prediction-operator-handoff",
            "verification_flags": {
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": artifact_sha256,
            "source_state": source_state,
        },
    }
    runbook_output = (
        Path(args.runbook_output) if getattr(args, "runbook_output", None) is not None else None
    )
    if runbook_output is not None:
        output_arg = getattr(args, "output", None)
        if output_arg is not None:
            result["output"] = str(Path(output_arg))
        result["runbook_output"] = str(runbook_output)
        runbook_sha256 = _write_prediction_operator_handoff_runbook(
            runbook_output,
            result,
        )
        result["runbook_output_sha256"] = runbook_sha256
    return _attach_optional_verification_output(args, result)


def _load_operator_handoff_payload(
    path: Path,
    *,
    label: str,
    expected_command: str,
    issues: list[str],
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"{label}_load_failed: {exc}")
        return {}
    if not isinstance(payload, dict):
        issues.append(f"{label}_must_be_object")
        return {}
    if payload.get("command") != expected_command:
        issues.append(f"{label}_command_mismatch")
    return payload


def _operator_handoff_load_readiness_summary(
    readiness_verify: dict[str, Any],
    *,
    issues: list[str],
) -> dict[str, Any]:
    artifact = readiness_verify.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        issues.append("readiness_summary_artifact_missing")
        return {}
    path = Path(artifact)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"readiness_summary_load_failed: {exc}")
        return {}
    if not isinstance(payload, dict):
        issues.append("readiness_summary_must_be_object")
        return {}
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded_sha256 = readiness_verify.get("artifact_sha256")
    if not isinstance(recorded_sha256, str) or not _is_sha256_hex(recorded_sha256):
        issues.append("readiness_summary_verify_artifact_sha256_invalid")
    elif recorded_sha256 != actual_sha256:
        issues.append("readiness_summary_verify_artifact_sha256_mismatch")
    return payload


def _operator_handoff_check_env_preflight_source(
    *,
    readiness_summary: dict[str, Any],
    env_path: Path,
    issues: list[str],
) -> bool:
    source_artifacts = readiness_summary.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        issues.append("readiness_source_artifacts_missing")
        return False
    env_source = source_artifacts.get("env_preflight_verify")
    if not isinstance(env_source, dict):
        issues.append("readiness_env_preflight_verify_source_missing")
        return False
    path_raw = env_source.get("path")
    if path_raw is not None and Path(str(path_raw)) != env_path:
        issues.append("readiness_env_preflight_verify_path_mismatch")
    actual_sha256 = _optional_file_sha256(env_path)
    recorded_sha256 = env_source.get("sha256")
    if not isinstance(recorded_sha256, str) or not _is_sha256_hex(recorded_sha256):
        issues.append("readiness_env_preflight_verify_sha256_invalid")
        return False
    if recorded_sha256 != actual_sha256:
        issues.append("readiness_env_preflight_verify_sha256_mismatch")
        return False
    return path_raw is None or Path(str(path_raw)) == env_path


def _operator_handoff_check_resume_links(
    *,
    resume_verify: dict[str, Any],
    readiness_verify_path: Path,
    readiness_verify: dict[str, Any],
    issues: list[str],
) -> bool:
    link = resume_verify.get("readiness_summary_verify")
    if not isinstance(link, dict):
        issues.append("resume_readiness_summary_verify_missing")
        return False
    matches = True
    if Path(str(link.get("path"))) != readiness_verify_path:
        issues.append("resume_readiness_summary_verify_path_mismatch")
        matches = False
    if link.get("artifact") != readiness_verify.get("artifact"):
        issues.append("resume_readiness_summary_verify_artifact_mismatch")
        matches = False
    link_sha256 = link.get("artifact_sha256")
    readiness_sha256 = readiness_verify.get("artifact_sha256")
    if not isinstance(link_sha256, str) or not _is_sha256_hex(link_sha256):
        issues.append("resume_readiness_summary_verify_sha256_invalid")
        matches = False
    elif not isinstance(readiness_sha256, str) or not _is_sha256_hex(readiness_sha256):
        issues.append("readiness_summary_verify_artifact_sha256_invalid")
        matches = False
    elif link_sha256 != readiness_sha256:
        issues.append("resume_readiness_summary_verify_sha256_mismatch")
        matches = False
    if not bool(link.get("ok")):
        issues.append("resume_readiness_summary_verify_not_ok")
        matches = False
    return matches


def _operator_handoff_plan(readiness_summary: dict[str, Any]) -> dict[str, Any]:
    env_preflight = (
        readiness_summary.get("env_preflight")
        if isinstance(readiness_summary.get("env_preflight"), dict)
        else {}
    )
    runtime = (
        readiness_summary.get("runtime")
        if isinstance(readiness_summary.get("runtime"), dict)
        else {}
    )
    resume_batches = [
        batch for batch in runtime.get("operator_resume_batches", []) if isinstance(batch, dict)
    ]
    congress_load = _operator_handoff_congress_load(readiness_summary)
    cutoff_audit = _operator_handoff_cutoff_audit(readiness_summary)
    source_url_audit = _operator_handoff_source_url_audit(readiness_summary)
    bill_sponsor_availability = _operator_handoff_bill_sponsor_availability(readiness_summary)
    jurisdiction_topology = _operator_handoff_jurisdiction_topology(readiness_summary)
    plan = {
        "missing_env": _string_list(env_preflight.get("missing_env")),
        "env_next_actions": _string_list(env_preflight.get("next_actions")),
        "next_live_actions": _string_list(readiness_summary.get("next_live_actions")),
        "next_actions_by_kind": _operator_handoff_next_actions_by_kind(
            readiness_summary.get("next_actions_by_kind")
        ),
        "eval_window_run": _operator_handoff_eval_window_run(readiness_summary),
        "operator_resume_batch_count": len(resume_batches),
        "operator_resume_batches": [
            _with_prediction_readiness_provenance(
                {
                    "phase": _plain_int_or_zero(batch.get("phase")),
                    "missing_requirements": _string_list(batch.get("missing_requirements")),
                    "actions": _string_list(batch.get("actions")),
                    "commands": _string_list(batch.get("commands")),
                },
                batch,
            )
            for batch in resume_batches
        ],
    }
    if congress_load:
        plan["congress_load"] = congress_load
    if cutoff_audit:
        plan["cutoff_audit"] = cutoff_audit
    if source_url_audit:
        plan["source_url_audit"] = source_url_audit
    if bill_sponsor_availability:
        plan["bill_sponsor_availability"] = bill_sponsor_availability
    if jurisdiction_topology:
        plan["jurisdiction_topology"] = jurisdiction_topology
    return plan


def _operator_handoff_congress_load(readiness_summary: dict[str, Any]) -> dict[str, Any]:
    local_inputs = (
        readiness_summary.get("local_inputs")
        if isinstance(readiness_summary.get("local_inputs"), dict)
        else {}
    )
    congress_load = (
        local_inputs.get("congress_load")
        if isinstance(local_inputs.get("congress_load"), dict)
        else {}
    )
    required = bool(readiness_summary.get("congress_load_required"))
    if not required and not congress_load:
        return {}
    source_state = (
        congress_load.get("source_state")
        if isinstance(congress_load.get("source_state"), dict)
        else {}
    )
    summary = {
        "required": required,
        "present": bool(congress_load.get("present")),
        "ok": bool(congress_load.get("ok")),
        "blockers": [
            blocker
            for blocker in _string_list(readiness_summary.get("blockers"))
            if blocker.startswith("local_input:congress_load")
        ],
    }
    for key in (
        "prediction_member_inputs_available",
        "prediction_bill_inputs_available",
        "prediction_vote_inputs_available",
    ):
        value = source_state.get(key)
        if isinstance(value, bool):
            summary[key] = value
    source_family_ids = _sorted_string_list_or_empty(source_state.get("source_family_ids"))
    if source_family_ids and _source_family_id_list(source_family_ids):
        summary["source_family_ids"] = source_family_ids
    return summary


def _operator_handoff_eval_window_run(readiness_summary: dict[str, Any]) -> dict[str, Any]:
    benchmark = (
        readiness_summary.get("benchmark")
        if isinstance(readiness_summary.get("benchmark"), dict)
        else {}
    )
    return _prediction_readiness_eval_window_run_summary(benchmark.get("eval_window_run"))


def _operator_handoff_cutoff_audit(readiness_summary: dict[str, Any]) -> dict[str, int]:
    run_metadata = (
        readiness_summary.get("run_metadata")
        if isinstance(readiness_summary.get("run_metadata"), dict)
        else {}
    )
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    return {
        key.removeprefix("benchmark_eval_cutoff_"): value
        for key, value in source_state.items()
        if key.startswith("benchmark_eval_cutoff_") and _is_non_negative_plain_int(value)
    }


def _operator_handoff_source_url_audit(readiness_summary: dict[str, Any]) -> dict[str, Any]:
    run_metadata = (
        readiness_summary.get("run_metadata")
        if isinstance(readiness_summary.get("run_metadata"), dict)
        else {}
    )
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    summary: dict[str, Any] = {}
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        value = source_state.get(key)
        if _is_non_negative_plain_int(value):
            summary[key] = value
    failures = _sorted_string_list_or_empty(
        source_state.get("source_url_audit_quality_gate_failures")
    )
    if failures:
        summary["source_url_audit_quality_gate_failures"] = failures
    return summary


def _operator_handoff_bill_sponsor_availability(
    readiness_summary: dict[str, Any],
) -> dict[str, int | float]:
    run_metadata = (
        readiness_summary.get("run_metadata")
        if isinstance(readiness_summary.get("run_metadata"), dict)
        else {}
    )
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    total = source_state.get("inventory_bill_sponsor_count")
    available = source_state.get("inventory_bill_available_sponsor_count")
    rate = source_state.get("inventory_bill_sponsor_availability_rate")
    fallback = source_state.get("inventory_bill_primary_sponsor_introduced_date_fallback_count")
    if not _is_non_negative_plain_int(total) or not _is_non_negative_plain_int(available):
        return {}
    if available > total:
        return {}
    summary: dict[str, int | float] = {
        "total": total,
        "available": available,
    }
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= float(rate) <= 1:
        summary["rate"] = float(rate)
    if _is_non_negative_plain_int(fallback):
        summary["primary_introduced_date_fallback_count"] = fallback
    return summary


def _operator_handoff_jurisdiction_topology(readiness_summary: dict[str, Any]) -> dict[str, Any]:
    benchmark = (
        readiness_summary.get("benchmark")
        if isinstance(readiness_summary.get("benchmark"), dict)
        else {}
    )
    source_state = (
        benchmark.get("run_metadata", {}).get("source_state")
        if isinstance(benchmark.get("run_metadata"), dict)
        and isinstance(benchmark.get("run_metadata", {}).get("source_state"), dict)
        else {}
    )
    summary: dict[str, Any] = {}
    count_keys = (
        "jurisdiction_count",
        "implemented_jurisdiction_count",
        "portable_jurisdiction_count",
        "legislative_body_count",
        "legislative_session_count",
    )
    for key in count_keys:
        value = benchmark.get(key)
        if not _is_non_negative_plain_int(value):
            value = source_state.get(key)
        if _is_non_negative_plain_int(value):
            summary[key] = value
    list_keys = (
        "jurisdiction_ids",
        "implemented_jurisdiction_ids",
        "portable_jurisdiction_ids",
        "legislative_body_ids",
        "legislative_session_ids",
        "source_family_ids",
    )
    for key in list_keys:
        values = _sorted_string_list_or_empty(benchmark.get(key))
        if not values:
            values = _sorted_string_list_or_empty(source_state.get(key))
        if values and (key != "source_family_ids" or _source_family_id_list(values)):
            summary[key] = values
    return summary


def _operator_handoff_next_actions_by_kind(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(name): _string_list(actions) for name, actions in value.items()}


def _operator_handoff_resume_batch_contract_issues(
    readiness_summary: dict[str, Any],
) -> list[str]:
    runtime = readiness_summary.get("runtime")
    if not isinstance(runtime, dict):
        return []
    raw_batches = runtime.get("operator_resume_batches")
    if not isinstance(raw_batches, list):
        return []
    return _prediction_resume_script_batch_contract_issues(raw_batches)


def _write_prediction_operator_handoff_runbook(
    path: Path,
    result: dict[str, Any],
) -> str:
    content = _prediction_operator_handoff_runbook_text(result)
    return _write_text_artifact(path, content)


def _handle_verify_prediction_operator_runbook(args: Any) -> dict[str, Any]:
    handoff_path = Path(args.handoff_verify)
    runbook_path = Path(args.runbook)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    handoff = _load_operator_handoff_payload(
        handoff_path,
        label="handoff_verify",
        expected_command="verify-prediction-operator-handoff",
        issues=issues,
    )
    try:
        runbook_text = runbook_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"runbook_load_failed: {exc}")
        runbook_text = ""

    expected_text = _prediction_operator_handoff_runbook_text(handoff)
    expected_sha256 = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
    runbook_sha256 = hashlib.sha256(runbook_text.encode("utf-8")).hexdigest()
    runbook_matches_expected = runbook_text == expected_text
    if not runbook_matches_expected:
        quality_gate_failures.append("runbook_content_mismatch")

    handoff_runbook_output = handoff.get("runbook_output")
    if isinstance(handoff_runbook_output, str) and handoff_runbook_output:
        if Path(handoff_runbook_output) != runbook_path:
            issues.append("runbook_output_path_mismatch")

    handoff_runbook_sha256 = handoff.get("runbook_output_sha256")
    handoff_runbook_sha256_matches = handoff_runbook_sha256 == runbook_sha256
    if (
        bool(getattr(args, "require_handoff_runbook_sha", False))
        and not handoff_runbook_sha256_matches
    ):
        quality_gate_failures.append("runbook_sha256_mismatch")

    verified_artifact_missing: list[str] = []
    if bool(getattr(args, "require_verified_artifact_hashes", False)):
        verified_artifact_missing = _operator_runbook_verified_artifact_missing(
            handoff,
            runbook_text,
        )
        quality_gate_failures.extend(verified_artifact_missing)

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_runbook_secret_literal_failures(runbook_text)
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "runbook_matches_expected": runbook_matches_expected,
        "verified_artifact_count": 3,
        "verified_artifact_missing_count": len(verified_artifact_missing),
        "secret_literal_count": len(secret_failures),
    }
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-runbook",
        "handoff_verify": str(handoff_path),
        "runbook": str(runbook_path),
        "handoff_sha256": _optional_file_sha256(handoff_path),
        "runbook_sha256": runbook_sha256,
        "expected_runbook_sha256": expected_sha256,
        "handoff_runbook_sha256": handoff_runbook_sha256,
        "handoff_runbook_sha256_matches": handoff_runbook_sha256_matches,
        "runbook_matches_expected": runbook_matches_expected,
        "verified_artifact_missing": verified_artifact_missing,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-runbook",
            "verification_flags": {
                "require_handoff_runbook_sha": bool(
                    getattr(args, "require_handoff_runbook_sha", False)
                ),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
                "require_verified_artifact_hashes": bool(
                    getattr(args, "require_verified_artifact_hashes", False)
                ),
            },
            "source_artifact_sha256": {
                "handoff_verify": _optional_file_sha256(handoff_path),
                "runbook": runbook_sha256,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_prediction_operator_status(args: Any) -> dict[str, Any]:
    runbook_verify_path = Path(args.runbook_verify)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    runbook_verify = _load_operator_handoff_payload(
        runbook_verify_path,
        label="runbook_verify",
        expected_command="verify-prediction-operator-runbook",
        issues=issues,
    )
    if runbook_verify and not bool(runbook_verify.get("ok")):
        quality_gate_failures.append("runbook_verify_not_ok")

    handoff_path_raw = runbook_verify.get("handoff_verify")
    handoff_path = Path(str(handoff_path_raw)) if handoff_path_raw else Path()
    if not isinstance(handoff_path_raw, str) or not handoff_path_raw:
        issues.append("handoff_verify_missing")
        handoff: dict[str, Any] = {}
    else:
        handoff = _load_operator_handoff_payload(
            handoff_path,
            label="handoff_verify",
            expected_command="verify-prediction-operator-handoff",
            issues=issues,
        )
        actual_handoff_sha256 = _optional_file_sha256(handoff_path)
        recorded_handoff_sha256 = runbook_verify.get("handoff_sha256")
        if recorded_handoff_sha256 is not None:
            if not isinstance(recorded_handoff_sha256, str) or not _is_sha256_hex(
                recorded_handoff_sha256
            ):
                issues.append("runbook_handoff_sha256_invalid")
            elif recorded_handoff_sha256 != actual_handoff_sha256:
                issues.append("runbook_handoff_sha256_mismatch")
    if handoff and not bool(handoff.get("ok")):
        quality_gate_failures.append("handoff_verify_not_ok")

    readiness_verify_path_raw = handoff.get("readiness_summary_verify")
    readiness_verify_path = (
        Path(str(readiness_verify_path_raw)) if readiness_verify_path_raw else Path()
    )
    if not isinstance(readiness_verify_path_raw, str) or not readiness_verify_path_raw:
        issues.append("readiness_summary_verify_missing")
        readiness_verify: dict[str, Any] = {}
    else:
        readiness_verify = _load_operator_handoff_payload(
            readiness_verify_path,
            label="readiness_summary_verify",
            expected_command="verify-prediction-offline-readiness-summary",
            issues=issues,
        )
    if readiness_verify and not bool(readiness_verify.get("ok")):
        quality_gate_failures.append("readiness_summary_verify_not_ok")

    readiness_summary = _operator_handoff_load_readiness_summary(
        readiness_verify,
        issues=issues,
    )
    plan = (
        _operator_handoff_plan(readiness_summary)
        if readiness_summary
        else _operator_status_plan_from_handoff(handoff)
    )
    blocker_count_raw = readiness_summary.get("blocker_count")
    if blocker_count_raw is not None and not _is_plain_int(blocker_count_raw):
        issues.append("readiness_summary.blocker_count must be an integer")
    elif blocker_count_raw is not None and not _is_non_negative_plain_int(blocker_count_raw):
        issues.append("readiness_summary.blocker_count must be a non-negative integer")
    blocker_count = blocker_count_raw if _is_non_negative_plain_int(blocker_count_raw) else 0
    launch_ready = (
        bool(readiness_summary.get("ok")) and blocker_count == 0 and not plan["missing_env"]
    )
    if bool(getattr(args, "require_launch_ready", False)) and not launch_ready:
        quality_gate_failures.append("launch_not_ready")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_handoff_secret_literal_failures(
            [runbook_verify, handoff, readiness_verify, readiness_summary]
        )
        quality_gate_failures.extend(secret_failures)

    verification_ok = (
        bool(runbook_verify.get("ok"))
        and bool(handoff.get("ok"))
        and bool(readiness_verify.get("ok"))
        and not issues
        and not [failure for failure in quality_gate_failures if failure != "launch_not_ready"]
    )
    source_state = {
        "verification_ok": verification_ok,
        "launch_ready": launch_ready,
        "missing_env_count": len(plan["missing_env"]),
        "blocker_count": blocker_count,
        "operator_resume_batch_count": plan["operator_resume_batch_count"],
        "next_live_action_count": len(plan["next_live_actions"]),
        "secret_literal_count": len(secret_failures),
    }
    eval_window_run = _prediction_readiness_eval_window_run_summary(plan.get("eval_window_run"))
    source_state.update(_operator_eval_window_run_label_gate_source_state(eval_window_run))
    source_state.update(_operator_eval_window_run_archive_source_state(eval_window_run))
    source_state.update(_operator_congress_load_source_state(plan.get("congress_load")))
    source_state.update(
        _operator_bill_sponsor_availability_source_state(plan.get("bill_sponsor_availability"))
    )
    source_state.update(
        _operator_jurisdiction_topology_source_state(plan.get("jurisdiction_topology"))
    )
    source_state.update(_operator_cutoff_audit_source_state(plan.get("cutoff_audit")))
    source_state.update(_operator_source_url_audit_source_state(plan.get("source_url_audit")))
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "prediction-operator-status",
        "status": "ready" if launch_ready else "blocked",
        "verification_ok": verification_ok,
        "launch_ready": launch_ready,
        "runbook_verify": str(runbook_verify_path),
        "handoff_verify": str(handoff_path) if handoff_path_raw else None,
        "readiness_summary_verify": (
            str(readiness_verify_path) if readiness_verify_path_raw else None
        ),
        "artifact_sha256": {
            "runbook_verify": _optional_file_sha256(runbook_verify_path),
            "handoff_verify": (_optional_file_sha256(handoff_path) if handoff_path_raw else None),
            "readiness_summary_verify": (
                _optional_file_sha256(readiness_verify_path) if readiness_verify_path_raw else None
            ),
        },
        "verified_chain": {
            "runbook_verify_ok": bool(runbook_verify.get("ok")),
            "handoff_verify_ok": bool(handoff.get("ok")),
            "readiness_summary_verify_ok": bool(readiness_verify.get("ok")),
            "readiness_summary_ok": bool(readiness_summary.get("ok")),
        },
        "missing_env": plan["missing_env"],
        "env_next_actions": plan["env_next_actions"],
        "next_live_actions": plan["next_live_actions"],
        "next_actions_by_kind": plan["next_actions_by_kind"],
        "eval_window_run": eval_window_run,
        "operator_resume_batch_count": plan["operator_resume_batch_count"],
        "operator_resume_batches": plan["operator_resume_batches"],
        "blocker_count": blocker_count,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "prediction-operator-status",
            "verification_flags": {
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
                "require_launch_ready": bool(getattr(args, "require_launch_ready", False)),
            },
            "source_artifact_sha256": {
                "runbook_verify": _optional_file_sha256(runbook_verify_path),
                "handoff_verify": (
                    _optional_file_sha256(handoff_path) if handoff_path_raw else None
                ),
                "readiness_summary_verify": (
                    _optional_file_sha256(readiness_verify_path)
                    if readiness_verify_path_raw
                    else None
                ),
            },
            "source_state": source_state,
        },
    }
    if plan.get("congress_load"):
        result["congress_load"] = plan["congress_load"]
    if plan.get("cutoff_audit"):
        result["cutoff_audit"] = plan["cutoff_audit"]
    if plan.get("source_url_audit"):
        result["source_url_audit"] = plan["source_url_audit"]
    if plan.get("bill_sponsor_availability"):
        result["bill_sponsor_availability"] = plan["bill_sponsor_availability"]
    if plan.get("jurisdiction_topology"):
        result["jurisdiction_topology"] = plan["jurisdiction_topology"]
    return _attach_optional_verification_output(args, result)


def _handle_verify_prediction_operator_status(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    try:
        status = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        status = {}
        issues.append(f"artifact_load_failed: {exc}")
    if not isinstance(status, dict):
        status = {}
        issues.append("artifact_must_be_object")
    if status.get("command") != "prediction-operator-status":
        issues.append("artifact_command_mismatch")

    runbook_verify_raw = getattr(args, "runbook_verify", None) or status.get("runbook_verify")
    if not isinstance(runbook_verify_raw, str) or not runbook_verify_raw:
        issues.append("runbook_verify_missing")
        runbook_verify_path = Path()
    else:
        runbook_verify_path = Path(runbook_verify_raw)
        if (
            getattr(args, "runbook_verify", None) is not None
            and status.get("runbook_verify") is not None
            and Path(str(status.get("runbook_verify"))) != runbook_verify_path
        ):
            issues.append("runbook_verify_path_mismatch")

    run_metadata = (
        status.get("run_metadata") if isinstance(status.get("run_metadata"), dict) else {}
    )
    if bool(getattr(args, "require_run_metadata", False)) and not run_metadata:
        quality_gate_failures.append("run_metadata_missing")
    elif run_metadata and run_metadata.get("command") != "prediction-operator-status":
        issues.append("run_metadata_command_mismatch")
    issues.extend(_operator_status_count_issues(status))
    status_metadata_source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    if run_metadata:
        source_state_sample_scope_issues = _operator_status_sample_source_state_issues(
            status_metadata_source_state
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_status_source_state_count_issues(status_metadata_source_state)
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_source_url_audit_source_state_issues(
                status_metadata_source_state
            )
        )
        issues.extend(
            f"run_metadata.source_state.{issue}" for issue in source_state_sample_scope_issues
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_status_source_state_mismatch_issues(
                status,
                status_metadata_source_state,
                allow_optional_sample_scope=True,
            )
        )

    recorded_source_sha = (
        run_metadata.get("source_artifact_sha256")
        if isinstance(run_metadata.get("source_artifact_sha256"), dict)
        else {}
    )
    status_artifact_sha = (
        status.get("artifact_sha256") if isinstance(status.get("artifact_sha256"), dict) else {}
    )
    actual_runbook_verify_sha = (
        _optional_file_sha256(runbook_verify_path) if runbook_verify_raw else None
    )
    source_artifact_mismatches: list[str] = []
    source_artifact_invalid: list[str] = []
    for source_name, recorded in (
        ("artifact", status_artifact_sha.get("runbook_verify")),
        ("run_metadata", recorded_source_sha.get("runbook_verify")),
    ):
        if recorded is None:
            continue
        source_key = f"{source_name}:runbook_verify"
        if not isinstance(recorded, str) or not _is_sha256_hex(recorded):
            source_artifact_invalid.append(source_key)
            issues.append(f"source_artifact_sha256_invalid:{source_key}")
            continue
        if recorded != actual_runbook_verify_sha:
            source_artifact_mismatches.append(source_name)
            issues.append("source_artifact_sha256_mismatch:runbook_verify")
            break

    status_flags = (
        run_metadata.get("verification_flags")
        if isinstance(run_metadata.get("verification_flags"), dict)
        else {}
    )
    recomputed_status = (
        _handle_prediction_operator_status(
            SimpleNamespace(
                command="prediction-operator-status",
                runbook_verify=str(runbook_verify_path),
                require_no_secret_literals=bool(
                    status_flags.get("require_no_secret_literals", False)
                ),
                require_launch_ready=bool(status_flags.get("require_launch_ready", False)),
                output=None,
            )
        )
        if runbook_verify_raw
        else {}
    )
    status_matches_recomputed = _operator_status_core(status) == _operator_status_core(
        recomputed_status
    )
    if not status_matches_recomputed:
        quality_gate_failures.append("status_content_mismatch")

    launch_ready = bool(recomputed_status.get("launch_ready"))
    if bool(getattr(args, "require_launch_ready", False)) and not launch_ready:
        quality_gate_failures.append("launch_not_ready")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_handoff_secret_literal_failures([status, recomputed_status])
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "status_matches_recomputed": status_matches_recomputed,
        "source_artifact_mismatch_count": len(source_artifact_mismatches),
        "source_artifact_invalid_count": len(source_artifact_invalid),
        "secret_literal_count": len(secret_failures),
        "launch_ready": launch_ready,
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(status_metadata_source_state)
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-status",
        "artifact": str(artifact_path),
        "artifact_sha256": _optional_file_sha256(artifact_path),
        "runbook_verify": str(runbook_verify_path) if runbook_verify_raw else None,
        "runbook_verify_sha256": actual_runbook_verify_sha,
        "status_matches_recomputed": status_matches_recomputed,
        "source_artifact_invalid": sorted(source_artifact_invalid),
        "source_artifact_invalid_count": len(source_artifact_invalid),
        "source_artifact_mismatch_count": len(source_artifact_mismatches),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-status",
            "verification_flags": {
                "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
                "require_launch_ready": bool(getattr(args, "require_launch_ready", False)),
            },
            "source_artifact_sha256": {
                "artifact": _optional_file_sha256(artifact_path),
                "runbook_verify": actual_runbook_verify_sha,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_prediction_operator_packet_manifest(args: Any) -> dict[str, Any]:
    status_verify_path = Path(args.status_verify)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    status_verify = _load_operator_handoff_payload(
        status_verify_path,
        label="status_verify",
        expected_command="verify-prediction-operator-status",
        issues=issues,
    )
    if status_verify and not bool(status_verify.get("ok")):
        quality_gate_failures.append("status_verify_not_ok")

    status_path = _path_from_payload(status_verify, "artifact")
    status = _load_json_packet_payload(status_path, "operator_status", issues)
    runbook_verify_path = _path_from_payload(status, "runbook_verify") or _path_from_payload(
        status_verify,
        "runbook_verify",
    )
    runbook_verify = _load_json_packet_payload(
        runbook_verify_path,
        "operator_runbook_verify",
        issues,
    )
    handoff_verify_path = _path_from_payload(status, "handoff_verify") or _path_from_payload(
        runbook_verify,
        "handoff_verify",
    )
    handoff_verify = _load_json_packet_payload(
        handoff_verify_path,
        "operator_handoff_verify",
        issues,
    )
    readiness_verify_path = _path_from_payload(
        status,
        "readiness_summary_verify",
    ) or _path_from_payload(handoff_verify, "readiness_summary_verify")
    readiness_verify = _load_json_packet_payload(
        readiness_verify_path,
        "readiness_summary_verify",
        issues,
    )
    readiness_path = _path_from_payload(readiness_verify, "artifact")
    readiness_summary = _load_json_packet_payload(
        readiness_path,
        "readiness_summary",
        issues,
    )
    resume_verify_path = _path_from_payload(handoff_verify, "resume_script_verify")
    resume_verify = _load_json_packet_payload(
        resume_verify_path,
        "resume_script_verify",
        issues,
        required=False,
    )
    env_verify_path = _path_from_payload(handoff_verify, "env_preflight_verify")
    env_verify = _load_json_packet_payload(
        env_verify_path,
        "runtime_env_preflight_verify",
        issues,
        required=False,
    )

    packet_files: list[dict[str, Any]] = []
    _operator_packet_add_file(packet_files, "operator_status_verify", status_verify_path)
    _operator_packet_add_file(packet_files, "operator_status", status_path)
    _operator_packet_add_file(packet_files, "operator_runbook_verify", runbook_verify_path)
    _operator_packet_add_file(packet_files, "operator_handoff_verify", handoff_verify_path)
    _operator_packet_add_file(
        packet_files,
        "operator_runbook",
        _path_from_payload(runbook_verify, "runbook")
        or _path_from_payload(handoff_verify, "runbook_output"),
    )
    _operator_packet_add_file(packet_files, "readiness_summary_verify", readiness_verify_path)
    _operator_packet_add_file(packet_files, "readiness_summary", readiness_path)
    _operator_packet_add_file(packet_files, "resume_script_verify", resume_verify_path)
    _operator_packet_add_file(
        packet_files,
        "resume_script",
        _path_from_payload(resume_verify, "script"),
    )
    _operator_packet_add_file(packet_files, "runtime_env_preflight_verify", env_verify_path)
    _operator_packet_add_file(
        packet_files,
        "runtime_env_preflight",
        _path_from_payload(env_verify, "artifact"),
    )
    _operator_packet_add_file(
        packet_files,
        "runtime_env_template",
        _path_from_payload(env_verify, "template_output"),
    )
    source_artifacts = (
        readiness_summary.get("source_artifacts")
        if isinstance(readiness_summary.get("source_artifacts"), dict)
        else {}
    )
    for name, payload in sorted(source_artifacts.items()):
        if isinstance(payload, dict):
            _operator_packet_add_file(
                packet_files,
                f"readiness_source:{name}",
                _path_from_payload(payload, "path"),
            )

    deduped_files = _operator_packet_dedupe_files(packet_files)
    missing_files = [entry["name"] for entry in deduped_files if not bool(entry["present"])]
    if bool(getattr(args, "require_existing_files", False)) and missing_files:
        quality_gate_failures.extend(f"packet_file_missing:{name}" for name in missing_files)

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_packet_secret_literal_failures(deduped_files)
        quality_gate_failures.extend(secret_failures)

    status_blocker_count_raw = status.get("blocker_count")
    if status_blocker_count_raw is not None and not _is_plain_int(status_blocker_count_raw):
        issues.append("status.blocker_count must be an integer")
    elif status_blocker_count_raw is not None and not _is_non_negative_plain_int(
        status_blocker_count_raw
    ):
        issues.append("status.blocker_count must be a non-negative integer")
    status_blocker_count = (
        status_blocker_count_raw if _is_non_negative_plain_int(status_blocker_count_raw) else 0
    )
    source_state = {
        "packet_file_count": len(deduped_files),
        "missing_file_count": len(missing_files),
        "secret_literal_count": len(secret_failures),
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(
            status_verify.get("run_metadata", {}).get("source_state", {})
            if isinstance(status_verify.get("run_metadata"), dict)
            else {}
        )
    )

    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "prediction-operator-packet-manifest",
        "status_verify": str(status_verify_path),
        "status": status.get("status"),
        "verification_ok": bool(status.get("verification_ok")),
        "launch_ready": bool(status.get("launch_ready")),
        "missing_env": _string_list(status.get("missing_env")),
        "blocker_count": status_blocker_count,
        "packet_files": deduped_files,
        "packet_file_count": len(deduped_files),
        "missing_files": missing_files,
        "missing_file_count": len(missing_files),
        "secret_literal_count": len(secret_failures),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "prediction-operator-packet-manifest",
            "verification_flags": {
                "require_existing_files": bool(getattr(args, "require_existing_files", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": {
                "status_verify": _optional_file_sha256(status_verify_path),
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_verify_prediction_operator_packet_manifest(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    try:
        manifest = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        manifest = {}
        issues.append(f"artifact_load_failed: {exc}")
    if not isinstance(manifest, dict):
        manifest = {}
        issues.append("artifact_must_be_object")
    if manifest.get("command") != "prediction-operator-packet-manifest":
        issues.append("artifact_command_mismatch")

    run_metadata = (
        manifest.get("run_metadata") if isinstance(manifest.get("run_metadata"), dict) else {}
    )
    if bool(getattr(args, "require_run_metadata", False)) and not run_metadata:
        quality_gate_failures.append("run_metadata_missing")
    issues.extend(_operator_packet_manifest_count_issues(manifest))
    manifest_metadata_source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    if run_metadata:
        source_state_sample_scope_issues = _operator_status_sample_source_state_issues(
            manifest_metadata_source_state
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_packet_manifest_source_state_count_issues(
                manifest_metadata_source_state
            )
        )
        issues.extend(
            f"run_metadata.source_state.{issue}" for issue in source_state_sample_scope_issues
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_packet_manifest_source_state_mismatch_issues(
                manifest,
                manifest_metadata_source_state,
                allow_optional_sample_scope=True,
            )
        )

    status_verify_raw = getattr(args, "status_verify", None) or manifest.get("status_verify")
    if not isinstance(status_verify_raw, str) or not status_verify_raw:
        issues.append("status_verify_missing")
        status_verify_path = Path()
    else:
        status_verify_path = Path(status_verify_raw)
    if (
        getattr(args, "status_verify", None) is not None
        and manifest.get("status_verify") is not None
        and Path(str(manifest.get("status_verify"))) != status_verify_path
    ):
        issues.append("status_verify_path_mismatch")

    recorded_source_sha = (
        run_metadata.get("source_artifact_sha256")
        if isinstance(run_metadata.get("source_artifact_sha256"), dict)
        else {}
    )
    actual_status_verify_sha = (
        _optional_file_sha256(status_verify_path) if status_verify_raw else None
    )
    source_artifact_invalid: list[str] = []
    source_artifact_mismatches: list[str] = []
    if run_metadata and "status_verify" in recorded_source_sha:
        recorded_status_verify_sha = recorded_source_sha.get("status_verify")
        if not isinstance(recorded_status_verify_sha, str) or not _is_sha256_hex(
            recorded_status_verify_sha
        ):
            source_artifact_invalid.append("status_verify")
            issues.append("source_artifact_sha256_invalid:status_verify")
        elif recorded_status_verify_sha != actual_status_verify_sha:
            source_artifact_mismatches.append("status_verify")
            issues.append("source_artifact_sha256_mismatch:status_verify")

    flags = (
        run_metadata.get("verification_flags")
        if isinstance(run_metadata.get("verification_flags"), dict)
        else {}
    )
    recomputed_manifest = (
        _handle_prediction_operator_packet_manifest(
            SimpleNamespace(
                command="prediction-operator-packet-manifest",
                status_verify=str(status_verify_path),
                require_existing_files=bool(flags.get("require_existing_files", False)),
                require_no_secret_literals=bool(flags.get("require_no_secret_literals", False)),
                output=None,
            )
        )
        if status_verify_raw
        else {}
    )
    manifest_matches_recomputed = _operator_packet_manifest_core(
        manifest
    ) == _operator_packet_manifest_core(recomputed_manifest)
    if not manifest_matches_recomputed:
        quality_gate_failures.append("packet_manifest_content_mismatch")

    packet_file_sha256_invalid, packet_file_sha256_mismatches = _operator_packet_file_sha256_state(
        manifest.get("packet_files")
    )
    if packet_file_sha256_invalid:
        issues.extend(f"packet_file_sha256_invalid:{name}" for name in packet_file_sha256_invalid)
    if packet_file_sha256_mismatches:
        issues.append("packet_file_sha256_mismatch")

    recomputed_missing_file_count_raw = recomputed_manifest.get("missing_file_count")
    if recomputed_missing_file_count_raw is not None and not _is_plain_int(
        recomputed_missing_file_count_raw
    ):
        issues.append("recomputed_manifest.missing_file_count must be an integer")
    missing_file_count = _plain_int_or_zero(recomputed_missing_file_count_raw)
    if bool(getattr(args, "require_existing_files", False)) and missing_file_count:
        quality_gate_failures.append("packet_files_missing")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        packet_files = (
            recomputed_manifest.get("packet_files")
            if isinstance(recomputed_manifest.get("packet_files"), list)
            else []
        )
        secret_failures = _operator_packet_secret_literal_failures(packet_files)
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "manifest_matches_recomputed": manifest_matches_recomputed,
        "source_artifact_invalid_count": len(source_artifact_invalid),
        "source_artifact_mismatch_count": len(source_artifact_mismatches),
        "packet_file_sha256_invalid_count": len(packet_file_sha256_invalid),
        "packet_file_sha256_mismatch_count": len(packet_file_sha256_mismatches),
        "missing_file_count": missing_file_count,
        "secret_literal_count": len(secret_failures),
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(manifest_metadata_source_state)
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-packet-manifest",
        "artifact": str(artifact_path),
        "artifact_sha256": _optional_file_sha256(artifact_path),
        "status_verify": str(status_verify_path) if status_verify_raw else None,
        "status_verify_sha256": actual_status_verify_sha,
        "manifest_matches_recomputed": manifest_matches_recomputed,
        "source_artifact_invalid": sorted(source_artifact_invalid),
        "source_artifact_invalid_count": len(source_artifact_invalid),
        "source_artifact_mismatches": sorted(source_artifact_mismatches),
        "source_artifact_mismatch_count": len(source_artifact_mismatches),
        "packet_file_sha256_invalid": sorted(packet_file_sha256_invalid),
        "packet_file_sha256_invalid_count": len(packet_file_sha256_invalid),
        "packet_file_sha256_mismatches": sorted(packet_file_sha256_mismatches),
        "packet_file_sha256_mismatch_count": len(packet_file_sha256_mismatches),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-packet-manifest",
            "verification_flags": {
                "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
                "require_existing_files": bool(getattr(args, "require_existing_files", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": {
                "artifact": _optional_file_sha256(artifact_path),
                "status_verify": (
                    _optional_file_sha256(status_verify_path) if status_verify_raw else None
                ),
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_prediction_operator_packet_export(args: Any) -> dict[str, Any]:
    manifest_verify_path = Path(args.manifest_verify)
    target_dir = Path(args.target_dir)
    files_dir = target_dir / "files"
    export_manifest_path = target_dir / "packet-export-manifest.json"
    packet_readme_path = target_dir / "README.md"
    packet_verifier_path = target_dir / "verify_packet.py"
    packet_resume_planner_path = target_dir / "resume_plan.py"
    packet_resume_runner_path = target_dir / "run_resume.py"
    packet_resume_runner_verifier_path = target_dir / "verify_run_resume.py"
    checksums_path = target_dir / "SHA256SUMS"
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    manifest_verify = _load_operator_handoff_payload(
        manifest_verify_path,
        label="packet_manifest_verify",
        expected_command="verify-prediction-operator-packet-manifest",
        issues=issues,
    )
    if manifest_verify and not bool(manifest_verify.get("ok")):
        quality_gate_failures.append("packet_manifest_verify_not_ok")

    manifest_path = _path_from_payload(manifest_verify, "artifact")
    packet_manifest = _load_json_packet_payload(
        manifest_path,
        "packet_manifest",
        issues,
    )
    packet_files = (
        packet_manifest.get("packet_files")
        if isinstance(packet_manifest.get("packet_files"), list)
        else []
    )

    exported_files: list[dict[str, Any]] = []
    skipped_files: list[str] = []
    used_names: set[str] = set()
    files_dir.mkdir(parents=True, exist_ok=True)
    for entry in packet_files:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "packet_file"))
        path_raw = entry.get("path")
        if not bool(entry.get("present")) or not isinstance(path_raw, str):
            skipped_files.append(name)
            continue
        source_path = Path(path_raw)
        if not source_path.is_file():
            skipped_files.append(name)
            continue
        exported_name = _operator_packet_export_filename(name, source_path, used_names)
        target_path = files_dir / exported_name
        shutil.copy2(source_path, target_path)
        exported_files.append(
            {
                "name": name,
                "source_path": str(source_path),
                "source_sha256": _optional_file_sha256(source_path),
                "exported_path": str(target_path),
                "exported_sha256": _optional_file_sha256(target_path),
                "bytes": target_path.stat().st_size,
            }
        )

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_packet_export_secret_literal_failures(exported_files)
        quality_gate_failures.extend(secret_failures)

    export_manifest = {
        "command": "prediction-operator-packet-export-manifest",
        "manifest_verify": str(manifest_verify_path),
        "manifest_verify_sha256": _optional_file_sha256(manifest_verify_path),
        "source_manifest": str(manifest_path) if manifest_path is not None else None,
        "source_manifest_sha256": _optional_file_sha256(manifest_path)
        if manifest_path is not None
        else None,
        "exported_files": exported_files,
        "exported_file_count": len(exported_files),
        "skipped_files": skipped_files,
        "skipped_file_count": len(skipped_files),
        "secret_literal_count": len(secret_failures),
    }
    export_manifest_sample_state = _operator_status_canonical_transitive_source_state(
        manifest_verify.get("run_metadata", {}).get("source_state", {})
        if isinstance(manifest_verify.get("run_metadata"), dict)
        else {}
    )
    if export_manifest_sample_state:
        export_manifest["run_metadata"] = {
            "source_state": export_manifest_sample_state,
        }
    export_manifest_sha256 = _write_json_artifact(
        export_manifest_path,
        export_manifest,
    )
    packet_verifier_sha256 = _write_text_artifact(
        packet_verifier_path,
        _prediction_operator_packet_standalone_verifier(),
    )
    packet_resume_planner_sha256 = _write_text_artifact(
        packet_resume_planner_path,
        _prediction_operator_packet_standalone_resume_planner(),
    )
    packet_resume_runner_sha256 = _write_text_artifact(
        packet_resume_runner_path,
        _prediction_operator_packet_standalone_resume_runner(),
    )
    packet_resume_runner_verifier_sha256 = _write_text_artifact(
        packet_resume_runner_verifier_path,
        _prediction_operator_packet_standalone_resume_runner_verifier(),
    )
    packet_readme = _prediction_operator_packet_export_readme(
        exported_files=exported_files,
        skipped_files=skipped_files,
        export_manifest_path=export_manifest_path,
        export_manifest_sha256=export_manifest_sha256,
    )
    packet_readme_sha256 = _write_text_artifact(packet_readme_path, packet_readme)
    checksums_text = _prediction_operator_packet_checksums_text(
        exported_files=exported_files,
        export_manifest_path=export_manifest_path,
        export_manifest_sha256=export_manifest_sha256,
        packet_readme_path=packet_readme_path,
        packet_readme_sha256=packet_readme_sha256,
        packet_verifier_path=packet_verifier_path,
        packet_verifier_sha256=packet_verifier_sha256,
        packet_resume_planner_path=packet_resume_planner_path,
        packet_resume_planner_sha256=packet_resume_planner_sha256,
        packet_resume_runner_path=packet_resume_runner_path,
        packet_resume_runner_sha256=packet_resume_runner_sha256,
        packet_resume_runner_verifier_path=packet_resume_runner_verifier_path,
        packet_resume_runner_verifier_sha256=packet_resume_runner_verifier_sha256,
    )
    checksums_sha256 = _write_text_artifact(checksums_path, checksums_text)
    source_state = {
        "exported_file_count": len(exported_files),
        "skipped_file_count": len(skipped_files),
        "secret_literal_count": len(secret_failures),
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(
            manifest_verify.get("run_metadata", {}).get("source_state", {})
            if isinstance(manifest_verify.get("run_metadata"), dict)
            else {}
        )
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "prediction-operator-packet-export",
        "manifest_verify": str(manifest_verify_path),
        "target_dir": str(target_dir),
        "files_dir": str(files_dir),
        "export_manifest": str(export_manifest_path),
        "export_manifest_sha256": export_manifest_sha256,
        "packet_readme": str(packet_readme_path),
        "packet_readme_sha256": packet_readme_sha256,
        "packet_verifier": str(packet_verifier_path),
        "packet_verifier_sha256": packet_verifier_sha256,
        "packet_resume_planner": str(packet_resume_planner_path),
        "packet_resume_planner_sha256": packet_resume_planner_sha256,
        "packet_resume_runner": str(packet_resume_runner_path),
        "packet_resume_runner_sha256": packet_resume_runner_sha256,
        "packet_resume_runner_verifier": str(packet_resume_runner_verifier_path),
        "packet_resume_runner_verifier_sha256": (packet_resume_runner_verifier_sha256),
        "checksums": str(checksums_path),
        "checksums_sha256": checksums_sha256,
        "exported_files": exported_files,
        "exported_file_count": len(exported_files),
        "skipped_files": skipped_files,
        "skipped_file_count": len(skipped_files),
        "secret_literal_count": len(secret_failures),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "prediction-operator-packet-export",
            "verification_flags": {
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": {
                "manifest_verify": _optional_file_sha256(manifest_verify_path),
                "packet_manifest": _optional_file_sha256(manifest_path)
                if manifest_path is not None
                else None,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _prediction_operator_packet_checksums_text(
    *,
    exported_files: list[dict[str, Any]],
    export_manifest_path: Path,
    export_manifest_sha256: str,
    packet_readme_path: Path,
    packet_readme_sha256: str | None,
    packet_verifier_path: Path | None = None,
    packet_verifier_sha256: str | None = None,
    packet_resume_planner_path: Path | None = None,
    packet_resume_planner_sha256: str | None = None,
    packet_resume_runner_path: Path | None = None,
    packet_resume_runner_sha256: str | None = None,
    packet_resume_runner_verifier_path: Path | None = None,
    packet_resume_runner_verifier_sha256: str | None = None,
) -> str:
    rows: list[tuple[str, str]] = []
    rows.append((export_manifest_sha256, export_manifest_path.name))
    if packet_readme_sha256 is not None:
        rows.append((packet_readme_sha256, packet_readme_path.name))
    if packet_verifier_path is not None and packet_verifier_sha256 is not None:
        rows.append((packet_verifier_sha256, packet_verifier_path.name))
    if packet_resume_planner_path is not None and packet_resume_planner_sha256 is not None:
        rows.append((packet_resume_planner_sha256, packet_resume_planner_path.name))
    if packet_resume_runner_path is not None and packet_resume_runner_sha256 is not None:
        rows.append((packet_resume_runner_sha256, packet_resume_runner_path.name))
    if (
        packet_resume_runner_verifier_path is not None
        and packet_resume_runner_verifier_sha256 is not None
    ):
        rows.append(
            (
                packet_resume_runner_verifier_sha256,
                packet_resume_runner_verifier_path.name,
            )
        )
    for entry in exported_files:
        exported_path = entry.get("exported_path")
        exported_sha256 = entry.get("exported_sha256")
        if isinstance(exported_path, str) and isinstance(exported_sha256, str):
            rows.append((exported_sha256, str(Path("files") / Path(exported_path).name)))
    return "".join(f"{sha256}  {path}\n" for sha256, path in rows)


def _prediction_operator_packet_export_readme(
    *,
    exported_files: list[dict[str, Any]],
    skipped_files: list[str],
    export_manifest_path: Path,
    export_manifest_sha256: str,
) -> str:
    eval_window_run = _prediction_operator_packet_export_eval_window_run(exported_files)
    congress_load = _prediction_operator_packet_export_congress_load(exported_files)
    bill_sponsor_availability = _prediction_operator_packet_export_bill_sponsor_availability(
        exported_files
    )
    jurisdiction_topology = _prediction_operator_packet_export_jurisdiction_topology(exported_files)
    source_url_audit = _prediction_operator_packet_export_source_url_audit(exported_files)
    lines = [
        "# Prediction Operator Packet",
        "",
        "This directory is a copied OpenPact prediction operator handoff packet.",
        "It is secret-free by construction; set real environment values outside this packet.",
        "",
        f"- Exported files: {len(exported_files)}",
        f"- Skipped files: {len(skipped_files)}",
        f"- Export manifest: `{export_manifest_path.name}`",
        f"- Export manifest sha256: `{export_manifest_sha256}`",
        "",
        "## Verify This Packet",
        "",
        "From an OpenPact checkout with the runtime installed, run:",
        "",
        "```bash",
        "python3 -m src.runtime.main verify-prediction-operator-packet-directory --packet-dir /path/to/packet --require-readme --require-checksums --require-exported-files --require-no-secret-literals --output packet-directory-verify.json",
        "python3 -m src.runtime.main prediction-operator-resume-plan --packet-dir /path/to/packet --require-verified-packet --require-no-secret-literals --output packet-resume-plan.json",
        "python3 -m src.runtime.main prediction-operator-resume-plan --packet-dir /path/to/packet --dotenv /path/to/openpact.env --require-verified-packet --require-no-secret-literals --output packet-resume-plan-with-env.json",
        "python3 -m src.runtime.main verify-prediction-operator-resume-plan --artifact packet-resume-plan.json --require-run-metadata --require-matches-current-packet --require-no-secret-literals --output packet-resume-plan-verify.json",
        "python3 -m src.runtime.main verify-prediction-operator-resume-run --packet-dir /path/to/packet --artifact /path/to/packet/run-resume-dry-run.json --dotenv /path/to/openpact.env --require-run-metadata --require-ok --require-dry-run --require-no-secret-literals --require-congress-prediction-inputs --require-strict-eval-window-run --require-selected-source-artifact-hashes --output packet-resume-run-verify.json",
        "```",
        "",
        "From this packet directory, verify file checksums with:",
        "",
        "```bash",
        "shasum -a 256 -c SHA256SUMS",
        "```",
        "",
        "Or run the standalone Python verifier from this packet directory:",
        "",
        "```bash",
        "python3 verify_packet.py",
        "python3 resume_plan.py",
        "python3 resume_plan.py --require-verified-packet",
        "python3 resume_plan.py --dotenv /path/to/openpact.env",
        "python3 run_resume.py --dotenv /path/to/openpact.env --repo-root /path/to/openpact --dry-run --output run-resume-dry-run.json",
        "python3 run_resume.py --dotenv /path/to/openpact.env --repo-root /path/to/openpact --phase 1 --dry-run --output run-resume-phase-1-dry-run.json",
        "python3 verify_run_resume.py --artifact run-resume-dry-run.json --dotenv /path/to/openpact.env --require-ok --require-strict-eval-window-run --require-selected-source-artifact-hashes",
        "python3 run_resume.py --dotenv /path/to/openpact.env --repo-root /path/to/openpact",
        "```",
        "",
        "The run-resume verifiers validate recorded SHA-256 hash shape, recompute packet file "
        "hashes, selected phase/command counts, selected resume provenance, derivable "
        "`run_metadata.source_state` counts, and `run_metadata.verification_flags`; they also "
        "surface structured hash missing/invalid/mismatch counts before accepting saved runner "
        "artifacts.",
        "",
        "## Annual Eval Window Run",
        "",
        f"- Present: {'yes' if bool(eval_window_run.get('present')) else 'no'}",
        f"- Status: {'green' if bool(eval_window_run.get('ok')) else 'blocked'}",
        f"- Window count: {_plain_int_or_zero(eval_window_run.get('window_count'))}",
        "- Expected verifier count: "
        f"{_plain_int_or_zero(eval_window_run.get('expected_window_verifier_count'))}",
        f"- Loaded verifier count: {_plain_int_or_zero(eval_window_run.get('loaded_verifier_count'))}",
        f"- Missing verifier count: {_plain_int_or_zero(eval_window_run.get('missing_verifier_count'))}",
        f"- Failing verifier count: {_plain_int_or_zero(eval_window_run.get('failing_verifier_count'))}",
        f"- Stale field count: {_plain_int_or_zero(eval_window_run.get('stale_field_count'))}",
        f"- Requires training labels: {'yes' if eval_window_run.get('requires_training_labels') is True else 'no'}",
        f"- Requires evaluation labels: {'yes' if eval_window_run.get('requires_evaluation_labels') is True else 'no'}",
        *_operator_eval_window_run_strict_gate_lines(eval_window_run),
        *_operator_eval_window_run_archive_lines(eval_window_run),
    ]
    sponsor_line = _operator_bill_sponsor_availability_line(bill_sponsor_availability)
    if sponsor_line is not None:
        lines.extend(["", "## Bill Sponsor Availability", "", sponsor_line])
    jurisdiction_lines = _operator_jurisdiction_topology_lines(jurisdiction_topology)
    if jurisdiction_lines:
        lines.extend(["", *jurisdiction_lines])
    source_url_lines = _operator_source_url_audit_lines(source_url_audit)
    if source_url_lines:
        lines.extend(["", *source_url_lines])
    if congress_load:
        lines.extend(
            [
                "",
                "## Congress Prediction Inputs",
                "",
                f"- Required: {'yes' if congress_load.get('required') is True else 'no'}",
                f"- Present: {'yes' if congress_load.get('present') is True else 'no'}",
                f"- Status: {'green' if congress_load.get('ok') is True else 'blocked'}",
                "- Member inputs: "
                f"{'available' if congress_load.get('prediction_member_inputs_available') is True else 'missing'}",
                "- Bill inputs: "
                f"{'available' if congress_load.get('prediction_bill_inputs_available') is True else 'missing'}",
                "- Vote inputs: "
                f"{'available' if congress_load.get('prediction_vote_inputs_available') is True else 'missing'}",
            ]
        )
        for key, label in (
            ("member_row_count", "Member rows"),
            ("bill_row_count", "Bill rows"),
            ("vote_event_row_count", "Vote event rows"),
            ("vote_cast_row_count", "Vote cast rows"),
        ):
            value = congress_load.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                lines.append(f"- {label}: {value}")
        source_family_ids = _string_list(congress_load.get("source_family_ids"))
        if source_family_ids:
            lines.append(
                "- Source families: "
                + ", ".join(f"`{source_family_id}`" for source_family_id in source_family_ids)
            )
        blockers = _string_list(congress_load.get("blockers"))
        lines.append("- Blockers:")
        if blockers:
            lines.extend(f"  - `{blocker}`" for blocker in blockers)
        else:
            lines.append("  - none")
    lines.extend(["", "## Files"])
    for entry in exported_files:
        exported_path = entry.get("exported_path")
        if isinstance(exported_path, str):
            display_path = Path("files") / Path(exported_path).name
        else:
            display_path = Path("files")
        lines.append(
            f"- `{display_path}` | `{entry.get('name')}` | sha256 `{entry.get('exported_sha256')}`"
        )
    if skipped_files:
        lines.extend(["", "## Skipped Files"])
        lines.extend(f"- `{name}`" for name in skipped_files)
    lines.append("")
    return "\n".join(lines)


def _prediction_operator_packet_export_eval_window_run(
    exported_files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path: Path | None = None
    for entry in exported_files:
        if entry.get("name") != "operator_status":
            continue
        exported_path = entry.get("exported_path")
        if isinstance(exported_path, str):
            status_path = Path(exported_path)
            break
    if status_path is None or not status_path.is_file():
        return _prediction_readiness_eval_window_run_summary(None)
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _prediction_readiness_eval_window_run_summary(None)
    if not isinstance(status, dict):
        return _prediction_readiness_eval_window_run_summary(None)
    return _prediction_readiness_eval_window_run_summary(status.get("eval_window_run"))


def _prediction_operator_packet_export_congress_load(
    exported_files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path = _prediction_operator_packet_export_status_path(exported_files)
    if status_path is None:
        return {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(status, dict):
        return {}
    congress_load = status.get("congress_load")
    return congress_load if isinstance(congress_load, dict) else {}


def _prediction_operator_packet_export_bill_sponsor_availability(
    exported_files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path = _prediction_operator_packet_export_status_path(exported_files)
    if status_path is None:
        return {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(status, dict):
        return {}
    bill_sponsor_availability = status.get("bill_sponsor_availability")
    return bill_sponsor_availability if isinstance(bill_sponsor_availability, dict) else {}


def _prediction_operator_packet_export_jurisdiction_topology(
    exported_files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path = _prediction_operator_packet_export_status_path(exported_files)
    if status_path is None:
        return {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(status, dict):
        return {}
    jurisdiction_topology = status.get("jurisdiction_topology")
    return jurisdiction_topology if isinstance(jurisdiction_topology, dict) else {}


def _prediction_operator_packet_export_source_url_audit(
    exported_files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path = _prediction_operator_packet_export_status_path(exported_files)
    if status_path is None:
        return {}
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(status, dict):
        return {}
    return _operator_source_url_audit_source_state(status.get("source_url_audit"))


def _prediction_operator_packet_export_status_path(
    exported_files: list[dict[str, Any]],
) -> Path | None:
    for entry in exported_files:
        if entry.get("name") != "operator_status":
            continue
        exported_path = entry.get("exported_path")
        if isinstance(exported_path, str):
            path = Path(exported_path)
            return path if path.is_file() else None
    return None


def _prediction_operator_packet_standalone_verifier() -> str:
    return '''#!/usr/bin/env python3
"""Verify an exported OpenPact prediction operator packet using only stdlib."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


TOKEN_PREFIX = "s" + "k-"
SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, issues: list[str]) -> dict[str, Any]:
    if not path.is_file():
        issues.append(f"missing:{path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        issues.append(f"invalid_json:{path.name}")
        return {}
    if not isinstance(payload, dict):
        issues.append(f"invalid_object:{path.name}")
        return {}
    return payload


def _checksums(path: Path, issues: list[str]) -> dict[str, str]:
    if not path.is_file():
        issues.append("missing:SHA256SUMS")
        return {}
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            issues.append("invalid_checksum_row")
            continue
        rows[parts[1].lstrip("*")] = parts[0]
    return rows


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _sorted_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item and item.strip() == item for item in value)
        and value == sorted(value)
        and len(set(value)) == len(value)
    )


def _source_family_id_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(SOURCE_FAMILY_ID_RE.fullmatch(item) is not None for item in value)
    )


def _source_state_issues(manifest: dict[str, Any]) -> list[str]:
    run_metadata = manifest.get("run_metadata")
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata, dict) and isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    issues: list[str] = []
    for key in (
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
    ):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"run_metadata_source_state_{key}_boolean")
    if "congress_load_source_family_ids" in source_state:
        value = source_state.get("congress_load_source_family_ids")
        if not _sorted_string_list(value):
            issues.append("run_metadata_source_state_congress_load_source_family_ids_sorted")
        elif not _source_family_id_list(value):
            issues.append("run_metadata_source_state_congress_load_source_family_ids_normalized")
    if "congress_load_source_family_count" in source_state:
        value = source_state.get("congress_load_source_family_count")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.append("run_metadata_source_state_congress_load_source_family_count_non_negative")
        elif isinstance(source_state.get("congress_load_source_family_ids"), list) and value != len(
            source_state["congress_load_source_family_ids"]
        ):
            issues.append("run_metadata_source_state_congress_load_source_family_count_mismatch")
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        if key in source_state and (
            not isinstance(source_state.get(key), int) or isinstance(source_state.get(key), bool)
            or source_state.get(key) < 0
        ):
            issues.append(f"run_metadata_source_state_{key}_non_negative_integer")
    if "source_url_audit_quality_gate_failures" in source_state and not _sorted_string_list(
        source_state.get("source_url_audit_quality_gate_failures")
    ):
        issues.append("run_metadata_source_state_source_url_audit_quality_gate_failures_sorted")
    return issues


def _secret_hits(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if TOKEN_PREFIX in text:
            hits.append(f"openai_token:{path.name}")
        if re.search(r"\\bpostgres(?:ql)?://\\S+", text):
            hits.append(f"postgres_dsn:{path.name}")
    return sorted(set(hits))


def main() -> int:
    root = Path(__file__).resolve().parent
    issues: list[str] = []
    manifest_path = root / "packet-export-manifest.json"
    readme_path = root / "README.md"
    verifier_path = root / "verify_packet.py"
    resume_planner_path = root / "resume_plan.py"
    resume_runner_path = root / "run_resume.py"
    resume_runner_verifier_path = root / "verify_run_resume.py"
    checksums_path = root / "SHA256SUMS"
    manifest = _read_json(manifest_path, issues)
    if manifest.get("command") != "prediction-operator-packet-export-manifest":
        issues.append("manifest_command_mismatch")
    issues.extend(_source_state_issues(manifest))

    exported_files = manifest.get("exported_files")
    if not isinstance(exported_files, list):
        exported_files = []
        issues.append("exported_files_missing")

    expected_checksums: dict[str, str] = {}
    for path in (
        manifest_path,
        readme_path,
        verifier_path,
        resume_planner_path,
        resume_runner_path,
        resume_runner_verifier_path,
    ):
        if path.is_file():
            expected_checksums[path.name] = _sha256(path)

    missing_files = 0
    invalid_hashes = 0
    hash_mismatches = 0
    secret_paths = [
        manifest_path,
        readme_path,
        verifier_path,
        resume_planner_path,
        resume_runner_path,
        resume_runner_verifier_path,
        checksums_path,
    ]
    for entry in exported_files:
        if not isinstance(entry, dict):
            issues.append("invalid_exported_file_entry")
            continue
        exported_path = entry.get("exported_path")
        expected_sha = entry.get("exported_sha256")
        if not isinstance(exported_path, str) or not isinstance(expected_sha, str):
            issues.append("invalid_exported_file_hash_entry")
            invalid_hashes += 1
            continue
        if not _is_sha256_hex(expected_sha):
            entry_name = entry.get("name") or Path(exported_path).name
            issues.append(f"invalid_exported_file_sha256:{entry_name}")
            invalid_hashes += 1
            continue
        local_path = root / "files" / Path(exported_path).name
        relative_path = str(Path("files") / local_path.name)
        expected_checksums[relative_path] = expected_sha
        secret_paths.append(local_path)
        if not local_path.is_file():
            missing_files += 1
            continue
        if _sha256(local_path) != expected_sha:
            hash_mismatches += 1

    actual_checksums = _checksums(checksums_path, issues)
    checksums_match = actual_checksums == expected_checksums
    secret_hits = _secret_hits(secret_paths)
    result = {
        "ok": not issues
        and missing_files == 0
        and invalid_hashes == 0
        and hash_mismatches == 0
        and checksums_match
        and not secret_hits,
        "command": "verify_packet.py",
        "exported_file_count": len(exported_files),
        "missing_exported_file_count": missing_files,
        "exported_file_sha256_invalid_count": invalid_hashes,
        "exported_file_sha256_mismatch_count": hash_mismatches,
        "checksums_content_matches": checksums_match,
        "secret_literal_count": len(secret_hits),
        "issues": sorted(set(issues)),
        "secret_hits": secret_hits,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _prediction_operator_packet_standalone_resume_planner() -> str:
    return '''#!/usr/bin/env python3
"""Dry-run an exported OpenPact prediction operator resume script."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_dotenv(path: Path | None) -> tuple[set[str], list[str]]:
    if path is None:
        return set(), []
    if not path.is_file():
        return set(), [f"dotenv file not found: {path}"]
    keys: set[str] = set()
    issues: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        if "=" not in stripped:
            issues.append(f"dotenv line {index}: missing '='")
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            issues.append(f"dotenv line {index}: missing key")
            continue
        if value.strip():
            keys.add(key)
    return keys, issues


def _read_json(path: Path, issues: list[str]) -> dict[str, Any]:
    if not path.is_file():
        issues.append(f"missing:{path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        issues.append(f"invalid_json:{path.name}")
        return {}
    if not isinstance(payload, dict):
        issues.append(f"invalid_object:{path.name}")
        return {}
    return payload


def _checksums(path: Path, issues: list[str]) -> dict[str, str]:
    if not path.is_file():
        issues.append("missing:SHA256SUMS")
        return {}
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            issues.append("invalid_checksum_row")
            continue
        rows[parts[1].lstrip("*")] = parts[0]
    return rows


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _verify_packet(root: Path) -> tuple[bool, list[str]]:
    issues: list[str] = []
    manifest_path = root / "packet-export-manifest.json"
    manifest = _read_json(manifest_path, issues)
    if manifest.get("command") != "prediction-operator-packet-export-manifest":
        issues.append("manifest_command_mismatch")
    exported_files = manifest.get("exported_files")
    if not isinstance(exported_files, list):
        exported_files = []
        issues.append("exported_files_missing")
    expected: dict[str, str] = {}
    for path in (
        manifest_path,
        root / "README.md",
        root / "verify_packet.py",
        root / "resume_plan.py",
        root / "run_resume.py",
        root / "verify_run_resume.py",
    ):
        if path.is_file():
            expected[path.name] = _sha256(path)
    for entry in exported_files:
        if not isinstance(entry, dict):
            issues.append("invalid_exported_file_entry")
            continue
        exported_path = entry.get("exported_path")
        expected_sha = entry.get("exported_sha256")
        if not isinstance(exported_path, str) or not isinstance(expected_sha, str):
            issues.append("invalid_exported_file_hash_entry")
            continue
        if not _is_sha256_hex(expected_sha):
            entry_name = entry.get("name") or Path(exported_path).name
            issues.append(f"invalid_exported_file_sha256:{entry_name}")
            continue
        local_path = root / "files" / Path(exported_path).name
        expected[str(Path("files") / local_path.name)] = expected_sha
        if not local_path.is_file():
            issues.append(f"missing:{Path('files') / local_path.name}")
            continue
        if _sha256(local_path) != expected_sha:
            issues.append(f"sha256_mismatch:{Path('files') / local_path.name}")
    actual = _checksums(root / "SHA256SUMS", issues)
    if actual != expected:
        issues.append("checksums_content_mismatch")
    return not issues, sorted(set(issues))


def _packet_file(root: Path, name: str, fallback: str) -> Path:
    manifest = _read_json(root / "packet-export-manifest.json", [])
    exported_files = manifest.get("exported_files")
    if isinstance(exported_files, list):
        for entry in exported_files:
            if not isinstance(entry, dict) or entry.get("name") != name:
                continue
            exported_path = entry.get("exported_path")
            if isinstance(exported_path, str):
                return root / "files" / Path(exported_path).name
    return root / "files" / fallback


def _env_source(name: str, dotenv_keys: set[str]) -> str:
    if os.environ.get(name):
        return "process"
    if name in dotenv_keys:
        return "dotenv"
    return "missing"


def _finalize(phase: dict[str, Any]) -> None:
    env_guards = sorted(set(str(v) for v in phase.get("env_guards", [])))
    env_sources = {
        key: value
        for key, value in phase.get("env_sources", {}).items()
        if key in env_guards
    }
    missing_env = sorted(env for env in env_guards if env_sources.get(env) == "missing")
    phase["env_guards"] = env_guards
    phase["env_sources"] = env_sources
    phase["missing_env"] = missing_env
    phase["runnable"] = not missing_env


def _parse(script_text: str, dotenv_keys: set[str]) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    comment_section: str | None = None
    for line in script_text.splitlines():
        stripped = line.strip()
        phase_match = re.fullmatch(r"# Phase (\\d+)", stripped)
        if phase_match:
            if current is not None:
                _finalize(current)
                phases.append(current)
            current = {
                "phase": int(phase_match.group(1)),
                "declared_missing_requirements": [],
                "actions": [],
                "env_guards": [],
                "env_sources": {},
                "missing_env": [],
                "commands": [],
                "source_artifacts": [],
                "additional_reasons": [],
            }
            comment_section = None
            continue
        if current is None:
            continue
        if stripped.startswith("# Missing requirements:"):
            comment_section = None
            raw = stripped.removeprefix("# Missing requirements:").strip()
            if raw and raw.lower() != "none":
                current["declared_missing_requirements"] = [
                    item.strip() for item in raw.split(",") if item.strip()
                ]
            continue
        if stripped.startswith("# Actions:"):
            comment_section = None
            raw = stripped.removeprefix("# Actions:").strip()
            if raw and raw.lower() != "none":
                current["actions"] = [
                    item.strip() for item in raw.split(",") if item.strip()
                ]
            continue
        if stripped == "# Source artifacts:":
            comment_section = "source_artifacts"
            continue
        if stripped == "# Additional reasons:":
            comment_section = "additional_reasons"
            continue
        if stripped.startswith("#   - ") and comment_section is not None:
            current[comment_section].append(stripped.removeprefix("#   - ").strip())
            continue
        guard_match = re.fullmatch(r': "\\$\\{([A-Za-z_][A-Za-z0-9_]*):\\?.+\\}"', stripped)
        if guard_match:
            comment_section = None
            env_name = guard_match.group(1)
            current["env_guards"].append(env_name)
            current["env_sources"][env_name] = _env_source(env_name, dotenv_keys)
            continue
        if stripped.startswith("python3 -m src.runtime.main "):
            comment_section = None
            current["commands"].append(stripped)
    if current is not None:
        _finalize(current)
        phases.append(current)
    return phases


def _plain_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


EVAL_WINDOW_RUN_GATE_KEYS = (
    "requires_training_labels",
    "requires_evaluation_labels",
    "requires_input_inventory_portable_ids",
    "requires_input_inventory_congress_source_families",
    "requires_input_inventory_official_source_thresholds",
    "requires_input_inventory_optional_evidence",
    "requires_eval_manifest_model_suite",
    "requires_eval_manifest_official_source_thresholds",
    "requires_eval_manifest_unknown_availability_failures",
    "requires_eval_manifest_ontology_feature_signals",
)


def _eval_window_run(root: Path) -> dict[str, Any]:
    status = _read_json(_packet_file(root, "operator_status", "operator_status.json"), [])
    component = status.get("eval_window_run")
    if not isinstance(component, dict):
        component = {}
    summary = {
        "present": bool(component.get("present")),
        "ok": bool(component.get("ok")),
        "checked": _plain_int(component.get("checked")),
        "artifact": component.get("artifact") if isinstance(component.get("artifact"), str) else None,
        "artifact_sha256": (
            component.get("artifact_sha256")
            if isinstance(component.get("artifact_sha256"), str)
            else None
        ),
        "plan": component.get("plan") if isinstance(component.get("plan"), str) else None,
        "plan_sha256": component.get("plan_sha256") if isinstance(component.get("plan_sha256"), str) else None,
        "window_count": _plain_int(component.get("window_count")),
        "expected_window_verifier_count": _plain_int(
            component.get("expected_window_verifier_count")
        ),
        "loaded_verifier_count": _plain_int(component.get("loaded_verifier_count")),
        "missing_verifier_count": _plain_int(component.get("missing_verifier_count")),
        "failing_verifier_count": _plain_int(component.get("failing_verifier_count")),
        "stale_field_count": _plain_int(component.get("stale_field_count")),
        "issue_count": _plain_int(component.get("issue_count")),
        "quality_gate_failure_count": _plain_int(component.get("quality_gate_failure_count")),
    }
    for key in EVAL_WINDOW_RUN_GATE_KEYS:
        value = component.get(key)
        if isinstance(value, bool):
            summary[key] = value
    return summary


def _congress_load(root: Path) -> dict[str, Any]:
    status = _read_json(_packet_file(root, "operator_status", "operator_status.json"), [])
    component = status.get("congress_load")
    if not isinstance(component, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "required",
        "present",
        "ok",
        "prediction_member_inputs_available",
        "prediction_bill_inputs_available",
        "prediction_vote_inputs_available",
    ):
        value = component.get(key)
        if isinstance(value, bool):
            summary[key] = value
    source_family_ids = component.get("source_family_ids")
    if (
        isinstance(source_family_ids, list)
        and all(isinstance(item, str) for item in source_family_ids)
    ):
        summary["source_family_ids"] = source_family_ids
    blockers = component.get("blockers")
    if isinstance(blockers, list):
        clean_blockers = [item for item in blockers if isinstance(item, str)]
        if clean_blockers:
            summary["blockers"] = clean_blockers
    for key in (
        "member_row_count",
        "bill_row_count",
        "vote_event_row_count",
        "vote_cast_row_count",
    ):
        value = component.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            summary[key] = value
    return summary


def _congress_source_state(congress_load: dict[str, Any]) -> dict[str, Any]:
    source_state: dict[str, Any] = {}
    for source_key, output_key in (
        ("required", "congress_load_required"),
        ("present", "congress_load_present"),
        ("ok", "congress_load_ok"),
        (
            "prediction_member_inputs_available",
            "congress_load_prediction_member_inputs_available",
        ),
        ("prediction_bill_inputs_available", "congress_load_prediction_bill_inputs_available"),
        ("prediction_vote_inputs_available", "congress_load_prediction_vote_inputs_available"),
    ):
        value = congress_load.get(source_key)
        if isinstance(value, bool):
            source_state[output_key] = value
    source_family_ids = congress_load.get("source_family_ids")
    if (
        isinstance(source_family_ids, list)
        and all(isinstance(item, str) for item in source_family_ids)
    ):
        source_state["congress_load_source_family_ids"] = source_family_ids
        source_state["congress_load_source_family_count"] = len(source_family_ids)
    for source_key, output_key in (
        ("member_row_count", "congress_load_member_row_count"),
        ("bill_row_count", "congress_load_bill_row_count"),
        ("vote_event_row_count", "congress_load_vote_event_row_count"),
        ("vote_cast_row_count", "congress_load_vote_cast_row_count"),
    ):
        value = congress_load.get(source_key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            source_state[output_key] = value
    return source_state


EVAL_WINDOW_RUN_SOURCE_STATE_KEYS = (
    ("requires_training_labels", "eval_window_run_requires_training_labels"),
    ("requires_evaluation_labels", "eval_window_run_requires_evaluation_labels"),
    (
        "requires_input_inventory_portable_ids",
        "eval_window_run_requires_input_inventory_portable_ids",
    ),
    (
        "requires_input_inventory_congress_source_families",
        "eval_window_run_requires_input_inventory_congress_source_families",
    ),
    (
        "requires_input_inventory_official_source_thresholds",
        "eval_window_run_requires_input_inventory_official_source_thresholds",
    ),
    (
        "requires_input_inventory_optional_evidence",
        "eval_window_run_requires_input_inventory_optional_evidence",
    ),
    ("requires_eval_manifest_model_suite", "eval_window_run_requires_eval_manifest_model_suite"),
    (
        "requires_eval_manifest_official_source_thresholds",
        "eval_window_run_requires_eval_manifest_official_source_thresholds",
    ),
    (
        "requires_eval_manifest_unknown_availability_failures",
        "eval_window_run_requires_eval_manifest_unknown_availability_failures",
    ),
    (
        "requires_eval_manifest_ontology_feature_signals",
        "eval_window_run_requires_eval_manifest_ontology_feature_signals",
    ),
)


def _eval_window_source_state(eval_window_run: dict[str, Any]) -> dict[str, Any]:
    source_state: dict[str, Any] = {}
    for source_key, output_key in EVAL_WINDOW_RUN_SOURCE_STATE_KEYS:
        value = eval_window_run.get(source_key)
        if isinstance(value, bool):
            source_state[output_key] = value
    return source_state


def _source_url_audit(root: Path) -> dict[str, Any]:
    status = _read_json(_packet_file(root, "operator_status", "operator_status.json"), [])
    component = status.get("source_url_audit")
    if not isinstance(component, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        value = component.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            summary[key] = value
    failures = component.get("source_url_audit_quality_gate_failures")
    if (
        isinstance(failures, list)
        and all(isinstance(item, str) and item for item in failures)
        and failures == sorted(failures)
    ):
        summary["source_url_audit_quality_gate_failures"] = failures
    return summary


def main() -> int:
    dotenv_path: Path | None = None
    require_verified_packet = False
    args = sys.argv[1:]
    while args:
        item = args.pop(0)
        if item == "--require-verified-packet":
            require_verified_packet = True
            continue
        if item == "--dotenv" and args:
            dotenv_path = Path(args.pop(0))
            continue
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": "resume_plan.py",
                    "issues": [
                        "usage: python3 resume_plan.py [--require-verified-packet] [--dotenv PATH]"
                    ],
                },
                sort_keys=True,
            )
        )
        return 2
    root = Path(__file__).resolve().parent
    packet_verified, packet_verify_issues = _verify_packet(root)
    script = _packet_file(root, "resume_script", "resume_script.sh")
    issues: list[str] = []
    if require_verified_packet and not packet_verified:
        issues.extend(f"packet_verify:{issue}" for issue in packet_verify_issues)
    dotenv_keys, dotenv_issues = _read_dotenv(dotenv_path)
    issues.extend(dotenv_issues)
    script_text = ""
    if not script.is_file():
        issues.append("resume_script_missing")
    else:
        script_text = script.read_text(encoding="utf-8", errors="ignore")
    phases = _parse(script_text, dotenv_keys)
    missing_env = sorted(
        {env for phase in phases for env in phase["missing_env"]}
    )
    dotenv_only = sorted(
        {
            env
            for phase in phases
            for env in phase["env_guards"]
            if not os.environ.get(env) and env in dotenv_keys
        }
    )
    command_count = sum(len(phase["commands"]) for phase in phases)
    blocked_phase_count = sum(1 for phase in phases if not phase["runnable"])
    eval_window_run = _eval_window_run(root)
    congress_load = _congress_load(root)
    source_url_audit = _source_url_audit(root)
    result = {
        "ok": not issues,
        "command": "resume_plan.py",
        "packet_verified": packet_verified,
        "packet_verify_issues": packet_verify_issues,
        "launch_ready": not issues and not missing_env,
        "phase_count": len(phases),
        "command_count": command_count,
        "runnable_phase_count": len(phases) - blocked_phase_count,
        "blocked_phase_count": blocked_phase_count,
        "missing_env": missing_env,
        "dotenv": {
            "path": str(dotenv_path) if dotenv_path is not None else None,
            "exists": dotenv_path.is_file() if dotenv_path is not None else None,
            "dotenv_only": dotenv_only,
            "dotenv_only_count": len(dotenv_only),
            "issues": dotenv_issues,
        },
        "eval_window_run": eval_window_run,
        "congress_load": congress_load,
        "source_url_audit": source_url_audit,
        "phases": phases,
        "issues": issues,
        "run_metadata": {
            "source_state": {
                **_congress_source_state(congress_load),
                **_eval_window_source_state(eval_window_run),
                **source_url_audit,
            }
        },
    }
    result["run_metadata"] = {
        "source_state": {
            **_congress_source_state(congress_load),
            **_eval_window_source_state(eval_window_run),
            **source_url_audit,
        }
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _prediction_operator_packet_standalone_resume_runner() -> str:
    return '''#!/usr/bin/env python3
"""Verify a packet, load a dotenv safely, and run its resume script."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _read_dotenv(path: Path | None) -> tuple[dict[str, str], list[str]]:
    if path is None:
        return {}, []
    if not path.is_file():
        return {}, [f"dotenv file not found: {path}"]
    values: dict[str, str] = {}
    issues: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        if "=" not in stripped:
            issues.append(f"dotenv line {index}: missing '='")
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            issues.append(f"dotenv line {index}: invalid key")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            values[key] = value
    return values, issues


def _usage() -> dict[str, object]:
    return {
        "ok": False,
        "command": "run_resume.py",
        "issues": [
            "usage: python3 run_resume.py [--dotenv PATH] [--repo-root PATH] [--phase N] [--dry-run] [--output PATH]"
        ],
    }


def _parse_args(
    argv: list[str],
) -> tuple[Path | None, Path, bool, list[int], Path | None, list[str]]:
    dotenv_path: Path | None = None
    repo_root = Path.cwd()
    dry_run = False
    selected_phases: list[int] = []
    output_path: Path | None = None
    issues: list[str] = []
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--dry-run":
            dry_run = True
            continue
        if item == "--dotenv" and args:
            dotenv_path = _resolve_path(args.pop(0))
            continue
        if item == "--repo-root" and args:
            repo_root = _resolve_path(args.pop(0))
            continue
        if item == "--output" and args:
            output_path = _resolve_path(args.pop(0))
            continue
        if item == "--phase" and args:
            raw_phase = args.pop(0)
            try:
                phase = int(raw_phase)
            except ValueError:
                issues.append(f"invalid phase: {raw_phase}")
                continue
            if phase <= 0:
                issues.append(f"invalid phase: {raw_phase}")
                continue
            selected_phases.append(phase)
            continue
        issues.extend(str(issue) for issue in _usage()["issues"])
        break
    return dotenv_path, repo_root, dry_run, sorted(set(selected_phases)), output_path, issues


def _run_plan(root: Path, dotenv_path: Path | None) -> tuple[int, dict[str, object], str]:
    cmd = [sys.executable, str(root / "resume_plan.py"), "--require-verified-packet"]
    if dotenv_path is not None:
        cmd.extend(["--dotenv", str(dotenv_path)])
    proc = subprocess.run(
        cmd,
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "command": "resume_plan.py",
            "issues": ["resume_plan_json_decode_failed"],
        }
    return proc.returncode, payload, proc.stderr


def _selected_phases(
    plan: dict[str, object],
    requested: list[int],
    issues: list[str],
) -> list[dict[str, object]]:
    phases = plan.get("phases")
    if not isinstance(phases, list):
        issues.append("resume_plan_phases_missing")
        return []
    normalized = [phase for phase in phases if isinstance(phase, dict)]
    if not requested:
        return normalized
    by_number = {
        phase.get("phase"): phase
        for phase in normalized
        if type(phase.get("phase")) is int
    }
    selected: list[dict[str, object]] = []
    for phase_number in requested:
        phase = by_number.get(phase_number)
        if phase is None:
            issues.append(f"phase_not_found:{phase_number}")
            continue
        selected.append(phase)
    return selected


def _phase_missing_env(phases: list[dict[str, object]]) -> list[str]:
    missing: set[str] = set()
    for phase in phases:
        values = phase.get("missing_env")
        if isinstance(values, list):
            missing.update(str(value) for value in values)
    return sorted(missing)


def _phase_commands(phases: list[dict[str, object]]) -> list[str]:
    commands: list[str] = []
    for phase in phases:
        values = phase.get("commands")
        if isinstance(values, list):
            commands.extend(str(value) for value in values)
    return commands


def _phase_values(phases: list[dict[str, object]], key: str) -> list[str]:
    values: list[str] = []
    for phase in phases:
        raw_values = phase.get(key)
        if not isinstance(raw_values, list):
            continue
        for value in raw_values:
            normalized = str(value)
            if normalized not in values:
                values.append(normalized)
    return values


def _sha256_optional(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_source_artifact_sha256(
    repo_root: Path,
    selected_source_artifacts: list[str],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for artifact in selected_source_artifacts:
        path = _resolve_path(artifact) if Path(artifact).is_absolute() else repo_root / artifact
        digest = _sha256_optional(path)
        if digest is not None:
            hashes[artifact] = digest
    return hashes


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _packet_file(root: Path, name: str, fallback: str) -> Path:
    manifest = _read_json(root / "packet-export-manifest.json")
    exported_files = manifest.get("exported_files")
    if isinstance(exported_files, list):
        for entry in exported_files:
            if not isinstance(entry, dict) or entry.get("name") != name:
                continue
            exported_path = entry.get("exported_path")
            if isinstance(exported_path, str):
                return root / "files" / Path(exported_path).name
    return root / "files" / fallback


def _is_non_negative_int_list(value: object) -> bool:
    return isinstance(value, list) and all(type(item) is int and item >= 0 for item in value)


def _is_sorted_unique_non_empty_string_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


def _packet_sample_source_state(root: Path) -> dict[str, object]:
    manifest = _read_json(root / "packet-export-manifest.json")
    run_metadata = manifest.get("run_metadata")
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata, dict) and isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    sample_state: dict[str, object] = {}
    vote_event_ids = source_state.get("selected_sample_vote_event_ids")
    if _is_non_negative_int_list(vote_event_ids):
        sample_state["selected_sample_vote_event_ids"] = vote_event_ids
    for key in (
        "selected_sample_bill_keys",
        "selected_sample_member_bioguide_ids",
        "selected_sample_jurisdiction_ids",
        "selected_sample_legislative_body_ids",
        "selected_sample_legislative_session_ids",
        "selected_sample_source_family_ids",
    ):
        value = source_state.get(key)
        if _is_sorted_unique_non_empty_string_list(value):
            sample_state[key] = value
    return sample_state


def _congress_source_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, object] = {}
    for source_key, output_key in (
        ("required", "congress_load_required"),
        ("present", "congress_load_present"),
        ("ok", "congress_load_ok"),
        (
            "prediction_member_inputs_available",
            "congress_load_prediction_member_inputs_available",
        ),
        ("prediction_bill_inputs_available", "congress_load_prediction_bill_inputs_available"),
        ("prediction_vote_inputs_available", "congress_load_prediction_vote_inputs_available"),
    ):
        field_value = value.get(source_key)
        if isinstance(field_value, bool):
            source_state[output_key] = field_value
    source_family_ids = value.get("source_family_ids")
    if (
        isinstance(source_family_ids, list)
        and all(isinstance(item, str) for item in source_family_ids)
    ):
        source_state["congress_load_source_family_ids"] = source_family_ids
        source_state["congress_load_source_family_count"] = len(source_family_ids)
    for source_key, output_key in (
        ("member_row_count", "congress_load_member_row_count"),
        ("bill_row_count", "congress_load_bill_row_count"),
        ("vote_event_row_count", "congress_load_vote_event_row_count"),
        ("vote_cast_row_count", "congress_load_vote_cast_row_count"),
    ):
        field_value = value.get(source_key)
        if isinstance(field_value, int) and not isinstance(field_value, bool) and field_value >= 0:
            source_state[output_key] = field_value
    return source_state


EVAL_WINDOW_RUN_SOURCE_STATE_KEYS = (
    (
        "requires_input_inventory_portable_ids",
        "eval_window_run_requires_input_inventory_portable_ids",
    ),
    (
        "requires_input_inventory_congress_source_families",
        "eval_window_run_requires_input_inventory_congress_source_families",
    ),
    (
        "requires_input_inventory_official_source_thresholds",
        "eval_window_run_requires_input_inventory_official_source_thresholds",
    ),
    (
        "requires_input_inventory_optional_evidence",
        "eval_window_run_requires_input_inventory_optional_evidence",
    ),
    ("requires_eval_manifest_model_suite", "eval_window_run_requires_eval_manifest_model_suite"),
    (
        "requires_eval_manifest_official_source_thresholds",
        "eval_window_run_requires_eval_manifest_official_source_thresholds",
    ),
    (
        "requires_eval_manifest_unknown_availability_failures",
        "eval_window_run_requires_eval_manifest_unknown_availability_failures",
    ),
    (
        "requires_eval_manifest_ontology_feature_signals",
        "eval_window_run_requires_eval_manifest_ontology_feature_signals",
    ),
)


def _eval_window_source_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, object] = {}
    for source_key, output_key in EVAL_WINDOW_RUN_SOURCE_STATE_KEYS:
        field_value = value.get(source_key)
        if isinstance(field_value, bool):
            source_state[output_key] = field_value
    return source_state


def _emit_result(result: dict[str, object], output_path: Path | None) -> None:
    payload = json.dumps(result, sort_keys=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.name}.{os.urandom(16).hex()}.tmp")
        try:
            temp_path.write_text(payload + "\\n", encoding="utf-8")
            temp_path.replace(output_path)
        finally:
            temp_path.unlink(missing_ok=True)
    print(payload)


def main() -> int:
    root = Path(__file__).resolve().parent
    dotenv_path, repo_root, dry_run, requested_phases, output_path, arg_issues = _parse_args(sys.argv[1:])
    dotenv_values, dotenv_issues = _read_dotenv(dotenv_path)
    plan_code, plan, plan_stderr = _run_plan(root, dotenv_path)
    issues = [*arg_issues, *dotenv_issues]
    if plan_code != 0:
        issues.append("resume_plan_failed")
    if plan_stderr.strip():
        issues.append("resume_plan_stderr")
    selected_phases = _selected_phases(plan, requested_phases, issues)
    selected_missing_env = _phase_missing_env(selected_phases)
    if requested_phases and selected_missing_env:
        for env_name in selected_missing_env:
            issues.append(f"selected_phase_missing_env:{env_name}")
    elif not requested_phases and not bool(plan.get("launch_ready")):
        issues.append("resume_plan_not_launch_ready")
    selected_commands = _phase_commands(selected_phases)
    selected_source_artifacts = _phase_values(selected_phases, "source_artifacts")
    selected_additional_reasons = _phase_values(selected_phases, "additional_reasons")
    resume_script_path = _packet_file(root, "resume_script", "resume_script.sh")
    sample_source_state = _packet_sample_source_state(root)
    selected_source_artifact_sha256 = _selected_source_artifact_sha256(
        repo_root,
        selected_source_artifacts,
    )

    base_result = {
        "command": "run_resume.py",
        "dry_run": dry_run,
        "env_validation": "presence_only",
        "repo_root": str(repo_root),
        "selected_phases": requested_phases,
        "selected_phase_count": len(selected_phases),
        "selected_command_count": len(selected_commands),
        "selected_source_artifacts": selected_source_artifacts,
        "selected_additional_reasons": selected_additional_reasons,
        "selected_missing_env": selected_missing_env,
        "output": str(output_path) if output_path is not None else None,
        "dotenv": {
            "path": str(dotenv_path) if dotenv_path is not None else None,
            "exists": dotenv_path.is_file() if dotenv_path is not None else None,
            "validation": "presence_only",
            "loaded_key_count": len(dotenv_values),
            "issues": dotenv_issues,
        },
        "plan": plan,
        "issues": sorted(set(issues)),
        "run_metadata": {
            "command": "run_resume.py",
            "verification_flags": {
                "dry_run": dry_run,
                "selected_phases": requested_phases,
                "dotenv": str(dotenv_path) if dotenv_path is not None else None,
                "output": str(output_path) if output_path is not None else None,
            },
            "source_artifact_sha256": {
                "packet_export_manifest": _sha256_optional(root / "packet-export-manifest.json"),
                "checksums": _sha256_optional(root / "SHA256SUMS"),
                "resume_plan": _sha256_optional(root / "resume_plan.py"),
                "run_resume": _sha256_optional(root / "run_resume.py"),
                "resume_script": _sha256_optional(resume_script_path),
                "dotenv": _sha256_optional(dotenv_path),
            },
            "selected_source_artifact_sha256": selected_source_artifact_sha256,
            "source_state": {
                "packet_verified": bool(plan.get("packet_verified")),
                "plan_launch_ready": bool(plan.get("launch_ready")),
                "plan_missing_env_count": len(plan.get("missing_env", []))
                if isinstance(plan.get("missing_env"), list)
                else None,
                "loaded_env_names": sorted(dotenv_values),
                "loaded_key_count": len(dotenv_values),
                "selected_phase_count": len(selected_phases),
                "selected_command_count": len(selected_commands),
                "selected_source_artifact_count": len(selected_source_artifacts),
                "selected_additional_reason_count": len(selected_additional_reasons),
                "selected_missing_env_count": len(selected_missing_env),
                "env_validation": "presence_only",
                **sample_source_state,
                **_congress_source_state(plan.get("congress_load")),
                **_eval_window_source_state(plan.get("eval_window_run")),
            },
        },
    }
    if dry_run or issues:
        result = {
            **base_result,
            "ok": not issues,
            "resume_exit_code": None,
        }
        _emit_result(result, output_path)
        return 0 if result["ok"] else 1

    if not repo_root.is_dir():
        result = {
            **base_result,
            "ok": False,
            "resume_exit_code": None,
            "issues": sorted(set([*issues, f"repo root not found: {repo_root}"])),
        }
        _emit_result(result, output_path)
        return 1
    if not resume_script_path.is_file():
        result = {
            **base_result,
            "ok": False,
            "resume_exit_code": None,
            "issues": sorted(set([*issues, "resume_script_missing"])),
        }
        _emit_result(result, output_path)
        return 1

    env = os.environ.copy()
    env.update(dotenv_values)
    if requested_phases:
        exit_code = 0
        for command in selected_commands:
            proc = subprocess.run(shlex.split(command), cwd=repo_root, env=env, check=False)
            if proc.returncode != 0:
                exit_code = proc.returncode
                break
    else:
        proc = subprocess.run(["bash", str(resume_script_path)], cwd=repo_root, env=env, check=False)
        exit_code = proc.returncode
    result = {
        **base_result,
        "ok": exit_code == 0,
        "resume_exit_code": exit_code,
    }
    _emit_result(result, output_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _prediction_operator_packet_standalone_resume_runner_verifier() -> str:
    return '''#!/usr/bin/env python3
"""Verify a run_resume.py JSON artifact against this packet."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


TOKEN_PREFIX = "s" + "k-"
SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
REQUIRED_CONGRESS_PREDICTION_SOURCE_FAMILY_IDS = {
    "committee_membership",
    "congress_bill",
    "congress_vote",
}
STRICT_EVAL_WINDOW_SOURCE_STATE_KEYS = {
    "eval_window_run_requires_input_inventory_portable_ids",
    "eval_window_run_requires_input_inventory_congress_source_families",
    "eval_window_run_requires_input_inventory_official_source_thresholds",
    "eval_window_run_requires_input_inventory_optional_evidence",
    "eval_window_run_requires_eval_manifest_model_suite",
    "eval_window_run_requires_eval_manifest_official_source_thresholds",
    "eval_window_run_requires_eval_manifest_unknown_availability_failures",
    "eval_window_run_requires_eval_manifest_ontology_feature_signals",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _load_json(path: Path, issues: list[str]) -> dict[str, Any]:
    if not path.is_file():
        issues.append(f"artifact_missing:{path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        issues.append("artifact_invalid_json")
        return {}
    if not isinstance(payload, dict):
        issues.append("artifact_not_object")
        return {}
    return payload


def _metadata(payload: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    raw = payload.get("run_metadata")
    if not isinstance(raw, dict):
        issues.append("run_metadata_missing")
        return {}
    if raw.get("command") != "run_resume.py":
        issues.append("run_metadata_command_mismatch")
    return raw


def _source_hashes(metadata: dict[str, Any], issues: list[str]) -> tuple[dict[str, str], set[str]]:
    raw = metadata.get("source_artifact_sha256")
    if not isinstance(raw, dict):
        issues.append("source_artifact_sha256_missing")
        return {}, set()
    hashes: dict[str, str] = {}
    invalid_hash_keys: set[str] = set()
    for key, value in raw.items():
        normalized_key = str(key)
        if not isinstance(value, str):
            issue = (
                "dotenv_hash_invalid"
                if normalized_key == "dotenv"
                else f"source_hash_invalid:{normalized_key}"
            )
            issues.append(issue)
            invalid_hash_keys.add(normalized_key)
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            issue = (
                "dotenv_hash_invalid"
                if normalized_key == "dotenv"
                else f"source_hash_invalid:{normalized_key}"
            )
            issues.append(issue)
            invalid_hash_keys.add(normalized_key)
            continue
        hashes[normalized_key] = value
    return hashes, invalid_hash_keys


def _packet_file(root: Path, name: str, fallback: str) -> Path:
    manifest = _load_json(root / "packet-export-manifest.json", [])
    exported_files = manifest.get("exported_files")
    if isinstance(exported_files, list):
        for entry in exported_files:
            if not isinstance(entry, dict) or entry.get("name") != name:
                continue
            exported_path = entry.get("exported_path")
            if isinstance(exported_path, str):
                return root / "files" / Path(exported_path).name
    return root / "files" / fallback


def _check_packet_hashes(
    root: Path,
    hashes: dict[str, str],
    invalid_hash_keys: set[str],
    issues: list[str],
) -> tuple[list[str], list[str], list[str]]:
    expected_paths = {
        "packet_export_manifest": root / "packet-export-manifest.json",
        "checksums": root / "SHA256SUMS",
        "resume_plan": root / "resume_plan.py",
        "run_resume": root / "run_resume.py",
        "resume_script": _packet_file(root, "resume_script", "resume_script.sh"),
    }
    source_hash_mismatches: list[str] = []
    source_hash_missing: list[str] = []
    source_file_missing: list[str] = []
    for key, path in expected_paths.items():
        if key in invalid_hash_keys:
            continue
        expected = hashes.get(key)
        if expected is None:
            source_hash_missing.append(key)
            issues.append(f"source_hash_missing:{key}")
            continue
        if not path.is_file():
            source_file_missing.append(key)
            issues.append(f"source_file_missing:{key}")
            continue
        if _sha256(path) != expected:
            source_hash_mismatches.append(key)
            issues.append(f"source_hash_mismatch:{key}")
    return source_hash_mismatches, source_hash_missing, source_file_missing


def _secret_hits(payload: dict[str, Any]) -> list[str]:
    text = json.dumps(payload, sort_keys=True)
    hits: list[str] = []
    if TOKEN_PREFIX in text:
        hits.append("openai_token")
    if re.search(r"\\bpostgres(?:ql)?://\\S+", text):
        hits.append("postgres_dsn")
    return sorted(set(hits))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _selected_phase_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    phases = [phase for phase in plan.get("phases", []) if isinstance(phase, dict)]
    selected = [
        value
        for value in payload.get("selected_phases", [])
        if type(value) is int
    ]
    if not selected:
        return phases
    selected_numbers = set(selected)
    return [phase for phase in phases if phase.get("phase") in selected_numbers]


def _phase_values(phases: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for phase in phases:
        for value in _string_list(phase.get(key)):
            if value not in values:
                values.append(value)
    return values


def _selected_provenance_issues(payload: dict[str, Any]) -> list[str]:
    phases = _selected_phase_rows(payload)
    issues: list[str] = []
    if _string_list(payload.get("selected_source_artifacts")) != _phase_values(
        phases,
        "source_artifacts",
    ):
        issues.append("selected_source_artifacts_mismatch")
    if _string_list(payload.get("selected_additional_reasons")) != _phase_values(
        phases,
        "additional_reasons",
    ):
        issues.append("selected_additional_reasons_mismatch")
    return issues


def _selected_count_issues(payload: dict[str, Any]) -> list[str]:
    phases = _selected_phase_rows(payload)
    issues: list[str] = []
    issues.extend(_selected_phases_shape_issues(payload.get("selected_phases")))
    if type(payload.get("selected_phase_count")) is not int:
        issues.append("selected_phase_count must be an integer")
    elif payload.get("selected_phase_count") < 0:
        issues.append("selected_phase_count must be a non-negative integer")
    elif payload.get("selected_phase_count") != len(phases):
        issues.append("selected_phase_count_mismatch")
    if type(payload.get("selected_command_count")) is not int:
        issues.append("selected_command_count must be an integer")
    elif payload.get("selected_command_count") < 0:
        issues.append("selected_command_count must be a non-negative integer")
    elif payload.get("selected_command_count") != _phase_value_count(phases, "commands"):
        issues.append("selected_command_count_mismatch")
    expected_missing_env = sorted(set(_phase_values(phases, "missing_env")))
    if _string_list(payload.get("selected_missing_env")) != expected_missing_env:
        issues.append("selected_missing_env_mismatch")
    return issues


def _selected_phases_shape_issues(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        return ["selected_phases must be a list of integers"]
    return []


def _plain_int_or_none(value: Any) -> int | None:
    return value if type(value) is int else None


def _phase_value_count(phases: list[dict[str, Any]], key: str) -> int:
    return sum(len(_string_list(phase.get(key))) for phase in phases)


def _plain_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if type(item) is int]


def _is_non_negative_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(type(item) is int and item >= 0 for item in value)


def _is_sorted_unique_non_empty_string_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


def _source_family_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and SOURCE_FAMILY_ID_RE.fullmatch(item) is not None
        for item in value
    )


def _optional_sample_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    int_keys = ("selected_sample_vote_event_ids",)
    string_keys = (
        "selected_sample_bill_keys",
        "selected_sample_member_bioguide_ids",
        "selected_sample_jurisdiction_ids",
        "selected_sample_legislative_body_ids",
        "selected_sample_legislative_session_ids",
        "selected_sample_source_family_ids",
    )
    for key in int_keys:
        if key in source_state and not _is_non_negative_int_list(source_state.get(key)):
            issues.append(
                f"run_metadata source_state {key} must be a list of non-negative integers"
            )
    for key in string_keys:
        if key in source_state and not _is_sorted_unique_non_empty_string_list(
            source_state.get(key)
        ):
            issues.append(
                f"run_metadata source_state {key} must be a sorted unique list of non-empty strings"
            )
        elif (
            key == "selected_sample_source_family_ids"
            and key in source_state
            and not _source_family_id_list(source_state.get(key))
        ):
            issues.append(
                f"run_metadata source_state {key} must contain normalized source family ids"
            )
    issues.extend(_sample_scoped_id_issues(source_state))
    return issues


def _congress_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
    ):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"run_metadata source_state {key} must be a boolean")
    if "congress_load_source_family_ids" in source_state and not _is_sorted_unique_non_empty_string_list(
        source_state.get("congress_load_source_family_ids")
    ):
        issues.append(
            "run_metadata source_state congress_load_source_family_ids must be a sorted unique list of non-empty strings"
        )
    elif "congress_load_source_family_ids" in source_state and not _source_family_id_list(
        source_state.get("congress_load_source_family_ids")
    ):
        issues.append(
            "run_metadata source_state congress_load_source_family_ids must contain normalized source family ids"
        )
    if "congress_load_source_family_count" in source_state:
        value = source_state.get("congress_load_source_family_count")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.append(
                "run_metadata source_state congress_load_source_family_count must be a non-negative integer"
            )
        elif isinstance(source_state.get("congress_load_source_family_ids"), list) and value != len(
            source_state["congress_load_source_family_ids"]
        ):
            issues.append("run_metadata source_state congress_load_source_family_count mismatch")
    for key in (
        "congress_load_member_row_count",
        "congress_load_bill_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    ):
        value = source_state.get(key)
        if key in source_state and (not isinstance(value, int) or isinstance(value, bool)):
            issues.append(f"run_metadata source_state {key} must be an integer")
        elif key in source_state and value < 0:
            issues.append(f"run_metadata source_state {key} must be a non-negative integer")
    issues.extend(_congress_prediction_input_row_count_issues(source_state))
    return issues


def _strict_eval_window_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in sorted(STRICT_EVAL_WINDOW_SOURCE_STATE_KEYS):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"run_metadata source_state {key} must be a boolean")
    return issues


def _strict_eval_window_run_ready(metadata: dict[str, Any]) -> bool:
    source_state = metadata.get("source_state")
    if not isinstance(source_state, dict):
        return False
    return all(source_state.get(key) is True for key in STRICT_EVAL_WINDOW_SOURCE_STATE_KEYS)


def _congress_prediction_input_row_count_issues(source_state: dict[str, Any]) -> list[str]:
    ready_booleans = all(
        source_state.get(key) is True
        for key in (
            "congress_load_required",
            "congress_load_present",
            "congress_load_ok",
            "congress_load_prediction_member_inputs_available",
            "congress_load_prediction_bill_inputs_available",
            "congress_load_prediction_vote_inputs_available",
        )
    )
    source_family_ids = source_state.get("congress_load_source_family_ids")
    ready_families = (
        _is_sorted_unique_non_empty_string_list(source_family_ids)
        and _source_family_id_list(source_family_ids)
        and REQUIRED_CONGRESS_PREDICTION_SOURCE_FAMILY_IDS.issubset(set(source_family_ids))
    )
    if not ready_booleans or not ready_families:
        return []
    issues: list[str] = []
    for key in (
        "congress_load_member_row_count",
        "congress_load_bill_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    ):
        value = source_state.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            issues.append(f"run_metadata source_state {key} must be positive for congress readiness")
    return issues


def _sample_scoped_id_issues(source_state: dict[str, Any]) -> list[str]:
    jurisdiction_key = "selected_sample_jurisdiction_ids"
    body_key = "selected_sample_legislative_body_ids"
    session_key = "selected_sample_legislative_session_ids"
    if not any(key in source_state for key in (jurisdiction_key, body_key, session_key)):
        return []
    jurisdiction_ids = source_state.get(jurisdiction_key)
    body_ids = source_state.get(body_key)
    session_ids = source_state.get(session_key)
    if not _is_sorted_unique_non_empty_string_list(jurisdiction_ids):
        return []
    jurisdiction_id_set = set(jurisdiction_ids)
    issues: list[str] = []
    body_id_set: set[str] = set()
    if body_ids is not None and _is_sorted_unique_non_empty_string_list(body_ids):
        body_id_set = set(body_ids)
        if any(
            len(parts := item.split(":")) != 2
            or parts[0] not in jurisdiction_id_set
            or not parts[1]
            for item in body_ids
        ):
            issues.append(
                "run_metadata source_state selected_sample_legislative_body_ids must contain scoped ids"
            )
    if session_ids is not None and _is_sorted_unique_non_empty_string_list(session_ids):
        if any(
            len(parts := item.split(":")) != 3
            or parts[0] not in jurisdiction_id_set
            or f"{parts[0]}:{parts[1]}" not in body_id_set
            or not parts[2]
            for item in session_ids
        ):
            issues.append(
                "run_metadata source_state selected_sample_legislative_session_ids must contain scoped ids"
            )
    return issues


def _expected_source_state(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    dotenv = payload.get("dotenv") if isinstance(payload.get("dotenv"), dict) else {}
    missing_env = plan.get("missing_env")
    loaded_key_count = dotenv.get("loaded_key_count")
    env_validation = payload.get("env_validation")
    expected = {
        "packet_verified": bool(plan.get("packet_verified")),
        "plan_launch_ready": bool(plan.get("launch_ready")),
        "plan_missing_env_count": len(missing_env) if isinstance(missing_env, list) else None,
        "loaded_key_count": _plain_int_or_none(loaded_key_count),
        "selected_phase_count": _plain_int_or_none(payload.get("selected_phase_count")),
        "selected_command_count": _plain_int_or_none(payload.get("selected_command_count")),
        "selected_source_artifact_count": len(_string_list(payload.get("selected_source_artifacts"))),
        "selected_additional_reason_count": len(
            _string_list(payload.get("selected_additional_reasons"))
        ),
        "selected_missing_env_count": len(_string_list(payload.get("selected_missing_env"))),
        "env_validation": env_validation if isinstance(env_validation, str) else None,
    }
    expected.update(_congress_source_state(plan.get("congress_load")))
    expected.update(_eval_window_source_state(plan.get("eval_window_run")))
    return expected


def _congress_source_state(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, Any] = {}
    for source_key, output_key in (
        ("required", "congress_load_required"),
        ("present", "congress_load_present"),
        ("ok", "congress_load_ok"),
        (
            "prediction_member_inputs_available",
            "congress_load_prediction_member_inputs_available",
        ),
        ("prediction_bill_inputs_available", "congress_load_prediction_bill_inputs_available"),
        ("prediction_vote_inputs_available", "congress_load_prediction_vote_inputs_available"),
    ):
        field_value = value.get(source_key)
        if isinstance(field_value, bool):
            source_state[output_key] = field_value
    source_family_ids = value.get("source_family_ids")
    if (
        isinstance(source_family_ids, list)
        and _is_sorted_unique_non_empty_string_list(source_family_ids)
        and _source_family_id_list(source_family_ids)
    ):
        source_state["congress_load_source_family_ids"] = source_family_ids
    for source_key, output_key in (
        ("member_row_count", "congress_load_member_row_count"),
        ("bill_row_count", "congress_load_bill_row_count"),
        ("vote_event_row_count", "congress_load_vote_event_row_count"),
        ("vote_cast_row_count", "congress_load_vote_cast_row_count"),
    ):
        field_value = value.get(source_key)
        if isinstance(field_value, int) and not isinstance(field_value, bool) and field_value >= 0:
            source_state[output_key] = field_value
    return source_state


EVAL_WINDOW_RUN_SOURCE_STATE_KEYS = (
    (
        "requires_input_inventory_portable_ids",
        "eval_window_run_requires_input_inventory_portable_ids",
    ),
    (
        "requires_input_inventory_congress_source_families",
        "eval_window_run_requires_input_inventory_congress_source_families",
    ),
    (
        "requires_input_inventory_official_source_thresholds",
        "eval_window_run_requires_input_inventory_official_source_thresholds",
    ),
    (
        "requires_input_inventory_optional_evidence",
        "eval_window_run_requires_input_inventory_optional_evidence",
    ),
    ("requires_eval_manifest_model_suite", "eval_window_run_requires_eval_manifest_model_suite"),
    (
        "requires_eval_manifest_official_source_thresholds",
        "eval_window_run_requires_eval_manifest_official_source_thresholds",
    ),
    (
        "requires_eval_manifest_unknown_availability_failures",
        "eval_window_run_requires_eval_manifest_unknown_availability_failures",
    ),
    (
        "requires_eval_manifest_ontology_feature_signals",
        "eval_window_run_requires_eval_manifest_ontology_feature_signals",
    ),
)


def _eval_window_source_state(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, bool] = {}
    for source_key, output_key in EVAL_WINDOW_RUN_SOURCE_STATE_KEYS:
        field_value = value.get(source_key)
        if isinstance(field_value, bool):
            source_state[output_key] = field_value
    return source_state


def _expected_verification_flags(payload: dict[str, Any]) -> dict[str, Any]:
    dotenv = payload.get("dotenv") if isinstance(payload.get("dotenv"), dict) else {}
    dotenv_path = dotenv.get("path")
    output = payload.get("output")
    return {
        "dry_run": bool(payload.get("dry_run")),
        "selected_phases": _plain_int_list(payload.get("selected_phases")),
        "dotenv": dotenv_path if isinstance(dotenv_path, str) else None,
        "output": output if isinstance(output, str) else None,
    }


def _source_state_issues(
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> list[str]:
    if not metadata:
        return []
    source_state = metadata.get("source_state")
    if not isinstance(source_state, dict):
        return ["run_metadata_source_state_mismatch"]
    sample_scope_issues = _optional_sample_source_state_shape_issues(source_state)
    shape_issues = [
        *sample_scope_issues,
        *_congress_source_state_shape_issues(source_state),
        *_strict_eval_window_source_state_shape_issues(source_state),
    ]
    if shape_issues:
        return shape_issues
    for key, value in _expected_source_state(payload).items():
        if source_state.get(key) != value:
            return ["run_metadata_source_state_mismatch"]
    return []


def _verification_flags_issues(
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> list[str]:
    if not metadata:
        return []
    flags = metadata.get("verification_flags")
    if not isinstance(flags, dict):
        return ["run_metadata_verification_flags_mismatch"]
    for key, value in _expected_verification_flags(payload).items():
        if flags.get(key) != value:
            return ["run_metadata_verification_flags_mismatch"]
    return []


def _parse_args(
    argv: list[str],
) -> tuple[Path | None, Path | None, bool, bool, bool, list[str]]:
    artifact: Path | None = None
    dotenv: Path | None = None
    require_ok = False
    require_strict_eval_window_run = False
    require_selected_source_artifact_hashes = False
    issues: list[str] = []
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--artifact" and args:
            artifact = _resolve(args.pop(0))
            continue
        if item == "--dotenv" and args:
            dotenv = _resolve(args.pop(0))
            continue
        if item == "--require-ok":
            require_ok = True
            continue
        if item == "--require-strict-eval-window-run":
            require_strict_eval_window_run = True
            continue
        if item == "--require-selected-source-artifact-hashes":
            require_selected_source_artifact_hashes = True
            continue
        issues.append(
            "usage: python3 verify_run_resume.py --artifact PATH [--dotenv PATH] "
            "[--require-ok] [--require-strict-eval-window-run] "
            "[--require-selected-source-artifact-hashes]"
        )
        break
    if artifact is None:
        issues.append("artifact_required")
    return (
        artifact,
        dotenv,
        require_ok,
        require_strict_eval_window_run,
        require_selected_source_artifact_hashes,
        issues,
    )


def main() -> int:
    root = Path(__file__).resolve().parent
    (
        artifact_path,
        dotenv_path,
        require_ok,
        require_strict_eval_window_run,
        require_selected_source_artifact_hashes,
        issues,
    ) = _parse_args(sys.argv[1:])
    payload = _load_json(artifact_path, issues) if artifact_path is not None else {}
    if payload.get("command") != "run_resume.py":
        issues.append("artifact_command_mismatch")
    env_validation = payload.get("env_validation")
    if env_validation != "presence_only":
        issues.append("env_validation_missing_or_unknown")
    if require_ok and not bool(payload.get("ok")):
        issues.append("artifact_not_ok")
    metadata = _metadata(payload, issues)
    strict_eval_window_run_ready = _strict_eval_window_run_ready(metadata)
    if require_strict_eval_window_run and not strict_eval_window_run_ready:
        issues.append("strict_eval_window_run_not_ready")
    hashes, invalid_hash_keys = _source_hashes(metadata, issues)
    source_hash_mismatches, source_hash_missing, source_file_missing = _check_packet_hashes(
        root,
        hashes,
        invalid_hash_keys,
        issues,
    )
    selected_count_issues = _selected_count_issues(payload)
    issues.extend(selected_count_issues)
    selected_provenance_issues = _selected_provenance_issues(payload)
    issues.extend(selected_provenance_issues)
    selected_source_artifact_count = len(_string_list(payload.get("selected_source_artifacts")))
    selected_source_artifact_hashes = metadata.get("selected_source_artifact_sha256")
    selected_source_artifact_hash_count = (
        sum(
            1
            for key, value in selected_source_artifact_hashes.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and _is_sha256_hex(value)
            and key in set(_string_list(payload.get("selected_source_artifacts")))
        )
        if isinstance(selected_source_artifact_hashes, dict)
        else 0
    )
    selected_source_artifact_hash_missing_count = max(
        0,
        selected_source_artifact_count - selected_source_artifact_hash_count,
    )
    selected_source_artifact_hash_coverage_complete = (
        selected_source_artifact_hash_missing_count == 0
    )
    if (
        require_selected_source_artifact_hashes
        and not selected_source_artifact_hash_coverage_complete
    ):
        issues.append("selected_source_artifact_hashes_incomplete")
    source_state_issues = _source_state_issues(payload, metadata)
    issues.extend(source_state_issues)
    verification_flags_issues = _verification_flags_issues(payload, metadata)
    issues.extend(verification_flags_issues)
    if dotenv_path is not None:
        expected_dotenv_hash = hashes.get("dotenv")
        if "dotenv" in invalid_hash_keys:
            pass
        elif expected_dotenv_hash is None:
            issues.append("dotenv_hash_missing")
        elif not dotenv_path.is_file():
            issues.append("dotenv_missing")
        elif _sha256(dotenv_path) != expected_dotenv_hash:
            issues.append("dotenv_hash_mismatch")
    secret_hits = _secret_hits(payload)
    if secret_hits:
        issues.extend(f"secret_literal:{hit}" for hit in secret_hits)
    result = {
        "ok": not issues,
        "command": "verify_run_resume.py",
        "artifact": str(artifact_path) if artifact_path is not None else None,
        "artifact_sha256": _sha256(artifact_path)
        if artifact_path is not None and artifact_path.is_file()
        else None,
        "dotenv": str(dotenv_path) if dotenv_path is not None else None,
        "env_validation": env_validation if isinstance(env_validation, str) else None,
        "require_ok": require_ok,
        "require_strict_eval_window_run": require_strict_eval_window_run,
        "require_selected_source_artifact_hashes": require_selected_source_artifact_hashes,
        "strict_eval_window_run_ready": strict_eval_window_run_ready,
        "selected_source_artifact_hash_count": selected_source_artifact_hash_count,
        "selected_source_artifact_hash_missing_count": selected_source_artifact_hash_missing_count,
        "selected_source_artifact_hash_coverage_complete": (
            selected_source_artifact_hash_coverage_complete
        ),
        "selected_counts_match": not selected_count_issues,
        "selected_provenance_matches": not selected_provenance_issues,
        "source_state_matches": not source_state_issues,
        "verification_flags_match": not verification_flags_issues,
        "source_hash_missing": sorted(source_hash_missing),
        "source_hash_missing_count": len(source_hash_missing),
        "source_hash_invalid": sorted(invalid_hash_keys),
        "source_hash_invalid_count": len(invalid_hash_keys),
        "source_file_missing": sorted(source_file_missing),
        "source_file_missing_count": len(source_file_missing),
        "source_hash_mismatches": sorted(source_hash_mismatches),
        "source_hash_mismatch_count": len(source_hash_mismatches),
        "secret_literal_count": len(secret_hits),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _handle_verify_prediction_operator_packet_export(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    export = _load_json_packet_payload(artifact_path, "packet_export", issues)
    if export.get("command") != "prediction-operator-packet-export":
        issues.append("artifact_command_mismatch")
    run_metadata = (
        export.get("run_metadata") if isinstance(export.get("run_metadata"), dict) else {}
    )
    if bool(getattr(args, "require_run_metadata", False)) and not run_metadata:
        quality_gate_failures.append("run_metadata_missing")
    issues.extend(_operator_packet_export_count_issues(export))
    export_metadata_source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    if run_metadata:
        source_state_sample_scope_issues = _operator_status_sample_source_state_issues(
            export_metadata_source_state
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_packet_export_source_state_count_issues(
                export_metadata_source_state
            )
        )
        issues.extend(
            f"run_metadata.source_state.{issue}" for issue in source_state_sample_scope_issues
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_packet_export_source_state_mismatch_issues(
                export,
                export_metadata_source_state,
                allow_optional_sample_scope=True,
            )
        )

    export_manifest_path = _path_from_payload(export, "export_manifest")
    export_manifest = _load_json_packet_payload(
        export_manifest_path,
        "export_manifest",
        issues,
        required=bool(getattr(args, "require_export_manifest", False)),
    )
    top_level_sha256_invalid: list[str] = []
    export_manifest_matches = True
    if export_manifest_path is None:
        export_manifest_matches = False
    else:
        actual_manifest_sha = _optional_file_sha256(export_manifest_path)
        if _operator_packet_recorded_sha256_invalid(
            export,
            "export_manifest_sha256",
            "export_manifest",
            issues,
            top_level_sha256_invalid,
        ):
            export_manifest_matches = False
        elif export.get("export_manifest_sha256") != actual_manifest_sha:
            export_manifest_matches = False
    if export_manifest and export_manifest.get("exported_files") != export.get("exported_files"):
        export_manifest_matches = False
    if bool(getattr(args, "require_export_manifest", False)) and not export_manifest_matches:
        quality_gate_failures.append("export_manifest_mismatch")

    packet_readme_path = _path_from_payload(export, "packet_readme")
    packet_readme_matches = True
    if packet_readme_path is None or not packet_readme_path.is_file():
        packet_readme_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "packet_readme_sha256",
        "packet_readme",
        issues,
        top_level_sha256_invalid,
    ):
        packet_readme_matches = False
    elif export.get("packet_readme_sha256") != _optional_file_sha256(packet_readme_path):
        packet_readme_matches = False

    packet_verifier_path = _path_from_payload(export, "packet_verifier")
    packet_verifier_matches = True
    if packet_verifier_path is None or not packet_verifier_path.is_file():
        packet_verifier_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "packet_verifier_sha256",
        "packet_verifier",
        issues,
        top_level_sha256_invalid,
    ):
        packet_verifier_matches = False
    elif export.get("packet_verifier_sha256") != _optional_file_sha256(packet_verifier_path):
        packet_verifier_matches = False

    packet_resume_planner_path = _path_from_payload(export, "packet_resume_planner")
    packet_resume_planner_matches = True
    if packet_resume_planner_path is None or not packet_resume_planner_path.is_file():
        packet_resume_planner_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "packet_resume_planner_sha256",
        "packet_resume_planner",
        issues,
        top_level_sha256_invalid,
    ):
        packet_resume_planner_matches = False
    elif export.get("packet_resume_planner_sha256") != _optional_file_sha256(
        packet_resume_planner_path
    ):
        packet_resume_planner_matches = False

    packet_resume_runner_path = _path_from_payload(export, "packet_resume_runner")
    packet_resume_runner_matches = True
    if packet_resume_runner_path is None or not packet_resume_runner_path.is_file():
        packet_resume_runner_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "packet_resume_runner_sha256",
        "packet_resume_runner",
        issues,
        top_level_sha256_invalid,
    ):
        packet_resume_runner_matches = False
    elif export.get("packet_resume_runner_sha256") != _optional_file_sha256(
        packet_resume_runner_path
    ):
        packet_resume_runner_matches = False

    packet_resume_runner_verifier_path = _path_from_payload(
        export,
        "packet_resume_runner_verifier",
    )
    packet_resume_runner_verifier_matches = True
    if (
        packet_resume_runner_verifier_path is None
        or not packet_resume_runner_verifier_path.is_file()
    ):
        packet_resume_runner_verifier_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "packet_resume_runner_verifier_sha256",
        "packet_resume_runner_verifier",
        issues,
        top_level_sha256_invalid,
    ):
        packet_resume_runner_verifier_matches = False
    elif export.get("packet_resume_runner_verifier_sha256") != _optional_file_sha256(
        packet_resume_runner_verifier_path
    ):
        packet_resume_runner_verifier_matches = False

    checksums_path = _path_from_payload(export, "checksums")
    checksums_matches = True
    if checksums_path is None or not checksums_path.is_file():
        checksums_matches = False
    elif _operator_packet_recorded_sha256_invalid(
        export,
        "checksums_sha256",
        "checksums",
        issues,
        top_level_sha256_invalid,
    ):
        checksums_matches = False
    elif export.get("checksums_sha256") != _optional_file_sha256(checksums_path):
        checksums_matches = False

    exported_files = (
        export.get("exported_files") if isinstance(export.get("exported_files"), list) else []
    )
    checksums_content_matches = _operator_packet_checksums_content_matches(
        checksums_path=checksums_path,
        export_manifest_path=export_manifest_path,
        packet_readme_path=packet_readme_path,
        packet_verifier_path=packet_verifier_path,
        packet_resume_planner_path=packet_resume_planner_path,
        packet_resume_runner_path=packet_resume_runner_path,
        packet_resume_runner_verifier_path=packet_resume_runner_verifier_path,
        exported_files=exported_files,
    )
    missing_exported_file_count = 0
    exported_file_sha256_invalid: list[str] = []
    exported_file_sha256_mismatch_count = 0
    for entry in exported_files:
        if not isinstance(entry, dict):
            continue
        path_raw = entry.get("exported_path")
        if not isinstance(path_raw, str) or not Path(path_raw).is_file():
            missing_exported_file_count += 1
            continue
        actual_sha = _optional_file_sha256(Path(path_raw))
        expected_sha = entry.get("exported_sha256")
        exported_file_name = str(entry.get("name") or Path(path_raw).name)
        if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
            exported_file_sha256_invalid.append(exported_file_name)
            issues.append(f"exported_file_sha256_invalid:{exported_file_name}")
        elif expected_sha != actual_sha:
            exported_file_sha256_mismatch_count += 1
    if bool(getattr(args, "require_exported_files", False)):
        if missing_exported_file_count:
            quality_gate_failures.append("exported_files_missing")
        if exported_file_sha256_invalid:
            quality_gate_failures.append("exported_file_sha256_invalid")
        if exported_file_sha256_mismatch_count:
            quality_gate_failures.append("exported_file_sha256_mismatch")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_packet_export_secret_literal_failures(
            [entry for entry in exported_files if isinstance(entry, dict)]
        )
        for extra_path, name in (
            (export_manifest_path, "packet-export-manifest"),
            (packet_readme_path, "README"),
            (packet_verifier_path, "verify_packet.py"),
            (packet_resume_planner_path, "resume_plan.py"),
            (packet_resume_runner_path, "run_resume.py"),
            (packet_resume_runner_verifier_path, "verify_run_resume.py"),
            (checksums_path, "SHA256SUMS"),
        ):
            if extra_path is None or not extra_path.is_file():
                continue
            text = extra_path.read_text(encoding="utf-8", errors="ignore")
            if "sk-" in text:
                secret_failures.append(f"secret_literal:openai_token:{name}")
            if re.search(r"\bpostgres(?:ql)?://\S+", text):
                secret_failures.append(f"secret_literal:postgres_dsn:{name}")
        quality_gate_failures.extend(secret_failures)

    source_state = {
        "export_manifest_matches": export_manifest_matches,
        "packet_readme_matches": packet_readme_matches,
        "packet_verifier_matches": packet_verifier_matches,
        "packet_resume_planner_matches": packet_resume_planner_matches,
        "packet_resume_runner_matches": packet_resume_runner_matches,
        "packet_resume_runner_verifier_matches": (packet_resume_runner_verifier_matches),
        "checksums_matches": checksums_matches,
        "checksums_content_matches": checksums_content_matches,
        "top_level_sha256_invalid_count": len(top_level_sha256_invalid),
        "exported_file_sha256_invalid_count": len(exported_file_sha256_invalid),
        "exported_file_sha256_mismatch_count": exported_file_sha256_mismatch_count,
        "missing_exported_file_count": missing_exported_file_count,
        "secret_literal_count": len(secret_failures),
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(export_metadata_source_state)
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-packet-export",
        "artifact": str(artifact_path),
        "artifact_sha256": _optional_file_sha256(artifact_path),
        "export_manifest": str(export_manifest_path) if export_manifest_path is not None else None,
        "export_manifest_sha256": _optional_file_sha256(export_manifest_path)
        if export_manifest_path is not None
        else None,
        "export_manifest_matches": export_manifest_matches,
        "packet_readme": str(packet_readme_path) if packet_readme_path is not None else None,
        "packet_readme_sha256": _optional_file_sha256(packet_readme_path)
        if packet_readme_path is not None
        else None,
        "packet_readme_matches": packet_readme_matches,
        "packet_verifier": str(packet_verifier_path) if packet_verifier_path is not None else None,
        "packet_verifier_sha256": _optional_file_sha256(packet_verifier_path)
        if packet_verifier_path is not None
        else None,
        "packet_verifier_matches": packet_verifier_matches,
        "packet_resume_planner": str(packet_resume_planner_path)
        if packet_resume_planner_path is not None
        else None,
        "packet_resume_planner_sha256": _optional_file_sha256(packet_resume_planner_path)
        if packet_resume_planner_path is not None
        else None,
        "packet_resume_planner_matches": packet_resume_planner_matches,
        "packet_resume_runner": str(packet_resume_runner_path)
        if packet_resume_runner_path is not None
        else None,
        "packet_resume_runner_sha256": _optional_file_sha256(packet_resume_runner_path)
        if packet_resume_runner_path is not None
        else None,
        "packet_resume_runner_matches": packet_resume_runner_matches,
        "packet_resume_runner_verifier": str(packet_resume_runner_verifier_path)
        if packet_resume_runner_verifier_path is not None
        else None,
        "packet_resume_runner_verifier_sha256": _optional_file_sha256(
            packet_resume_runner_verifier_path
        )
        if packet_resume_runner_verifier_path is not None
        else None,
        "packet_resume_runner_verifier_matches": (packet_resume_runner_verifier_matches),
        "checksums": str(checksums_path) if checksums_path is not None else None,
        "checksums_sha256": _optional_file_sha256(checksums_path)
        if checksums_path is not None
        else None,
        "checksums_matches": checksums_matches,
        "checksums_content_matches": checksums_content_matches,
        "top_level_sha256_invalid": sorted(top_level_sha256_invalid),
        "top_level_sha256_invalid_count": len(top_level_sha256_invalid),
        "exported_file_sha256_invalid": sorted(exported_file_sha256_invalid),
        "exported_file_sha256_invalid_count": len(exported_file_sha256_invalid),
        "exported_file_sha256_mismatch_count": exported_file_sha256_mismatch_count,
        "missing_exported_file_count": missing_exported_file_count,
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-packet-export",
            "verification_flags": {
                "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
                "require_export_manifest": bool(getattr(args, "require_export_manifest", False)),
                "require_exported_files": bool(getattr(args, "require_exported_files", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": {
                "artifact": _optional_file_sha256(artifact_path),
                "export_manifest": _optional_file_sha256(export_manifest_path)
                if export_manifest_path is not None
                else None,
                "packet_readme": _optional_file_sha256(packet_readme_path)
                if packet_readme_path is not None
                else None,
                "packet_verifier": _optional_file_sha256(packet_verifier_path)
                if packet_verifier_path is not None
                else None,
                "packet_resume_planner": _optional_file_sha256(packet_resume_planner_path)
                if packet_resume_planner_path is not None
                else None,
                "packet_resume_runner": _optional_file_sha256(packet_resume_runner_path)
                if packet_resume_runner_path is not None
                else None,
                "packet_resume_runner_verifier": _optional_file_sha256(
                    packet_resume_runner_verifier_path
                )
                if packet_resume_runner_verifier_path is not None
                else None,
                "checksums": _optional_file_sha256(checksums_path)
                if checksums_path is not None
                else None,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _operator_packet_export_count_issues(export: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "exported_file_count",
        "skipped_file_count",
        "secret_literal_count",
    ):
        if not _is_plain_int(export.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(export.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    for key in ("issue_count", "quality_gate_failure_count"):
        if key in export and not _is_plain_int(export.get(key)):
            issues.append(f"{key} must be an integer")
        elif key in export and not _is_non_negative_plain_int(export.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    exported_files = export.get("exported_files")
    if (
        _is_non_negative_plain_int(export.get("exported_file_count"))
        and isinstance(exported_files, list)
        and export["exported_file_count"] != len(exported_files)
    ):
        issues.append("exported_file_count_mismatch")
    skipped_files = export.get("skipped_files")
    if (
        _is_non_negative_plain_int(export.get("skipped_file_count"))
        and isinstance(skipped_files, list)
        and export["skipped_file_count"] != len(skipped_files)
    ):
        issues.append("skipped_file_count_mismatch")
    artifact_issues = export.get("issues")
    if (
        _is_non_negative_plain_int(export.get("issue_count"))
        and isinstance(artifact_issues, list)
        and export["issue_count"] != len(artifact_issues)
    ):
        issues.append("issue_count_mismatch")
    failures = export.get("quality_gate_failures")
    if (
        _is_non_negative_plain_int(export.get("quality_gate_failure_count"))
        and isinstance(failures, list)
        and export["quality_gate_failure_count"] != len(failures)
    ):
        issues.append("quality_gate_failure_count_mismatch")
    return issues


def _operator_packet_export_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "exported_file_count",
        "skipped_file_count",
        "secret_literal_count",
    ):
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    return issues


def _operator_packet_export_source_state_mismatch_issues(
    export: dict[str, Any],
    source_state: dict[str, Any],
    *,
    allow_optional_sample_scope: bool = False,
) -> list[str]:
    issues: list[str] = []
    expected = {
        "exported_file_count": export.get("exported_file_count"),
        "skipped_file_count": export.get("skipped_file_count"),
        "secret_literal_count": export.get("secret_literal_count"),
    }
    optional_sample_keys = (
        _operator_status_sample_source_state_keys(source_state)
        if allow_optional_sample_scope
        else set()
    )
    for key in sorted(set(source_state) - set(expected) - optional_sample_keys):
        issues.append(f"{key} unexpected")
    for key, expected_value in expected.items():
        if not _is_non_negative_plain_int(expected_value):
            continue
        actual_value = source_state.get(key)
        if not _is_non_negative_plain_int(actual_value):
            continue
        if actual_value != expected_value:
            issues.append(f"{key} mismatch")
    return issues


def _handle_verify_prediction_operator_packet_directory(args: Any) -> dict[str, Any]:
    packet_dir = Path(args.packet_dir)
    issues: list[str] = []
    quality_gate_failures: list[str] = []

    export_manifest_path = packet_dir / "packet-export-manifest.json"
    readme_path = packet_dir / "README.md"
    packet_verifier_path = packet_dir / "verify_packet.py"
    packet_resume_planner_path = packet_dir / "resume_plan.py"
    packet_resume_runner_path = packet_dir / "run_resume.py"
    packet_resume_runner_verifier_path = packet_dir / "verify_run_resume.py"
    checksums_path = packet_dir / "SHA256SUMS"
    export_manifest = _load_json_packet_payload(
        export_manifest_path,
        "export_manifest",
        issues,
        required=True,
    )
    if export_manifest.get("command") != "prediction-operator-packet-export-manifest":
        issues.append("export_manifest_command_mismatch")
    issues.extend(
        f"export_manifest.{issue}"
        for issue in _operator_packet_export_count_issues(export_manifest)
    )
    export_manifest_run_metadata = (
        export_manifest.get("run_metadata")
        if isinstance(export_manifest.get("run_metadata"), dict)
        else {}
    )
    export_manifest_source_state = (
        export_manifest_run_metadata.get("source_state")
        if isinstance(export_manifest_run_metadata.get("source_state"), dict)
        else {}
    )
    issues.extend(
        f"export_manifest.run_metadata.source_state.{issue}"
        for issue in _operator_status_sample_source_state_issues(export_manifest_source_state)
    )

    readme_present = readme_path.is_file()
    packet_verifier_present = packet_verifier_path.is_file()
    packet_resume_planner_present = packet_resume_planner_path.is_file()
    packet_resume_runner_present = packet_resume_runner_path.is_file()
    packet_resume_runner_verifier_present = packet_resume_runner_verifier_path.is_file()
    checksums_present = checksums_path.is_file()
    if bool(getattr(args, "require_readme", False)) and not readme_present:
        quality_gate_failures.append("readme_missing")
    if bool(getattr(args, "require_checksums", False)) and not checksums_present:
        quality_gate_failures.append("checksums_missing")

    exported_files = (
        export_manifest.get("exported_files")
        if isinstance(export_manifest.get("exported_files"), list)
        else []
    )
    missing_exported_file_count = 0
    exported_file_sha256_invalid: list[str] = []
    exported_file_sha256_mismatch_count = 0
    directory_exported_files: list[dict[str, Any]] = []
    for entry in exported_files:
        if not isinstance(entry, dict):
            continue
        exported_path_raw = entry.get("exported_path")
        relative_name = (
            Path(exported_path_raw).name
            if isinstance(exported_path_raw, str)
            else str(entry.get("name", "packet_file"))
        )
        local_path = packet_dir / "files" / relative_name
        expected_sha = entry.get("exported_sha256")
        directory_exported_files.append(
            {
                **entry,
                "exported_path": str(local_path),
            }
        )
        if not local_path.is_file():
            missing_exported_file_count += 1
            continue
        exported_file_name = str(entry.get("name") or relative_name)
        if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
            exported_file_sha256_invalid.append(exported_file_name)
            issues.append(f"exported_file_sha256_invalid:{exported_file_name}")
        elif _optional_file_sha256(local_path) != expected_sha:
            exported_file_sha256_mismatch_count += 1
    if bool(getattr(args, "require_exported_files", False)):
        if missing_exported_file_count:
            quality_gate_failures.append("exported_files_missing")
        if exported_file_sha256_invalid:
            quality_gate_failures.append("exported_file_sha256_invalid")
        if exported_file_sha256_mismatch_count:
            quality_gate_failures.append("exported_file_sha256_mismatch")

    checksums_content_matches = _operator_packet_checksums_content_matches(
        checksums_path=checksums_path,
        export_manifest_path=export_manifest_path,
        packet_readme_path=readme_path,
        packet_verifier_path=packet_verifier_path if packet_verifier_present else None,
        packet_resume_planner_path=packet_resume_planner_path
        if packet_resume_planner_present
        else None,
        packet_resume_runner_path=packet_resume_runner_path
        if packet_resume_runner_present
        else None,
        packet_resume_runner_verifier_path=packet_resume_runner_verifier_path
        if packet_resume_runner_verifier_present
        else None,
        exported_files=directory_exported_files,
    )
    if bool(getattr(args, "require_checksums", False)) and not checksums_content_matches:
        quality_gate_failures.append("checksums_content_mismatch")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _operator_packet_export_secret_literal_failures(directory_exported_files)
        for extra_path, name in (
            (export_manifest_path, "packet-export-manifest"),
            (readme_path, "README"),
            (packet_verifier_path, "verify_packet.py"),
            (packet_resume_planner_path, "resume_plan.py"),
            (packet_resume_runner_path, "run_resume.py"),
            (packet_resume_runner_verifier_path, "verify_run_resume.py"),
            (checksums_path, "SHA256SUMS"),
        ):
            if extra_path.is_file():
                text = extra_path.read_text(encoding="utf-8", errors="ignore")
                if "sk-" in text:
                    secret_failures.append(f"secret_literal:openai_token:{name}")
                if re.search(r"\bpostgres(?:ql)?://\S+", text):
                    secret_failures.append(f"secret_literal:postgres_dsn:{name}")
        quality_gate_failures.extend(sorted(set(secret_failures)))

    source_state = {
        "export_manifest_present": export_manifest_path.is_file(),
        "readme_present": readme_present,
        "packet_verifier_present": packet_verifier_present,
        "packet_resume_planner_present": packet_resume_planner_present,
        "packet_resume_runner_present": packet_resume_runner_present,
        "packet_resume_runner_verifier_present": (packet_resume_runner_verifier_present),
        "checksums_present": checksums_present,
        "checksums_content_matches": checksums_content_matches,
        "exported_file_count": len(directory_exported_files),
        "missing_exported_file_count": missing_exported_file_count,
        "exported_file_sha256_invalid_count": len(exported_file_sha256_invalid),
        "exported_file_sha256_mismatch_count": exported_file_sha256_mismatch_count,
        "secret_literal_count": len(set(secret_failures)),
    }
    source_state.update(
        _operator_status_canonical_transitive_source_state(export_manifest_source_state)
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-packet-directory",
        "packet_dir": str(packet_dir),
        "export_manifest": str(export_manifest_path),
        "export_manifest_present": export_manifest_path.is_file(),
        "export_manifest_sha256": _optional_file_sha256(export_manifest_path),
        "readme": str(readme_path),
        "readme_present": readme_present,
        "readme_sha256": _optional_file_sha256(readme_path),
        "packet_verifier": str(packet_verifier_path),
        "packet_verifier_present": packet_verifier_present,
        "packet_verifier_sha256": _optional_file_sha256(packet_verifier_path),
        "packet_resume_planner": str(packet_resume_planner_path),
        "packet_resume_planner_present": packet_resume_planner_present,
        "packet_resume_planner_sha256": _optional_file_sha256(packet_resume_planner_path),
        "packet_resume_runner": str(packet_resume_runner_path),
        "packet_resume_runner_present": packet_resume_runner_present,
        "packet_resume_runner_sha256": _optional_file_sha256(packet_resume_runner_path),
        "packet_resume_runner_verifier": str(packet_resume_runner_verifier_path),
        "packet_resume_runner_verifier_present": (packet_resume_runner_verifier_present),
        "packet_resume_runner_verifier_sha256": _optional_file_sha256(
            packet_resume_runner_verifier_path
        ),
        "checksums": str(checksums_path),
        "checksums_present": checksums_present,
        "checksums_sha256": _optional_file_sha256(checksums_path),
        "checksums_content_matches": checksums_content_matches,
        "exported_file_count": len(directory_exported_files),
        "missing_exported_file_count": missing_exported_file_count,
        "exported_file_sha256_invalid": sorted(exported_file_sha256_invalid),
        "exported_file_sha256_invalid_count": len(exported_file_sha256_invalid),
        "exported_file_sha256_mismatch_count": exported_file_sha256_mismatch_count,
        "secret_literal_count": len(set(secret_failures)),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-packet-directory",
            "verification_flags": {
                "require_readme": bool(getattr(args, "require_readme", False)),
                "require_checksums": bool(getattr(args, "require_checksums", False)),
                "require_exported_files": bool(getattr(args, "require_exported_files", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
            },
            "source_artifact_sha256": {
                "export_manifest": _optional_file_sha256(export_manifest_path),
                "readme": _optional_file_sha256(readme_path),
                "packet_verifier": _optional_file_sha256(packet_verifier_path),
                "packet_resume_planner": _optional_file_sha256(packet_resume_planner_path),
                "packet_resume_runner": _optional_file_sha256(packet_resume_runner_path),
                "packet_resume_runner_verifier": _optional_file_sha256(
                    packet_resume_runner_verifier_path
                ),
                "checksums": _optional_file_sha256(checksums_path),
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_prediction_operator_resume_plan(args: Any) -> dict[str, Any]:
    packet_dir = Path(args.packet_dir)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    require_no_secret_literals = bool(getattr(args, "require_no_secret_literals", False))
    packet_verify = _handle_verify_prediction_operator_packet_directory(
        SimpleNamespace(
            packet_dir=str(packet_dir),
            require_readme=True,
            require_checksums=True,
            require_exported_files=True,
            require_no_secret_literals=require_no_secret_literals,
            output=None,
        )
    )
    packet_verify_ok = bool(packet_verify.get("ok"))
    if bool(getattr(args, "require_verified_packet", False)) and not packet_verify_ok:
        quality_gate_failures.append("packet_directory_verify_not_ok")

    resume_script_path = _prediction_operator_packet_resume_script_path(packet_dir)
    operator_status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    eval_window_run = _prediction_operator_packet_eval_window_run(packet_dir)
    congress_load = _prediction_operator_packet_congress_load(packet_dir)
    bill_sponsor_availability = _prediction_operator_packet_bill_sponsor_availability(packet_dir)
    jurisdiction_topology = _prediction_operator_packet_jurisdiction_topology(packet_dir)
    cutoff_audit = _prediction_operator_packet_cutoff_audit(packet_dir)
    source_url_audit = _prediction_operator_packet_source_url_audit(packet_dir)
    script_text = ""
    if resume_script_path is None:
        issues.append("resume_script_missing")
    else:
        script_text = resume_script_path.read_text(encoding="utf-8", errors="ignore")
    dotenv_path = Path(args.dotenv) if getattr(args, "dotenv", None) is not None else None
    dotenv_keys, dotenv_issues = _read_dotenv_key_presence(dotenv_path)
    issues.extend(dotenv_issues)
    phases = _prediction_operator_resume_script_plan(
        script_text,
        dotenv_keys=dotenv_keys,
    )
    dotenv_only = sorted(
        {
            env
            for phase in phases
            for env in phase["env_guards"]
            if not os.environ.get(env) and env in dotenv_keys
        }
    )
    missing_env = sorted({env for phase in phases for env in phase["missing_env"]})
    blocked_phase_count = sum(1 for phase in phases if not bool(phase["runnable"]))
    secret_failures: list[str] = []
    if require_no_secret_literals and script_text:
        secret_failures = _prediction_resume_script_secret_literal_failures(script_text)
        quality_gate_failures.extend(secret_failures)
    command_count = sum(len(phase["commands"]) for phase in phases)
    source_state = {
        "packet_verify_ok": packet_verify_ok,
        "phase_count": len(phases),
        "command_count": command_count,
        "missing_env_count": len(missing_env),
        "blocked_phase_count": blocked_phase_count,
        "dotenv_only_count": len(dotenv_only),
        "secret_literal_count": len(secret_failures),
        "eval_window_run_present": bool(eval_window_run.get("present")),
        "eval_window_run_ok": bool(eval_window_run.get("ok")),
        "eval_window_run_missing_verifier_count": _plain_int_or_zero(
            eval_window_run.get("missing_verifier_count")
        ),
        "eval_window_run_failing_verifier_count": _plain_int_or_zero(
            eval_window_run.get("failing_verifier_count")
        ),
        "eval_window_run_stale_field_count": _plain_int_or_zero(
            eval_window_run.get("stale_field_count")
        ),
    }
    source_state.update(_operator_eval_window_run_label_gate_source_state(eval_window_run))
    source_state.update(_operator_eval_window_run_archive_source_state(eval_window_run))
    packet_verify_source_state = (
        packet_verify.get("run_metadata", {}).get("source_state", {})
        if isinstance(packet_verify.get("run_metadata"), dict)
        else {}
    )
    source_state.update(
        _prediction_operator_resume_plan_canonical_sample_source_state(packet_verify_source_state)
    )
    source_state.update(_operator_congress_load_source_state(congress_load))
    source_state.update(_operator_bill_sponsor_availability_source_state(bill_sponsor_availability))
    source_state.update(_operator_jurisdiction_topology_source_state(jurisdiction_topology))
    source_state.update(_operator_cutoff_audit_source_state(cutoff_audit))
    source_state.update(_operator_source_url_audit_source_state(source_url_audit))
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "prediction-operator-resume-plan",
        "packet_dir": str(packet_dir),
        "packet_verify_ok": packet_verify_ok,
        "resume_script": str(resume_script_path) if resume_script_path else None,
        "resume_script_sha256": _optional_file_sha256(resume_script_path)
        if resume_script_path is not None
        else None,
        "dotenv": {
            "path": str(dotenv_path) if dotenv_path is not None else None,
            "exists": dotenv_path.is_file() if dotenv_path is not None else None,
            "dotenv_only": dotenv_only,
            "dotenv_only_count": len(dotenv_only),
            "issues": dotenv_issues,
        },
        "launch_ready": not missing_env and not issues and not quality_gate_failures,
        "phase_count": len(phases),
        "command_count": command_count,
        "runnable_phase_count": len(phases) - blocked_phase_count,
        "blocked_phase_count": blocked_phase_count,
        "missing_env": missing_env,
        "phases": phases,
        "eval_window_run": eval_window_run,
        "congress_load": congress_load,
        "bill_sponsor_availability": bill_sponsor_availability,
        "jurisdiction_topology": jurisdiction_topology,
        "cutoff_audit": cutoff_audit,
        "source_url_audit": source_url_audit,
        "secret_literal_count": len(secret_failures),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "prediction-operator-resume-plan",
            "verification_flags": {
                "require_verified_packet": bool(getattr(args, "require_verified_packet", False)),
                "require_no_secret_literals": require_no_secret_literals,
                "dotenv": str(dotenv_path) if dotenv_path is not None else None,
            },
            "source_artifact_sha256": {
                "resume_script": _optional_file_sha256(resume_script_path)
                if resume_script_path is not None
                else None,
                "packet_export_manifest": _optional_file_sha256(
                    packet_dir / "packet-export-manifest.json"
                ),
                "operator_status": _optional_file_sha256(operator_status_path)
                if operator_status_path is not None
                else None,
                "dotenv": _optional_file_sha256(dotenv_path) if dotenv_path is not None else None,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _handle_verify_prediction_operator_resume_plan(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    artifact = _load_json_packet_payload(artifact_path, "resume_plan", issues)
    if artifact.get("command") != "prediction-operator-resume-plan":
        issues.append("artifact_command_mismatch")
    run_metadata = (
        artifact.get("run_metadata") if isinstance(artifact.get("run_metadata"), dict) else {}
    )
    run_metadata_present = bool(run_metadata)
    if bool(getattr(args, "require_run_metadata", False)) and not run_metadata_present:
        quality_gate_failures.append("run_metadata_missing")

    issues.extend(_prediction_operator_resume_plan_count_issues(artifact))

    packet_dir_raw = artifact.get("packet_dir")
    recomputed: dict[str, Any] = {}
    if isinstance(packet_dir_raw, str):
        recomputed = _handle_prediction_operator_resume_plan(
            SimpleNamespace(
                packet_dir=packet_dir_raw,
                require_verified_packet=bool(artifact.get("packet_verify_ok")),
                require_no_secret_literals=bool(getattr(args, "require_no_secret_literals", False)),
                dotenv=(
                    artifact.get("dotenv", {}).get("path")
                    if isinstance(artifact.get("dotenv"), dict)
                    else None
                ),
                output=None,
            )
        )
    else:
        issues.append("packet_dir_missing")

    artifact_core = _prediction_operator_resume_plan_core(artifact)
    recomputed_core = _prediction_operator_resume_plan_core(recomputed)
    artifact_matches_current_packet = bool(artifact_core) and artifact_core == recomputed_core
    if (
        bool(getattr(args, "require_matches_current_packet", False))
        and not artifact_matches_current_packet
    ):
        quality_gate_failures.append("resume_plan_mismatch")

    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    expected_source_state = {
        "packet_verify_ok": bool(artifact.get("packet_verify_ok")),
        "phase_count": artifact.get("phase_count"),
        "command_count": artifact.get("command_count"),
        "missing_env_count": len(_string_list(artifact.get("missing_env"))),
        "blocked_phase_count": artifact.get("blocked_phase_count"),
        "dotenv_only_count": (
            artifact.get("dotenv", {}).get("dotenv_only_count")
            if isinstance(artifact.get("dotenv"), dict)
            else 0
        ),
        "secret_literal_count": artifact.get("secret_literal_count"),
        "eval_window_run_present": bool(
            artifact.get("eval_window_run", {}).get("present")
            if isinstance(artifact.get("eval_window_run"), dict)
            else False
        ),
        "eval_window_run_ok": bool(
            artifact.get("eval_window_run", {}).get("ok")
            if isinstance(artifact.get("eval_window_run"), dict)
            else False
        ),
        "eval_window_run_missing_verifier_count": _plain_int_or_zero(
            artifact.get("eval_window_run", {}).get("missing_verifier_count")
            if isinstance(artifact.get("eval_window_run"), dict)
            else None
        ),
        "eval_window_run_failing_verifier_count": _plain_int_or_zero(
            artifact.get("eval_window_run", {}).get("failing_verifier_count")
            if isinstance(artifact.get("eval_window_run"), dict)
            else None
        ),
        "eval_window_run_stale_field_count": _plain_int_or_zero(
            artifact.get("eval_window_run", {}).get("stale_field_count")
            if isinstance(artifact.get("eval_window_run"), dict)
            else None
        ),
    }
    expected_source_state.update(
        _operator_eval_window_run_label_gate_source_state(
            artifact.get("eval_window_run")
            if isinstance(artifact.get("eval_window_run"), dict)
            else {}
        )
    )
    expected_source_state.update(
        _operator_eval_window_run_archive_source_state(
            artifact.get("eval_window_run")
            if isinstance(artifact.get("eval_window_run"), dict)
            else {}
        )
    )
    expected_source_state.update(
        _operator_congress_load_source_state(artifact.get("congress_load"))
    )
    expected_source_state.update(
        _operator_bill_sponsor_availability_source_state(artifact.get("bill_sponsor_availability"))
    )
    expected_source_state.update(
        _operator_jurisdiction_topology_source_state(artifact.get("jurisdiction_topology"))
    )
    expected_source_state.update(_operator_cutoff_audit_source_state(artifact.get("cutoff_audit")))
    source_state_sample_scope_issues = (
        _prediction_operator_resume_plan_sample_source_state_issues(source_state)
        if run_metadata_present
        else []
    )
    source_state_mismatch_issues = (
        _prediction_operator_resume_plan_source_state_mismatch_issues(
            source_state,
            expected_source_state,
        )
        if run_metadata_present and not source_state_sample_scope_issues
        else []
    )
    source_state_matches = (
        run_metadata_present
        and not source_state_sample_scope_issues
        and not source_state_mismatch_issues
    )
    if run_metadata_present:
        source_state_count_issues = _prediction_operator_resume_plan_source_state_count_issues(
            source_state
        )
        issues.extend(f"run_metadata.source_state.{issue}" for issue in source_state_count_issues)
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_jurisdiction_topology_source_state_issues(source_state)
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_bill_sponsor_availability_source_state_issues(source_state)
        )
        issues.extend(
            f"run_metadata.source_state.{issue}"
            for issue in _operator_source_url_audit_source_state_issues(source_state)
        )
        issues.extend(
            f"run_metadata.source_state.{issue}" for issue in source_state_sample_scope_issues
        )
        if not source_state_count_issues and not source_state_sample_scope_issues:
            issues.extend(
                f"run_metadata.source_state.{issue}" for issue in source_state_mismatch_issues
            )
    else:
        source_state_count_issues = []
    if (
        run_metadata_present
        and not source_state_count_issues
        and not source_state_sample_scope_issues
        and not source_state_matches
    ):
        issues.append("run_metadata_source_state_mismatch")
    verification_flags_match = _prediction_operator_resume_plan_verification_flags_match(
        artifact,
        run_metadata,
    )
    if run_metadata_present and not verification_flags_match:
        issues.append("run_metadata_verification_flags_mismatch")
    source_artifact_sha256_invalid = (
        _prediction_operator_resume_plan_source_artifact_sha256_invalid(run_metadata)
    )
    if run_metadata_present:
        issues.extend(
            f"run_metadata_source_artifact_sha256_invalid:{key}"
            for key in source_artifact_sha256_invalid
        )
    source_artifact_sha256_matches = (
        _prediction_operator_resume_plan_source_artifact_sha256_matches(
            artifact,
            run_metadata,
        )
    )
    if (
        run_metadata_present
        and not source_artifact_sha256_invalid
        and not source_artifact_sha256_matches
    ):
        issues.append("run_metadata_source_artifact_sha256_mismatch")

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        if artifact_path.is_file():
            text = artifact_path.read_text(encoding="utf-8", errors="ignore")
            if "sk-" in text:
                secret_failures.append("secret_literal:openai_token:artifact")
            if re.search(r"\bpostgres(?:ql)?://\S+", text):
                secret_failures.append("secret_literal:postgres_dsn:artifact")
            quality_gate_failures.extend(secret_failures)

    launch_ready = bool(artifact.get("launch_ready"))
    if bool(getattr(args, "require_launch_ready", False)) and not launch_ready:
        quality_gate_failures.append("launch_not_ready")

    result_source_state = {
        "artifact_matches_current_packet": artifact_matches_current_packet,
        "source_state_matches": source_state_matches,
        "verification_flags_match": verification_flags_match,
        "source_artifact_sha256_matches": source_artifact_sha256_matches,
        "source_artifact_sha256_invalid_count": len(source_artifact_sha256_invalid),
        "phase_count": artifact.get("phase_count"),
        "command_count": artifact.get("command_count"),
        "missing_env_count": len(_string_list(artifact.get("missing_env"))),
        "dotenv_only_count": (
            artifact.get("dotenv", {}).get("dotenv_only_count")
            if isinstance(artifact.get("dotenv"), dict)
            else 0
        ),
        "secret_literal_count": len(secret_failures),
        "eval_window_run_present": bool(
            artifact.get("eval_window_run", {}).get("present")
            if isinstance(artifact.get("eval_window_run"), dict)
            else False
        ),
        "eval_window_run_ok": bool(
            artifact.get("eval_window_run", {}).get("ok")
            if isinstance(artifact.get("eval_window_run"), dict)
            else False
        ),
    }
    result_source_state.update(
        _operator_eval_window_run_label_gate_source_state(
            artifact.get("eval_window_run")
            if isinstance(artifact.get("eval_window_run"), dict)
            else {}
        )
    )
    result_source_state.update(
        _operator_eval_window_run_archive_source_state(
            artifact.get("eval_window_run")
            if isinstance(artifact.get("eval_window_run"), dict)
            else {}
        )
    )
    result_source_state.update(
        _prediction_operator_resume_plan_canonical_sample_source_state(source_state)
    )
    result_source_state.update(_operator_congress_load_source_state(artifact.get("congress_load")))
    result_source_state.update(
        _operator_bill_sponsor_availability_source_state(artifact.get("bill_sponsor_availability"))
    )
    result_source_state.update(
        _operator_jurisdiction_topology_source_state(artifact.get("jurisdiction_topology"))
    )
    result_source_state.update(_operator_cutoff_audit_source_state(artifact.get("cutoff_audit")))
    result_source_state.update(
        _operator_source_url_audit_source_state(artifact.get("source_url_audit"))
    )
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-resume-plan",
        "artifact": str(artifact_path),
        "artifact_sha256": _optional_file_sha256(artifact_path),
        "packet_dir": packet_dir_raw if isinstance(packet_dir_raw, str) else None,
        "run_metadata_present": run_metadata_present,
        "artifact_matches_current_packet": artifact_matches_current_packet,
        "source_state_matches": source_state_matches,
        "verification_flags_match": verification_flags_match,
        "source_artifact_sha256_matches": source_artifact_sha256_matches,
        "source_artifact_sha256_invalid": source_artifact_sha256_invalid,
        "source_artifact_sha256_invalid_count": len(source_artifact_sha256_invalid),
        "launch_ready": launch_ready,
        "phase_count": artifact.get("phase_count"),
        "command_count": artifact.get("command_count"),
        "missing_env": _string_list(artifact.get("missing_env")),
        "dotenv": artifact.get("dotenv") if isinstance(artifact.get("dotenv"), dict) else {},
        "eval_window_run": _prediction_readiness_eval_window_run_summary(
            artifact.get("eval_window_run")
        ),
        "congress_load": _operator_congress_load_core(artifact.get("congress_load")),
        "bill_sponsor_availability": _operator_bill_sponsor_availability_core(
            artifact.get("bill_sponsor_availability")
        ),
        "jurisdiction_topology": _operator_jurisdiction_topology_core(
            artifact.get("jurisdiction_topology")
        ),
        "cutoff_audit": _operator_cutoff_audit_core(artifact.get("cutoff_audit")),
        "source_url_audit": _operator_source_url_audit_source_state(
            artifact.get("source_url_audit")
        ),
        "secret_literal_count": len(secret_failures),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-resume-plan",
            "verification_flags": {
                "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
                "require_matches_current_packet": bool(
                    getattr(args, "require_matches_current_packet", False)
                ),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
                "require_launch_ready": bool(getattr(args, "require_launch_ready", False)),
            },
            "source_artifact_sha256": {
                "artifact": _optional_file_sha256(artifact_path),
                "resume_script": (
                    artifact.get("resume_script_sha256")
                    if isinstance(artifact.get("resume_script_sha256"), str)
                    else None
                ),
            },
            "source_state": result_source_state,
        },
    }
    return _attach_optional_verification_output(args, result)


def _prediction_operator_resume_plan_count_issues(
    artifact: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "phase_count",
        "command_count",
        "runnable_phase_count",
        "blocked_phase_count",
        "secret_literal_count",
    ):
        issues.extend(_prediction_operator_resume_plan_count_shape_issues(key, artifact.get(key)))
    dotenv = artifact.get("dotenv")
    if isinstance(dotenv, dict):
        issues.extend(
            _prediction_operator_resume_plan_count_shape_issues(
                "dotenv.dotenv_only_count",
                dotenv.get("dotenv_only_count"),
            )
        )
    phases = artifact.get("phases")
    phase_count = artifact.get("phase_count")
    if (
        isinstance(phases, list)
        and _is_non_negative_plain_int(phase_count)
        and phase_count != len(phases)
    ):
        issues.append("phase_count_mismatch")
    command_count_raw = artifact.get("command_count")
    if _is_non_negative_plain_int(command_count_raw) and isinstance(phases, list):
        command_count = sum(
            len(phase.get("commands", []))
            for phase in phases
            if isinstance(phase, dict) and isinstance(phase.get("commands"), list)
        )
        if command_count_raw != command_count:
            issues.append("command_count_mismatch")
    return issues


def _prediction_operator_resume_plan_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "phase_count",
        "command_count",
        "missing_env_count",
        "blocked_phase_count",
        "dotenv_only_count",
        "secret_literal_count",
        "eval_window_run_missing_verifier_count",
        "eval_window_run_failing_verifier_count",
        "eval_window_run_stale_field_count",
    ):
        issues.extend(
            _prediction_operator_resume_plan_count_shape_issues(key, source_state.get(key))
        )
    issues.extend(_operator_cutoff_audit_source_state_count_issues(source_state))
    issues.extend(_operator_eval_window_run_archive_source_state_issues(source_state))
    for key in _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS:
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"{key} must be a boolean")
    return issues


def _prediction_operator_resume_plan_source_state_mismatch_issues(
    source_state: dict[str, Any],
    expected_source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    optional_sample_keys = _prediction_operator_resume_plan_valid_sample_source_state_keys(
        source_state
    )
    for key in sorted(set(source_state) - set(expected_source_state) - optional_sample_keys):
        issues.append(f"{key} unexpected")
    for key, expected_value in expected_source_state.items():
        if source_state.get(key) != expected_value:
            issues.append(f"{key} mismatch")
    return issues


def _prediction_operator_resume_plan_valid_sample_source_state_keys(
    source_state: dict[str, Any],
) -> set[str]:
    valid_keys: set[str] = set()
    scoped_sample_issues = _prediction_operator_sample_scoped_id_issues(source_state)
    if _prediction_operator_resume_plan_non_negative_int_list(
        source_state.get("selected_sample_vote_event_ids")
    ):
        valid_keys.add("selected_sample_vote_event_ids")
    for key in _PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS:
        if _prediction_operator_resume_plan_sorted_string_list(source_state.get(key)):
            valid_keys.add(key)
    if scoped_sample_issues:
        valid_keys.discard("selected_sample_legislative_body_ids")
        valid_keys.discard("selected_sample_legislative_session_ids")
    return valid_keys


_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS = frozenset(
    {
        "selected_sample_bill_keys",
        "selected_sample_member_bioguide_ids",
        "selected_sample_jurisdiction_ids",
        "selected_sample_legislative_body_ids",
        "selected_sample_legislative_session_ids",
        "selected_sample_source_family_ids",
    }
)


def _prediction_operator_resume_plan_sample_source_state_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if "selected_sample_vote_event_ids" in source_state and not (
        _prediction_operator_resume_plan_non_negative_int_list(
            source_state.get("selected_sample_vote_event_ids")
        )
    ):
        issues.append("selected_sample_vote_event_ids must be a list of non-negative integers")
    for key in sorted(_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS):
        if key in source_state and not _prediction_operator_resume_plan_sorted_string_list(
            source_state.get(key)
        ):
            issues.append(f"{key} must be a sorted unique list of non-empty strings")
        elif (
            key == "selected_sample_source_family_ids"
            and key in source_state
            and not _source_family_id_list(source_state.get(key))
        ):
            issues.append(f"{key} must contain normalized source family ids")
    issues.extend(_prediction_operator_sample_scoped_id_issues(source_state))
    return issues


def _operator_eval_window_run_archive_source_state_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    archive_path = source_state.get("eval_window_run_congress_archive_manifest_path")
    if "eval_window_run_congress_archive_manifest_path" in source_state and not isinstance(
        archive_path,
        str,
    ):
        issues.append("eval_window_run_congress_archive_manifest_path must be a string")
    archive_sha256 = source_state.get("eval_window_run_congress_archive_manifest_sha256")
    if "eval_window_run_congress_archive_manifest_sha256" in source_state and (
        not isinstance(archive_sha256, str) or not _is_sha256_hex(archive_sha256)
    ):
        issues.append("eval_window_run_congress_archive_manifest_sha256 invalid")
    return issues


def _prediction_operator_sample_scoped_id_issues(source_state: dict[str, Any]) -> list[str]:
    jurisdiction_key = "selected_sample_jurisdiction_ids"
    body_key = "selected_sample_legislative_body_ids"
    session_key = "selected_sample_legislative_session_ids"
    if not any(key in source_state for key in (jurisdiction_key, body_key, session_key)):
        return []
    jurisdiction_ids = source_state.get(jurisdiction_key)
    body_ids = source_state.get(body_key)
    session_ids = source_state.get(session_key)
    if not _prediction_operator_resume_plan_sorted_string_list(jurisdiction_ids):
        return []
    jurisdiction_id_set = set(jurisdiction_ids)
    issues: list[str] = []
    body_id_set: set[str] = set()
    if body_ids is not None and _prediction_operator_resume_plan_sorted_string_list(body_ids):
        body_id_set = set(body_ids)
        if any(
            len(parts := item.split(":")) != 2
            or parts[0] not in jurisdiction_id_set
            or not parts[1]
            for item in body_ids
        ):
            issues.append("selected_sample_legislative_body_ids must contain scoped ids")
    if session_ids is not None and _prediction_operator_resume_plan_sorted_string_list(session_ids):
        if any(
            len(parts := item.split(":")) != 3
            or parts[0] not in jurisdiction_id_set
            or f"{parts[0]}:{parts[1]}" not in body_id_set
            or not parts[2]
            for item in session_ids
        ):
            issues.append("selected_sample_legislative_session_ids must contain scoped ids")
    return issues


def _prediction_operator_resume_plan_canonical_sample_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    vote_event_ids = source_state.get("selected_sample_vote_event_ids")
    if _prediction_operator_resume_plan_non_negative_int_list(vote_event_ids):
        canonical["selected_sample_vote_event_ids"] = vote_event_ids
    for key in sorted(_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS):
        value = source_state.get(key)
        if _prediction_operator_resume_plan_sorted_string_list(value):
            canonical[key] = value
    return canonical


def _prediction_operator_resume_plan_non_negative_int_list(value: object) -> bool:
    return isinstance(value, list) and all(_is_non_negative_plain_int(item) for item in value)


def _prediction_operator_resume_plan_sorted_string_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


def _prediction_operator_resume_plan_count_shape_issues(
    key: str,
    value: object,
) -> list[str]:
    if not _is_plain_int(value):
        return [f"{key} must be an integer"]
    if value < 0:
        return [f"{key} must be a non-negative integer"]
    return []


def _prediction_operator_resume_plan_verification_flags_match(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> bool:
    if not run_metadata:
        return True
    verification_flags = run_metadata.get("verification_flags")
    if not isinstance(verification_flags, dict):
        return False
    dotenv = artifact.get("dotenv") if isinstance(artifact.get("dotenv"), dict) else {}
    expected_dotenv = dotenv.get("path") if isinstance(dotenv.get("path"), str) else None
    return verification_flags.get("dotenv") == expected_dotenv


def _prediction_operator_resume_plan_source_artifact_sha256_matches(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> bool:
    if not run_metadata:
        return True
    source_hashes = run_metadata.get("source_artifact_sha256")
    if not isinstance(source_hashes, dict):
        return False
    packet_dir_raw = artifact.get("packet_dir")
    packet_dir = Path(packet_dir_raw) if isinstance(packet_dir_raw, str) else None
    dotenv = artifact.get("dotenv") if isinstance(artifact.get("dotenv"), dict) else {}
    dotenv_path_raw = dotenv.get("path")
    dotenv_path = Path(dotenv_path_raw) if isinstance(dotenv_path_raw, str) else None
    operator_status_path = (
        _prediction_operator_packet_operator_status_path(packet_dir)
        if packet_dir is not None
        else None
    )
    expected = {
        "resume_script": artifact.get("resume_script_sha256")
        if isinstance(artifact.get("resume_script_sha256"), str)
        else None,
        "packet_export_manifest": _optional_file_sha256(packet_dir / "packet-export-manifest.json")
        if packet_dir is not None
        else None,
        "operator_status": _optional_file_sha256(operator_status_path)
        if operator_status_path is not None
        else None,
        "dotenv": _optional_file_sha256(dotenv_path) if dotenv_path is not None else None,
    }
    return all(source_hashes.get(key) == value for key, value in expected.items())


def _prediction_operator_resume_plan_source_artifact_sha256_invalid(
    run_metadata: dict[str, Any],
) -> list[str]:
    source_hashes = run_metadata.get("source_artifact_sha256")
    if not isinstance(source_hashes, dict):
        return []
    invalid: list[str] = []
    for key, value in source_hashes.items():
        if not isinstance(key, str):
            continue
        if value is None:
            continue
        if not isinstance(value, str) or not _is_sha256_hex(value):
            invalid.append(key)
    return sorted(invalid)


def _prediction_operator_resume_plan_core(plan: dict[str, Any]) -> dict[str, Any]:
    if not plan:
        return {}
    return {
        "packet_verify_ok": bool(plan.get("packet_verify_ok")),
        "resume_script_sha256": plan.get("resume_script_sha256"),
        "launch_ready": bool(plan.get("launch_ready")),
        "phase_count": plan.get("phase_count"),
        "command_count": plan.get("command_count"),
        "runnable_phase_count": plan.get("runnable_phase_count"),
        "blocked_phase_count": plan.get("blocked_phase_count"),
        "missing_env": _string_list(plan.get("missing_env")),
        "dotenv": plan.get("dotenv") if isinstance(plan.get("dotenv"), dict) else {},
        "phases": plan.get("phases") if isinstance(plan.get("phases"), list) else [],
        "eval_window_run": _prediction_readiness_eval_window_run_summary(
            plan.get("eval_window_run")
        ),
        "congress_load": _operator_congress_load_core(plan.get("congress_load")),
        "bill_sponsor_availability": _operator_bill_sponsor_availability_core(
            plan.get("bill_sponsor_availability")
        ),
        "jurisdiction_topology": _operator_jurisdiction_topology_core(
            plan.get("jurisdiction_topology")
        ),
        "cutoff_audit": _operator_cutoff_audit_core(plan.get("cutoff_audit")),
        "source_url_audit": _operator_source_url_audit_source_state(plan.get("source_url_audit")),
        "secret_literal_count": plan.get("secret_literal_count"),
        "issues": _string_list(plan.get("issues")),
        "quality_gate_failures": _string_list(plan.get("quality_gate_failures")),
    }


def _prediction_operator_packet_operator_status_path(packet_dir: Path) -> Path | None:
    direct = packet_dir / "files" / "operator_status.json"
    if direct.is_file():
        return direct
    manifest = _load_json_object_or_none(packet_dir / "packet-export-manifest.json")
    if not isinstance(manifest, dict):
        return None
    exported_files = manifest.get("exported_files")
    if not isinstance(exported_files, list):
        return None
    for entry in exported_files:
        if not isinstance(entry, dict) or entry.get("name") != "operator_status":
            continue
        exported_path = entry.get("exported_path")
        if isinstance(exported_path, str):
            candidate = packet_dir / "files" / Path(exported_path).name
            if candidate.is_file():
                return candidate
    return None


def _prediction_operator_packet_eval_window_run(packet_dir: Path) -> dict[str, Any]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return _prediction_readiness_eval_window_run_summary(None)
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return _prediction_readiness_eval_window_run_summary(None)
    return _prediction_readiness_eval_window_run_summary(status.get("eval_window_run"))


def _prediction_operator_packet_congress_load(packet_dir: Path) -> dict[str, Any]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return {}
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return {}
    return _operator_congress_load_core(status.get("congress_load"))


def _prediction_operator_packet_bill_sponsor_availability(packet_dir: Path) -> dict[str, Any]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return {}
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return {}
    return _operator_bill_sponsor_availability_core(status.get("bill_sponsor_availability"))


def _prediction_operator_packet_jurisdiction_topology(packet_dir: Path) -> dict[str, Any]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return {}
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return {}
    return _operator_jurisdiction_topology_core(status.get("jurisdiction_topology"))


def _prediction_operator_packet_cutoff_audit(packet_dir: Path) -> dict[str, int]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return {}
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return {}
    return _operator_cutoff_audit_core(status.get("cutoff_audit"))


def _prediction_operator_packet_source_url_audit(packet_dir: Path) -> dict[str, Any]:
    status_path = _prediction_operator_packet_operator_status_path(packet_dir)
    if status_path is None:
        return {}
    status = _load_json_object_or_none(status_path)
    if not isinstance(status, dict):
        return {}
    return _operator_source_url_audit_source_state(status.get("source_url_audit"))


def _prediction_operator_packet_resume_script_path(packet_dir: Path) -> Path | None:
    manifest = _load_json_object_or_none(packet_dir / "packet-export-manifest.json")
    if isinstance(manifest, dict):
        exported_files = manifest.get("exported_files")
        if isinstance(exported_files, list):
            for entry in exported_files:
                if not isinstance(entry, dict) or entry.get("name") != "resume_script":
                    continue
                exported_path = entry.get("exported_path")
                if isinstance(exported_path, str):
                    candidate = packet_dir / "files" / Path(exported_path).name
                    if candidate.is_file():
                        return candidate
    direct = packet_dir / "files" / "resume_script.sh"
    if direct.is_file():
        return direct
    return None


def _prediction_operator_resume_script_plan(
    script_text: str,
    *,
    dotenv_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    dotenv_key_set = dotenv_keys or set()
    phases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    comment_section: str | None = None
    for line in script_text.splitlines():
        stripped = line.strip()
        phase_match = re.fullmatch(r"# Phase (\d+)", stripped)
        if phase_match:
            if current is not None:
                _prediction_operator_finalize_resume_phase(current)
                phases.append(current)
            current = {
                "phase": int(phase_match.group(1)),
                "declared_missing_requirements": [],
                "actions": [],
                "env_guards": [],
                "env_sources": {},
                "missing_env": [],
                "commands": [],
                "source_artifacts": [],
                "additional_reasons": [],
            }
            comment_section = None
            continue
        if current is None:
            continue
        if stripped.startswith("# Missing requirements:"):
            comment_section = None
            raw = stripped.removeprefix("# Missing requirements:").strip()
            if raw and raw.lower() != "none":
                current["declared_missing_requirements"] = [
                    item.strip() for item in raw.split(",") if item.strip()
                ]
            continue
        if stripped.startswith("# Actions:"):
            comment_section = None
            raw = stripped.removeprefix("# Actions:").strip()
            if raw and raw.lower() != "none":
                current["actions"] = [item.strip() for item in raw.split(",") if item.strip()]
            continue
        if stripped == "# Source artifacts:":
            comment_section = "source_artifacts"
            continue
        if stripped == "# Additional reasons:":
            comment_section = "additional_reasons"
            continue
        if stripped.startswith("#   - ") and comment_section is not None:
            current[comment_section].append(stripped.removeprefix("#   - ").strip())
            continue
        guard_match = re.fullmatch(r': "\$\{([A-Za-z_][A-Za-z0-9_]*):\?.+\}"', stripped)
        if guard_match:
            comment_section = None
            env_name = guard_match.group(1)
            current["env_guards"].append(env_name)
            current["env_sources"][env_name] = _prediction_operator_env_source(
                env_name,
                dotenv_keys=dotenv_key_set,
            )
            if current["env_sources"][env_name] == "missing":
                current["missing_env"].append(env_name)
            continue
        if stripped.startswith("python3 -m src.runtime.main "):
            comment_section = None
            current["commands"].append(stripped)
    if current is not None:
        _prediction_operator_finalize_resume_phase(current)
        phases.append(current)
    return phases


def _prediction_operator_finalize_resume_phase(phase: dict[str, Any]) -> None:
    phase["env_guards"] = sorted(set(_string_list(phase.get("env_guards"))))
    phase["missing_env"] = sorted(set(_string_list(phase.get("missing_env"))))
    phase["runnable"] = not phase["missing_env"]


def _prediction_operator_env_source(name: str, *, dotenv_keys: set[str]) -> str:
    if os.environ.get(name):
        return "process"
    if name in dotenv_keys:
        return "dotenv"
    return "missing"


def _operator_packet_checksums_content_matches(
    *,
    checksums_path: Path | None,
    export_manifest_path: Path | None,
    packet_readme_path: Path | None,
    exported_files: list[Any],
    packet_verifier_path: Path | None = None,
    packet_resume_planner_path: Path | None = None,
    packet_resume_runner_path: Path | None = None,
    packet_resume_runner_verifier_path: Path | None = None,
) -> bool:
    if checksums_path is None or not checksums_path.is_file():
        return False
    expected: dict[str, str] = {}
    if export_manifest_path is not None and export_manifest_path.is_file():
        manifest_sha = _optional_file_sha256(export_manifest_path)
        if manifest_sha is not None:
            expected[export_manifest_path.name] = manifest_sha
    if packet_readme_path is not None and packet_readme_path.is_file():
        readme_sha = _optional_file_sha256(packet_readme_path)
        if readme_sha is not None:
            expected[packet_readme_path.name] = readme_sha
    if packet_verifier_path is not None and packet_verifier_path.is_file():
        verifier_sha = _optional_file_sha256(packet_verifier_path)
        if verifier_sha is not None:
            expected[packet_verifier_path.name] = verifier_sha
    if packet_resume_planner_path is not None and packet_resume_planner_path.is_file():
        planner_sha = _optional_file_sha256(packet_resume_planner_path)
        if planner_sha is not None:
            expected[packet_resume_planner_path.name] = planner_sha
    if packet_resume_runner_path is not None and packet_resume_runner_path.is_file():
        runner_sha = _optional_file_sha256(packet_resume_runner_path)
        if runner_sha is not None:
            expected[packet_resume_runner_path.name] = runner_sha
    if (
        packet_resume_runner_verifier_path is not None
        and packet_resume_runner_verifier_path.is_file()
    ):
        verifier_sha = _optional_file_sha256(packet_resume_runner_verifier_path)
        if verifier_sha is not None:
            expected[packet_resume_runner_verifier_path.name] = verifier_sha
    for entry in exported_files:
        if not isinstance(entry, dict):
            continue
        exported_path = entry.get("exported_path")
        exported_sha = entry.get("exported_sha256")
        if not isinstance(exported_path, str) or not isinstance(exported_sha, str):
            continue
        relative_path = str(Path("files") / Path(exported_path).name)
        expected[relative_path] = exported_sha

    actual: dict[str, str] = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            return False
        sha256, relative_path = parts
        actual[relative_path.lstrip("*")] = sha256
    return actual == expected


def _operator_packet_recorded_sha256_invalid(
    payload: dict[str, Any],
    key: str,
    label: str,
    issues: list[str],
    invalid_labels: list[str],
) -> bool:
    if key not in payload:
        return False
    value = payload.get(key)
    if isinstance(value, str) and _is_sha256_hex(value):
        return False
    invalid_labels.append(label)
    issues.append(f"{key}_invalid")
    return True


def _operator_packet_export_filename(
    name: str,
    source_path: Path,
    used_names: set[str],
) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "packet_file"
    suffix = source_path.suffix
    candidate = f"{safe_name}{suffix}" if not safe_name.endswith(suffix) else safe_name
    index = 2
    while candidate in used_names:
        stem = candidate.removesuffix(suffix) if suffix else candidate
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used_names.add(candidate)
    return candidate


def _operator_packet_export_secret_literal_failures(
    exported_files: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for entry in exported_files:
        path_raw = entry.get("exported_path")
        if not isinstance(path_raw, str):
            continue
        exported_path = Path(path_raw)
        if not exported_path.is_file():
            continue
        text = exported_path.read_text(encoding="utf-8", errors="ignore")
        if "sk-" in text:
            failures.append(f"secret_literal:openai_token:{entry.get('name')}")
        if re.search(r"\bpostgres(?:ql)?://\S+", text):
            failures.append(f"secret_literal:postgres_dsn:{entry.get('name')}")
    return sorted(set(failures))


def _operator_packet_manifest_core(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(manifest.get("ok")),
        "status_verify": manifest.get("status_verify"),
        "status": manifest.get("status"),
        "verification_ok": bool(manifest.get("verification_ok")),
        "launch_ready": bool(manifest.get("launch_ready")),
        "missing_env": _string_list(manifest.get("missing_env")),
        "blocker_count": _plain_int_or_zero(manifest.get("blocker_count")),
        "packet_files": manifest.get("packet_files"),
        "packet_file_count": _plain_int_or_zero(manifest.get("packet_file_count")),
        "missing_files": _string_list(manifest.get("missing_files")),
        "missing_file_count": _plain_int_or_zero(manifest.get("missing_file_count")),
        "secret_literal_count": _plain_int_or_zero(manifest.get("secret_literal_count")),
        "issues": sorted(_string_list(manifest.get("issues"))),
        "quality_gate_failures": sorted(_string_list(manifest.get("quality_gate_failures"))),
    }


def _operator_packet_manifest_count_issues(manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "blocker_count",
        "packet_file_count",
        "missing_file_count",
        "secret_literal_count",
        "issue_count",
        "quality_gate_failure_count",
    ):
        if not _is_plain_int(manifest.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(manifest.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    packet_files = manifest.get("packet_files")
    if (
        _is_non_negative_plain_int(manifest.get("packet_file_count"))
        and isinstance(packet_files, list)
        and manifest["packet_file_count"] != len(packet_files)
    ):
        issues.append("packet_file_count_mismatch")
    missing_files = manifest.get("missing_files")
    if (
        _is_non_negative_plain_int(manifest.get("missing_file_count"))
        and isinstance(missing_files, list)
        and manifest["missing_file_count"] != len(missing_files)
    ):
        issues.append("missing_file_count_mismatch")
    artifact_issues = manifest.get("issues")
    if (
        _is_non_negative_plain_int(manifest.get("issue_count"))
        and isinstance(artifact_issues, list)
        and manifest["issue_count"] != len(artifact_issues)
    ):
        issues.append("issue_count_mismatch")
    failures = manifest.get("quality_gate_failures")
    if (
        _is_non_negative_plain_int(manifest.get("quality_gate_failure_count"))
        and isinstance(failures, list)
        and manifest["quality_gate_failure_count"] != len(failures)
    ):
        issues.append("quality_gate_failure_count_mismatch")
    return issues


def _operator_packet_manifest_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "packet_file_count",
        "missing_file_count",
        "secret_literal_count",
    ):
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    issues.extend(_operator_cutoff_audit_source_state_count_issues(source_state))
    return issues


def _operator_packet_manifest_source_state_mismatch_issues(
    manifest: dict[str, Any],
    source_state: dict[str, Any],
    *,
    allow_optional_sample_scope: bool = False,
) -> list[str]:
    issues: list[str] = []
    expected = {
        "packet_file_count": manifest.get("packet_file_count"),
        "missing_file_count": manifest.get("missing_file_count"),
        "secret_literal_count": manifest.get("secret_literal_count"),
    }
    optional_sample_keys = (
        _operator_status_sample_source_state_keys(source_state)
        if allow_optional_sample_scope
        else set()
    )
    for key in sorted(set(source_state) - set(expected) - optional_sample_keys):
        issues.append(f"{key} unexpected")
    for key, expected_value in expected.items():
        if not _is_non_negative_plain_int(expected_value):
            continue
        actual_value = source_state.get(key)
        if not _is_non_negative_plain_int(actual_value):
            continue
        if actual_value != expected_value:
            issues.append(f"{key} mismatch")
    return issues


def _operator_packet_file_sha256_state(packet_files: Any) -> tuple[list[str], list[str]]:
    if not isinstance(packet_files, list):
        return [], []
    invalid: list[str] = []
    mismatches: list[str] = []
    for entry in packet_files:
        if not isinstance(entry, dict) or not bool(entry.get("present")):
            continue
        path_raw = entry.get("path")
        if not isinstance(path_raw, str):
            continue
        name = str(entry.get("name") or Path(path_raw).name)
        expected_sha = entry.get("sha256")
        if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
            invalid.append(name)
            continue
        actual = _optional_file_sha256(Path(path_raw))
        if expected_sha != actual:
            mismatches.append(name)
    return sorted(set(invalid)), sorted(set(mismatches))


def _path_from_payload(payload: dict[str, Any], key: str) -> Path | None:
    value = payload.get(key)
    return Path(value) if isinstance(value, str) and value else None


def _load_json_packet_payload(
    path: Path | None,
    label: str,
    issues: list[str],
    *,
    required: bool = True,
) -> dict[str, Any]:
    if path is None:
        if required:
            issues.append(f"{label}_path_missing")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"{label}_load_failed: {exc}")
        return {}
    if not isinstance(payload, dict):
        issues.append(f"{label}_must_be_object")
        return {}
    return payload


def _operator_packet_add_file(
    packet_files: list[dict[str, Any]],
    name: str,
    path: Path | None,
) -> None:
    if path is None:
        packet_files.append(
            {
                "name": name,
                "path": None,
                "present": False,
                "sha256": None,
                "bytes": None,
            }
        )
        return
    present = path.is_file()
    packet_files.append(
        {
            "name": name,
            "path": str(path),
            "present": present,
            "sha256": _optional_file_sha256(path),
            "bytes": path.stat().st_size if present else None,
        }
    )


def _operator_packet_dedupe_files(
    packet_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for entry in packet_files:
        key = (str(entry.get("name")), cast(str | None, entry.get("path")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _operator_packet_secret_literal_failures(
    packet_files: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for entry in packet_files:
        path_raw = entry.get("path")
        if not bool(entry.get("present")) or not isinstance(path_raw, str):
            continue
        path = Path(path_raw)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sk-" in text:
            failures.append(f"secret_literal:openai_token:{entry.get('name')}")
        if re.search(r"\bpostgres(?:ql)?://\S+", text):
            failures.append(f"secret_literal:postgres_dsn:{entry.get('name')}")
    return sorted(set(failures))


def _operator_status_core(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(status.get("ok")),
        "status": status.get("status"),
        "verification_ok": bool(status.get("verification_ok")),
        "launch_ready": bool(status.get("launch_ready")),
        "runbook_verify": status.get("runbook_verify"),
        "handoff_verify": status.get("handoff_verify"),
        "readiness_summary_verify": status.get("readiness_summary_verify"),
        "artifact_sha256": status.get("artifact_sha256"),
        "verified_chain": status.get("verified_chain"),
        "missing_env": _string_list(status.get("missing_env")),
        "env_next_actions": _string_list(status.get("env_next_actions")),
        "next_live_actions": _string_list(status.get("next_live_actions")),
        "next_actions_by_kind": _operator_handoff_next_actions_by_kind(
            status.get("next_actions_by_kind")
        ),
        "eval_window_run": _prediction_readiness_eval_window_run_summary(
            status.get("eval_window_run")
        ),
        "congress_load": _operator_congress_load_core(status.get("congress_load")),
        "bill_sponsor_availability": _operator_bill_sponsor_availability_core(
            status.get("bill_sponsor_availability")
        ),
        "jurisdiction_topology": _operator_jurisdiction_topology_core(
            status.get("jurisdiction_topology")
        ),
        "operator_resume_batch_count": _plain_int_or_zero(
            status.get("operator_resume_batch_count")
        ),
        "operator_resume_batches": _operator_status_batches(status.get("operator_resume_batches")),
        "blocker_count": _plain_int_or_zero(status.get("blocker_count")),
        "issues": sorted(_string_list(status.get("issues"))),
        "quality_gate_failures": sorted(_string_list(status.get("quality_gate_failures"))),
    }


def _operator_congress_load_core(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    core: dict[str, Any] = {}
    for key in (
        "required",
        "present",
        "ok",
        "prediction_member_inputs_available",
        "prediction_bill_inputs_available",
        "prediction_vote_inputs_available",
    ):
        if key in value:
            core[key] = bool(value.get(key))
    blockers = _string_list(value.get("blockers"))
    if blockers:
        core["blockers"] = blockers
    source_family_ids = _sorted_string_list_or_empty(value.get("source_family_ids"))
    if source_family_ids and _source_family_id_list(source_family_ids):
        core["source_family_ids"] = source_family_ids
    return core


def _operator_bill_sponsor_availability_core(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    total = value.get("total")
    available = value.get("available")
    if not _is_non_negative_plain_int(total) or not _is_non_negative_plain_int(available):
        return {}
    if available > total:
        return {}
    core: dict[str, Any] = {"total": total, "available": available}
    rate = value.get("rate")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= float(rate) <= 1:
        core["rate"] = float(rate)
    fallback = value.get("primary_introduced_date_fallback_count")
    if _is_non_negative_plain_int(fallback):
        core["primary_introduced_date_fallback_count"] = fallback
    return core


def _operator_jurisdiction_topology_core(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    core: dict[str, Any] = {}
    for key in (
        "jurisdiction_count",
        "implemented_jurisdiction_count",
        "portable_jurisdiction_count",
        "legislative_body_count",
        "legislative_session_count",
    ):
        if _is_non_negative_plain_int(value.get(key)):
            core[key] = value[key]
    for key in (
        "jurisdiction_ids",
        "implemented_jurisdiction_ids",
        "portable_jurisdiction_ids",
        "legislative_body_ids",
        "legislative_session_ids",
        "source_family_ids",
    ):
        values = _sorted_string_list_or_empty(value.get(key))
        if values and (key != "source_family_ids" or _source_family_id_list(values)):
            core[key] = values
    return core


def _operator_status_count_issues(status: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "operator_resume_batch_count",
        "blocker_count",
        "issue_count",
        "quality_gate_failure_count",
    ):
        if not _is_plain_int(status.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(status.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    batches = status.get("operator_resume_batches")
    if (
        _is_non_negative_plain_int(status.get("operator_resume_batch_count"))
        and isinstance(batches, list)
        and status["operator_resume_batch_count"] != len(batches)
    ):
        issues.append("operator_resume_batch_count_mismatch")
    issue_count = status.get("issue_count")
    status_issues = status.get("issues")
    if (
        _is_non_negative_plain_int(issue_count)
        and isinstance(status_issues, list)
        and issue_count != len(status_issues)
    ):
        issues.append("issue_count_mismatch")
    failure_count = status.get("quality_gate_failure_count")
    failures = status.get("quality_gate_failures")
    if (
        _is_non_negative_plain_int(failure_count)
        and isinstance(failures, list)
        and failure_count != len(failures)
    ):
        issues.append("quality_gate_failure_count_mismatch")
    return issues


def _operator_status_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "missing_env_count",
        "blocker_count",
        "operator_resume_batch_count",
        "next_live_action_count",
        "secret_literal_count",
    ):
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    return issues


_OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS = (
    ("requires_training_labels", "eval_window_run_requires_training_labels"),
    ("requires_evaluation_labels", "eval_window_run_requires_evaluation_labels"),
    (
        "requires_input_inventory_portable_ids",
        "eval_window_run_requires_input_inventory_portable_ids",
    ),
    (
        "requires_input_inventory_congress_source_families",
        "eval_window_run_requires_input_inventory_congress_source_families",
    ),
    (
        "requires_input_inventory_official_source_thresholds",
        "eval_window_run_requires_input_inventory_official_source_thresholds",
    ),
    (
        "requires_input_inventory_optional_evidence",
        "eval_window_run_requires_input_inventory_optional_evidence",
    ),
    ("requires_eval_manifest_model_suite", "eval_window_run_requires_eval_manifest_model_suite"),
    (
        "requires_eval_manifest_official_source_thresholds",
        "eval_window_run_requires_eval_manifest_official_source_thresholds",
    ),
    (
        "requires_eval_manifest_unknown_availability_failures",
        "eval_window_run_requires_eval_manifest_unknown_availability_failures",
    ),
    (
        "requires_eval_manifest_ontology_feature_signals",
        "eval_window_run_requires_eval_manifest_ontology_feature_signals",
    ),
)

_OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS = tuple(
    output_key for _, output_key in _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS
)


def _operator_eval_window_run_label_gate_source_state(
    eval_window_run: dict[str, Any],
) -> dict[str, bool]:
    source_state: dict[str, bool] = {}
    for source_key, output_key in _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS:
        value = eval_window_run.get(source_key)
        if isinstance(value, bool):
            source_state[output_key] = value
    return source_state


def _operator_eval_window_run_archive_source_state(
    eval_window_run: dict[str, Any],
) -> dict[str, str]:
    archive_manifest = eval_window_run.get("congress_archive_manifest")
    if not isinstance(archive_manifest, dict):
        return {}
    path = archive_manifest.get("path")
    sha256 = archive_manifest.get("sha256")
    if not isinstance(path, str) or not isinstance(sha256, str):
        return {}
    return {
        "eval_window_run_congress_archive_manifest_path": path,
        "eval_window_run_congress_archive_manifest_sha256": sha256,
    }


def _operator_congress_load_source_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, Any] = {}
    for source_key, output_key in (
        ("required", "congress_load_required"),
        ("present", "congress_load_present"),
        ("ok", "congress_load_ok"),
        (
            "prediction_member_inputs_available",
            "congress_load_prediction_member_inputs_available",
        ),
        ("prediction_bill_inputs_available", "congress_load_prediction_bill_inputs_available"),
        ("prediction_vote_inputs_available", "congress_load_prediction_vote_inputs_available"),
    ):
        field_value = value.get(source_key)
        if isinstance(field_value, bool):
            source_state[output_key] = field_value
    source_family_ids = _sorted_string_list_or_empty(value.get("source_family_ids"))
    if source_family_ids and _source_family_id_list(source_family_ids):
        source_state["congress_load_source_family_ids"] = source_family_ids
        source_state["congress_load_source_family_count"] = len(source_family_ids)
    return source_state


def _operator_bill_sponsor_availability_source_state(value: Any) -> dict[str, Any]:
    core = _operator_bill_sponsor_availability_core(value)
    if not core:
        return {}
    source_state: dict[str, Any] = {
        "bill_sponsor_total_count": core["total"],
        "bill_sponsor_available_count": core["available"],
    }
    if isinstance(core.get("rate"), float):
        source_state["bill_sponsor_availability_rate"] = core["rate"]
    if _is_non_negative_plain_int(core.get("primary_introduced_date_fallback_count")):
        source_state["bill_sponsor_primary_introduced_date_fallback_count"] = core[
            "primary_introduced_date_fallback_count"
        ]
    return source_state


def _operator_bill_sponsor_availability_source_state_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "bill_sponsor_total_count",
        "bill_sponsor_available_count",
        "bill_sponsor_primary_introduced_date_fallback_count",
    ):
        if key not in source_state:
            continue
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    rate = source_state.get("bill_sponsor_availability_rate")
    if rate is not None and (
        not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0 <= float(rate) <= 1
    ):
        issues.append("bill_sponsor_availability_rate must be a number between 0 and 1")
    total = source_state.get("bill_sponsor_total_count")
    available = source_state.get("bill_sponsor_available_count")
    if (
        _is_non_negative_plain_int(total)
        and _is_non_negative_plain_int(available)
        and available > total
    ):
        issues.append(
            "bill_sponsor_available_count must be less than or equal to bill_sponsor_total_count"
        )
    return issues


def _operator_bill_sponsor_availability_transitive_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key in (
        "bill_sponsor_total_count",
        "bill_sponsor_available_count",
        "bill_sponsor_primary_introduced_date_fallback_count",
    ):
        if _is_non_negative_plain_int(source_state.get(key)):
            canonical[key] = source_state[key]
    rate = source_state.get("bill_sponsor_availability_rate")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= float(rate) <= 1:
        canonical["bill_sponsor_availability_rate"] = float(rate)
    return canonical


def _operator_cutoff_audit_source_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        f"cutoff_audit_{key}": count
        for key, count in value.items()
        if isinstance(key, str) and _is_non_negative_plain_int(count)
    }


def _operator_source_url_audit_source_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    source_state: dict[str, Any] = {}
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        count = value.get(key)
        if _is_non_negative_plain_int(count):
            source_state[key] = count
    failures = _sorted_string_list_or_empty(value.get("source_url_audit_quality_gate_failures"))
    if failures:
        source_state["source_url_audit_quality_gate_failures"] = failures
    return source_state


def _operator_source_url_audit_source_state_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        if key not in source_state:
            continue
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    if "source_url_audit_quality_gate_failures" in source_state and not (
        _operator_status_sorted_string_list(
            source_state.get("source_url_audit_quality_gate_failures")
        )
    ):
        issues.append(
            "source_url_audit_quality_gate_failures must be a sorted unique list of non-empty strings"
        )
    return issues


def _operator_source_url_audit_transitive_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key in (
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
    ):
        if _is_non_negative_plain_int(source_state.get(key)):
            canonical[key] = source_state[key]
    failures = _sorted_string_list_or_empty(
        source_state.get("source_url_audit_quality_gate_failures")
    )
    if failures:
        canonical["source_url_audit_quality_gate_failures"] = failures
    return canonical


def _operator_jurisdiction_topology_source_state(value: Any) -> dict[str, Any]:
    core = _operator_jurisdiction_topology_core(value)
    if not core:
        return {}
    source_state: dict[str, Any] = {}
    for key, state_key in (
        ("jurisdiction_count", "jurisdiction_topology_jurisdiction_count"),
        ("implemented_jurisdiction_count", "jurisdiction_topology_implemented_jurisdiction_count"),
        ("portable_jurisdiction_count", "jurisdiction_topology_portable_jurisdiction_count"),
        ("legislative_body_count", "jurisdiction_topology_legislative_body_count"),
        ("legislative_session_count", "jurisdiction_topology_legislative_session_count"),
    ):
        if _is_non_negative_plain_int(core.get(key)):
            source_state[state_key] = core[key]
    for key, state_key in (
        ("jurisdiction_ids", "jurisdiction_topology_jurisdiction_ids"),
        ("implemented_jurisdiction_ids", "jurisdiction_topology_implemented_jurisdiction_ids"),
        ("portable_jurisdiction_ids", "jurisdiction_topology_portable_jurisdiction_ids"),
        ("legislative_body_ids", "jurisdiction_topology_legislative_body_ids"),
        ("legislative_session_ids", "jurisdiction_topology_legislative_session_ids"),
        ("source_family_ids", "jurisdiction_topology_source_family_ids"),
    ):
        values = _sorted_string_list_or_empty(core.get(key))
        if values and (key != "source_family_ids" or _source_family_id_list(values)):
            source_state[state_key] = values
    return source_state


def _operator_jurisdiction_topology_source_state_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "jurisdiction_topology_jurisdiction_count",
        "jurisdiction_topology_implemented_jurisdiction_count",
        "jurisdiction_topology_portable_jurisdiction_count",
        "jurisdiction_topology_legislative_body_count",
        "jurisdiction_topology_legislative_session_count",
    ):
        if key not in source_state:
            continue
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"{key} must be a non-negative integer")
    for key in (
        "jurisdiction_topology_jurisdiction_ids",
        "jurisdiction_topology_implemented_jurisdiction_ids",
        "jurisdiction_topology_portable_jurisdiction_ids",
        "jurisdiction_topology_legislative_body_ids",
        "jurisdiction_topology_legislative_session_ids",
        "jurisdiction_topology_source_family_ids",
    ):
        if key not in source_state:
            continue
        if not _operator_status_sorted_string_list(source_state.get(key)):
            issues.append(f"{key} must be a sorted unique list of non-empty strings")
        elif key == "jurisdiction_topology_source_family_ids" and not _source_family_id_list(
            source_state.get(key)
        ):
            issues.append(f"{key} must contain normalized source family ids")
    return issues


def _operator_jurisdiction_topology_transitive_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key in (
        "jurisdiction_topology_jurisdiction_count",
        "jurisdiction_topology_implemented_jurisdiction_count",
        "jurisdiction_topology_portable_jurisdiction_count",
        "jurisdiction_topology_legislative_body_count",
        "jurisdiction_topology_legislative_session_count",
    ):
        if _is_non_negative_plain_int(source_state.get(key)):
            canonical[key] = source_state[key]
    for key in (
        "jurisdiction_topology_jurisdiction_ids",
        "jurisdiction_topology_implemented_jurisdiction_ids",
        "jurisdiction_topology_portable_jurisdiction_ids",
        "jurisdiction_topology_legislative_body_ids",
        "jurisdiction_topology_legislative_session_ids",
        "jurisdiction_topology_source_family_ids",
    ):
        values = _sorted_string_list_or_empty(source_state.get(key))
        if values and (
            key != "jurisdiction_topology_source_family_ids" or _source_family_id_list(values)
        ):
            canonical[key] = values
    return canonical


def _operator_cutoff_audit_source_state_count_issues(
    source_state: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key, value in source_state.items():
        if not isinstance(key, str) or not key.startswith("cutoff_audit_"):
            continue
        if not _is_plain_int(value):
            issues.append(f"{key} must be an integer")
        elif not _is_non_negative_plain_int(value):
            issues.append(f"{key} must be a non-negative integer")
    return issues


def _operator_cutoff_audit_transitive_source_state(
    source_state: dict[str, Any],
) -> dict[str, int]:
    return {
        key: value
        for key, value in source_state.items()
        if isinstance(key, str)
        and key.startswith("cutoff_audit_")
        and _is_non_negative_plain_int(value)
    }


def _operator_cutoff_audit_core(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: count
        for key, count in value.items()
        if isinstance(key, str) and _is_non_negative_plain_int(count)
    }


def _operator_status_source_state_mismatch_issues(
    status: dict[str, Any],
    source_state: dict[str, Any],
    *,
    allow_optional_sample_scope: bool = False,
) -> list[str]:
    issues: list[str] = []
    expected = {
        "verification_ok": bool(status.get("verification_ok")),
        "launch_ready": bool(status.get("launch_ready")),
        "missing_env_count": len(_string_list(status.get("missing_env"))),
        "blocker_count": status.get("blocker_count"),
        "operator_resume_batch_count": status.get("operator_resume_batch_count"),
        "next_live_action_count": len(_string_list(status.get("next_live_actions"))),
        "secret_literal_count": status.get("secret_literal_count", 0),
    }
    eval_window_run = _prediction_readiness_eval_window_run_summary(status.get("eval_window_run"))
    expected.update(_operator_eval_window_run_label_gate_source_state(eval_window_run))
    expected.update(_operator_eval_window_run_archive_source_state(eval_window_run))
    expected.update(_operator_congress_load_source_state(status.get("congress_load")))
    expected.update(
        _operator_bill_sponsor_availability_source_state(status.get("bill_sponsor_availability"))
    )
    expected.update(
        _operator_jurisdiction_topology_source_state(status.get("jurisdiction_topology"))
    )
    expected.update(_operator_cutoff_audit_source_state(status.get("cutoff_audit")))
    expected.update(_operator_source_url_audit_source_state(status.get("source_url_audit")))
    optional_sample_keys = (
        _operator_status_sample_source_state_keys(source_state)
        if allow_optional_sample_scope
        else set()
    )
    for key in sorted(set(source_state) - set(expected) - optional_sample_keys):
        issues.append(f"{key} unexpected")
    for key, expected_value in expected.items():
        if isinstance(expected_value, bool):
            actual_value = source_state.get(key)
            if isinstance(actual_value, bool) and actual_value != expected_value:
                issues.append(f"{key} mismatch")
            continue
        if isinstance(expected_value, list):
            actual_value = source_state.get(key)
            if isinstance(actual_value, list) and actual_value != expected_value:
                issues.append(f"{key} mismatch")
            continue
        if not _is_non_negative_plain_int(expected_value):
            continue
        actual_value = source_state.get(key)
        if not _is_non_negative_plain_int(actual_value):
            continue
        if actual_value != expected_value:
            issues.append(f"{key} mismatch")
    return issues


def _operator_status_sample_source_state_keys(source_state: dict[str, Any]) -> set[str]:
    known_keys = {
        "selected_sample_vote_event_ids",
        *_OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS,
        "eval_window_run_congress_archive_manifest_path",
        "eval_window_run_congress_archive_manifest_sha256",
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
        "congress_load_source_family_ids",
        "congress_load_source_family_count",
        "source_url_audit_quality_gate_failure_count",
        "source_url_audit_quality_gate_failures",
        "source_url_audit_gap_count",
        "source_url_audit_missing_url_sourced_prediction_count",
        "source_url_audit_missing_official_source_prediction_count",
        *_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS,
    }
    keys = set(source_state) & known_keys
    scoped_sample_issues = _prediction_operator_sample_scoped_id_issues(source_state)
    if scoped_sample_issues:
        keys.discard("selected_sample_legislative_body_ids")
        keys.discard("selected_sample_legislative_session_ids")
    return keys


def _operator_status_sample_source_state_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if "selected_sample_vote_event_ids" in source_state and not (
        _operator_status_non_negative_int_list(source_state.get("selected_sample_vote_event_ids"))
    ):
        issues.append("selected_sample_vote_event_ids must be a list of non-negative integers")
    for key in (
        *_OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS,
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
        "congress_load_source_family_ids",
    ):
        if key in source_state and not isinstance(source_state.get(key), bool):
            if key == "congress_load_source_family_ids":
                continue
            issues.append(f"{key} must be a boolean")
    if (
        "congress_load_source_family_ids" in source_state
        and not _operator_status_sorted_string_list(
            source_state.get("congress_load_source_family_ids")
        )
    ):
        issues.append(
            "congress_load_source_family_ids must be a sorted unique list of non-empty strings"
        )
    elif "congress_load_source_family_ids" in source_state and not _source_family_id_list(
        source_state.get("congress_load_source_family_ids")
    ):
        issues.append("congress_load_source_family_ids must contain normalized source family ids")
    if "congress_load_source_family_count" in source_state:
        value = source_state.get("congress_load_source_family_count")
        if not _is_non_negative_plain_int(value):
            issues.append("congress_load_source_family_count must be a non-negative integer")
        elif isinstance(source_state.get("congress_load_source_family_ids"), list) and value != len(
            source_state["congress_load_source_family_ids"]
        ):
            issues.append("congress_load_source_family_count mismatch")
    issues.extend(_operator_eval_window_run_archive_source_state_issues(source_state))
    issues.extend(_operator_source_url_audit_source_state_issues(source_state))
    issues.extend(_operator_bill_sponsor_availability_source_state_issues(source_state))
    issues.extend(_operator_jurisdiction_topology_source_state_issues(source_state))
    for key in sorted(_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS):
        if key in source_state and not _operator_status_sorted_string_list(source_state.get(key)):
            issues.append(f"{key} must be a sorted unique list of non-empty strings")
        elif (
            key == "selected_sample_source_family_ids"
            and key in source_state
            and not _source_family_id_list(source_state.get(key))
        ):
            issues.append(f"{key} must contain normalized source family ids")
    issues.extend(_prediction_operator_sample_scoped_id_issues(source_state))
    return issues


def _operator_status_canonical_sample_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    vote_event_ids = source_state.get("selected_sample_vote_event_ids")
    if _operator_status_non_negative_int_list(vote_event_ids):
        canonical["selected_sample_vote_event_ids"] = vote_event_ids
    for key in sorted(_PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS):
        value = source_state.get(key)
        if _operator_status_sorted_string_list(value):
            canonical[key] = value
    return canonical


def _operator_status_canonical_transitive_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    canonical = _operator_status_canonical_sample_source_state(source_state)
    for key in (
        *_OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS,
        "eval_window_run_congress_archive_manifest_path",
        "eval_window_run_congress_archive_manifest_sha256",
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
        "congress_load_source_family_ids",
        "congress_load_source_family_count",
    ):
        value = source_state.get(key)
        if (
            isinstance(value, bool)
            or (
                key == "congress_load_source_family_ids"
                and _operator_status_sorted_string_list(value)
                and _source_family_id_list(value)
            )
            or (key == "congress_load_source_family_count" and _is_non_negative_plain_int(value))
        ):
            canonical[key] = value
    source_family_ids = canonical.get("congress_load_source_family_ids")
    if (
        "congress_load_source_family_count" not in canonical
        and isinstance(source_family_ids, list)
        and _operator_status_sorted_string_list(source_family_ids)
        and _source_family_id_list(source_family_ids)
    ):
        canonical["congress_load_source_family_count"] = len(source_family_ids)
    canonical.update(_operator_cutoff_audit_transitive_source_state(source_state))
    canonical.update(_operator_source_url_audit_transitive_source_state(source_state))
    canonical.update(_operator_bill_sponsor_availability_transitive_source_state(source_state))
    canonical.update(_operator_jurisdiction_topology_transitive_source_state(source_state))
    return canonical


def _operator_status_non_negative_int_list(value: object) -> bool:
    return isinstance(value, list) and all(_is_non_negative_plain_int(item) for item in value)


def _operator_status_sorted_string_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


def _operator_status_batches(value: Any) -> list[dict[str, Any]]:
    return (
        [
            {
                "phase": _plain_int_or_zero(batch.get("phase")),
                "missing_requirements": _string_list(batch.get("missing_requirements")),
                "actions": _string_list(batch.get("actions")),
                "commands": _string_list(batch.get("commands")),
            }
            for batch in value
            if isinstance(batch, dict)
        ]
        if isinstance(value, list)
        else []
    )


def _operator_status_plan_from_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    plan = handoff.get("operator_plan") if isinstance(handoff.get("operator_plan"), dict) else {}
    batches = [
        batch for batch in plan.get("operator_resume_batches", []) if isinstance(batch, dict)
    ]
    status_plan = {
        "missing_env": _string_list(plan.get("missing_env")),
        "env_next_actions": _string_list(plan.get("env_next_actions")),
        "next_live_actions": _string_list(plan.get("next_live_actions")),
        "next_actions_by_kind": _operator_handoff_next_actions_by_kind(
            plan.get("next_actions_by_kind")
        ),
        "operator_resume_batch_count": len(batches),
        "operator_resume_batches": [
            {
                "phase": _plain_int_or_zero(batch.get("phase")),
                "missing_requirements": _string_list(batch.get("missing_requirements")),
                "actions": _string_list(batch.get("actions")),
                "commands": _string_list(batch.get("commands")),
            }
            for batch in batches
        ],
    }
    if isinstance(plan.get("congress_load"), dict):
        status_plan["congress_load"] = plan["congress_load"]
    if isinstance(plan.get("cutoff_audit"), dict):
        status_plan["cutoff_audit"] = plan["cutoff_audit"]
    return status_plan


def _operator_runbook_verified_artifact_missing(
    handoff: dict[str, Any],
    runbook_text: str,
) -> list[str]:
    artifact_sha256 = (
        handoff.get("artifact_sha256") if isinstance(handoff.get("artifact_sha256"), dict) else {}
    )
    missing: list[str] = []
    for name in (
        "env_preflight_verify",
        "readiness_summary_verify",
        "resume_script_verify",
    ):
        path = handoff.get(name)
        if not isinstance(path, str) or path not in runbook_text:
            missing.append(f"verified_artifact_missing:{name}:path")
        sha256 = artifact_sha256.get(name)
        if not isinstance(sha256, str) or sha256 not in runbook_text:
            missing.append(f"verified_artifact_missing:{name}:sha256")
    return missing


def _operator_runbook_secret_literal_failures(runbook_text: str) -> list[str]:
    failures: list[str] = []
    if "sk-" in runbook_text:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", runbook_text):
        failures.append("secret_literal:postgres_dsn")
    return sorted(set(failures))


def _operator_bill_sponsor_availability_line(summary: dict[str, Any]) -> str | None:
    total = summary.get("total")
    available = summary.get("available")
    if not _is_non_negative_plain_int(total) or not _is_non_negative_plain_int(available):
        return None
    if available > total:
        return None
    rate = summary.get("rate")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0 <= float(rate) <= 1:
        percent = f"{float(rate) * 100:.2f}%"
    else:
        percent = f"{(available / total * 100) if total else 0.0:.2f}%"
    fallback = summary.get("primary_introduced_date_fallback_count")
    suffix = ""
    if _is_non_negative_plain_int(fallback):
        suffix = f"; primary introduced-date fallback: {fallback}"
    return f"- Bill sponsor availability: {available}/{total} ({percent}){suffix}"


def _operator_jurisdiction_topology_lines(summary: dict[str, Any]) -> list[str]:
    core = _operator_jurisdiction_topology_core(summary)
    if not core:
        return []
    lines = [
        "## Jurisdiction Topology",
        "",
        f"- Jurisdictions: {_plain_int_or_zero(core.get('jurisdiction_count'))}",
        f"- Implemented jurisdictions: {_plain_int_or_zero(core.get('implemented_jurisdiction_count'))}",
        f"- Portable jurisdictions: {_plain_int_or_zero(core.get('portable_jurisdiction_count'))}",
        f"- Legislative bodies: {_plain_int_or_zero(core.get('legislative_body_count'))}",
        f"- Legislative sessions: {_plain_int_or_zero(core.get('legislative_session_count'))}",
    ]
    for label, key in (
        ("Jurisdiction ids", "jurisdiction_ids"),
        ("Implemented jurisdiction ids", "implemented_jurisdiction_ids"),
        ("Portable jurisdiction ids", "portable_jurisdiction_ids"),
        ("Legislative body ids", "legislative_body_ids"),
        ("Legislative session ids", "legislative_session_ids"),
        ("Source families", "source_family_ids"),
    ):
        values = _string_list(core.get(key))
        if values:
            lines.append(f"- {label}: " + ", ".join(f"`{value}`" for value in values))
    return lines


def _operator_source_url_audit_lines(summary: dict[str, Any]) -> list[str]:
    source_state = _operator_source_url_audit_source_state(summary)
    if not source_state:
        return []
    lines = [
        "## Source URL Audit",
        "",
        "- Quality gate failures: "
        f"{_plain_int_or_zero(source_state.get('source_url_audit_quality_gate_failure_count'))}",
        f"- Source URL gap count: {_plain_int_or_zero(source_state.get('source_url_audit_gap_count'))}",
        "- Missing URL-sourced predictions: "
        f"{_plain_int_or_zero(source_state.get('source_url_audit_missing_url_sourced_prediction_count'))}",
        "- Missing official-source predictions: "
        f"{_plain_int_or_zero(source_state.get('source_url_audit_missing_official_source_prediction_count'))}",
    ]
    failures = _string_list(source_state.get("source_url_audit_quality_gate_failures"))
    if failures:
        lines.append("- Failure names: " + ", ".join(f"`{failure}`" for failure in failures))
    return lines


def _prediction_operator_handoff_runbook_text(result: dict[str, Any]) -> str:
    plan = result.get("operator_plan") if isinstance(result.get("operator_plan"), dict) else {}
    lines = [
        "# Prediction Operator Handoff",
        "",
        f"- Status: {'green' if bool(result.get('ok')) else 'blocked'}",
        f"- Issue count: {_plain_int_or_zero(result.get('issue_count'))}",
        f"- Quality gate failure count: {_plain_int_or_zero(result.get('quality_gate_failure_count'))}",
        "",
        "## Verified Artifacts",
    ]
    artifact_sha256 = (
        result.get("artifact_sha256") if isinstance(result.get("artifact_sha256"), dict) else {}
    )
    verified_artifacts = [
        (
            "Env preflight verify",
            result.get("env_preflight_verify"),
            artifact_sha256.get("env_preflight_verify"),
        ),
        (
            "Readiness summary verify",
            result.get("readiness_summary_verify"),
            artifact_sha256.get("readiness_summary_verify"),
        ),
        (
            "Resume script verify",
            result.get("resume_script_verify"),
            artifact_sha256.get("resume_script_verify"),
        ),
    ]
    for label, path, sha256 in verified_artifacts:
        lines.append(f"- {label}: `{path}`")
        lines.append(f"  - sha256: `{sha256}`")
    lines.extend(
        [
            "",
            "## Missing Environment",
        ]
    )
    missing_env = _string_list(plan.get("missing_env"))
    if missing_env:
        lines.extend(f"- `{name}`" for name in missing_env)
    else:
        lines.append("- none")
    lines.extend(["", "## Environment Setup Actions"])
    env_actions = _string_list(plan.get("env_next_actions"))
    if env_actions:
        lines.extend(f"- `{action}`" for action in env_actions)
    else:
        lines.append("- none")
    lines.extend(["", "## Next Live Actions"])
    next_live_actions = _string_list(plan.get("next_live_actions"))
    if next_live_actions:
        lines.extend(f"- `{action}`" for action in next_live_actions)
    else:
        lines.append("- none")
    congress_load = plan.get("congress_load") if isinstance(plan.get("congress_load"), dict) else {}
    if congress_load:
        lines.extend(
            [
                "",
                "## Congress Prediction Inputs",
                "",
                f"- Required: {'yes' if congress_load.get('required') is True else 'no'}",
                f"- Present: {'yes' if congress_load.get('present') is True else 'no'}",
                f"- Status: {'green' if congress_load.get('ok') is True else 'blocked'}",
                "- Member inputs: "
                f"{'available' if congress_load.get('prediction_member_inputs_available') is True else 'missing'}",
                "- Bill inputs: "
                f"{'available' if congress_load.get('prediction_bill_inputs_available') is True else 'missing'}",
                "- Vote inputs: "
                f"{'available' if congress_load.get('prediction_vote_inputs_available') is True else 'missing'}",
            ]
        )
        source_family_ids = _string_list(congress_load.get("source_family_ids"))
        if source_family_ids:
            lines.append(
                "- Source families: "
                + ", ".join(f"`{source_family_id}`" for source_family_id in source_family_ids)
            )
        blockers = _string_list(congress_load.get("blockers"))
        lines.append("- Blockers:")
        if blockers:
            lines.extend(f"  - `{blocker}`" for blocker in blockers)
        else:
            lines.append("  - none")
    bill_sponsor_availability = (
        plan.get("bill_sponsor_availability")
        if isinstance(plan.get("bill_sponsor_availability"), dict)
        else {}
    )
    sponsor_line = _operator_bill_sponsor_availability_line(bill_sponsor_availability)
    if sponsor_line is not None:
        lines.extend(["", "## Bill Sponsor Availability", "", sponsor_line])
    jurisdiction_topology = (
        plan.get("jurisdiction_topology")
        if isinstance(plan.get("jurisdiction_topology"), dict)
        else {}
    )
    jurisdiction_lines = _operator_jurisdiction_topology_lines(jurisdiction_topology)
    if jurisdiction_lines:
        lines.extend(["", *jurisdiction_lines])
    cutoff_audit = plan.get("cutoff_audit") if isinstance(plan.get("cutoff_audit"), dict) else {}
    if cutoff_audit:
        lines.extend(
            [
                "",
                "## Cutoff Audit",
                "",
                f"- Bill signal rows: {_plain_int_or_zero(cutoff_audit.get('bill_signal_row_count'))}",
                "- Cutoff-safe bill signal rows: "
                f"{_plain_int_or_zero(cutoff_audit.get('cutoff_bill_signal_row_count'))}",
                "- Unknown availability bill signal rows: "
                f"{_plain_int_or_zero(cutoff_audit.get('unknown_availability_bill_signal_row_count'))}",
                "- Excluded future bill signal rows: "
                f"{_plain_int_or_zero(cutoff_audit.get('excluded_future_bill_signal_row_count'))}",
                "- Unknown availability ontology edges: "
                f"{_plain_int_or_zero(cutoff_audit.get('unknown_availability_ontology_edge_count'))}",
                "- Excluded future ontology edges: "
                f"{_plain_int_or_zero(cutoff_audit.get('excluded_future_ontology_edge_count'))}",
            ]
        )
    eval_window_run = (
        plan.get("eval_window_run") if isinstance(plan.get("eval_window_run"), dict) else {}
    )
    lines.extend(
        [
            "",
            "## Annual Eval Window Run",
            "",
            f"- Present: {'yes' if bool(eval_window_run.get('present')) else 'no'}",
            f"- Status: {'green' if bool(eval_window_run.get('ok')) else 'blocked'}",
            f"- Window count: {_plain_int_or_zero(eval_window_run.get('window_count'))}",
            "- Expected verifier count: "
            f"{_plain_int_or_zero(eval_window_run.get('expected_window_verifier_count'))}",
            f"- Loaded verifier count: {_plain_int_or_zero(eval_window_run.get('loaded_verifier_count'))}",
            f"- Missing verifier count: {_plain_int_or_zero(eval_window_run.get('missing_verifier_count'))}",
            f"- Failing verifier count: {_plain_int_or_zero(eval_window_run.get('failing_verifier_count'))}",
            f"- Stale field count: {_plain_int_or_zero(eval_window_run.get('stale_field_count'))}",
            f"- Requires training labels: {'yes' if eval_window_run.get('requires_training_labels') is True else 'no'}",
            f"- Requires evaluation labels: {'yes' if eval_window_run.get('requires_evaluation_labels') is True else 'no'}",
            *_operator_eval_window_run_strict_gate_lines(eval_window_run),
            *_operator_eval_window_run_archive_lines(eval_window_run),
        ]
    )
    lines.extend(["", "## Resume Phases"])
    batches = [
        batch for batch in plan.get("operator_resume_batches", []) if isinstance(batch, dict)
    ]
    if not batches:
        lines.append("- none")
    for batch in batches:
        phase = _plain_int_or_zero(batch.get("phase"))
        lines.extend(
            [
                "",
                f"### Phase {phase}",
                "",
                "Missing requirements:",
            ]
        )
        missing_requirements = _string_list(batch.get("missing_requirements"))
        if missing_requirements:
            lines.extend(f"- `{requirement}`" for requirement in missing_requirements)
        else:
            lines.append("- none")
        lines.extend(["", "Actions:"])
        actions = _string_list(batch.get("actions"))
        if actions:
            lines.extend(f"- `{action}`" for action in actions)
        else:
            lines.append("- none")
        source_artifacts = _string_list(batch.get("source_artifacts"))
        if source_artifacts:
            lines.extend(["", "Source artifacts:"])
            lines.extend(f"- `{artifact}`" for artifact in source_artifacts)
        additional_reasons = _string_list(batch.get("additional_reasons"))
        if additional_reasons:
            lines.extend(["", "Additional reasons:"])
            lines.extend(f"- {reason}" for reason in additional_reasons)
        lines.extend(["", "Commands:", ""])
        commands = _string_list(batch.get("commands"))
        if commands:
            lines.append("```bash")
            lines.extend(commands)
            lines.append("```")
        else:
            lines.append("- none")
    handoff_output = result.get("output")
    runbook_output = result.get("runbook_output")
    if isinstance(handoff_output, str) and isinstance(runbook_output, str):
        lines.extend(
            [
                "",
                "## Verify This Runbook",
                "",
                "```bash",
                "python3 -m src.runtime.main verify-prediction-operator-runbook "
                f"--handoff-verify {handoff_output} --runbook {runbook_output} "
                "--require-handoff-runbook-sha --require-no-secret-literals "
                "--require-verified-artifact-hashes",
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _operator_eval_window_run_archive_lines(eval_window_run: dict[str, Any]) -> list[str]:
    archive_manifest = eval_window_run.get("congress_archive_manifest")
    if not isinstance(archive_manifest, dict):
        return []
    path = archive_manifest.get("path")
    sha256 = archive_manifest.get("sha256")
    if not isinstance(path, str) or not isinstance(sha256, str):
        return []
    return [
        f"- Congress archive manifest: `{path}`",
        f"- Congress archive manifest sha256: `{sha256}`",
    ]


def _operator_eval_window_run_strict_gate_lines(eval_window_run: dict[str, Any]) -> list[str]:
    labels = (
        (
            "requires_input_inventory_portable_ids",
            "Requires input inventory portable ids",
        ),
        (
            "requires_input_inventory_congress_source_families",
            "Requires input inventory Congress source families",
        ),
        (
            "requires_input_inventory_official_source_thresholds",
            "Requires input inventory official-source thresholds",
        ),
        (
            "requires_input_inventory_optional_evidence",
            "Requires input inventory optional evidence",
        ),
        ("requires_eval_manifest_model_suite", "Requires eval manifest model suite"),
        (
            "requires_eval_manifest_official_source_thresholds",
            "Requires eval manifest official-source thresholds",
        ),
        (
            "requires_eval_manifest_unknown_availability_failures",
            "Requires eval manifest unknown-availability failures",
        ),
        (
            "requires_eval_manifest_ontology_feature_signals",
            "Requires eval manifest ontology feature signals",
        ),
    )
    return [
        f"- {label}: {'yes' if eval_window_run.get(key) is True else 'no'}"
        for key, label in labels
        if isinstance(eval_window_run.get(key), bool)
    ]


def _operator_handoff_secret_literal_failures(
    payloads: list[dict[str, Any]],
) -> list[str]:
    serialized = json.dumps(payloads, sort_keys=True)
    failures: list[str] = []
    if "sk-" in serialized:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", serialized):
        failures.append("secret_literal:postgres_dsn")
    return sorted(set(failures))


def _optional_file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _load_prediction_readiness_artifact(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    artifact = _artifact_reference(path)
    if not path.is_file():
        return artifact, {}, ["artifact missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return artifact, {}, [f"failed to load artifact JSON: {exc}"]
    if not isinstance(payload, dict):
        return artifact, {}, ["artifact JSON must be an object"]
    return artifact, payload, []


def _prediction_readiness_benchmark_summary(payload: dict[str, Any]) -> dict[str, Any]:
    components = payload.get("components")
    component_summaries: dict[str, dict[str, Any]] = {}
    eval_window_run = _prediction_readiness_eval_window_run_summary(None)
    if isinstance(components, dict):
        for name, component in components.items():
            if not isinstance(component, dict):
                continue
            if str(name) == "eval_window_run":
                eval_window_run = _prediction_readiness_eval_window_run_summary(component)
            component_summaries[str(name)] = {
                "ok": bool(component.get("ok")),
                "issue_count": _plain_int_or_zero(component.get("issue_count")),
                "quality_gate_failure_count": _plain_int_or_zero(
                    component.get("quality_gate_failure_count")
                ),
                "issues": _string_list(component.get("issues")),
                "quality_gate_failures": _string_list(component.get("quality_gate_failures")),
            }
    source_state = _prediction_readiness_benchmark_source_state(payload)
    summary = {
        "ok": bool(payload.get("ok")),
        "component_count": _plain_int_or_zero(payload.get("component_count")),
        "components": component_summaries,
        "issues": _string_list(payload.get("issues")),
        "issue_count": _plain_int_or_zero(payload.get("issue_count")),
        "quality_gate_failures": _string_list(payload.get("quality_gate_failures")),
        "quality_gate_failure_count": _plain_int_or_zero(payload.get("quality_gate_failure_count")),
        "backfill_recommendation_count": _plain_int_or_zero(
            payload.get("backfill_recommendation_count")
        ),
        "eval_window_run": eval_window_run,
        "run_metadata": {"source_state": source_state},
    }
    if "jurisdiction_count" in source_state:
        summary["jurisdiction_count"] = source_state["jurisdiction_count"]
    if "implemented_jurisdiction_count" in source_state:
        summary["implemented_jurisdiction_count"] = source_state["implemented_jurisdiction_count"]
    if "portable_jurisdiction_count" in source_state:
        summary["portable_jurisdiction_count"] = source_state["portable_jurisdiction_count"]
    if "legislative_body_count" in source_state:
        summary["legislative_body_count"] = source_state["legislative_body_count"]
    if "legislative_session_count" in source_state:
        summary["legislative_session_count"] = source_state["legislative_session_count"]
    summary.update(_prediction_readiness_benchmark_backtest_source_coverage(source_state))
    return summary


def _prediction_readiness_benchmark_source_state(payload: dict[str, Any]) -> dict[str, Any]:
    run_metadata = payload.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return {}
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return {}
    return source_state


_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS = (
    "backtest_feature_source_backed_prediction_count",
    "backtest_feature_source_missing_prediction_count",
    "backtest_official_feature_source_backed_prediction_count",
    "backtest_unofficial_feature_source_backed_prediction_count",
    "backtest_legislative_feature_source_anchor_count",
    "backtest_legislative_feature_source_anchor_context_count",
    "backtest_legislative_feature_source_anchor_missing_context_count",
)

_BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS = (
    "backtest_feature_source_coverage_rate",
    "backtest_official_feature_source_coverage_rate",
)

_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS = (
    "inventory_training_feature_vote_history_member_count",
    "inventory_training_feature_vote_history_source_member_count",
    "inventory_evaluation_feature_vote_history_member_count",
    "inventory_evaluation_feature_vote_history_source_member_count",
)

_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS = (
    "inventory_training_feature_vote_history_source_coverage_rate",
    "inventory_evaluation_feature_vote_history_source_coverage_rate",
)

_BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS = (
    "inventory_training_label_source_url_count",
    "inventory_evaluation_label_source_url_count",
    "inventory_official_training_label_source_url_count",
    "inventory_official_evaluation_label_source_url_count",
    "inventory_bill_source_url_count",
    "inventory_official_bill_source_url_count",
    "inventory_bill_sponsor_count",
    "inventory_bill_available_sponsor_count",
    "inventory_bill_primary_sponsor_introduced_date_fallback_count",
    "inventory_sourced_ontology_edge_count",
    "inventory_official_sourced_ontology_edge_count",
    "inventory_portable_rows_missing_jurisdiction_id_count",
    "inventory_portable_rows_missing_body_id_count",
    "inventory_portable_rows_missing_session_id_count",
)

_BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS = (
    "inventory_training_label_source_url_coverage_rate",
    "inventory_evaluation_label_source_url_coverage_rate",
    "inventory_training_label_official_source_url_coverage_rate",
    "inventory_evaluation_label_official_source_url_coverage_rate",
    "inventory_bill_source_url_coverage_rate",
    "inventory_bill_official_source_url_coverage_rate",
    "inventory_bill_sponsor_availability_rate",
    "inventory_ontology_source_anchor_coverage_rate",
    "inventory_ontology_official_source_anchor_coverage_rate",
)

_BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS = _PREDICTION_EVAL_CUTOFF_AUDIT_KEYS

_BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS = tuple(
    f"eval_cutoff_{key}" for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS
)

_OFFLINE_READINESS_BENCHMARK_EVAL_CUTOFF_AUDIT_KEYS = tuple(
    f"benchmark_{key}" for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS
)

_PREDICTION_OFFLINE_READINESS_SOURCE_STATE_STRING_LIST_KEYS = (
    "benchmark_jurisdiction_ids",
    "benchmark_implemented_jurisdiction_ids",
    "benchmark_portable_jurisdiction_ids",
    "benchmark_legislative_body_ids",
    "benchmark_legislative_session_ids",
    "source_url_audit_sample_bill_keys",
    "source_url_audit_sample_member_bioguide_ids",
    "source_url_audit_sample_jurisdiction_ids",
    "source_url_audit_sample_legislative_body_ids",
    "source_url_audit_sample_legislative_session_ids",
    "source_url_audit_sample_source_family_ids",
    "backfill_plan_sample_bill_keys",
    "backfill_plan_sample_member_bioguide_ids",
    "backfill_plan_sample_jurisdiction_ids",
    "backfill_plan_sample_legislative_body_ids",
    "backfill_plan_sample_legislative_session_ids",
    "backfill_plan_sample_source_family_ids",
    "congress_load_source_family_ids",
)

_BENCHMARK_BACKTEST_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS,
)

_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS,
)

_BENCHMARK_INVENTORY_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS,
)

_BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS = (
    "source_url_audit_quality_gate_failure_count",
    "source_url_audit_gap_count",
    "source_url_audit_missing_url_sourced_prediction_count",
    "source_url_audit_missing_official_source_prediction_count",
    "source_url_audit_sample_case_count",
    "source_url_audit_portable_sample_missing_body_id_count",
    "source_url_audit_portable_sample_missing_session_id_count",
)

_BENCHMARK_SOURCE_URL_AUDIT_INT_LIST_KEYS = ("source_url_audit_sample_vote_event_ids",)

_BENCHMARK_SOURCE_URL_AUDIT_STRING_LIST_KEYS = (
    "source_url_audit_quality_gate_failures",
    "source_url_audit_sample_bill_keys",
    "source_url_audit_sample_member_bioguide_ids",
    "source_url_audit_sample_source_family_ids",
    "source_url_audit_sample_jurisdiction_ids",
    "source_url_audit_sample_legislative_body_ids",
    "source_url_audit_sample_legislative_session_ids",
)


def _prediction_readiness_benchmark_backtest_source_coverage(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: source_state[key]
        for key in _BENCHMARK_BACKTEST_SOURCE_COVERAGE_KEYS
        if key in source_state
    }


def _prediction_readiness_benchmark_inventory_feature_source_coverage(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: source_state[key]
        for key in _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_KEYS
        if key in source_state
    }


def _prediction_readiness_benchmark_inventory_source_coverage(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: source_state[key]
        for key in _BENCHMARK_INVENTORY_SOURCE_COVERAGE_KEYS
        if key in source_state
    }


def _prediction_readiness_benchmark_source_url_audit_gaps(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: source_state[key]
        for key in (
            *_BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS,
            *_BENCHMARK_SOURCE_URL_AUDIT_INT_LIST_KEYS,
            *_BENCHMARK_SOURCE_URL_AUDIT_STRING_LIST_KEYS,
        )
        if key in source_state
    }


def _prediction_readiness_benchmark_eval_cutoff_audit(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        f"benchmark_{key}": source_state[key]
        for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS
        if key in source_state
    }


def _prediction_readiness_eval_window_run_summary(component: Any) -> dict[str, Any]:
    if not isinstance(component, dict):
        return {
            "present": False,
            "ok": False,
            "checked": 0,
            "artifact": None,
            "artifact_sha256": None,
            "plan": None,
            "plan_sha256": None,
            "window_count": 0,
            "expected_window_verifier_count": 0,
            "loaded_verifier_count": 0,
            "missing_verifier_count": 0,
            "failing_verifier_count": 0,
            "stale_field_count": 0,
            "issue_count": 0,
            "quality_gate_failure_count": 0,
        }
    if "present" in component:
        artifact = component.get("artifact")
        artifact_sha256 = component.get("artifact_sha256")
        plan = component.get("plan")
        plan_sha256 = component.get("plan_sha256")
        return {
            "present": bool(component.get("present")),
            "ok": bool(component.get("ok")),
            "checked": _plain_int_or_zero(component.get("checked")),
            "artifact": artifact if isinstance(artifact, str) else None,
            "artifact_sha256": artifact_sha256 if isinstance(artifact_sha256, str) else None,
            "plan": plan if isinstance(plan, str) else None,
            "plan_sha256": plan_sha256 if isinstance(plan_sha256, str) else None,
            "window_count": _plain_int_or_zero(component.get("window_count")),
            "expected_window_verifier_count": _plain_int_or_zero(
                component.get("expected_window_verifier_count")
            ),
            "loaded_verifier_count": _plain_int_or_zero(component.get("loaded_verifier_count")),
            "missing_verifier_count": _plain_int_or_zero(component.get("missing_verifier_count")),
            "failing_verifier_count": _plain_int_or_zero(component.get("failing_verifier_count")),
            "stale_field_count": _plain_int_or_zero(component.get("stale_field_count")),
            "issue_count": _plain_int_or_zero(component.get("issue_count")),
            "quality_gate_failure_count": _plain_int_or_zero(
                component.get("quality_gate_failure_count")
            ),
            **_prediction_readiness_eval_window_run_archive_summary(component),
            **_prediction_readiness_eval_window_run_label_gate_summary(component),
            **_prediction_readiness_eval_window_run_strict_gate_summary(component),
        }
    current_result = (
        component.get("current_result") if isinstance(component.get("current_result"), dict) else {}
    )
    run_metadata = (
        component.get("run_metadata") if isinstance(component.get("run_metadata"), dict) else {}
    )
    source_state = (
        run_metadata.get("source_state")
        if isinstance(run_metadata.get("source_state"), dict)
        else {}
    )
    artifact = component.get("artifact")
    artifact_sha256 = component.get("artifact_sha256")
    plan = current_result.get("plan")
    plan_sha256 = current_result.get("plan_sha256")
    return {
        "present": True,
        "ok": bool(component.get("ok")),
        "checked": _plain_int_or_zero(component.get("checked")),
        "artifact": artifact if isinstance(artifact, str) else None,
        "artifact_sha256": artifact_sha256 if isinstance(artifact_sha256, str) else None,
        "plan": plan if isinstance(plan, str) else None,
        "plan_sha256": plan_sha256 if isinstance(plan_sha256, str) else None,
        "window_count": _plain_int_or_zero(current_result.get("window_count")),
        "expected_window_verifier_count": _plain_int_or_zero(
            current_result.get("expected_window_verifier_count")
        ),
        "loaded_verifier_count": _plain_int_or_zero(current_result.get("loaded_verifier_count")),
        "missing_verifier_count": _plain_int_or_zero(current_result.get("missing_verifier_count")),
        "failing_verifier_count": _plain_int_or_zero(current_result.get("failing_verifier_count")),
        "stale_field_count": _plain_int_or_zero(source_state.get("stale_field_count")),
        "issue_count": _plain_int_or_zero(component.get("issue_count")),
        "quality_gate_failure_count": _plain_int_or_zero(
            component.get("quality_gate_failure_count")
        ),
        **_prediction_readiness_eval_window_run_archive_summary(source_state),
        **_prediction_readiness_eval_window_run_label_gate_summary(source_state),
        **_prediction_readiness_eval_window_run_strict_gate_summary(source_state),
    }


def _prediction_readiness_eval_window_run_archive_summary(
    source: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    congress_archive_manifest = source.get("congress_archive_manifest")
    if not isinstance(congress_archive_manifest, dict):
        return {}
    path = congress_archive_manifest.get("path")
    sha256 = congress_archive_manifest.get("sha256")
    if not isinstance(path, str) or not isinstance(sha256, str):
        return {}
    return {
        "congress_archive_manifest": {
            "path": path,
            "sha256": sha256,
        }
    }


def _prediction_readiness_eval_window_run_label_gate_summary(
    source: dict[str, Any],
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key in ("requires_training_labels", "requires_evaluation_labels"):
        value = source.get(key)
        if isinstance(value, bool):
            result[key] = value
    return result


def _prediction_readiness_eval_window_run_strict_gate_summary(
    source: dict[str, Any],
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key, _ in _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS:
        if key in {"requires_training_labels", "requires_evaluation_labels"}:
            continue
        value = source.get(key)
        if isinstance(value, bool):
            result[key] = value
    return result


def _prediction_readiness_runtime_summary(
    payload: dict[str, Any],
    *,
    backfill_plan_steps: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    readiness_rows = [
        row for row in payload.get("runtime_readiness_by_step", []) if isinstance(row, dict)
    ]
    enriched_readiness_rows = [
        _prediction_readiness_enrich_runtime_row(
            row,
            backfill_plan_steps=backfill_plan_steps,
        )
        for row in readiness_rows
    ]
    blocked_steps = [row for row in enriched_readiness_rows if not bool(row.get("runnable"))]
    unlock_sequence = _prediction_readiness_unlock_sequence_by_requirement_set(blocked_steps)
    return {
        "ok": bool(payload.get("ok")),
        "missing_runtime_requirements": _string_list(payload.get("missing_runtime_requirements")),
        "missing_runtime_requirements_by_step": [
            row
            for row in payload.get("missing_runtime_requirements_by_step", [])
            if isinstance(row, dict)
        ],
        "runtime_readiness_by_step": enriched_readiness_rows,
        "blocked_steps": blocked_steps,
        "blocked_steps_by_missing_requirement": (
            _prediction_readiness_blocked_steps_by_missing_requirement(blocked_steps)
        ),
        "unlock_summary_by_missing_requirement": (
            _prediction_readiness_unlock_summary_by_missing_requirement(blocked_steps)
        ),
        "env_unlock_priority": _prediction_readiness_env_unlock_priority(blocked_steps),
        "unlock_sequence_by_requirement_set": unlock_sequence,
        "operator_resume_batches": _prediction_readiness_operator_resume_batches(unlock_sequence),
        "runtime_ready_step_count": sum(
            1 for row in enriched_readiness_rows if bool(row.get("runnable"))
        ),
        "runtime_blocked_step_count": sum(
            1 for row in enriched_readiness_rows if not bool(row.get("runnable"))
        ),
        "quality_gate_failures": _string_list(payload.get("quality_gate_failures")),
        "quality_gate_failure_count": _plain_int_or_zero(payload.get("quality_gate_failure_count")),
    }


def _prediction_readiness_blocked_steps_by_missing_requirement(
    blocked_steps: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in blocked_steps:
        missing_requirements = _string_list(row.get("missing_runtime_requirements"))
        for requirement in missing_requirements:
            grouped.setdefault(requirement, []).append(
                _with_prediction_readiness_provenance(
                    {
                        "index": row.get("index"),
                        "action": row.get("action"),
                        "source_requirements": _string_list(row.get("source_requirements")),
                        "suggested_commands": _string_list(row.get("suggested_commands")),
                    },
                    row,
                )
            )
    return {key: grouped[key] for key in sorted(grouped)}


def _prediction_readiness_unlock_summary_by_missing_requirement(
    blocked_steps: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requirements = sorted(
        {
            requirement
            for row in blocked_steps
            for requirement in _string_list(row.get("missing_runtime_requirements"))
        }
    )
    summary: dict[str, dict[str, Any]] = {}
    for requirement in requirements:
        impacted_steps: list[dict[str, Any]] = []
        solely_blocked_steps: list[dict[str, Any]] = []
        co_blocked_steps: list[dict[str, Any]] = []
        for row in blocked_steps:
            missing_requirements = _string_list(row.get("missing_runtime_requirements"))
            if requirement not in missing_requirements:
                continue
            step = {
                "index": row.get("index"),
                "action": row.get("action"),
            }
            impacted_steps.append(step)
            if len(missing_requirements) == 1:
                solely_blocked_steps.append(step)
            else:
                co_blocked_steps.append(
                    {
                        **step,
                        "other_missing_requirements": [
                            item for item in missing_requirements if item != requirement
                        ],
                    }
                )
        summary[requirement] = {
            "impacted_step_count": len(impacted_steps),
            "solely_blocked_step_count": len(solely_blocked_steps),
            "co_blocked_step_count": len(co_blocked_steps),
            "solely_blocked_steps": solely_blocked_steps,
            "co_blocked_steps": co_blocked_steps,
        }
    return summary


def _prediction_readiness_env_unlock_priority(
    blocked_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summary = _prediction_readiness_unlock_summary_by_missing_requirement(blocked_steps)
    priority_rows = [
        {
            "requirement": requirement,
            "impacted_step_count": item["impacted_step_count"],
            "solely_blocked_step_count": item["solely_blocked_step_count"],
            "co_blocked_step_count": item["co_blocked_step_count"],
            "actions": sorted(
                {
                    str(step.get("action"))
                    for step in [
                        *item["solely_blocked_steps"],
                        *item["co_blocked_steps"],
                    ]
                    if step.get("action") is not None
                }
            ),
        }
        for requirement, item in summary.items()
    ]
    return sorted(
        priority_rows,
        key=lambda row: (
            -int(row["solely_blocked_step_count"]),
            -int(row["impacted_step_count"]),
            str(row["requirement"]),
        ),
    )


def _prediction_readiness_unlock_sequence_by_requirement_set(
    blocked_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in blocked_steps:
        missing_requirements = tuple(sorted(_string_list(row.get("missing_runtime_requirements"))))
        if not missing_requirements:
            continue
        grouped.setdefault(missing_requirements, []).append(
            _with_prediction_readiness_provenance(
                {
                    "index": row.get("index"),
                    "action": row.get("action"),
                    "source_requirements": _string_list(row.get("source_requirements")),
                    "suggested_commands": _string_list(row.get("suggested_commands")),
                },
                row,
            )
        )
    sequence = [
        {
            "missing_requirements": list(requirements),
            "requirement_count": len(requirements),
            "unlocked_step_count": len(steps),
            "steps": sorted(
                steps,
                key=lambda step: (
                    _prediction_backfill_action_order(str(step.get("action"))),
                    _plain_int_or_zero(step.get("index")),
                    str(step.get("action")),
                ),
            ),
        }
        for requirements, steps in grouped.items()
    ]
    return sorted(
        sequence,
        key=_prediction_readiness_unlock_sequence_sort_key,
    )


def _prediction_readiness_unlock_sequence_sort_key(
    row: dict[str, Any],
) -> tuple[int, int, int, list[str]]:
    steps = [step for step in row.get("steps", []) if isinstance(step, dict)]
    first_action_order = min(
        (_prediction_backfill_action_order(str(step.get("action"))) for step in steps),
        default=999,
    )
    first_index = min(
        (_plain_int_or_zero(step.get("index")) for step in steps),
        default=0,
    )
    return (
        first_action_order,
        first_index,
        int(row["requirement_count"]),
        _string_list(row.get("missing_requirements")),
    )


def _prediction_readiness_operator_resume_batches(
    unlock_sequence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for index, row in enumerate(unlock_sequence, start=1):
        commands: list[str] = []
        actions: list[str] = []
        source_requirements: list[str] = []
        source_artifacts: list[str] = []
        additional_reasons: list[str] = []
        for step in row.get("steps", []):
            if not isinstance(step, dict):
                continue
            action = step.get("action")
            if action is not None:
                actions.append(str(action))
            for command in _string_list(step.get("suggested_commands")):
                if command not in commands:
                    commands.append(command)
            for requirement in _string_list(step.get("source_requirements")):
                if requirement not in source_requirements:
                    source_requirements.append(requirement)
            for artifact in _string_list(step.get("source_artifacts")):
                if artifact not in source_artifacts:
                    source_artifacts.append(artifact)
            for reason in _string_list(step.get("additional_reasons")):
                if reason not in additional_reasons:
                    additional_reasons.append(reason)
        batch = {
            "phase": index,
            "missing_requirements": _string_list(row.get("missing_requirements")),
            "requirement_count": _plain_int_or_zero(row.get("requirement_count")),
            "action_count": len(actions),
            "actions": actions,
            "command_count": len(commands),
            "commands": commands,
        }
        if source_requirements:
            batch["source_requirements"] = source_requirements
        if source_artifacts:
            batch["source_artifacts"] = source_artifacts
        if additional_reasons:
            batch["additional_reasons"] = additional_reasons
        batches.append(batch)
    return batches


def _prediction_readiness_backfill_plan_steps(
    payload: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        plan = payload.get("backfill_plan")
    if not isinstance(plan, dict):
        return {}
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return {}
    indexed_steps: dict[int, dict[str, Any]] = {}
    for index, step in enumerate(steps):
        if isinstance(step, dict):
            indexed_steps[index] = step
    return indexed_steps


def _prediction_readiness_backfill_plan_source_state(
    payload: dict[str, Any],
) -> dict[str, Any]:
    run_metadata = payload.get("run_metadata")
    if isinstance(run_metadata, dict):
        source_state = run_metadata.get("source_state")
        if isinstance(source_state, dict):
            return _prediction_readiness_backfill_plan_sample_source_state(source_state)
    steps = _prediction_readiness_backfill_plan_steps(payload)
    if not steps:
        return {}
    sample_case_count = 0
    sample_vote_event_ids: list[int] = []
    sample_bill_keys: set[str] = set()
    sample_member_bioguide_ids: set[str] = set()
    sample_jurisdiction_ids: set[str] = set()
    sample_legislative_body_ids: set[str] = set()
    sample_legislative_session_ids: set[str] = set()
    sample_source_family_ids: set[str] = set()
    for step in steps.values():
        for source_family_id in _string_list(step.get("sample_source_family_ids")):
            sample_source_family_ids.add(source_family_id)
        sample_cases = step.get("sample_cases")
        if not isinstance(sample_cases, list):
            continue
        for sample_case in sample_cases:
            if not isinstance(sample_case, dict):
                continue
            sample_case_count += 1
            vote_event_id = sample_case.get("vote_event_id")
            if _is_non_negative_plain_int(vote_event_id):
                sample_vote_event_ids.append(cast(int, vote_event_id))
            bill_key = sample_case.get("bill_context_key") or sample_case.get("bill_key")
            if isinstance(bill_key, str) and bill_key.strip() == bill_key and bill_key:
                sample_bill_keys.add(bill_key)
            member_bioguide_id = sample_case.get("member_bioguide_id")
            if (
                isinstance(member_bioguide_id, str)
                and member_bioguide_id.strip() == member_bioguide_id
                and member_bioguide_id
            ):
                sample_member_bioguide_ids.add(member_bioguide_id)
            jurisdiction_id = sample_case.get("jurisdiction_id")
            if isinstance(jurisdiction_id, str) and jurisdiction_id.strip():
                jurisdiction_id = jurisdiction_id.strip()
                sample_jurisdiction_ids.add(jurisdiction_id)
                legislative_body_id = sample_case.get("legislative_body_id")
                if isinstance(legislative_body_id, str) and legislative_body_id.strip():
                    legislative_body_id = legislative_body_id.strip()
                    sample_legislative_body_ids.add(f"{jurisdiction_id}:{legislative_body_id}")
                    legislative_session_id = sample_case.get("legislative_session_id")
                    if isinstance(legislative_session_id, str) and legislative_session_id.strip():
                        sample_legislative_session_ids.add(
                            f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id.strip()}"
                        )
    if not sample_case_count:
        return {}
    return {
        "sample_case_count": sample_case_count,
        "sample_vote_event_ids": sample_vote_event_ids,
        "sample_bill_keys": sorted(sample_bill_keys),
        "sample_member_bioguide_ids": sorted(sample_member_bioguide_ids),
        "sample_jurisdiction_ids": sorted(sample_jurisdiction_ids),
        "sample_legislative_body_ids": sorted(sample_legislative_body_ids),
        "sample_legislative_session_ids": sorted(sample_legislative_session_ids),
        "sample_source_family_ids": sorted(sample_source_family_ids),
    }


def _prediction_readiness_backfill_plan_sample_source_state(
    source_state: dict[str, Any],
) -> dict[str, Any]:
    sample_state: dict[str, Any] = {}
    for key in (
        "sample_case_count",
        "sample_vote_event_ids",
        "sample_bill_keys",
        "sample_member_bioguide_ids",
        "sample_jurisdiction_ids",
        "sample_legislative_body_ids",
        "sample_legislative_session_ids",
        "sample_source_family_ids",
    ):
        if key in source_state:
            sample_state[key] = source_state[key]
    return sample_state


def _prediction_readiness_backfill_plan_source_state_for_run_metadata(
    payload: dict[str, Any],
) -> dict[str, Any]:
    backfill_plan = payload.get("backfill_plan")
    if not isinstance(backfill_plan, dict):
        return {}
    source_state = backfill_plan.get("source_state")
    if not isinstance(source_state, dict):
        return {}
    mapped: dict[str, Any] = {}
    for key, value in source_state.items():
        if key.startswith("sample_"):
            mapped[f"backfill_plan_{key}"] = value
    return mapped


def _prediction_readiness_env_preflight_summary(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not payload:
        return {
            "present": False,
            "ok": False,
            "checked": 0,
            "missing_env": [],
            "missing_env_count": 0,
            "dotenv_only": [],
            "dotenv_only_count": 0,
            "issues": [],
            "issue_count": 0,
            "next_actions": [],
            "next_actions_by_env": {},
        }
    dotenv = payload.get("dotenv")
    dotenv_only: list[str] = []
    if isinstance(dotenv, dict):
        dotenv_only = _string_list(dotenv.get("dotenv_only"))
    next_actions_by_env = _runtime_env_next_actions_by_env_summary(
        payload.get("next_actions_by_env")
    )
    next_actions = sorted(
        {action for actions in next_actions_by_env.values() for action in actions}
        | set(_string_list(payload.get("next_actions")))
    )
    return {
        "present": True,
        "ok": bool(payload.get("ok")),
        "checked": _plain_int_or_zero(payload.get("checked")),
        "missing_env": _string_list(payload.get("missing_env")),
        "missing_env_count": _plain_int_or_zero(payload.get("missing_env_count")),
        "dotenv_only": dotenv_only,
        "dotenv_only_count": len(dotenv_only),
        "issues": _string_list(payload.get("issues")),
        "issue_count": _plain_int_or_zero(payload.get("issue_count")),
        "next_actions": next_actions,
        "next_actions_by_env": next_actions_by_env,
    }


def _prediction_readiness_env_preflight_verify_summary(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not payload:
        return {
            "present": False,
            "ok": False,
            "issues": [],
            "issue_count": 0,
            "quality_gate_failures": [],
            "quality_gate_failure_count": 0,
        }
    return {
        "present": True,
        "ok": bool(payload.get("ok")),
        "issues": _string_list(payload.get("issues")),
        "issue_count": _plain_int_or_zero(payload.get("issue_count")),
        "quality_gate_failures": _string_list(payload.get("quality_gate_failures")),
        "quality_gate_failure_count": _plain_int_or_zero(payload.get("quality_gate_failure_count")),
    }


def _runtime_env_next_actions_by_env_summary(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(name): _string_list(actions) for name, actions in value.items()}


def _prediction_readiness_env_consistency(
    *,
    runtime: dict[str, Any],
    env_preflight: dict[str, Any],
) -> dict[str, Any]:
    runtime_missing = sorted(
        requirement.removeprefix("env:")
        for requirement in runtime.get("missing_runtime_requirements", [])
        if isinstance(requirement, str) and requirement.startswith("env:")
    )
    preflight_missing = sorted(_string_list(env_preflight.get("missing_env")))
    if not bool(env_preflight.get("present")):
        return {
            "checked": False,
            "mismatch": False,
            "runtime_missing_env": runtime_missing,
            "preflight_missing_env": [],
            "only_runtime_missing": runtime_missing,
            "only_preflight_missing": [],
        }
    runtime_set = set(runtime_missing)
    preflight_set = set(preflight_missing)
    return {
        "checked": True,
        "mismatch": runtime_set != preflight_set,
        "runtime_missing_env": runtime_missing,
        "preflight_missing_env": preflight_missing,
        "only_runtime_missing": sorted(runtime_set - preflight_set),
        "only_preflight_missing": sorted(preflight_set - runtime_set),
    }


def _prediction_readiness_enrich_runtime_row(
    row: dict[str, Any],
    *,
    backfill_plan_steps: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(row)
    index = row.get("index")
    if not _is_plain_int(index):
        return enriched
    plan_step = backfill_plan_steps.get(index)
    if not isinstance(plan_step, dict):
        return enriched
    enriched["source_requirements"] = _string_list(plan_step.get("source_requirements"))
    enriched["suggested_commands"] = _string_list(plan_step.get("suggested_commands"))
    enriched["reason"] = (
        str(plan_step["reason"]) if isinstance(plan_step.get("reason"), str) else None
    )
    enriched["blocked_by"] = _string_list(plan_step.get("blocked_by"))
    _attach_prediction_readiness_provenance(enriched, plan_step)
    return enriched


def _with_prediction_readiness_provenance(
    row: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(row)
    _attach_prediction_readiness_provenance(enriched, source)
    return enriched


def _attach_prediction_readiness_provenance(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    source_artifacts = _string_list(source.get("source_artifacts"))
    if source_artifacts:
        target["source_artifacts"] = source_artifacts
    additional_reasons = _string_list(source.get("additional_reasons"))
    if additional_reasons:
        target["additional_reasons"] = additional_reasons


def _prediction_readiness_local_input_summaries(
    payloads: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        "congress_load": _prediction_readiness_local_input_summary(
            payloads.get("congress_load_summary"),
            metric_names=(
                "member_row_count",
                "member_term_row_count",
                "bill_row_count",
                "bill_sponsor_row_count",
                "vote_event_row_count",
                "vote_cast_row_count",
            ),
            source_state=True,
        ),
        "fec_inputs": _prediction_readiness_local_input_summary(
            payloads.get("fec_inputs_verify"),
            metric_names=(
                "committee_master_rows",
                "candidate_committee_linkage_rows",
                "individual_contributions_rows",
                "member_fec_crosswalk_rows",
            ),
        ),
        "public_statement_rows": _prediction_readiness_local_input_summary(
            payloads.get("public_statement_rows_verify"),
            metric_names=(
                "row_count",
                "member_count",
                "sector_count",
                "official_source_url_count",
            ),
        ),
    }


def _prediction_readiness_local_input_summary(
    payload: dict[str, Any] | None,
    *,
    metric_names: tuple[str, ...],
    source_state: bool = False,
) -> dict[str, Any]:
    if not payload:
        return {
            "present": False,
            "ok": False,
            "metrics": {},
            "issue_count": 0,
            "quality_gate_failure_count": 0,
        }
    metrics = {
        name: payload.get(name)
        for name in metric_names
        if isinstance(payload.get(name), (int, float)) and not isinstance(payload.get(name), bool)
    }
    row_counts = payload.get("row_counts")
    if isinstance(row_counts, dict):
        for name, count in row_counts.items():
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                metrics[str(name)] = count
    summary = {
        "present": True,
        "ok": bool(payload.get("ok")),
        "metrics": metrics,
        "issue_count": _plain_int_or_zero(payload.get("issue_count")),
        "quality_gate_failure_count": _plain_int_or_zero(payload.get("quality_gate_failure_count")),
    }
    if source_state and isinstance(payload.get("source_state"), dict):
        source_state_payload = payload["source_state"]
        summary["source_state"] = source_state_payload
        for name in metric_names:
            value = source_state_payload.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[name] = value
    return summary


def _prediction_readiness_congress_load_requirement_blockers(
    summary: dict[str, Any],
    *,
    required: bool,
) -> list[str]:
    if not required:
        return []
    if not bool(summary.get("present")):
        return ["local_input:congress_load_missing"]
    blockers: list[str] = []
    if not bool(summary.get("ok")):
        blockers.append("local_input:congress_load_not_ok")
    source_state = summary.get("source_state")
    if not isinstance(source_state, dict):
        blockers.append("local_input:congress_load_source_state_missing")
        return blockers
    for key in (
        "prediction_member_inputs_available",
        "prediction_bill_inputs_available",
        "prediction_vote_inputs_available",
    ):
        if source_state.get(key) is not True:
            blockers.append(f"local_input:congress_load_{key}_missing")
    for row_key, availability_key in (
        ("member_row_count", "prediction_member_inputs_available"),
        ("bill_row_count", "prediction_bill_inputs_available"),
        ("vote_event_row_count", "prediction_vote_inputs_available"),
        ("vote_cast_row_count", "prediction_vote_inputs_available"),
    ):
        if (
            source_state.get(availability_key) is True
            and _plain_int_or_zero(source_state.get(row_key)) <= 0
        ):
            blockers.append(f"local_input:congress_load_{row_key}_missing")
    source_family_ids_raw = source_state.get("source_family_ids")
    source_family_ids_valid = _prediction_readiness_source_family_ids_valid(source_family_ids_raw)
    if not source_family_ids_valid:
        blockers.append("local_input:congress_load_source_family_ids_invalid")
        source_family_ids: set[str] = set()
    else:
        source_family_ids_list = cast(list[str], source_family_ids_raw)
        source_family_ids = set(source_family_ids_list)
        source_family_count = source_state.get("source_family_count")
        if (
            not isinstance(source_family_count, int)
            or isinstance(source_family_count, bool)
            or source_family_count < 0
        ):
            blockers.append("local_input:congress_load_source_family_count_invalid")
        elif source_family_count != len(source_family_ids_list):
            blockers.append("local_input:congress_load_source_family_count_mismatch")
    for key, source_family_id in (
        ("prediction_member_inputs_available", "committee_membership"),
        ("prediction_bill_inputs_available", "congress_bill"),
        ("prediction_vote_inputs_available", "congress_vote"),
    ):
        if source_state.get(key) is True and source_family_id not in source_family_ids:
            blockers.append(f"local_input:congress_load_source_family_{source_family_id}_missing")
    return blockers


def _prediction_readiness_source_family_ids_valid(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(
            isinstance(item, str)
            and item
            and item.strip() == item
            and _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None
            for item in value
        )
        and value == sorted(value)
        and len(set(value)) == len(value)
    )


def _prediction_readiness_next_live_actions(
    *,
    benchmark: dict[str, Any],
    runtime: dict[str, Any],
) -> list[str]:
    actions = [
        f"set:{requirement.removeprefix('env:')}"
        for requirement in runtime["missing_runtime_requirements"]
        if requirement.startswith("env:")
    ]
    components = benchmark.get("components")
    if isinstance(components, dict):
        if not bool(components.get("bill_semantics_cache", {}).get("ok", False)):
            actions.append("materialize_bill_semantics_cache")
        if not bool(components.get("source_url_audit", {}).get("ok", True)):
            actions.append("repair_prediction_source_urls")
        if not bool(components.get("inventory", {}).get("ok", True)):
            actions.append("refresh_prediction_input_inventory")
        if not bool(components.get("eval_manifest", {}).get("ok", True)):
            actions.append("refresh_prediction_eval_manifest")
        if not bool(components.get("eval_window_run", {}).get("ok", True)):
            actions.append("verify_prediction_eval_window_run")
    return sorted(dict.fromkeys(actions))


def _prediction_readiness_blocker_summary(
    *,
    blockers: list[str],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    categories = {
        "benchmark_quality_gates": 0,
        "benchmark_issues": 0,
        "runtime_quality_gates": 0,
        "runtime_requirements": 0,
        "local_inputs": 0,
        "source_artifacts": 0,
        "env_preflight": 0,
    }
    for blocker in blockers:
        if blocker.startswith("benchmark_issue:"):
            categories["benchmark_issues"] += 1
        elif blocker.startswith("benchmark:"):
            categories["benchmark_quality_gates"] += 1
        elif blocker.startswith("runtime_requirement:"):
            categories["runtime_requirements"] += 1
        elif blocker.startswith("runtime:"):
            categories["runtime_quality_gates"] += 1
        elif blocker.startswith("local_input:"):
            categories["local_inputs"] += 1
        elif blocker.startswith("source_artifact:"):
            categories["source_artifacts"] += 1
        elif blocker.startswith(
            (
                "env_preflight:",
                "env_preflight_issue:",
                "env_preflight_verify:",
                "env_preflight_verify_issue:",
            )
        ):
            categories["env_preflight"] += 1
        elif blocker.startswith("eval_window_run:"):
            categories["eval_window_run"] = categories.get("eval_window_run", 0) + 1
    return {
        "total": len(blockers),
        "by_category": categories,
        "by_benchmark_component": _prediction_readiness_component_blocker_counts(benchmark),
    }


def _prediction_readiness_component_blocker_counts(
    benchmark: dict[str, Any],
) -> dict[str, dict[str, int]]:
    components = benchmark.get("components")
    if not isinstance(components, dict):
        return {}
    counts: dict[str, dict[str, int]] = {}
    for name, component in components.items():
        if not isinstance(component, dict):
            continue
        issue_count = _plain_int_or_zero(component.get("issue_count"))
        quality_gate_failure_count = _plain_int_or_zero(component.get("quality_gate_failure_count"))
        counts[str(name)] = {
            "issue_count": issue_count,
            "quality_gate_failure_count": quality_gate_failure_count,
            "total": issue_count + quality_gate_failure_count,
        }
    return counts


def _prediction_readiness_next_actions_by_kind(
    actions: list[str],
) -> dict[str, list[str]]:
    grouped = {
        "env_setup": [],
        "data_refresh": [],
        "semantic_materialization": [],
        "source_repair": [],
    }
    for action in actions:
        if action.startswith("set:"):
            grouped["env_setup"].append(action)
        elif action == "materialize_bill_semantics_cache":
            grouped["semantic_materialization"].append(action)
        elif action == "repair_prediction_source_urls":
            grouped["source_repair"].append(action)
        else:
            grouped["data_refresh"].append(action)
    return grouped


def _prediction_offline_readiness_run_metadata(
    result: dict[str, Any],
) -> dict[str, Any]:
    source_artifacts = result.get("source_artifacts")
    artifact_sha256: dict[str, str | None] = {}
    if isinstance(source_artifacts, dict):
        for name, artifact in source_artifacts.items():
            if isinstance(artifact, dict):
                sha256 = artifact.get("sha256")
                artifact_sha256[str(name)] = str(sha256) if sha256 is not None else None
    source_state = {
        "offline_input_ok": result["offline_input_ok"],
        "congress_load_required": bool(result.get("congress_load_required")),
        "live_ready": result["live_ready"],
        "blocker_count": result["blocker_count"],
        "blocker_summary": result["blocker_summary"],
        "benchmark_component_count": result["benchmark"]["component_count"],
        "benchmark_quality_gate_failure_count": result["benchmark"]["quality_gate_failure_count"],
        "missing_runtime_requirements": result["runtime"]["missing_runtime_requirements"],
        "env_preflight_missing_env": result["env_preflight"]["missing_env"],
        "env_preflight_issue_count": result["env_preflight"]["issue_count"],
        "env_consistency": result["env_consistency"],
        "runtime_ready_step_count": result["runtime"]["runtime_ready_step_count"],
        "runtime_blocked_step_count": result["runtime"]["runtime_blocked_step_count"],
    }
    if _plain_int_or_zero(result["benchmark"].get("jurisdiction_count")):
        source_state["benchmark_jurisdiction_count"] = _plain_int_or_zero(
            result["benchmark"].get("jurisdiction_count")
        )
    if _sorted_string_list_or_empty(result["benchmark"].get("jurisdiction_ids")):
        source_state["benchmark_jurisdiction_ids"] = _sorted_string_list_or_empty(
            result["benchmark"].get("jurisdiction_ids")
        )
    if _plain_int_or_zero(result["benchmark"].get("implemented_jurisdiction_count")):
        source_state["benchmark_implemented_jurisdiction_count"] = _plain_int_or_zero(
            result["benchmark"].get("implemented_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(result["benchmark"].get("implemented_jurisdiction_ids")):
        source_state["benchmark_implemented_jurisdiction_ids"] = _sorted_string_list_or_empty(
            result["benchmark"].get("implemented_jurisdiction_ids")
        )
    if _plain_int_or_zero(result["benchmark"].get("portable_jurisdiction_count")):
        source_state["benchmark_portable_jurisdiction_count"] = _plain_int_or_zero(
            result["benchmark"].get("portable_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(result["benchmark"].get("portable_jurisdiction_ids")):
        source_state["benchmark_portable_jurisdiction_ids"] = _sorted_string_list_or_empty(
            result["benchmark"].get("portable_jurisdiction_ids")
        )
    if _plain_int_or_zero(result["benchmark"].get("legislative_body_count")):
        source_state["benchmark_legislative_body_count"] = _plain_int_or_zero(
            result["benchmark"].get("legislative_body_count")
        )
    if _sorted_string_list_or_empty(result["benchmark"].get("legislative_body_ids")):
        source_state["benchmark_legislative_body_ids"] = _sorted_string_list_or_empty(
            result["benchmark"].get("legislative_body_ids")
        )
    if _plain_int_or_zero(result["benchmark"].get("legislative_session_count")):
        source_state["benchmark_legislative_session_count"] = _plain_int_or_zero(
            result["benchmark"].get("legislative_session_count")
        )
    if _sorted_string_list_or_empty(result["benchmark"].get("legislative_session_ids")):
        source_state["benchmark_legislative_session_ids"] = _sorted_string_list_or_empty(
            result["benchmark"].get("legislative_session_ids")
        )
    benchmark_source_state = _prediction_readiness_benchmark_source_state(result["benchmark"])
    if _plain_int_or_zero(benchmark_source_state.get("jurisdiction_count")):
        source_state["benchmark_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("jurisdiction_ids")):
        source_state["benchmark_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("implemented_jurisdiction_count")):
        source_state["benchmark_implemented_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("implemented_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("implemented_jurisdiction_ids")):
        source_state["benchmark_implemented_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("implemented_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("portable_jurisdiction_count")):
        source_state["benchmark_portable_jurisdiction_count"] = _plain_int_or_zero(
            benchmark_source_state.get("portable_jurisdiction_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("portable_jurisdiction_ids")):
        source_state["benchmark_portable_jurisdiction_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("portable_jurisdiction_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("legislative_body_count")):
        source_state["benchmark_legislative_body_count"] = _plain_int_or_zero(
            benchmark_source_state.get("legislative_body_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("legislative_body_ids")):
        source_state["benchmark_legislative_body_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("legislative_body_ids")
        )
    if _plain_int_or_zero(benchmark_source_state.get("legislative_session_count")):
        source_state["benchmark_legislative_session_count"] = _plain_int_or_zero(
            benchmark_source_state.get("legislative_session_count")
        )
    if _sorted_string_list_or_empty(benchmark_source_state.get("legislative_session_ids")):
        source_state["benchmark_legislative_session_ids"] = _sorted_string_list_or_empty(
            benchmark_source_state.get("legislative_session_ids")
        )
    source_state.update(
        _prediction_readiness_benchmark_backtest_source_coverage(benchmark_source_state)
    )
    source_state.update(
        _prediction_readiness_benchmark_inventory_feature_source_coverage(benchmark_source_state)
    )
    source_state.update(
        _prediction_readiness_benchmark_inventory_source_coverage(benchmark_source_state)
    )
    source_state.update(
        _prediction_readiness_benchmark_source_url_audit_gaps(benchmark_source_state)
    )
    source_state.update(_prediction_readiness_benchmark_eval_cutoff_audit(benchmark_source_state))
    source_state.update(_prediction_readiness_backfill_plan_source_state_for_run_metadata(result))
    eval_window_run = result["benchmark"].get("eval_window_run")
    if isinstance(eval_window_run, dict) and bool(eval_window_run.get("present")):
        source_state["eval_window_run"] = _prediction_readiness_eval_window_run_source_state(
            eval_window_run
        )
    env_preflight_verify = result.get("env_preflight_verify")
    if isinstance(env_preflight_verify, dict) and bool(env_preflight_verify.get("present")):
        source_state["env_preflight_verify_issue_count"] = env_preflight_verify["issue_count"]
        source_state["env_preflight_verify_quality_gate_failure_count"] = env_preflight_verify[
            "quality_gate_failure_count"
        ]
    local_inputs = result.get("local_inputs")
    if isinstance(local_inputs, dict):
        congress_load = local_inputs.get("congress_load")
        if isinstance(congress_load, dict):
            congress_source_state = congress_load.get("source_state")
            if isinstance(congress_source_state, dict):
                source_state.update(
                    _prediction_readiness_congress_load_source_state(congress_source_state)
                )
    return {
        "command": "prediction-offline-readiness-summary",
        "source_artifact_sha256": artifact_sha256,
        "source_state": source_state,
    }


def _prediction_readiness_congress_load_source_state(
    congress_source_state: dict[str, Any],
) -> dict[str, Any]:
    source_state: dict[str, Any] = {}
    for key in (
        "prediction_member_inputs_available",
        "prediction_bill_inputs_available",
        "prediction_vote_inputs_available",
    ):
        value = congress_source_state.get(key)
        if isinstance(value, bool):
            source_state[f"congress_load_{key}"] = value
    source_family_ids = _sorted_string_list_or_empty(congress_source_state.get("source_family_ids"))
    if source_family_ids:
        source_state["congress_load_source_family_ids"] = source_family_ids
        source_state["congress_load_source_family_count"] = len(source_family_ids)
    for key in (
        "member_row_count",
        "member_term_row_count",
        "bill_row_count",
        "bill_sponsor_row_count",
        "vote_event_row_count",
        "vote_cast_row_count",
    ):
        value = _plain_int_or_zero(congress_source_state.get(key))
        if value:
            source_state[f"congress_load_{key}"] = value
    return source_state


def _prediction_readiness_eval_window_run_source_state(
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "present": bool(summary.get("present")),
        "ok": bool(summary.get("ok")),
        "checked": _plain_int_or_zero(summary.get("checked")),
        "window_count": _plain_int_or_zero(summary.get("window_count")),
        "missing_verifier_count": _plain_int_or_zero(summary.get("missing_verifier_count")),
        "failing_verifier_count": _plain_int_or_zero(summary.get("failing_verifier_count")),
        "stale_field_count": _plain_int_or_zero(summary.get("stale_field_count")),
        **_prediction_readiness_eval_window_run_archive_summary(summary),
        **_prediction_readiness_eval_window_run_label_gate_summary(summary),
        **_prediction_readiness_eval_window_run_strict_gate_summary(summary),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _prediction_benchmark_backfill_plan_artifact(
    *,
    args: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    artifacts = result.get("artifacts")
    artifact_sha256: dict[str, str | None] = {}
    if isinstance(artifacts, dict):
        for name, artifact in artifacts.items():
            if isinstance(artifact, dict):
                sha256 = artifact.get("sha256")
                artifact_sha256[str(name)] = str(sha256) if sha256 is not None else None
    return {
        "command": "prediction-backfill-plan",
        "source_command": "verify-prediction-benchmark",
        "plan": result["backfill_plan"],
        "recommendation_count": result["backfill_recommendation_count"],
        "quality_gate_failures": result["quality_gate_failures"],
        "quality_gate_failure_count": result["quality_gate_failure_count"],
        "issues": result["issues"],
        "issue_count": result["issue_count"],
        "run_metadata": {
            "source_command": "verify-prediction-benchmark",
            "verification_flags": _prediction_benchmark_verification_flags(args),
            "source_artifact_sha256": artifact_sha256,
            "source_state": _prediction_benchmark_source_state(result),
        },
    }


def _prediction_benchmark_run_metadata(
    args: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    artifacts = result.get("artifacts")
    artifact_sha256: dict[str, str | None] = {}
    if isinstance(artifacts, dict):
        for name, artifact in artifacts.items():
            if isinstance(artifact, dict):
                sha256 = artifact.get("sha256")
                artifact_sha256[str(name)] = str(sha256) if sha256 is not None else None
    return {
        "command": "verify-prediction-benchmark",
        "verification_flags": _prediction_benchmark_verification_flags(args),
        "artifact_sha256": artifact_sha256,
        "source_state": _prediction_benchmark_source_state(result),
    }


def _prediction_benchmark_source_state(result: dict[str, Any]) -> dict[str, Any]:
    components = result.get("components")
    component_names: list[str] = []
    component_source_states: dict[str, Any] = {}
    if isinstance(components, dict):
        for name, component in components.items():
            component_names.append(str(name))
            if not isinstance(component, dict):
                continue
            run_metadata = component.get("run_metadata")
            if not isinstance(run_metadata, dict):
                continue
            source_state = run_metadata.get("source_state")
            if source_state is not None:
                component_source_states[str(name)] = source_state
    scope_counts = _prediction_benchmark_component_scope_counts(component_source_states)
    backfill_plan = result.get("backfill_plan")
    backfill_plan_step_count = None
    if isinstance(backfill_plan, dict):
        backfill_plan_step_count = backfill_plan.get("step_count")
    backfill_plan_missing_runtime_requirements_by_step = result.get(
        "backfill_plan_missing_runtime_requirements_by_step",
        [],
    )
    if not isinstance(backfill_plan_missing_runtime_requirements_by_step, list):
        backfill_plan_missing_runtime_requirements_by_step = []
    backfill_plan_runtime_readiness_by_step = result.get(
        "backfill_plan_runtime_readiness_by_step",
        [],
    )
    if not isinstance(backfill_plan_runtime_readiness_by_step, list):
        backfill_plan_runtime_readiness_by_step = []
    source_state = {
        "component_count": result.get("component_count"),
        "component_names": sorted(component_names),
        "component_source_state_names": sorted(component_source_states),
        "checked": result.get("checked"),
        "quality_gate_failure_count": result.get("quality_gate_failure_count"),
        "backfill_recommendation_count": result.get("backfill_recommendation_count"),
        "backfill_plan_step_count": backfill_plan_step_count,
        "backfill_plan_missing_runtime_requirements": result.get(
            "backfill_plan_missing_runtime_requirements",
            [],
        ),
        "backfill_plan_missing_runtime_requirement_step_count": len(
            backfill_plan_missing_runtime_requirements_by_step
        ),
        "backfill_plan_missing_runtime_requirements_by_step": (
            backfill_plan_missing_runtime_requirements_by_step
        ),
        "backfill_plan_runtime_ready_step_count": sum(
            1
            for step in backfill_plan_runtime_readiness_by_step
            if isinstance(step, dict) and step.get("runnable") is True
        ),
        "backfill_plan_runtime_blocked_step_count": sum(
            1
            for step in backfill_plan_runtime_readiness_by_step
            if isinstance(step, dict) and step.get("runnable") is False
        ),
        "backfill_plan_runtime_readiness_by_step": (backfill_plan_runtime_readiness_by_step),
        "component_source_states": component_source_states,
    }
    source_state.update(scope_counts)
    source_state.update(_prediction_benchmark_inventory_source_gaps(component_source_states))
    source_state.update(_prediction_benchmark_backtest_source_coverage(component_source_states))
    source_state.update(_prediction_benchmark_source_url_audit_gaps(component_source_states))
    source_state.update(_prediction_benchmark_eval_cutoff_audit(component_source_states))
    return source_state


def _prediction_benchmark_inventory_source_gaps(
    component_source_states: dict[str, Any],
) -> dict[str, Any]:
    inventory_state = component_source_states.get("inventory")
    if not isinstance(inventory_state, dict):
        return {}
    count_keys = (
        "training_labels_missing_feature_member_count",
        "evaluation_labels_missing_feature_member_count",
        "portable_rows_missing_jurisdiction_id_count",
        "portable_rows_missing_body_id_count",
        "portable_rows_missing_session_id_count",
        "training_label_source_url_count",
        "evaluation_label_source_url_count",
        "official_training_label_source_url_count",
        "official_evaluation_label_source_url_count",
        "bill_source_url_count",
        "official_bill_source_url_count",
        "bill_sponsor_count",
        "bill_available_sponsor_count",
        "bill_primary_sponsor_introduced_date_fallback_count",
        "sourced_ontology_edge_count",
        "official_sourced_ontology_edge_count",
        "training_feature_vote_history_member_count",
        "training_feature_vote_history_source_member_count",
        "evaluation_feature_vote_history_member_count",
        "evaluation_feature_vote_history_source_member_count",
    )
    rate_keys = (
        "training_label_source_url_coverage_rate",
        "evaluation_label_source_url_coverage_rate",
        "training_label_official_source_url_coverage_rate",
        "evaluation_label_official_source_url_coverage_rate",
        "bill_source_url_coverage_rate",
        "bill_official_source_url_coverage_rate",
        "bill_sponsor_availability_rate",
        "ontology_source_anchor_coverage_rate",
        "ontology_official_source_anchor_coverage_rate",
        "training_feature_vote_history_source_coverage_rate",
        "evaluation_feature_vote_history_source_coverage_rate",
    )
    source_state: dict[str, Any] = {
        f"inventory_{key}": inventory_state[key]
        for key in count_keys
        if _is_non_negative_plain_int(inventory_state.get(key))
    }
    source_state.update(
        {
            f"inventory_{key}": inventory_state[key]
            for key in rate_keys
            if key in inventory_state
            and _prediction_eval_source_state_rate_valid(inventory_state.get(key))
        }
    )
    return source_state


def _prediction_benchmark_backtest_source_coverage(
    component_source_states: dict[str, Any],
) -> dict[str, Any]:
    backtest_state = component_source_states.get("backtest")
    if not isinstance(backtest_state, dict):
        return {}
    keys = (
        "feature_source_backed_prediction_count",
        "feature_source_missing_prediction_count",
        "feature_source_coverage_rate",
        "official_feature_source_backed_prediction_count",
        "unofficial_feature_source_backed_prediction_count",
        "official_feature_source_coverage_rate",
        "legislative_feature_source_anchor_count",
        "legislative_feature_source_anchor_context_count",
        "legislative_feature_source_anchor_missing_context_count",
    )
    return {f"backtest_{key}": backtest_state[key] for key in keys if key in backtest_state}


def _prediction_benchmark_source_url_audit_gaps(
    component_source_states: dict[str, Any],
) -> dict[str, Any]:
    source_url_audit_state = component_source_states.get("source_url_audit")
    if not isinstance(source_url_audit_state, dict):
        return {}
    count_keys = (
        "quality_gate_failure_count",
        "gap_count",
        "missing_url_sourced_prediction_count",
        "missing_official_source_prediction_count",
        "sample_case_count",
        "portable_sample_missing_body_id_count",
        "portable_sample_missing_session_id_count",
    )
    int_list_keys = ("sample_vote_event_ids",)
    string_list_keys = (
        "quality_gate_failures",
        "sample_bill_keys",
        "sample_member_bioguide_ids",
        "sample_source_family_ids",
        "sample_jurisdiction_ids",
        "sample_legislative_body_ids",
        "sample_legislative_session_ids",
    )
    source_state = {
        f"source_url_audit_{key}": source_url_audit_state[key]
        for key in count_keys
        if _is_non_negative_plain_int(source_url_audit_state.get(key))
    }
    source_state.update(
        {
            f"source_url_audit_{key}": source_url_audit_state[key]
            for key in int_list_keys
            if _prediction_backfill_plan_non_negative_int_list(source_url_audit_state.get(key))
        }
    )
    source_state.update(
        {
            f"source_url_audit_{key}": source_url_audit_state[key]
            for key in string_list_keys
            if _prediction_backfill_plan_sorted_string_list(source_url_audit_state.get(key))
        }
    )
    return source_state


def _prediction_benchmark_eval_cutoff_audit(
    component_source_states: dict[str, Any],
) -> dict[str, Any]:
    eval_state = component_source_states.get("eval_manifest")
    if not isinstance(eval_state, dict):
        eval_state = component_source_states.get("eval")
    if not isinstance(eval_state, dict):
        return {}
    cutoff_audit = eval_state.get("cutoff_audit")
    if not isinstance(cutoff_audit, dict):
        return {}
    return {
        f"eval_cutoff_{key}": value
        for key, value in cutoff_audit.items()
        if key in _BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS and _is_non_negative_plain_int(value)
    }


def _prediction_benchmark_component_scope_counts(
    component_source_states: dict[str, Any],
) -> dict[str, Any]:
    jurisdiction_count = 0
    implemented_jurisdiction_count = 0
    portable_jurisdiction_count = 0
    legislative_body_count = 0
    legislative_session_count = 0
    jurisdiction_ids: set[str] = set()
    implemented_jurisdiction_ids: set[str] = set()
    portable_jurisdiction_ids: set[str] = set()
    legislative_body_ids: set[str] = set()
    legislative_session_ids: set[str] = set()
    for source_state in component_source_states.values():
        if not isinstance(source_state, dict):
            continue
        jurisdiction_count = max(
            jurisdiction_count,
            _plain_int_or_zero(source_state.get("jurisdiction_count")),
        )
        implemented_jurisdiction_count = max(
            implemented_jurisdiction_count,
            _plain_int_or_zero(source_state.get("implemented_jurisdiction_count")),
        )
        portable_jurisdiction_count = max(
            portable_jurisdiction_count,
            _plain_int_or_zero(source_state.get("portable_jurisdiction_count")),
        )
        legislative_body_count = max(
            legislative_body_count,
            _plain_int_or_zero(source_state.get("legislative_body_count")),
        )
        legislative_session_count = max(
            legislative_session_count,
            _plain_int_or_zero(source_state.get("legislative_session_count")),
        )
        jurisdiction_ids.update(_sorted_string_list_or_empty(source_state.get("jurisdiction_ids")))
        implemented_jurisdiction_ids.update(
            _sorted_string_list_or_empty(source_state.get("implemented_jurisdiction_ids"))
        )
        portable_jurisdiction_ids.update(
            _sorted_string_list_or_empty(source_state.get("portable_jurisdiction_ids"))
        )
        legislative_body_ids.update(
            _sorted_string_list_or_empty(source_state.get("legislative_body_ids"))
        )
        legislative_session_ids.update(
            _sorted_string_list_or_empty(source_state.get("legislative_session_ids"))
        )
    counts: dict[str, Any] = {}
    if jurisdiction_ids:
        counts["jurisdiction_count"] = len(jurisdiction_ids)
    elif jurisdiction_count:
        counts["jurisdiction_count"] = jurisdiction_count
    if jurisdiction_ids:
        counts["jurisdiction_ids"] = sorted(jurisdiction_ids)
    if implemented_jurisdiction_ids:
        counts["implemented_jurisdiction_count"] = len(implemented_jurisdiction_ids)
    elif implemented_jurisdiction_count:
        counts["implemented_jurisdiction_count"] = implemented_jurisdiction_count
    if implemented_jurisdiction_ids:
        counts["implemented_jurisdiction_ids"] = sorted(implemented_jurisdiction_ids)
    if portable_jurisdiction_ids:
        counts["portable_jurisdiction_count"] = len(portable_jurisdiction_ids)
    elif portable_jurisdiction_count:
        counts["portable_jurisdiction_count"] = portable_jurisdiction_count
    if portable_jurisdiction_ids:
        counts["portable_jurisdiction_ids"] = sorted(portable_jurisdiction_ids)
    if legislative_body_ids:
        counts["legislative_body_count"] = len(legislative_body_ids)
    elif legislative_body_count:
        counts["legislative_body_count"] = legislative_body_count
    if legislative_body_ids:
        counts["legislative_body_ids"] = sorted(legislative_body_ids)
    if legislative_session_ids:
        counts["legislative_session_count"] = len(legislative_session_ids)
    elif legislative_session_count:
        counts["legislative_session_count"] = legislative_session_count
    if legislative_session_ids:
        counts["legislative_session_ids"] = sorted(legislative_session_ids)
    return counts


def _sorted_string_list_or_empty(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    if not all(isinstance(item, str) and item.strip() == item and item for item in value):
        return []
    if value != sorted(value) or len(set(value)) != len(value):
        return []
    return value


def _prediction_benchmark_verification_flags(args: Any) -> dict[str, Any]:
    return {
        "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
        "require_clean_inventory": bool(getattr(args, "require_clean_inventory", False)),
        "require_inventory_congress_archive_manifest": bool(
            getattr(args, "require_inventory_congress_archive_manifest", False)
        ),
        "require_portable_jurisdiction_ids": bool(
            getattr(args, "require_portable_jurisdiction_ids", False)
        ),
        "require_portable_body_ids": bool(getattr(args, "require_portable_body_ids", False)),
        "require_portable_session_ids": bool(getattr(args, "require_portable_session_ids", False)),
        "require_evaluated_backtest": bool(getattr(args, "require_evaluated_backtest", False)),
        "require_backtest_source_urls": bool(getattr(args, "require_backtest_source_urls", False)),
        "require_backtest_official_source_urls": bool(
            getattr(args, "require_backtest_official_source_urls", False)
        ),
        "require_backtest_congress_archive_manifest": bool(
            getattr(args, "require_backtest_congress_archive_manifest", False)
        ),
        "require_backtest_ontology_feature_signals": bool(
            getattr(args, "require_backtest_ontology_feature_signals", False)
        ),
        "require_backtest_source_families": _sorted_string_list_or_empty(
            list(getattr(args, "require_backtest_source_families", None) or [])
        ),
        "require_ready_quality": bool(getattr(args, "require_ready_quality", False)),
        "require_eval_failure_analysis": bool(
            getattr(args, "require_eval_failure_analysis", False)
        ),
        "require_eval_backfill_recommendations": bool(
            getattr(args, "require_eval_backfill_recommendations", False)
        ),
        "require_eval_congress_archive_manifest": bool(
            getattr(args, "require_eval_congress_archive_manifest", False)
        ),
        "require_eval_fail_on_unknown_bill_semantic_availability": bool(
            getattr(
                args,
                "require_eval_fail_on_unknown_bill_semantic_availability",
                False,
            )
        ),
        "require_eval_fail_on_unknown_bill_signal_availability": bool(
            getattr(
                args,
                "require_eval_fail_on_unknown_bill_signal_availability",
                False,
            )
        ),
        "require_eval_fail_on_unknown_ontology_edge_availability": bool(
            getattr(
                args,
                "require_eval_fail_on_unknown_ontology_edge_availability",
                False,
            )
        ),
        "require_eval_fail_on_unknown_contribution_signal_availability": bool(
            getattr(
                args,
                "require_eval_fail_on_unknown_contribution_signal_availability",
                False,
            )
        ),
        "require_eval_fail_on_unknown_statement_signal_availability": bool(
            getattr(
                args,
                "require_eval_fail_on_unknown_statement_signal_availability",
                False,
            )
        ),
        "require_eval_ontology_feature_signals": bool(
            getattr(args, "require_eval_ontology_feature_signals", False)
        ),
        "require_eval_source_families": _sorted_string_list_or_empty(
            list(getattr(args, "require_eval_source_families", None) or [])
        ),
        "require_bill_semantics_plan_source_anchors": bool(
            getattr(args, "require_bill_semantics_plan_source_anchors", False)
        ),
        "min_training_labels": getattr(args, "min_training_labels", None),
        "min_evaluation_labels": getattr(args, "min_evaluation_labels", None),
        "min_training_feature_vote_history_source_coverage_rate": getattr(
            args,
            "min_training_feature_vote_history_source_coverage_rate",
            None,
        ),
        "min_evaluation_feature_vote_history_source_coverage_rate": getattr(
            args,
            "min_evaluation_feature_vote_history_source_coverage_rate",
            None,
        ),
        "min_training_label_source_url_coverage_rate": getattr(
            args,
            "min_training_label_source_url_coverage_rate",
            None,
        ),
        "min_evaluation_label_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_label_source_url_coverage_rate",
            None,
        ),
        "min_training_label_official_source_url_coverage_rate": getattr(
            args,
            "min_training_label_official_source_url_coverage_rate",
            None,
        ),
        "min_evaluation_label_official_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_label_official_source_url_coverage_rate",
            None,
        ),
        "min_bill_source_url_coverage_rate": getattr(
            args,
            "min_bill_source_url_coverage_rate",
            None,
        ),
        "min_bill_official_source_url_coverage_rate": getattr(
            args,
            "min_bill_official_source_url_coverage_rate",
            None,
        ),
        "min_bill_sponsor_availability_rate": getattr(
            args,
            "min_bill_sponsor_availability_rate",
            None,
        ),
        "require_no_bill_sponsor_introduced_date_fallbacks": bool(
            getattr(args, "require_no_bill_sponsor_introduced_date_fallbacks", False)
        ),
        "min_ontology_source_anchor_coverage_rate": getattr(
            args,
            "min_ontology_source_anchor_coverage_rate",
            None,
        ),
        "min_ontology_official_source_anchor_coverage_rate": getattr(
            args,
            "min_ontology_official_source_anchor_coverage_rate",
            None,
        ),
        "min_fec_member_attribution_rate": getattr(
            args,
            "min_fec_member_attribution_rate",
            None,
        ),
        "min_fec_contributions": getattr(args, "min_fec_contributions", None),
        "min_member_attributed_fec_contributions": getattr(
            args,
            "min_member_attributed_fec_contributions",
            None,
        ),
        "min_members_with_fec_candidate_id": getattr(
            args,
            "min_members_with_fec_candidate_id",
            None,
        ),
        "min_public_statement_signals": getattr(
            args,
            "min_public_statement_signals",
            None,
        ),
        "min_members_with_public_statement_signals": getattr(
            args,
            "min_members_with_public_statement_signals",
            None,
        ),
        "min_bill_semantic_coverage_rate": getattr(
            args,
            "min_bill_semantic_coverage_rate",
            None,
        ),
        "min_bill_metadata_coverage_rate": getattr(
            args,
            "min_bill_metadata_coverage_rate",
            None,
        ),
        "min_training_feature_source_coverage_rate": getattr(
            args,
            "min_training_feature_source_coverage_rate",
            None,
        ),
        "min_evaluation_feature_source_coverage_rate": getattr(
            args,
            "min_evaluation_feature_source_coverage_rate",
            None,
        ),
        "min_training_feature_source_url_coverage_rate": getattr(
            args,
            "min_training_feature_source_url_coverage_rate",
            None,
        ),
        "min_training_feature_official_source_coverage_rate": getattr(
            args,
            "min_training_feature_official_source_coverage_rate",
            None,
        ),
        "min_evaluation_feature_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_feature_source_url_coverage_rate",
            None,
        ),
        "min_evaluation_feature_official_source_coverage_rate": getattr(
            args,
            "min_evaluation_feature_official_source_coverage_rate",
            None,
        ),
        "min_evaluation_source_url_coverage_rate": getattr(
            args,
            "min_evaluation_source_url_coverage_rate",
            None,
        ),
        "require_backtest_model_name": getattr(
            args,
            "require_backtest_model_name",
            None,
        ),
        "require_eval_model_names": _required_string_list(
            getattr(args, "require_eval_model_name", None)
        ),
        "bill_semantics_root": getattr(args, "bill_semantics_root", None),
        "require_bill_semantics_cache": bool(getattr(args, "require_bill_semantics_cache", False))
        or bool(
            _required_model_names(
                getattr(args, "require_bill_semantics_model_name", None),
                issues=None,
                label="require_bill_semantics_model_name",
            )
        )
        or bool(getattr(args, "require_bill_semantics_source_inputs_sha256", False)),
        "require_bill_semantics_model_names": _required_model_names(
            getattr(args, "require_bill_semantics_model_name", None),
            issues=None,
            label="require_bill_semantics_model_name",
        ),
        "require_bill_semantics_source_inputs_sha256": bool(
            getattr(
                args,
                "require_bill_semantics_source_inputs_sha256",
                False,
            )
        ),
        "source_url_audit": getattr(args, "source_url_audit", None),
        "require_source_url_audit": bool(getattr(args, "require_source_url_audit", False)),
        "require_source_url_audit_no_gaps": bool(
            getattr(args, "require_source_url_audit_no_gaps", False)
        ),
        "require_source_url_audit_no_official_source_gaps": bool(
            getattr(args, "require_source_url_audit_no_official_source_gaps", False)
        ),
        "require_source_url_audit_no_portable_context_gaps": bool(
            getattr(args, "require_source_url_audit_no_portable_context_gaps", False)
        ),
        "eval_window_run_verify": getattr(args, "eval_window_run_verify", None),
        "require_eval_window_run_verify": bool(
            getattr(args, "require_eval_window_run_verify", False)
        ),
        "check_backfill_runtime_requirements": bool(
            getattr(args, "check_backfill_runtime_requirements", False)
        ),
    }


def _prediction_benchmark_eval_window_run_verify_result(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "command": "verify-prediction-eval-window-run",
            "artifact": str(path),
            "checked": 0,
            "issues": [f"failed to load artifact: {exc}"],
            "issue_count": 1,
            "quality_gate_failures": [],
            "quality_gate_failure_count": 0,
            "run_metadata": _prediction_benchmark_eval_window_run_component_metadata(
                path,
                source_state={"artifact_loaded": False},
            ),
        }
    if not isinstance(artifact, dict):
        issues.append("artifact must be an object")
        artifact = {}
    if artifact.get("command") != "verify-prediction-eval-window-run":
        issues.append("artifact command must be verify-prediction-eval-window-run")
    if artifact.get("ok") is not True:
        quality_gate_failures.append("eval_window_run_verify_not_ok")
    plan = artifact.get("plan")
    summary_verify = artifact.get("summary_verify")
    current: dict[str, Any] | None = None
    if not isinstance(plan, str) or not plan:
        issues.append("artifact plan missing")
    else:
        current = _handle_verify_prediction_eval_window_run(
            SimpleNamespace(
                command="verify-prediction-eval-window-run",
                plan=plan,
                summary_verify=summary_verify if isinstance(summary_verify, str) else None,
                require_window_verifiers=True,
                require_summary_verify=True,
                output=None,
            )
        )
        if current.get("ok") is not True:
            quality_gate_failures.append("eval_window_run_current_verify_not_ok")
            issues.extend(f"current: {issue}" for issue in _string_list(current.get("issues")))
        stale_fields = _prediction_benchmark_eval_window_run_stale_fields(artifact, current)
        if stale_fields:
            quality_gate_failures.append("eval_window_run_verify_stale")
            issues.extend(f"stale_field:{field}" for field in stale_fields)
    checked = 1
    if current is not None and _is_plain_int(current.get("checked")):
        checked += int(current["checked"])
    source_state = {
        "artifact_loaded": bool(artifact),
        "artifact_ok": artifact.get("ok"),
        "artifact_sha256": _optional_file_sha256(path),
        "current_ok": current.get("ok") if current is not None else None,
        "current_checked": current.get("checked") if current is not None else None,
        "stale_field_count": sum(1 for issue in issues if issue.startswith("stale_field:")),
        **_prediction_benchmark_eval_window_run_strict_source_state(current),
    }
    return {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-eval-window-run",
        "artifact": str(path),
        "artifact_sha256": _optional_file_sha256(path),
        "checked": checked,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "current_result": current,
        "run_metadata": _prediction_benchmark_eval_window_run_component_metadata(
            path,
            source_state=source_state,
        ),
    }


def _prediction_benchmark_eval_window_run_strict_source_state(
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    if current is None:
        return {}
    run_metadata = current.get("run_metadata")
    source_state = run_metadata.get("source_state") if isinstance(run_metadata, dict) else None
    if not isinstance(source_state, dict):
        return {}
    strict_keys = {
        "requires_input_inventory_portable_ids",
        "requires_input_inventory_congress_source_families",
        "requires_input_inventory_official_source_thresholds",
        "requires_input_inventory_optional_evidence",
        "requires_eval_manifest_model_suite",
        "requires_eval_manifest_official_source_thresholds",
        "requires_eval_manifest_unknown_availability_failures",
        "requires_eval_manifest_ontology_feature_signals",
    }
    result = {
        key: source_state[key]
        for key in sorted(strict_keys)
        if isinstance(source_state.get(key), bool)
    }
    result["strict_eval_window_run_ready"] = all(result.get(key) is True for key in strict_keys)
    return result


def _prediction_benchmark_eval_window_run_stale_fields(
    artifact: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    fields = [
        "plan",
        "plan_sha256",
        "summary_verify",
        "checked",
        "window_count",
        "expected_window_verifier_count",
        "loaded_verifier_count",
        "missing_verifier_count",
        "failing_verifier_count",
    ]
    stale = [field for field in fields if artifact.get(field) != current.get(field)]
    if artifact.get("window_verifiers") != current.get("window_verifiers"):
        stale.append("window_verifiers")
    if artifact.get("summary_verifier") != current.get("summary_verifier"):
        stale.append("summary_verifier")
    stale.extend(_prediction_benchmark_eval_window_run_source_state_stale_fields(artifact, current))
    return stale


def _prediction_benchmark_eval_window_run_source_state_stale_fields(
    artifact: dict[str, Any],
    current: dict[str, Any],
) -> list[str]:
    artifact_run_metadata = artifact.get("run_metadata")
    current_run_metadata = current.get("run_metadata")
    if not isinstance(artifact_run_metadata, dict) or not isinstance(current_run_metadata, dict):
        return []
    artifact_source_state = artifact_run_metadata.get("source_state")
    current_source_state = current_run_metadata.get("source_state")
    if not isinstance(artifact_source_state, dict) or not isinstance(current_source_state, dict):
        return []
    stale: list[str] = []
    for key in sorted(set(artifact_source_state) - set(current_source_state)):
        stale.append(f"run_metadata.source_state.{key}")
    for key in sorted(set(artifact_source_state) & set(current_source_state)):
        if artifact_source_state.get(key) != current_source_state.get(key):
            stale.append(f"run_metadata.source_state.{key}")
    return stale


def _prediction_benchmark_eval_window_run_component_metadata(
    path: Path,
    *,
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": "verify-prediction-eval-window-run",
        "artifact": str(path),
        "artifact_sha256": _optional_file_sha256(path),
        "source_state": source_state,
    }


def _missing_prediction_eval_window_run_verify_result() -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-prediction-eval-window-run",
        "artifact": None,
        "checked": 0,
        "issues": [],
        "issue_count": 0,
        "quality_gate_failures": ["eval_window_run_verify_missing"],
        "quality_gate_failure_count": 1,
        "run_metadata": {
            "command": "verify-prediction-eval-window-run",
            "artifact": None,
            "source_state": {"artifact_loaded": False},
        },
    }


def _missing_bill_semantics_cache_result(
    required_model_names: list[str],
    *,
    require_source_inputs_sha256: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-bill-semantics",
        "root": None,
        "index_path": None,
        "index_sha256": None,
        "bill_count": 0,
        "issues": [],
        "quality_gate_failures": ["bill_semantics_root_missing"],
        "run_metadata": {
            "command": "verify-bill-semantics",
            "verification_flags": {
                "require_model_names": required_model_names,
                "require_source_inputs_sha256": require_source_inputs_sha256,
            },
            "root": None,
            "index_sha256": None,
            "source_state": _bill_semantics_unavailable_source_state(),
        },
    }


def _missing_prediction_source_url_audit_result() -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-prediction-source-url-audit",
        "artifact": None,
        "checked": 0,
        "issues": ["source URL audit artifact missing"],
        "quality_gate_failures": ["source_url_audit_missing"],
        "gap_count": 0,
        "missing_url_sourced_prediction_count": 0,
        "missing_official_source_prediction_count": 0,
        "run_metadata": {
            "command": "verify-prediction-source-url-audit",
            "source_state": {
                "artifact": None,
                "artifact_sha256": None,
                "eval_report_path": None,
                "eval_report_sha256": None,
                "gap_count": 0,
                "missing_url_sourced_prediction_count": 0,
                "missing_official_source_prediction_count": 0,
            },
        },
    }


def _attach_optional_verification_output(
    args: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    output_arg = getattr(args, "output", None)
    if output_arg is None:
        return result
    output = Path(output_arg)
    output_sha256 = _write_json_artifact(output, result)
    result["output"] = str(output)
    result["output_sha256"] = output_sha256
    return result


def _bill_semantics_verify_run_metadata(
    args: Any,
    root: Path,
    index_sha256: str | None,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-bill-semantics",
        "verification_flags": {
            "require_model_names": _required_string_list(getattr(args, "require_model_name", None)),
            "require_source_inputs_sha256": bool(
                getattr(args, "require_source_inputs_sha256", False)
            ),
        },
        "root": str(root),
        "index_sha256": index_sha256,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _bill_semantics_verify_source_state(
    *,
    payloads: list[Any],
    index_payload: BillSemanticIndexPayload,
    unofficial_source_anchor_count: int,
) -> dict[str, Any]:
    return {
        "bill_count": len(payloads),
        "bill_keys": [payload.bill_key for payload in payloads],
        "model_names": sorted(
            {payload.model_name for payload in payloads if payload.model_name is not None}
        ),
        "source_bill_count": index_payload.source_bill_count,
        "source_bill_keys": list(index_payload.source_bill_keys),
        "source_inputs_sha256": index_payload.source_inputs_sha256,
        "unofficial_source_anchor_count": unofficial_source_anchor_count,
    }


def _bill_semantics_unavailable_source_state(
    *,
    root: Path | None = None,
    index_path: Path | None = None,
    index_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "root": str(root) if root is not None else None,
        "index_path": str(index_path) if index_path is not None else None,
        "index_sha256": index_sha256,
        "bill_count": 0,
        "bill_keys": [],
        "model_names": [],
        "source_bill_count": None,
        "source_bill_keys": [],
        "source_inputs_sha256": None,
        "unofficial_source_anchor_count": None,
    }


def _is_sha256_hex(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _prediction_eval_optional_sha256_issue(label: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _is_sha256_hex(value):
        return f"{label} invalid"
    return None


def _prediction_eval_optional_path_string_issue(label: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return f"{label} must be a string or null"
    if value.strip() == "":
        return f"{label} must be non-empty"
    if value != value.strip():
        return f"{label} must be trimmed"
    return None


def _append_issue_once(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _string_list_has_duplicates(values: list[str]) -> bool:
    return len(set(values)) != len(values)


def _string_list_has_empty(values: list[str]) -> bool:
    return any(value.strip() == "" for value in values)


def _string_list_has_untrimmed(values: list[str]) -> bool:
    return any(value != value.strip() for value in values)


def _bill_semantics_plan_verify_run_metadata(
    args: Any,
    plan_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-bill-semantics-plan",
        "verification_flags": {
            "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
            "require_source_report": bool(getattr(args, "require_source_report", False)),
            "require_matched_source_anchors": bool(
                getattr(args, "require_matched_source_anchors", False)
            ),
        },
        "artifact_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()
        if plan_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _bill_semantics_plan_verify_source_state(
    *,
    feature_cutoff: Any | None = None,
    target_bill_keys: list[str],
    matched_bill_keys: list[str],
    unmatched_bill_keys: list[str],
    unsupported_target_bill_keys: list[str] | None = None,
    matched_bills: list[Any],
    requested_count: int | None,
    inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    matched_source_anchor_count = 0
    matched_bill_source_anchor_count = 0
    for bill in matched_bills:
        if not isinstance(bill, dict):
            continue
        source_anchors = bill.get("source_anchors")
        if isinstance(source_anchors, list):
            matched_source_anchor_count += len(source_anchors)
            if source_anchors:
                matched_bill_source_anchor_count += 1
    source_state = {
        "feature_cutoff": feature_cutoff,
        "target_bill_count": len(target_bill_keys),
        "matched_bill_count": len(matched_bill_keys),
        "unmatched_bill_count": len(unmatched_bill_keys),
        "target_bill_keys": target_bill_keys,
        "matched_bill_keys": matched_bill_keys,
        "unmatched_bill_keys": unmatched_bill_keys,
        "matched_source_anchor_count": matched_source_anchor_count,
        "matched_bill_source_anchor_count": matched_bill_source_anchor_count,
        "requested_count": requested_count,
        "missing_from_report": inputs.get("missing_from_report") if inputs else None,
        "missing_from_report_sha256": (
            inputs.get("missing_from_report_sha256") if inputs else None
        ),
    }
    unsupported_target_bill_keys = unsupported_target_bill_keys or []
    if unsupported_target_bill_keys:
        source_state["unsupported_target_bill_count"] = len(unsupported_target_bill_keys)
        source_state["unsupported_target_bill_keys"] = unsupported_target_bill_keys
    return source_state


def _prediction_backtest_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-backtest",
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_evaluated_predictions": bool(
                getattr(args, "require_evaluated_predictions", False)
            ),
            "require_prediction_source_urls": bool(
                getattr(args, "require_prediction_source_urls", False)
            ),
            "require_official_prediction_source_urls": bool(
                getattr(args, "require_official_prediction_source_urls", False)
            ),
            "require_model_name": getattr(args, "require_model_name", None),
            "require_bill_semantics_cache": (
                bool(getattr(args, "require_bill_semantics_cache", False))
                or bool(
                    _required_string_list(getattr(args, "require_bill_semantics_model_name", None))
                )
                or bool(
                    getattr(
                        args,
                        "require_bill_semantics_source_inputs_sha256",
                        False,
                    )
                )
            ),
            "require_bill_semantics_model_names": _required_string_list(
                getattr(args, "require_bill_semantics_model_name", None)
            ),
            "require_bill_semantics_source_inputs_sha256": bool(
                getattr(
                    args,
                    "require_bill_semantics_source_inputs_sha256",
                    False,
                )
            ),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_eval_manifest_verify_run_metadata(
    args: Any,
    manifest_path: Path,
    *,
    artifacts: dict[Any, Any] | None = None,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verification_flags = {
        "require_artifact_run_metadata": bool(
            getattr(args, "require_artifact_run_metadata", False)
        ),
        "require_congress_archive_manifest": bool(
            getattr(args, "require_congress_archive_manifest", False)
        ),
        "require_ready_quality": bool(getattr(args, "require_ready_quality", False)),
        "require_failure_analysis": bool(getattr(args, "require_failure_analysis", False)),
        "require_backfill_recommendations": bool(
            getattr(args, "require_backfill_recommendations", False)
        ),
        "require_fail_on_unknown_bill_semantic_availability": bool(
            getattr(args, "require_fail_on_unknown_bill_semantic_availability", False)
        ),
        "require_fail_on_unknown_bill_signal_availability": bool(
            getattr(args, "require_fail_on_unknown_bill_signal_availability", False)
        ),
        "require_fail_on_unknown_ontology_edge_availability": bool(
            getattr(args, "require_fail_on_unknown_ontology_edge_availability", False)
        ),
        "require_fail_on_unknown_contribution_signal_availability": bool(
            getattr(args, "require_fail_on_unknown_contribution_signal_availability", False)
        ),
        "require_fail_on_unknown_statement_signal_availability": bool(
            getattr(args, "require_fail_on_unknown_statement_signal_availability", False)
        ),
        "require_bill_semantics_cache": (
            bool(getattr(args, "require_bill_semantics_cache", False))
            or bool(_required_string_list(getattr(args, "require_bill_semantics_model_name", None)))
            or bool(
                getattr(
                    args,
                    "require_bill_semantics_source_inputs_sha256",
                    False,
                )
            )
        ),
        "require_bill_semantics_model_names": _required_string_list(
            getattr(args, "require_bill_semantics_model_name", None)
        ),
        "require_bill_semantics_source_inputs_sha256": bool(
            getattr(
                args,
                "require_bill_semantics_source_inputs_sha256",
                False,
            )
        ),
        "require_model_names": _required_string_list(getattr(args, "require_model_name", None)),
        "require_ontology_feature_signals": bool(
            getattr(args, "require_ontology_feature_signals", False)
        ),
        "require_source_families": _required_source_family_ids(
            getattr(args, "require_source_families", None),
            issues=None,
        ),
    }
    for arg_name, _ in _PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS:
        verification_flags[arg_name] = getattr(args, arg_name, None)
    run_metadata = {
        "command": "verify-prediction-eval-manifest",
        "verification_flags": verification_flags,
        "artifact_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_path.is_file()
        else None,
    }
    if artifacts is not None:
        run_metadata["source_artifact_sha256"] = _prediction_eval_manifest_source_artifact_sha256(
            artifacts
        )
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_eval_manifest_source_artifact_sha256(
    artifacts: dict[Any, Any],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in ("report", "dataset"):
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict):
            continue
        sha256 = artifact.get("sha256")
        if isinstance(sha256, str) and _is_sha256_hex(sha256):
            hashes[name] = sha256
    return hashes


def _prediction_eval_manifest_verify_source_state(
    *,
    manifest: dict[str, Any],
    artifacts: dict[Any, Any],
    ontology_feature_signal_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    windows = manifest.get("windows")
    inputs = manifest.get("inputs")
    manifest_source_state = manifest.get("source_state")
    source_state: dict[str, Any] = {
        "artifact_count": len(artifacts),
        "artifact_names": sorted(str(name) for name in artifacts),
        "training_feature_cutoff": None,
        "train_start": None,
        "train_end": None,
        "feature_cutoff": None,
        "label_start": None,
        "label_end": None,
        "bill_semantics_root": None,
        "bill_semantics_index_sha256": None,
        "bill_semantics_model_names": [],
        "manifest_source_state": (
            manifest_source_state if isinstance(manifest_source_state, dict) else None
        ),
        "ontology_feature_signal_state": ontology_feature_signal_state
        if ontology_feature_signal_state is not None
        else _prediction_eval_manifest_ontology_feature_signal_state(artifacts),
    }
    if isinstance(windows, dict):
        source_state.update(
            {
                "training_feature_cutoff": windows.get("training_feature_cutoff"),
                "train_start": windows.get("train_start"),
                "train_end": windows.get("train_end"),
                "feature_cutoff": windows.get("feature_cutoff"),
                "label_start": windows.get("label_start"),
                "label_end": windows.get("label_end"),
            }
        )
    if isinstance(inputs, dict):
        source_state["bill_semantics_root"] = inputs.get("bill_semantics_root")
        source_state["bill_semantics_index_sha256"] = inputs.get("bill_semantics_index_sha256")
        source_state["congress_archive_manifest"] = inputs.get("congress_archive_manifest")
        model_names = inputs.get("bill_semantics_model_names")
        if isinstance(model_names, list):
            source_state["bill_semantics_model_names"] = [
                str(model_name) for model_name in model_names
            ]
    return source_state


def _artifact_reference(path: Path) -> dict[str, str | None]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    }


def _prediction_benchmark_backfill_recommendations(
    eval_manifest_path: Path,
) -> list[dict[str, Any]]:
    try:
        manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(manifest, dict):
        return []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    report = _load_prediction_eval_report_from_artifacts(artifacts)
    if report is None:
        return []
    return [
        recommendation.model_dump(mode="json") for recommendation in report.backfill_recommendations
    ]


def _prediction_benchmark_inventory_backfill_recommendations(
    inventory_result: object,
    inventory_path: Path,
) -> list[dict[str, Any]]:
    if not isinstance(inventory_result, dict):
        return []
    failures = _string_list(inventory_result.get("quality_gate_failures"))
    refresh_failures = [
        failure
        for failure in failures
        if failure.endswith("_official_source_url_coverage_below_min")
        or failure == "ontology_official_source_anchor_coverage_below_min"
    ]
    missing_required_source_family_ids = _missing_required_source_family_ids(failures)
    recommendations: list[dict[str, Any]] = []
    fec_failures = [
        failure
        for failure in failures
        if failure
        in {
            "fec_contribution_count_below_min",
            "member_attributed_fec_contribution_count_below_min",
            "members_with_fec_candidate_id_count_below_min",
        }
    ]
    if fec_failures:
        recommendations.append(
            {
                "action": "load_fec_donations_and_member_crosswalks",
                "priority_score": 32,
                "reason": (
                    "FEC donation/member-crosswalk evidence is below the strict inventory gate."
                ),
                "affected_case_count": len(fec_failures),
                "missing_bill_keys": [],
                "unavailable_signal_counts": {"inventory_quality_gate_failures": len(fec_failures)},
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "artifact": str(inventory_path),
            }
        )
    statement_failures = [
        failure
        for failure in failures
        if failure
        in {
            "public_statement_signal_count_below_min",
            "members_with_public_statement_signal_count_below_min",
        }
    ]
    if statement_failures:
        recommendations.append(
            {
                "action": "load_source_backed_public_statement_signals",
                "priority_score": 31,
                "reason": ("Public-statement evidence is below the strict inventory gate."),
                "affected_case_count": len(statement_failures),
                "missing_bill_keys": [],
                "unavailable_signal_counts": {
                    "inventory_quality_gate_failures": len(statement_failures)
                },
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "artifact": str(inventory_path),
            }
        )
    missing_feature_member_reasons = [
        reason
        for reason in _string_list(inventory_result.get("blocking_reasons"))
        if reason
        in {
            "training_labels_missing_feature_members",
            "evaluation_labels_missing_feature_members",
        }
    ]
    if missing_feature_member_reasons:
        run_metadata = inventory_result.get("run_metadata")
        source_state = run_metadata.get("source_state") if isinstance(run_metadata, dict) else {}
        unavailable_counts = {
            key: source_state.get(key)
            for key in (
                "training_labels_missing_feature_member_count",
                "evaluation_labels_missing_feature_member_count",
            )
            if isinstance(source_state, dict) and key in source_state
        }
        recommendations.append(
            {
                "action": "backfill_cutoff_feature_members",
                "priority_score": 34,
                "reason": (
                    "Some label members are absent from the cutoff feature snapshot, "
                    "so benchmark predictions would be unscorable until member/vote "
                    "history is backfilled or windows are regenerated."
                ),
                "affected_case_count": sum(
                    value for value in unavailable_counts.values() if _is_plain_int(value)
                ),
                "missing_bill_keys": [],
                "unavailable_signal_counts": unavailable_counts,
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "artifact": str(inventory_path),
            }
        )
    if refresh_failures or missing_required_source_family_ids:
        reason = (
            "Prediction input inventory is missing official source coverage "
            "required by the strict benchmark gate."
        )
        if missing_required_source_family_ids:
            reason = (
                "Prediction input inventory is missing required source families "
                "needed by the strict benchmark gate."
            )
        recommendations.append(
            {
                "action": "refresh_prediction_input_inventory",
                "priority_score": 24,
                "reason": reason,
                "affected_case_count": len(refresh_failures)
                + len(missing_required_source_family_ids),
                "missing_bill_keys": [],
                "unavailable_signal_counts": {
                    "inventory_quality_gate_failures": len(refresh_failures)
                    + len(missing_required_source_family_ids)
                },
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "sample_source_family_ids": missing_required_source_family_ids,
                "additional_reasons": [
                    "missing_required_source_families:"
                    + ",".join(missing_required_source_family_ids)
                ]
                if missing_required_source_family_ids
                else [],
                "artifact": str(inventory_path),
            }
        )
    return recommendations


def _missing_required_source_family_ids(failures: list[str]) -> list[str]:
    prefix = "missing_required_source_family:"
    return sorted(
        {
            failure.removeprefix(prefix)
            for failure in failures
            if failure.startswith(prefix)
            and _SOURCE_FAMILY_ID_RE.fullmatch(failure.removeprefix(prefix))
        }
    )


def _prediction_benchmark_backtest_backfill_recommendations(
    backtest_result: object,
    backtest_path: Path,
) -> list[dict[str, Any]]:
    if not isinstance(backtest_result, dict):
        return []
    failures = _string_list(backtest_result.get("quality_gate_failures"))
    missing_required_source_family_ids = _missing_required_source_family_ids(failures)
    recommendations: list[dict[str, Any]] = []
    if missing_required_source_family_ids:
        recommendations.append(
            {
                "action": "refresh_prediction_backtest",
                "priority_score": 25,
                "reason": (
                    "Backtest artifact is missing source families required by the "
                    "strict benchmark gate."
                ),
                "affected_case_count": len(missing_required_source_family_ids),
                "missing_bill_keys": [],
                "unavailable_signal_counts": {
                    "backtest_quality_gate_failures": len(missing_required_source_family_ids)
                },
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "sample_source_family_ids": missing_required_source_family_ids,
                "additional_reasons": [
                    "missing_required_source_families:"
                    + ",".join(missing_required_source_family_ids)
                ],
                "artifact": str(backtest_path),
            }
        )
    if "missing_ontology_feature_source_anchors" not in failures:
        return recommendations
    state = backtest_result.get("ontology_feature_signal_state")
    affected_case_count = 1
    if isinstance(state, dict):
        count = state.get("missing_source_anchor_prediction_count")
        if _is_plain_int(count):
            affected_case_count = max(1, int(count))
    recommendations.append(
        {
            "action": "backfill_feature_source_urls",
            "priority_score": 34,
            "reason": (
                "Backtest ontology feature signals are present but lack official "
                "URL-backed source anchors."
            ),
            "affected_case_count": affected_case_count,
            "missing_bill_keys": [],
            "unavailable_signal_counts": {
                "backtest_quality_gate_failures": 1,
            },
            "sample_vote_event_ids": [],
            "sample_cases": [],
            "artifact": str(backtest_path),
        }
    )
    return recommendations


def _prediction_benchmark_eval_manifest_backfill_recommendations(
    eval_manifest_result: object,
    eval_manifest_path: Path,
) -> list[dict[str, Any]]:
    if not isinstance(eval_manifest_result, dict):
        return []
    failures = _string_list(eval_manifest_result.get("quality_gate_failures"))
    issues = _string_list(eval_manifest_result.get("issues"))
    source_anchor_failures = [
        failure for failure in failures if failure == "missing_ontology_feature_source_anchors"
    ]
    learned_signal_failures = [
        failure for failure in failures if failure == "missing_learned_ontology_feature_signals"
    ]
    missing_required_source_family_ids = _missing_required_source_family_ids(failures)
    refresh_failures = [
        failure
        for failure in failures
        if failure
        in {
            "coverage_missing:training_feature_official_source_coverage_rate",
            "coverage_missing:evaluation_feature_official_source_coverage_rate",
        }
    ]
    refresh_issues = [
        issue
        for issue in issues
        if issue in {"manifest source_state mismatch"}
        or issue.startswith("manifest run_metadata mismatch:")
        or issue.endswith(": run_metadata mismatch")
    ]
    recommendations: list[dict[str, Any]] = []
    if source_anchor_failures:
        state = eval_manifest_result.get("ontology_feature_signal_state")
        affected_case_count = len(source_anchor_failures)
        if isinstance(state, dict):
            missing_signal_names = _string_list(state.get("missing_source_anchor_signal_names"))
            affected_case_count = max(1, len(missing_signal_names))
        recommendations.append(
            {
                "action": "backfill_feature_source_urls",
                "priority_score": 33,
                "reason": (
                    "Eval dataset ontology feature signals are present but lack "
                    "official URL-backed source anchors."
                ),
                "affected_case_count": affected_case_count,
                "missing_bill_keys": [],
                "unavailable_signal_counts": {
                    "eval_manifest_quality_gate_failures": len(source_anchor_failures)
                },
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "artifact": str(eval_manifest_path),
            }
        )
    all_refresh_failures = [
        *refresh_failures,
        *learned_signal_failures,
        *missing_required_source_family_ids,
    ]
    if all_refresh_failures or refresh_issues:
        reason = (
            "Prediction eval manifest provenance is stale or missing strict "
            "official feature-source coverage fields."
        )
        if learned_signal_failures:
            reason = (
                "Prediction eval report must be regenerated so every required "
                "ontology feature family is present in the learned signal surface."
            )
        if missing_required_source_family_ids:
            reason = (
                "Prediction eval report must be regenerated so the manifest includes "
                "source families required by the strict benchmark gate."
            )
        unavailable_signal_counts: dict[str, int] = {}
        if all_refresh_failures:
            unavailable_signal_counts["eval_manifest_quality_gate_failures"] = len(
                all_refresh_failures
            )
        if refresh_issues:
            unavailable_signal_counts["eval_manifest_issues"] = len(refresh_issues)
        recommendations.append(
            {
                "action": "refresh_prediction_eval_report",
                "priority_score": 26,
                "reason": reason,
                "affected_case_count": len(all_refresh_failures) + len(refresh_issues),
                "missing_bill_keys": [],
                "unavailable_signal_counts": unavailable_signal_counts,
                "sample_vote_event_ids": [],
                "sample_cases": [],
                "sample_source_family_ids": missing_required_source_family_ids,
                "additional_reasons": [
                    "missing_required_source_families:"
                    + ",".join(missing_required_source_family_ids)
                ]
                if missing_required_source_family_ids
                else [],
                "artifact": str(eval_manifest_path),
            }
        )
    return recommendations


def _prediction_benchmark_source_url_audit_backfill_recommendations(
    source_url_audit_result: object,
    source_url_audit_path: Path | None,
) -> list[dict[str, Any]]:
    if source_url_audit_path is None or not isinstance(source_url_audit_result, dict):
        return []
    failures = _string_list(source_url_audit_result.get("quality_gate_failures"))
    if not any(
        failure
        in {
            "feature_source_url_gaps",
            "feature_source_official_url_gaps",
            "portable_sample_missing_body_id_gaps",
            "portable_sample_missing_session_id_gaps",
        }
        for failure in failures
    ):
        return []
    artifact = _load_json_object_or_none(source_url_audit_path)
    sample_vote_event_ids: list[Any] = []
    sample_cases: list[Any] = []
    sample_source_family_ids: list[str] = []
    if isinstance(artifact, dict):
        for gap in _object_list(artifact.get("gaps")):
            if not isinstance(gap, dict):
                continue
            sample_vote_event_ids = _append_unique_objects(
                sample_vote_event_ids,
                _object_list(gap.get("sample_vote_event_ids")),
            )
            sample_cases = _append_unique_objects(
                sample_cases,
                _object_list(gap.get("sample_cases")),
            )
            sample_source_family_ids = _append_unique_strings(
                sample_source_family_ids,
                _string_list(gap.get("source_family_ids")),
            )
    missing_url_count = _plain_int_or_zero(
        source_url_audit_result.get("missing_url_sourced_prediction_count")
    )
    missing_official_count = _plain_int_or_zero(
        source_url_audit_result.get("missing_official_source_prediction_count")
    )
    result_source_state = _run_metadata_source_state(source_url_audit_result)
    portable_missing_body_count = _plain_int_or_zero(
        result_source_state.get("portable_sample_missing_body_id_count")
        if isinstance(result_source_state, dict)
        else None
    )
    portable_missing_session_count = _plain_int_or_zero(
        result_source_state.get("portable_sample_missing_session_id_count")
        if isinstance(result_source_state, dict)
        else None
    )
    if isinstance(artifact, dict):
        portable_missing_body_count = max(
            portable_missing_body_count,
            _plain_int_or_zero(artifact.get("portable_sample_missing_body_id_count")),
        )
        portable_missing_session_count = max(
            portable_missing_session_count,
            _plain_int_or_zero(artifact.get("portable_sample_missing_session_id_count")),
        )
    affected_case_count = max(
        missing_url_count,
        missing_official_count,
        portable_missing_body_count,
        portable_missing_session_count,
        len(sample_cases),
        1,
    )
    unavailable_signal_counts = {
        "source_url_audit_quality_gate_failures": len(failures),
        "missing_url_sourced_prediction_count": missing_url_count,
        "missing_official_source_prediction_count": missing_official_count,
    }
    if portable_missing_body_count:
        unavailable_signal_counts["portable_sample_missing_body_id_count"] = (
            portable_missing_body_count
        )
    if portable_missing_session_count:
        unavailable_signal_counts["portable_sample_missing_session_id_count"] = (
            portable_missing_session_count
        )
    return [
        {
            "action": "backfill_feature_source_urls",
            "priority_score": 35,
            "reason": (
                "Prediction source URL audit found feature predictions without "
                "required URL-backed official source anchors or portable "
                "legislative context."
            ),
            "affected_case_count": affected_case_count,
            "missing_bill_keys": [],
            "unavailable_signal_counts": unavailable_signal_counts,
            "sample_vote_event_ids": sample_vote_event_ids,
            "sample_cases": sample_cases,
            "sample_source_family_ids": sample_source_family_ids,
            "artifact": str(source_url_audit_path),
        }
    ]


def _run_metadata_source_state(result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    run_metadata = result.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return {}
    source_state = run_metadata.get("source_state")
    return source_state if isinstance(source_state, dict) else {}


def _coalesce_prediction_benchmark_backfill_recommendations(
    recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    ordered_actions: list[str] = []
    for recommendation in recommendations:
        canonical = _canonical_prediction_benchmark_backfill_recommendation(recommendation)
        if canonical is None:
            continue
        action = canonical["action"]
        if action not in merged:
            merged[action] = canonical
            ordered_actions.append(action)
            continue
        _merge_prediction_benchmark_backfill_recommendation(
            merged[action],
            canonical,
        )
    return [merged[action] for action in ordered_actions]


def _canonical_prediction_benchmark_backfill_recommendation(
    recommendation: dict[str, Any],
) -> dict[str, Any] | None:
    action = _strict_nonblank_string(recommendation.get("action"))
    if action is None:
        return None
    canonical: dict[str, Any] = {
        "action": action,
        "priority_score": _plain_int_or_zero(recommendation.get("priority_score")),
        "affected_case_count": _plain_int_or_zero(recommendation.get("affected_case_count")),
        "missing_bill_keys": _strict_string_list(recommendation.get("missing_bill_keys")),
        "unavailable_signal_counts": _strict_int_count_dict(
            recommendation.get("unavailable_signal_counts")
        ),
        "sample_vote_event_ids": _strict_non_negative_int_list(
            recommendation.get("sample_vote_event_ids")
        ),
        "sample_cases": _strict_object_list(recommendation.get("sample_cases")),
    }
    sample_source_family_ids = _normalized_source_family_ids(
        recommendation.get("sample_source_family_ids")
    )
    if sample_source_family_ids:
        canonical["sample_source_family_ids"] = sample_source_family_ids
    reason = _strict_nonblank_string(recommendation.get("reason"))
    if reason is not None:
        canonical["reason"] = reason
    artifact = _strict_nonblank_string(recommendation.get("artifact"))
    if artifact is not None:
        canonical["artifact"] = artifact
    artifacts = _strict_string_list(recommendation.get("artifacts"))
    if artifacts:
        canonical["artifacts"] = artifacts
    additional_reasons = _strict_string_list(recommendation.get("additional_reasons"))
    if additional_reasons:
        canonical["additional_reasons"] = additional_reasons
    return canonical


def _merge_prediction_benchmark_backfill_recommendation(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    target["priority_score"] = max(
        _plain_int_or_zero(target.get("priority_score")),
        _plain_int_or_zero(source.get("priority_score")),
    )
    target["affected_case_count"] = _plain_int_or_zero(
        target.get("affected_case_count")
    ) + _plain_int_or_zero(source.get("affected_case_count"))
    target["missing_bill_keys"] = _append_unique_strings(
        _strict_string_list(target.get("missing_bill_keys")),
        _strict_string_list(source.get("missing_bill_keys")),
    )
    target["sample_vote_event_ids"] = _append_unique_objects(
        _strict_non_negative_int_list(target.get("sample_vote_event_ids")),
        _strict_non_negative_int_list(source.get("sample_vote_event_ids")),
    )
    target["sample_cases"] = _append_unique_objects(
        _strict_object_list(target.get("sample_cases")),
        _strict_object_list(source.get("sample_cases")),
    )
    target["sample_source_family_ids"] = _append_unique_strings(
        _normalized_source_family_ids(target.get("sample_source_family_ids")),
        _normalized_source_family_ids(source.get("sample_source_family_ids")),
    )
    target["sample_source_family_ids"] = _normalized_source_family_ids(
        target.get("sample_source_family_ids")
    )
    target["unavailable_signal_counts"] = _merge_int_count_dicts(
        target.get("unavailable_signal_counts"),
        source.get("unavailable_signal_counts"),
    )
    source_reason = _strict_nonblank_string(source.get("reason"))
    if source_reason is not None and source_reason != target.get("reason"):
        target["additional_reasons"] = _append_unique_strings(
            _strict_string_list(target.get("additional_reasons")),
            [source_reason],
        )
    artifacts = _append_unique_strings(
        _strict_string_values(target.get("artifacts")),
        _strict_string_values(target.get("artifact")),
    )
    artifacts = _append_unique_strings(artifacts, _strict_string_values(source.get("artifact")))
    artifacts = _append_unique_strings(artifacts, _strict_string_values(source.get("artifacts")))
    if artifacts:
        target["artifacts"] = artifacts


def _strict_nonblank_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or value != value.strip():
        return None
    return value


def _strict_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item and item == item.strip()]


def _normalized_source_family_ids(value: Any) -> list[str]:
    return sorted(
        {
            item
            for item in _strict_string_list(value)
            if _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None
        }
    )


def _strict_non_negative_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    items: list[int] = []
    for item in value:
        if type(item) is int and item >= 0 and item not in items:
            items.append(item)
    return items


def _strict_object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _object_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _strict_string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return _strict_string_list(value)
    if value is None:
        return []
    item = _strict_nonblank_string(value)
    return [item] if item is not None else []


def _string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _append_unique_objects(existing: list[Any], additions: list[Any]) -> list[Any]:
    merged = list(existing)
    seen = {_stable_json_key(item) for item in merged}
    for addition in additions:
        key = _stable_json_key(addition)
        if key in seen:
            continue
        merged.append(addition)
        seen.add(key)
    return merged


def _stable_json_key(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _merge_int_count_dicts(target: object, source: object) -> dict[str, Any]:
    merged = _strict_int_count_dict(target)
    if not isinstance(source, dict):
        return merged
    for key, value in source.items():
        name = _strict_nonblank_string(key)
        if name is None or not _is_plain_int(value):
            continue
        merged[name] = merged.get(name, 0) + int(value)
    return merged


def _strict_int_count_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: int(count)
        for key, count in value.items()
        if _strict_nonblank_string(key) is not None and _is_plain_int(count)
    }


def _prediction_benchmark_backfill_plan(
    *,
    inventory_path: Path,
    eval_manifest_path: Path,
    bill_semantics_plan_path: Path,
    recommendations: list[dict[str, Any]],
    semantic_model_name: str,
) -> dict[str, Any]:
    report_path = _prediction_benchmark_eval_report_path(eval_manifest_path)
    missing_metadata_bill_keys = {
        bill_key
        for recommendation in recommendations
        if recommendation.get("action") == "load_missing_bill_metadata"
        for bill_key in _string_list(recommendation.get("missing_bill_keys"))
    }
    steps = [
        _prediction_benchmark_backfill_step(
            recommendation=recommendation,
            inventory_path=inventory_path,
            eval_manifest_path=eval_manifest_path,
            bill_semantics_plan_path=bill_semantics_plan_path,
            report_path=report_path,
            semantic_model_name=semantic_model_name,
            missing_metadata_bill_keys=missing_metadata_bill_keys,
        )
        for recommendation in recommendations
    ]
    steps = _ordered_prediction_benchmark_backfill_steps(steps)
    return {
        "step_count": len(steps),
        "source_eval_manifest": str(eval_manifest_path),
        "source_eval_report": str(report_path) if report_path is not None else None,
        "steps": steps,
    }


def _prediction_benchmark_backfill_plan_missing_runtime_requirements(
    plan: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    missing: list[str] = []
    missing_by_step: list[dict[str, Any]] = []
    readiness_by_step: list[dict[str, Any]] = []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return missing, missing_by_step, readiness_by_step
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        runtime_requirements = _string_list(step.get("runtime_requirements"))
        step_missing = _missing_prediction_backfill_runtime_requirements(runtime_requirements)
        step_satisfied = [
            requirement for requirement in runtime_requirements if requirement not in step_missing
        ]
        action = step.get("action")
        action_name = str(action) if action is not None else f"steps[{index}]"
        readiness_by_step.append(
            {
                "index": index,
                "action": action_name,
                "runnable": not step_missing,
                "runtime_requirements": runtime_requirements,
                "missing_runtime_requirements": step_missing,
                "satisfied_runtime_requirements": step_satisfied,
            }
        )
        if not step_missing:
            continue
        missing.extend(step_missing)
        missing_by_step.append(
            {
                "index": index,
                "action": action_name,
                "missing_runtime_requirements": step_missing,
            }
        )
    return sorted(set(missing)), missing_by_step, readiness_by_step


def _prediction_benchmark_eval_report_path(eval_manifest_path: Path) -> Path | None:
    manifest = _load_json_object_or_none(eval_manifest_path)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    report = artifacts.get("report") if isinstance(artifacts, dict) else None
    report_path = report.get("path") if isinstance(report, dict) else None
    return Path(str(report_path)) if report_path else None


def _prediction_benchmark_backfill_step(
    *,
    recommendation: dict[str, Any],
    inventory_path: Path,
    eval_manifest_path: Path,
    bill_semantics_plan_path: Path,
    report_path: Path | None,
    semantic_model_name: str,
    missing_metadata_bill_keys: set[str],
) -> dict[str, Any]:
    action = _strict_nonblank_string(recommendation.get("action")) or "unknown"
    missing_bill_keys = _strict_string_list(recommendation.get("missing_bill_keys"))
    source_artifacts = _append_unique_strings(
        _strict_string_values(recommendation.get("artifact")),
        _strict_string_values(recommendation.get("artifacts")),
    )
    sample_source_family_ids = _normalized_source_family_ids(
        recommendation.get("sample_source_family_ids")
    )
    step = {
        "action": action,
        "priority_score": _plain_int_or_zero(recommendation.get("priority_score")),
        "affected_case_count": _plain_int_or_zero(recommendation.get("affected_case_count")),
        "reason": _strict_nonblank_string(recommendation.get("reason")),
        "missing_bill_keys": missing_bill_keys,
        "unavailable_signal_counts": _strict_int_count_dict(
            recommendation.get("unavailable_signal_counts")
        ),
        "sample_vote_event_ids": _strict_non_negative_int_list(
            recommendation.get("sample_vote_event_ids")
        ),
        "sample_cases": _strict_object_list(recommendation.get("sample_cases")),
        "sample_source_family_ids": sample_source_family_ids,
        "blocked_by": _prediction_benchmark_backfill_blockers(
            action=action,
            missing_bill_keys=missing_bill_keys,
            missing_metadata_bill_keys=missing_metadata_bill_keys,
        ),
        "source_requirements": _prediction_benchmark_backfill_source_requirements(
            action,
            unsupported_target_bill_keys=_prediction_backfill_unsupported_semantic_bill_keys(
                action,
                missing_bill_keys,
            ),
            sample_source_family_ids=sample_source_family_ids,
        ),
        "runtime_requirements": _prediction_benchmark_backfill_runtime_requirements(
            action=action,
            report_path=report_path,
        ),
        "suggested_commands": _prediction_benchmark_backfill_commands(
            action=action,
            inventory_path=inventory_path,
            eval_manifest_path=eval_manifest_path,
            bill_semantics_plan_path=bill_semantics_plan_path,
            source_artifacts=source_artifacts,
            sample_source_family_ids=sample_source_family_ids,
            missing_bill_keys=missing_bill_keys,
            report_path=report_path,
            semantic_model_name=semantic_model_name,
        ),
    }
    unsupported_target_bill_keys = _prediction_backfill_unsupported_semantic_bill_keys(
        action,
        missing_bill_keys,
    )
    if unsupported_target_bill_keys:
        step["unsupported_target_bill_keys"] = unsupported_target_bill_keys
    if source_artifacts:
        step["source_artifacts"] = source_artifacts
    additional_reasons = _strict_string_list(recommendation.get("additional_reasons"))
    if additional_reasons:
        step["additional_reasons"] = additional_reasons
    return step


_PREDICTION_BACKFILL_DATA_ACTION_ORDER = {
    "load_missing_bill_metadata": 10,
    "materialize_missing_bill_semantics": 20,
    "backfill_feature_source_urls": 30,
    "backfill_cutoff_feature_members": 35,
    "load_source_backed_public_statement_signals": 40,
    "load_fec_donations_and_member_crosswalks": 50,
    "timestamp_bill_signal_availability": 55,
    "timestamp_bill_semantic_availability": 60,
    "timestamp_contribution_signal_availability": 65,
    "timestamp_ontology_edge_availability": 70,
    "timestamp_statement_signal_availability": 75,
}

_PREDICTION_BACKFILL_REFRESH_ACTION_ORDER = {
    "refresh_prediction_input_inventory": 90,
    "refresh_prediction_backtest": 95,
    "refresh_prediction_eval_report": 100,
}


def _ordered_prediction_benchmark_backfill_steps(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    present_data_actions = [
        str(step.get("action"))
        for step in steps
        if str(step.get("action")) in _PREDICTION_BACKFILL_DATA_ACTION_ORDER
    ]
    present_data_actions.sort(key=_prediction_backfill_action_order)
    for step in steps:
        action = str(step.get("action"))
        if action == "refresh_prediction_input_inventory":
            step["blocked_by"] = _append_unique_strings(
                _string_list(step.get("blocked_by")),
                present_data_actions,
            )
        elif action == "refresh_prediction_eval_report":
            refresh_blockers = [
                *present_data_actions,
                *[
                    str(other.get("action"))
                    for other in steps
                    if str(other.get("action")) == "refresh_prediction_input_inventory"
                ],
            ]
            step["blocked_by"] = _append_unique_strings(
                _string_list(step.get("blocked_by")),
                refresh_blockers,
            )
    return sorted(
        steps,
        key=lambda step: (
            _prediction_backfill_action_order(str(step.get("action"))),
            -_plain_int_or_zero(step.get("priority_score")),
            str(step.get("action")),
        ),
    )


def _prediction_backfill_action_order(action: str) -> int:
    if action in _PREDICTION_BACKFILL_DATA_ACTION_ORDER:
        return _PREDICTION_BACKFILL_DATA_ACTION_ORDER[action]
    if action in _PREDICTION_BACKFILL_REFRESH_ACTION_ORDER:
        return _PREDICTION_BACKFILL_REFRESH_ACTION_ORDER[action]
    return 80


def _append_unique_strings(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(merged)
    for addition in additions:
        if addition and addition not in seen:
            merged.append(addition)
            seen.add(addition)
    return merged


def _prediction_benchmark_backfill_blockers(
    *,
    action: str,
    missing_bill_keys: list[str],
    missing_metadata_bill_keys: set[str],
) -> list[str]:
    if (
        action == "materialize_missing_bill_semantics"
        and set(missing_bill_keys) & missing_metadata_bill_keys
    ):
        return ["load_missing_bill_metadata"]
    return []


def _prediction_backfill_unsupported_semantic_bill_keys(
    action: str,
    missing_bill_keys: list[str],
) -> list[str]:
    if action not in {
        "materialize_missing_bill_semantics",
        "timestamp_bill_semantic_availability",
    }:
        return []
    return _unsupported_bill_semantic_target_keys(missing_bill_keys)


_JURISDICTION_SEMANTIC_ADAPTER_SOURCE_REQUIREMENT = (
    "Non-Congress bill keys require a jurisdiction-specific bill metadata and semantic "
    "materialization adapter before the Congress.gov bill-semantics command can satisfy them."
)


def _prediction_benchmark_backfill_source_requirements(
    action: str,
    *,
    unsupported_target_bill_keys: list[str] | None = None,
    sample_source_family_ids: list[str] | None = None,
) -> list[str]:
    requirements = {
        "load_missing_bill_metadata": [
            "Load official Congress.gov bill metadata with bill source URLs before semantic extraction."
        ],
        "materialize_missing_bill_semantics": [
            "Use cutoff-available bill metadata and official source anchors for every semantic payload."
        ],
        "backfill_feature_source_urls": [
            "Reload or repair feature-producing rows so every claim-bearing feature signal carries an official HTTPS source URL and every non-Congress sample has explicit legislative_body_id and legislative_session_id context."
        ],
        "backfill_cutoff_feature_members": [
            "Backfill Congress member/vote history rows for every labeled member before the feature cutoff, or regenerate the prediction windows so labels only include feature-covered members."
        ],
        "load_source_backed_public_statement_signals": [
            "Raw or prepared statement rows must include statement_date and official House/Senate source URLs; prepared rows must carry normalized sectors before recompute."
        ],
        "load_fec_donations_and_member_crosswalks": [
            "Load FEC bulk files and member FEC crosswalk rows with deterministic source artifacts."
        ],
        "timestamp_bill_semantic_availability": [
            "Semantic payloads must include available_at timestamps derived from cutoff-safe source dates."
        ],
        "timestamp_bill_signal_availability": [
            "Bill signal rows must include cutoff-safe bill availability dates and dated sponsor rows from official Congress.gov metadata."
        ],
        "timestamp_contribution_signal_availability": [
            "Contribution signal rows must include contribution_date, date, or as_of_date timestamps derived from cutoff-safe FEC sources."
        ],
        "timestamp_ontology_edge_availability": [
            "Ontology edges must include a recognized availability date before prediction use."
        ],
        "timestamp_statement_signal_availability": [
            "Public-statement signal rows must include statement_date, date, or as_of_date timestamps derived from official statement sources."
        ],
        "refresh_prediction_input_inventory": [
            "Regenerate the inventory artifact from current DB rows so strict gates can evaluate official label, bill, and ontology source coverage."
        ],
        "refresh_prediction_backtest": [
            "Regenerate the backtest artifact from current DB rows so strict gates can evaluate official prediction labels and feature source families."
        ],
        "refresh_prediction_eval_report": [
            "Regenerate the eval report and manifest so strict gates can evaluate official feature-source coverage fields."
        ],
    }
    action_requirements = list(requirements.get(action, []))
    if action == "refresh_prediction_input_inventory" and sample_source_family_ids:
        action_requirements.append(
            "Inventory must include the required source families: "
            + ", ".join(sample_source_family_ids)
            + "."
        )
    if action == "refresh_prediction_backtest" and sample_source_family_ids:
        action_requirements.append(
            "Backtest must include the required source families: "
            + ", ".join(sample_source_family_ids)
            + "."
        )
    if action == "refresh_prediction_eval_report" and sample_source_family_ids:
        action_requirements.append(
            "Eval manifest must include the required source families: "
            + ", ".join(sample_source_family_ids)
            + "."
        )
    if unsupported_target_bill_keys:
        action_requirements.append(_JURISDICTION_SEMANTIC_ADAPTER_SOURCE_REQUIREMENT)
    return action_requirements


def _prediction_benchmark_backfill_runtime_requirements(
    *,
    action: str,
    report_path: Path | None,
) -> list[str]:
    requirements = {
        "load_missing_bill_metadata": [
            "env:OPENPACT_POSTGRES_DSN",
            "env:OPENPACT_CONGRESS_API_KEY",
        ],
        "materialize_missing_bill_semantics": [
            "env:OPENPACT_POSTGRES_DSN",
            "env:OPENAI_API_KEY",
            f"file:{report_path}" if report_path is not None else "file:<eval-report>",
        ],
        "backfill_feature_source_urls": ["env:OPENPACT_POSTGRES_DSN"],
        "backfill_cutoff_feature_members": ["env:OPENPACT_POSTGRES_DSN"],
        "load_source_backed_public_statement_signals": [
            "env:OPENPACT_POSTGRES_DSN",
            "file:data/taxonomy/sectors.yaml",
        ],
        "load_fec_donations_and_member_crosswalks": [
            "env:OPENPACT_POSTGRES_DSN",
            "file:data/crosswalks/member_fec.csv",
            "file:data/fec/cm.txt",
            "file:data/fec/ccl.txt",
            "file:data/fec/itcont.txt",
        ],
        "timestamp_bill_semantic_availability": [
            "env:OPENPACT_POSTGRES_DSN",
            "env:OPENAI_API_KEY",
            f"file:{report_path}" if report_path is not None else "file:<eval-report>",
        ],
        "timestamp_bill_signal_availability": [
            "env:OPENPACT_POSTGRES_DSN",
            "env:OPENPACT_CONGRESS_API_KEY",
        ],
        "timestamp_contribution_signal_availability": [
            "env:OPENPACT_POSTGRES_DSN",
            "file:data/fec/cm.txt",
            "file:data/fec/ccl.txt",
            "file:data/fec/itcont.txt",
            "file:data/crosswalks/member_fec.csv",
        ],
        "timestamp_ontology_edge_availability": ["env:OPENPACT_POSTGRES_DSN"],
        "timestamp_statement_signal_availability": [
            "env:OPENPACT_POSTGRES_DSN",
            "file:data/prepared/public-statement-sector-rows.jsonl",
        ],
        "refresh_prediction_input_inventory": ["env:OPENPACT_POSTGRES_DSN"],
        "refresh_prediction_backtest": ["env:OPENPACT_POSTGRES_DSN"],
        "refresh_prediction_eval_report": ["env:OPENPACT_POSTGRES_DSN"],
    }
    return requirements.get(action, [])


def _prediction_benchmark_backfill_commands(
    *,
    action: str,
    inventory_path: Path,
    eval_manifest_path: Path,
    bill_semantics_plan_path: Path,
    missing_bill_keys: list[str],
    report_path: Path | None,
    semantic_model_name: str,
    source_artifacts: list[str] | None = None,
    sample_source_family_ids: list[str] | None = None,
) -> list[str]:
    feature_cutoff = _prediction_benchmark_eval_feature_cutoff(eval_manifest_path)
    feature_cutoff_arg = f"--feature-cutoff {feature_cutoff} " if feature_cutoff else ""
    if action == "refresh_prediction_input_inventory":
        return _prediction_input_inventory_refresh_commands(inventory_path)
    if action == "refresh_prediction_backtest":
        return _prediction_backtest_refresh_commands(
            Path(source_artifacts[0]) if source_artifacts else None,
            sample_source_family_ids or [],
        )
    if action == "refresh_prediction_eval_report":
        return _prediction_eval_report_refresh_commands(
            eval_manifest_path,
            sample_source_family_ids or [],
        )
    if action == "load_missing_bill_metadata":
        return [
            f"python3 -m src.runtime.main load-congress --congress {congress}"
            for congress in _congresses_from_bill_keys(missing_bill_keys)
        ]
    if action == "materialize_missing_bill_semantics" and report_path is not None:
        strict_unmatched_flag = (
            ""
            if _unsupported_bill_semantic_target_keys(missing_bill_keys)
            else "--fail-on-unmatched-targets "
        )
        return [
            "python3 -m src.runtime.main materialize-bill-semantics "
            f"--output-root out/bill-semantics --missing-from-report {report_path} "
            f"{feature_cutoff_arg}"
            f"--model {semantic_model_name} --dry-run "
            f"--plan-output {bill_semantics_plan_path} "
            f"{strict_unmatched_flag}"
            "--summary-output out/bill-semantics-materialize-summary.json",
            "python3 -m src.runtime.main verify-bill-semantics-plan "
            f"--plan {bill_semantics_plan_path} --require-source-report "
            f"--require-matched-source-anchors {strict_unmatched_flag}"
            "--output out/bill-semantics-plan-verify.json",
            "python3 -m src.runtime.main materialize-bill-semantics "
            f"--output-root out/bill-semantics --missing-from-report {report_path} "
            f"{feature_cutoff_arg}"
            f"--model {semantic_model_name} {strict_unmatched_flag}"
            "--summary-output out/bill-semantics-materialize-summary.json",
            "python3 -m src.runtime.main verify-bill-semantics "
            f"--root out/bill-semantics --require-model-name {semantic_model_name} "
            "--require-source-inputs-sha256 --output out/bill-semantics-verify.json",
        ]
    if action == "timestamp_bill_semantic_availability" and report_path is not None:
        strict_unmatched_flag = (
            ""
            if _unsupported_bill_semantic_target_keys(missing_bill_keys)
            else "--fail-on-unmatched-targets "
        )
        return [
            "python3 -m src.runtime.main materialize-bill-semantics "
            f"--output-root out/bill-semantics --missing-from-report {report_path} "
            f"{feature_cutoff_arg}"
            f"--model {semantic_model_name} {strict_unmatched_flag}"
            "--summary-output out/bill-semantics-materialize-summary.json",
            "python3 -m src.runtime.main verify-bill-semantics "
            f"--root out/bill-semantics --require-model-name {semantic_model_name} "
            "--require-source-inputs-sha256 --output out/bill-semantics-verify.json",
        ]
    if action == "timestamp_bill_signal_availability":
        congresses = _congresses_from_bill_keys(
            missing_bill_keys
        ) or _prediction_benchmark_eval_label_congresses(eval_manifest_path)
        return [
            *[
                f"python3 -m src.runtime.main load-congress --congress {congress}"
                for congress in congresses
            ],
            "python3 -m src.runtime.main recompute",
        ]
    if action == "backfill_feature_source_urls" and report_path is not None:
        return [
            "python3 -m src.runtime.main prediction-source-url-audit "
            f"--eval-report {report_path} --fail-on-gaps "
            "--output out/prediction-source-url-audit.json",
            "python3 -m src.runtime.main verify-prediction-source-url-audit "
            "--artifact out/prediction-source-url-audit.json "
            "--require-run-metadata --require-no-gaps "
            "--require-no-official-source-gaps "
            "--output out/prediction-source-url-audit-verify.json",
        ]
    if action == "load_source_backed_public_statement_signals":
        return [
            "python3 -m src.runtime.main materialize-public-statement-rss "
            "--output data/raw/public-statements.jsonl "
            "--summary-output out/public-statement-rss-materialize-summary.json",
            "python3 -m src.runtime.main materialize-public-statement-rows "
            "--input data/raw/public-statements.jsonl "
            "--output data/prepared/public-statement-sector-rows.jsonl "
            "--summary-output out/public-statement-rows-materialize-summary.json",
            "python3 -m src.runtime.main verify-public-statement-rows "
            "--statement-rows data/prepared/public-statement-sector-rows.jsonl "
            "--min-rows 1 --output out/public-statement-rows-verify.json",
            "python3 -m src.runtime.main recompute "
            "--statement-rows data/prepared/public-statement-sector-rows.jsonl",
        ]
    if action == "timestamp_ontology_edge_availability":
        return ["python3 -m src.runtime.main recompute"]
    if action == "timestamp_contribution_signal_availability":
        return [
            "python3 -m src.runtime.main verify-fec-inputs "
            "--committee-master data/fec/cm.txt "
            "--candidate-committee-linkage data/fec/ccl.txt "
            "--individual-contributions data/fec/itcont.txt "
            "--member-fec-crosswalk data/crosswalks/member_fec.csv "
            "--require-member-fec-crosswalk "
            "--min-committee-rows 1 "
            "--min-linkage-rows 1 "
            "--min-contribution-rows 1 "
            "--min-member-fec-rows 1 "
            "--output out/fec-inputs-verify.json",
            "python3 -m src.runtime.main recompute",
        ]
    if action == "timestamp_statement_signal_availability":
        return [
            "python3 -m src.runtime.main verify-public-statement-rows "
            "--statement-rows data/prepared/public-statement-sector-rows.jsonl "
            "--min-rows 1 --output out/public-statement-rows-verify.json",
            "python3 -m src.runtime.main recompute "
            "--statement-rows data/prepared/public-statement-sector-rows.jsonl",
        ]
    if action == "load_fec_donations_and_member_crosswalks":
        return [
            "python3 -m src.runtime.main materialize-fec-bulk-files "
            "--cycle 2024 --output-dir data/fec "
            "--member-fec-crosswalk data/crosswalks/member_fec.csv "
            "--summary-output out/fec-bulk-materialize-summary.json",
            "python3 -m src.runtime.main materialize-member-fec-crosswalk "
            "--output data/crosswalks/member_fec.csv "
            "--summary-output out/member-fec-crosswalk-materialize-summary.json",
            "python3 -m src.runtime.main verify-fec-inputs "
            "--committee-master data/fec/cm.txt "
            "--candidate-committee-linkage data/fec/ccl.txt "
            "--individual-contributions data/fec/itcont.txt "
            "--member-fec-crosswalk data/crosswalks/member_fec.csv "
            "--require-member-fec-crosswalk "
            "--min-committee-rows 1 "
            "--min-linkage-rows 1 "
            "--min-contribution-rows 1 "
            "--min-member-fec-rows 1 "
            "--output out/fec-inputs-verify.json",
            "python3 -m src.runtime.main load-member-fec-crosswalk-local "
            "--crosswalk data/crosswalks/member_fec.csv "
            "--source-url https://raw.githubusercontent.com/unitedstates/"
            "congress-legislators/main/legislators-current.yaml",
            "python3 -m src.runtime.main load-fec-local "
            "--committee-master data/fec/cm.txt "
            "--candidate-committee-linkage data/fec/ccl.txt "
            "--individual-contributions data/fec/itcont.txt "
            "--committee-source-url https://www.fec.gov/files/bulk-downloads/2024/cm24.zip "
            "--linkage-source-url https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip "
            "--contribution-source-url https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip",
        ]
    return []


def _prediction_benchmark_eval_feature_cutoff(eval_manifest_path: Path) -> str | None:
    manifest = _load_json_object_or_none(eval_manifest_path)
    if manifest is None:
        return None
    windows = manifest.get("windows")
    if isinstance(windows, dict):
        feature_cutoff = windows.get("feature_cutoff")
        if isinstance(feature_cutoff, str) and feature_cutoff.strip():
            return feature_cutoff.strip()
    run_metadata = manifest.get("run_metadata")
    if isinstance(run_metadata, dict):
        feature_cutoff = run_metadata.get("feature_cutoff")
        if isinstance(feature_cutoff, str) and feature_cutoff.strip():
            return feature_cutoff.strip()
    return None


def _prediction_benchmark_eval_label_congresses(eval_manifest_path: Path) -> list[int]:
    manifest = _load_json_object_or_none(eval_manifest_path)
    if manifest is None:
        return []
    windows = manifest.get("windows")
    if not isinstance(windows, dict):
        return []
    congresses: set[int] = set()
    for key in ("label_start", "label_end"):
        value = windows.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            congresses.add(current_congress_for_date(dt.date.fromisoformat(value.strip())))
        except ValueError:
            continue
    return sorted(congresses)


def _prediction_input_inventory_refresh_commands(inventory_path: Path) -> list[str]:
    artifact = _load_json_object_or_none(inventory_path)
    if artifact is None:
        return []
    required_dates = [
        "training_feature_cutoff",
        "train_start",
        "train_end",
        "feature_cutoff",
        "label_start",
        "label_end",
    ]
    if not all(isinstance(artifact.get(key), str) for key in required_dates):
        return []
    run_metadata = artifact.get("run_metadata")
    congress_archive_manifest = (
        run_metadata.get("congress_archive_manifest") if isinstance(run_metadata, dict) else None
    )
    congress_archive_manifest_path = (
        congress_archive_manifest.get("path")
        if isinstance(congress_archive_manifest, dict)
        else None
    )
    congress_archive_manifest_arg = (
        f"--congress-archive-manifest {congress_archive_manifest_path} "
        if isinstance(congress_archive_manifest_path, str) and congress_archive_manifest_path
        else ""
    )
    require_congress_archive_manifest_arg = (
        " --require-congress-archive-manifest" if congress_archive_manifest_arg else ""
    )
    return [
        "python3 -m src.runtime.main prediction-input-inventory "
        f"--training-feature-cutoff {artifact['training_feature_cutoff']} "
        f"--train-start {artifact['train_start']} "
        f"--train-end {artifact['train_end']} "
        f"--feature-cutoff {artifact['feature_cutoff']} "
        f"--label-start {artifact['label_start']} "
        f"--label-end {artifact['label_end']} "
        f"{congress_archive_manifest_arg}"
        f"--output {inventory_path}",
        "python3 -m src.runtime.main verify-prediction-input-inventory "
        f"--artifact {inventory_path} --require-run-metadata "
        f"{require_congress_archive_manifest_arg}"
        "--require-clean-inventory "
        "--require-portable-jurisdiction-ids "
        "--require-portable-body-ids "
        "--require-portable-session-ids "
        "--require-source-family congress_vote "
        "--require-source-family congress_bill "
        "--min-training-label-official-source-url-coverage-rate 0.95 "
        "--min-evaluation-label-official-source-url-coverage-rate 0.95 "
        "--min-bill-official-source-url-coverage-rate 0.95 "
        "--min-bill-sponsor-availability-rate 0.95 "
        "--min-ontology-official-source-anchor-coverage-rate 0.95 "
        "--min-fec-contributions 1 "
        "--min-member-attributed-fec-contributions 1 "
        "--min-members-with-fec-candidate-id 1 "
        "--min-public-statement-signals 1 "
        "--min-members-with-public-statement-signals 1",
    ]


def _prediction_backtest_refresh_commands(
    backtest_path: Path | None,
    required_source_family_ids: list[str],
) -> list[str]:
    if backtest_path is None:
        return []
    artifact = _load_json_object_or_none(backtest_path)
    if artifact is None:
        return []
    required_dates = ["feature_cutoff", "label_start", "label_end"]
    if not all(isinstance(artifact.get(key), str) for key in required_dates):
        return []
    model_arg = "ontology" if artifact.get("model_name") == "ontology_signal_model" else "baseline"
    run_metadata = artifact.get("run_metadata")
    bill_semantics_root = (
        run_metadata.get("bill_semantics_root") if isinstance(run_metadata, dict) else None
    )
    bill_semantics_arg = (
        f" --bill-semantics-root {bill_semantics_root}"
        if model_arg == "ontology" and isinstance(bill_semantics_root, str) and bill_semantics_root
        else ""
    )
    congress_archive_manifest = (
        run_metadata.get("congress_archive_manifest") if isinstance(run_metadata, dict) else None
    )
    congress_archive_manifest_path = (
        congress_archive_manifest.get("path")
        if isinstance(congress_archive_manifest, dict)
        else None
    )
    congress_archive_manifest_arg = (
        f"--congress-archive-manifest {congress_archive_manifest_path} "
        if isinstance(congress_archive_manifest_path, str) and congress_archive_manifest_path
        else ""
    )
    require_congress_archive_manifest_arg = (
        "--require-congress-archive-manifest " if congress_archive_manifest_arg else ""
    )
    required_family_args = "".join(
        f" --require-source-family {source_family_id}"
        for source_family_id in required_source_family_ids
    )
    return [
        "python3 -m src.runtime.main prediction-backtest "
        f"--model {model_arg} "
        f"--feature-cutoff {artifact['feature_cutoff']} "
        f"--label-start {artifact['label_start']} "
        f"--label-end {artifact['label_end']}"
        f"{bill_semantics_arg} "
        f"{congress_archive_manifest_arg}"
        f"--output {backtest_path}",
        "python3 -m src.runtime.main verify-prediction-backtest "
        f"--artifact {backtest_path} "
        "--require-run-metadata "
        "--require-evaluated-predictions "
        "--require-prediction-source-urls "
        "--require-official-prediction-source-urls"
        f"{require_congress_archive_manifest_arg}"
        f"{required_family_args}",
    ]


def _prediction_eval_report_refresh_commands(
    eval_manifest_path: Path,
    required_source_family_ids: list[str],
) -> list[str]:
    manifest = _load_json_object_or_none(eval_manifest_path)
    if manifest is None:
        return []
    windows = manifest.get("windows")
    artifacts = manifest.get("artifacts")
    if not isinstance(windows, dict) or not isinstance(artifacts, dict):
        return []
    required_dates = [
        "training_feature_cutoff",
        "train_start",
        "train_end",
        "feature_cutoff",
        "label_start",
        "label_end",
    ]
    if not all(isinstance(windows.get(key), str) for key in required_dates):
        return []
    report = artifacts.get("report")
    dataset = artifacts.get("dataset")
    report_path = report.get("path") if isinstance(report, dict) else None
    dataset_path = dataset.get("path") if isinstance(dataset, dict) else None
    if not report_path or not dataset_path:
        return []
    inputs = manifest.get("inputs")
    bill_semantics_root = inputs.get("bill_semantics_root") if isinstance(inputs, dict) else None
    bill_semantics_args = (
        f"--bill-semantics-root {bill_semantics_root} " if bill_semantics_root else ""
    )
    congress_archive_manifest = (
        inputs.get("congress_archive_manifest") if isinstance(inputs, dict) else None
    )
    congress_archive_manifest_path = (
        congress_archive_manifest.get("path")
        if isinstance(congress_archive_manifest, dict)
        else None
    )
    congress_archive_manifest_arg = (
        f"--congress-archive-manifest {congress_archive_manifest_path} "
        if isinstance(congress_archive_manifest_path, str) and congress_archive_manifest_path
        else ""
    )
    require_congress_archive_manifest_arg = (
        "--require-congress-archive-manifest " if congress_archive_manifest_arg else ""
    )
    required_family_args = "".join(
        f"--require-source-family {source_family_id} "
        for source_family_id in required_source_family_ids
    )
    return [
        "python3 -m src.runtime.main prediction-eval-report "
        f"--training-feature-cutoff {windows['training_feature_cutoff']} "
        f"--train-start {windows['train_start']} "
        f"--train-end {windows['train_end']} "
        f"--feature-cutoff {windows['feature_cutoff']} "
        f"--label-start {windows['label_start']} "
        f"--label-end {windows['label_end']} "
        f"{bill_semantics_args}"
        f"{congress_archive_manifest_arg}"
        f"--output {report_path} "
        f"--dataset-output {dataset_path} "
        f"--manifest-output {eval_manifest_path} "
        "--fail-on-unknown-bill-semantic-availability "
        "--fail-on-unknown-bill-signal-availability "
        "--fail-on-unknown-ontology-edge-availability "
        "--fail-on-unknown-contribution-signal-availability "
        "--fail-on-unknown-statement-signal-availability "
        "--fail-on-mixed-bill-semantics-models",
        "python3 -m src.runtime.main verify-prediction-eval-manifest "
        f"--manifest {eval_manifest_path} "
        "--require-artifact-run-metadata "
        f"{require_congress_archive_manifest_arg}"
        "--require-ready-quality "
        "--require-failure-analysis "
        "--require-backfill-recommendations "
        "--require-fail-on-unknown-bill-semantic-availability "
        "--require-fail-on-unknown-bill-signal-availability "
        "--require-fail-on-unknown-ontology-edge-availability "
        "--require-fail-on-unknown-contribution-signal-availability "
        "--require-fail-on-unknown-statement-signal-availability "
        "--require-model-name member_vote_rate_baseline "
        "--require-model-name ontology_signal_model "
        "--require-model-name learned_signal_logistic "
        "--require-ontology-feature-signals "
        f"{required_family_args}"
        "--min-bill-semantic-coverage-rate 0.95 "
        "--min-bill-metadata-coverage-rate 0.95 "
        "--min-training-feature-source-url-coverage-rate 0.95 "
        "--min-training-feature-official-source-coverage-rate 0.95 "
        "--min-evaluation-feature-source-url-coverage-rate 0.95 "
        "--min-evaluation-feature-official-source-coverage-rate 0.95",
    ]


def _congresses_from_bill_keys(bill_keys: list[str]) -> list[int]:
    congresses: set[int] = set()
    for bill_key in bill_keys:
        congress_raw = bill_key.split("-", 1)[0]
        if congress_raw.isdigit():
            congresses.add(int(congress_raw))
    return sorted(congresses)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _prediction_benchmark_bundle_consistency_issues(
    *,
    inventory_path: Path,
    backtest_path: Path,
    eval_manifest_path: Path,
    bill_semantics_plan_path: Path,
    source_url_audit_path: Path | None = None,
) -> list[str]:
    issues: list[str] = []
    inventory = _load_json_object_or_none(inventory_path)
    backtest = _load_json_object_or_none(backtest_path)
    eval_manifest = _load_json_object_or_none(eval_manifest_path)
    bill_semantics_plan = _load_json_object_or_none(bill_semantics_plan_path)
    if inventory is None or backtest is None or eval_manifest is None:
        return issues

    windows = eval_manifest.get("windows")
    if isinstance(windows, dict):
        for key in (
            "training_feature_cutoff",
            "train_start",
            "train_end",
            "feature_cutoff",
            "label_start",
            "label_end",
        ):
            if inventory.get(key) != windows.get(key):
                issues.append(f"benchmark window mismatch: inventory.{key}")
        for key in ("feature_cutoff", "label_start", "label_end"):
            if backtest.get(key) != windows.get(key):
                issues.append(f"benchmark window mismatch: backtest.{key}")

    issues.extend(
        _prediction_benchmark_congress_archive_manifest_consistency_issues(
            inventory=inventory,
            backtest=backtest,
            eval_manifest=eval_manifest,
        )
    )

    artifacts = eval_manifest.get("artifacts")
    plan_inputs = (
        bill_semantics_plan.get("inputs") if isinstance(bill_semantics_plan, dict) else None
    )
    if isinstance(artifacts, dict) and isinstance(plan_inputs, dict):
        report_artifact = artifacts.get("report")
        if isinstance(report_artifact, dict):
            if plan_inputs.get("missing_from_report") != report_artifact.get("path"):
                issues.append("benchmark source report mismatch: path")
            plan_report_sha256 = plan_inputs.get("missing_from_report_sha256")
            if not isinstance(plan_report_sha256, str) or not _is_sha256_hex(plan_report_sha256):
                issues.append("benchmark source report invalid: sha256")
            elif plan_report_sha256 != report_artifact.get("sha256"):
                issues.append("benchmark source report mismatch: sha256")
            if source_url_audit_path is not None:
                source_url_audit = _load_json_object_or_none(source_url_audit_path)
                if source_url_audit is not None:
                    if source_url_audit.get("eval_report_path") != report_artifact.get("path"):
                        issues.append("benchmark source-url audit mismatch: eval_report_path")
                    audit_report_sha256 = source_url_audit.get("eval_report_sha256")
                    if not isinstance(audit_report_sha256, str) or not _is_sha256_hex(
                        audit_report_sha256
                    ):
                        issues.append("benchmark source-url audit invalid: eval_report_sha256")
                    elif audit_report_sha256 != report_artifact.get("sha256"):
                        issues.append("benchmark source-url audit mismatch: eval_report_sha256")
    return issues


def _prediction_benchmark_congress_archive_manifest_consistency_issues(
    *,
    inventory: dict[Any, Any],
    backtest: dict[Any, Any],
    eval_manifest: dict[Any, Any],
) -> list[str]:
    references = {
        "inventory": _prediction_benchmark_congress_archive_manifest_reference(
            inventory.get("run_metadata")
        ),
        "backtest": _prediction_benchmark_congress_archive_manifest_reference(
            backtest.get("run_metadata")
        ),
        "eval_manifest": _prediction_benchmark_congress_archive_manifest_reference(
            eval_manifest.get("inputs")
        ),
    }
    present = {name: reference for name, reference in references.items() if reference is not None}
    if len(present) < 2:
        return []
    issues: list[str] = []
    baseline_name, baseline_reference = next(iter(present.items()))
    for name, reference in list(present.items())[1:]:
        if reference.get("sha256") != baseline_reference.get("sha256"):
            issues.append(
                "benchmark congress archive manifest mismatch: "
                f"{name}.sha256 != {baseline_name}.sha256"
            )
        if reference.get("path") != baseline_reference.get("path"):
            issues.append(
                f"benchmark congress archive manifest mismatch: {name}.path != {baseline_name}.path"
            )
    return issues


def _prediction_benchmark_congress_archive_manifest_reference(
    container: Any,
) -> dict[str, str] | None:
    manifest = container.get("congress_archive_manifest") if isinstance(container, dict) else None
    if not isinstance(manifest, dict):
        return None
    path = manifest.get("path")
    sha256 = manifest.get("sha256")
    if not isinstance(path, str) or not isinstance(sha256, str):
        return None
    return {"path": path, "sha256": sha256}


_PREDICTION_BACKFILL_SUPPORTED_COMMAND_ACTIONS = {
    "refresh_prediction_input_inventory",
    "refresh_prediction_backtest",
    "refresh_prediction_eval_report",
    "load_missing_bill_metadata",
    "materialize_missing_bill_semantics",
    "backfill_feature_source_urls",
    "load_source_backed_public_statement_signals",
    "load_fec_donations_and_member_crosswalks",
    "timestamp_bill_semantic_availability",
    "timestamp_bill_signal_availability",
    "timestamp_contribution_signal_availability",
    "timestamp_ontology_edge_availability",
    "timestamp_statement_signal_availability",
}

_PREDICTION_BACKFILL_SUGGESTED_COMMAND_PREFIXES = {
    "refresh_prediction_input_inventory": (
        "python3 -m src.runtime.main prediction-input-inventory ",
        "python3 -m src.runtime.main verify-prediction-input-inventory ",
    ),
    "refresh_prediction_backtest": (
        "python3 -m src.runtime.main prediction-backtest ",
        "python3 -m src.runtime.main verify-prediction-backtest ",
    ),
    "refresh_prediction_eval_report": (
        "python3 -m src.runtime.main prediction-eval-report ",
        "python3 -m src.runtime.main verify-prediction-eval-manifest ",
    ),
    "load_missing_bill_metadata": ("python3 -m src.runtime.main load-congress ",),
    "materialize_missing_bill_semantics": (
        "python3 -m src.runtime.main materialize-bill-semantics ",
        "python3 -m src.runtime.main verify-bill-semantics-plan ",
        "python3 -m src.runtime.main verify-bill-semantics ",
    ),
    "backfill_feature_source_urls": (
        "python3 -m src.runtime.main prediction-source-url-audit ",
        "python3 -m src.runtime.main verify-prediction-source-url-audit ",
    ),
    "load_source_backed_public_statement_signals": (
        "python3 -m src.runtime.main materialize-public-statement-rss ",
        "python3 -m src.runtime.main materialize-public-statement-rows ",
        "python3 -m src.runtime.main verify-public-statement-rows ",
        "python3 -m src.runtime.main recompute ",
    ),
    "load_fec_donations_and_member_crosswalks": (
        "python3 -m src.runtime.main materialize-fec-bulk-files ",
        "python3 -m src.runtime.main materialize-member-fec-crosswalk ",
        "python3 -m src.runtime.main verify-fec-inputs ",
        "python3 -m src.runtime.main load-member-fec-crosswalk-local ",
        "python3 -m src.runtime.main load-fec-local ",
    ),
    "timestamp_bill_semantic_availability": (
        "python3 -m src.runtime.main materialize-bill-semantics ",
        "python3 -m src.runtime.main verify-bill-semantics-plan ",
        "python3 -m src.runtime.main verify-bill-semantics ",
    ),
    "timestamp_bill_signal_availability": (
        "python3 -m src.runtime.main load-congress ",
        "python3 -m src.runtime.main recompute",
    ),
    "timestamp_contribution_signal_availability": (
        "python3 -m src.runtime.main verify-fec-inputs ",
        "python3 -m src.runtime.main recompute",
    ),
    "timestamp_ontology_edge_availability": ("python3 -m src.runtime.main recompute",),
    "timestamp_statement_signal_availability": (
        "python3 -m src.runtime.main verify-public-statement-rows ",
        "python3 -m src.runtime.main recompute ",
    ),
}


def _prediction_backfill_suggested_command_supported(
    *,
    action: str,
    command: str,
) -> bool:
    prefixes = _PREDICTION_BACKFILL_SUGGESTED_COMMAND_PREFIXES.get(action)
    if not prefixes:
        return False
    return any(command.startswith(prefix) for prefix in prefixes)


def _has_strict_bill_semantics_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-bill-semantics ")
        and "--require-source-inputs-sha256" in command
        and "--require-model-name " in command
        for command in commands
    )


def _has_strict_source_url_audit_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-source-url-audit ")
        and "--require-run-metadata" in command
        and "--require-no-gaps" in command
        and "--require-no-official-source-gaps" in command
        for command in commands
    )


def _has_prediction_input_inventory_archive_manifest_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main prediction-input-inventory ")
        and "--congress-archive-manifest " in command
        for command in commands
    )


def _has_prediction_input_inventory_archive_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-input-inventory ")
        and "--require-congress-archive-manifest" in command
        for command in commands
    )


def _has_strict_prediction_input_inventory_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--require-run-metadata",
        "--require-clean-inventory",
        "--require-portable-jurisdiction-ids",
        "--require-portable-body-ids",
        "--require-portable-session-ids",
        "--require-source-family congress_vote",
        "--require-source-family congress_bill",
        "--min-training-label-official-source-url-coverage-rate 0.95",
        "--min-evaluation-label-official-source-url-coverage-rate 0.95",
        "--min-bill-official-source-url-coverage-rate 0.95",
        "--min-bill-sponsor-availability-rate 0.95",
        "--min-ontology-official-source-anchor-coverage-rate 0.95",
        "--min-fec-contributions 1",
        "--min-member-attributed-fec-contributions 1",
        "--min-members-with-fec-candidate-id 1",
        "--min-public-statement-signals 1",
        "--min-members-with-public-statement-signals 1",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-input-inventory ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _has_prediction_backtest_archive_manifest_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main prediction-backtest ")
        and "--congress-archive-manifest " in command
        for command in commands
    )


def _has_prediction_backtest_archive_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-backtest ")
        and "--require-congress-archive-manifest" in command
        for command in commands
    )


def _has_strict_prediction_backtest_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--require-run-metadata",
        "--require-evaluated-predictions",
        "--require-prediction-source-urls",
        "--require-official-prediction-source-urls",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-backtest ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _has_strict_prediction_eval_manifest_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--require-artifact-run-metadata",
        "--require-ready-quality",
        "--require-failure-analysis",
        "--require-backfill-recommendations",
        "--require-fail-on-unknown-bill-semantic-availability",
        "--require-fail-on-unknown-bill-signal-availability",
        "--require-fail-on-unknown-ontology-edge-availability",
        "--require-fail-on-unknown-contribution-signal-availability",
        "--require-fail-on-unknown-statement-signal-availability",
        "--require-model-name member_vote_rate_baseline",
        "--require-model-name ontology_signal_model",
        "--require-model-name learned_signal_logistic",
        "--require-ontology-feature-signals",
        "--min-bill-semantic-coverage-rate 0.95",
        "--min-bill-metadata-coverage-rate 0.95",
        "--min-training-feature-source-url-coverage-rate 0.95",
        "--min-training-feature-official-source-coverage-rate 0.95",
        "--min-evaluation-feature-source-url-coverage-rate 0.95",
        "--min-evaluation-feature-official-source-coverage-rate 0.95",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-eval-manifest ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _has_prediction_eval_report_archive_manifest_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main prediction-eval-report ")
        and "--congress-archive-manifest " in command
        for command in commands
    )


def _has_prediction_eval_manifest_archive_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-eval-manifest ")
        and "--require-congress-archive-manifest" in command
        for command in commands
    )


def _has_strict_fec_inputs_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--committee-master data/fec/cm.txt",
        "--candidate-committee-linkage data/fec/ccl.txt",
        "--individual-contributions data/fec/itcont.txt",
        "--member-fec-crosswalk data/crosswalks/member_fec.csv",
        "--require-member-fec-crosswalk",
        "--min-committee-rows 1",
        "--min-linkage-rows 1",
        "--min-contribution-rows 1",
        "--min-member-fec-rows 1",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-fec-inputs ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _handle_verify_prediction_backfill_plan(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-backfill-plan",
                "artifact": str(artifact_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_backfill_plan_verify_run_metadata(
                    args,
                    artifact_path,
                ),
            },
        )

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load artifact: {exc}"])
    if not isinstance(artifact, dict):
        return failure_result(issues=["artifact must be an object"])

    if "backfill_plan" in artifact:
        plan = artifact.get("backfill_plan")
    elif "plan" in artifact:
        plan = artifact.get("plan")
    else:
        plan = artifact
    if not isinstance(plan, dict):
        return failure_result(issues=["backfill_plan must be an object"])

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if bool(getattr(args, "require_run_metadata", False)):
        _validate_prediction_backfill_plan_run_metadata(
            artifact=artifact,
            plan=plan,
            issues=issues,
        )
    steps_raw = plan.get("steps")
    steps = steps_raw if isinstance(steps_raw, list) else []
    if not isinstance(steps_raw, list):
        issues.append("steps must be a list")
    expected_step_count = plan.get("step_count")
    if not _is_plain_int(expected_step_count):
        issues.append("step_count must be an integer")
    elif not _is_non_negative_plain_int(expected_step_count):
        issues.append("step_count must be a non-negative integer")
    elif expected_step_count != len(steps):
        issues.append("step_count mismatch")

    missing_runtime_requirements: list[str] = []
    missing_runtime_requirements_by_step: list[dict[str, Any]] = []
    runtime_readiness_by_step: list[dict[str, Any]] = []
    missing_metadata_bill_keys = _prediction_backfill_plan_missing_metadata_bill_keys(steps)
    supported_command_step_count = 0
    for index, step_raw in enumerate(steps):
        if not isinstance(step_raw, dict):
            issues.append(f"steps[{index}] must be an object")
            continue
        action = step_raw.get("action")
        action_name = str(action) if action is not None else f"steps[{index}]"
        if not isinstance(action, str) or not action:
            issues.append(f"steps[{index}].action missing")
        for key in ("priority_score", "affected_case_count"):
            if not _is_plain_int(step_raw.get(key)):
                issues.append(f"steps[{index}].{key} must be an integer")
            elif not _is_non_negative_plain_int(step_raw.get(key)):
                issues.append(f"steps[{index}].{key} must be a non-negative integer")
        for key in (
            "missing_bill_keys",
            "blocked_by",
            "source_requirements",
            "runtime_requirements",
            "suggested_commands",
        ):
            if not isinstance(step_raw.get(key), list):
                issues.append(f"steps[{index}].{key} must be a list")
            elif not _prediction_backfill_plan_string_list(step_raw.get(key)):
                issues.append(f"steps[{index}].{key} must contain only non-empty strings")
        for key in ("source_artifacts", "additional_reasons", "unsupported_target_bill_keys"):
            if key in step_raw and not isinstance(step_raw.get(key), list):
                issues.append(f"steps[{index}].{key} must be a list")
            elif key in step_raw and not _prediction_backfill_plan_string_list(step_raw.get(key)):
                issues.append(f"steps[{index}].{key} must contain only non-empty strings")
        expected_unsupported = _prediction_backfill_unsupported_semantic_bill_keys(
            action_name,
            _string_list(step_raw.get("missing_bill_keys")),
        )
        if expected_unsupported:
            actual_unsupported = _string_list(step_raw.get("unsupported_target_bill_keys"))
            if actual_unsupported != expected_unsupported:
                issues.append(f"steps[{index}].unsupported_target_bill_keys mismatch")
        elif "unsupported_target_bill_keys" in step_raw:
            actual_unsupported = _string_list(step_raw.get("unsupported_target_bill_keys"))
            if actual_unsupported:
                issues.append(f"steps[{index}].unsupported_target_bill_keys mismatch")
        _validate_prediction_backfill_sample_cases(
            step_raw.get("sample_cases"),
            sample_vote_event_ids=step_raw.get("sample_vote_event_ids"),
            step_index=index,
            issues=issues,
        )
        if (
            "sample_source_family_ids" in step_raw
            and not _prediction_backfill_plan_sorted_string_list(
                step_raw.get("sample_source_family_ids")
            )
        ):
            issues.append(f"steps[{index}].sample_source_family_ids must be a sorted string list")
        elif "sample_source_family_ids" in step_raw and not _source_family_id_list(
            step_raw.get("sample_source_family_ids")
        ):
            issues.append(
                f"steps[{index}].sample_source_family_ids must contain normalized source family ids"
            )
        if bool(getattr(args, "require_source_requirements", False)) and not _string_list(
            step_raw.get("source_requirements")
        ):
            quality_gate_failures.append(f"missing_source_requirements:{action_name}")
        if (
            bool(getattr(args, "require_source_requirements", False))
            and expected_unsupported
            and _JURISDICTION_SEMANTIC_ADAPTER_SOURCE_REQUIREMENT
            not in _string_list(step_raw.get("source_requirements"))
        ):
            quality_gate_failures.append("missing_source_requirement:jurisdiction_semantic_adapter")
        if bool(getattr(args, "require_runtime_requirements", False)) and not _string_list(
            step_raw.get("runtime_requirements")
        ):
            quality_gate_failures.append(f"missing_runtime_requirements:{action_name}")
        if bool(getattr(args, "check_runtime_requirements", False)):
            runtime_requirements = _string_list(step_raw.get("runtime_requirements"))
            step_missing_runtime_requirements = _missing_prediction_backfill_runtime_requirements(
                runtime_requirements
            )
            step_satisfied_runtime_requirements = [
                requirement
                for requirement in runtime_requirements
                if requirement not in step_missing_runtime_requirements
            ]
            runtime_readiness_by_step.append(
                {
                    "index": index,
                    "action": action_name,
                    "runnable": not step_missing_runtime_requirements,
                    "runtime_requirements": runtime_requirements,
                    "missing_runtime_requirements": step_missing_runtime_requirements,
                    "satisfied_runtime_requirements": (step_satisfied_runtime_requirements),
                }
            )
            if step_missing_runtime_requirements:
                missing_runtime_requirements.extend(step_missing_runtime_requirements)
                missing_runtime_requirements_by_step.append(
                    {
                        "index": index,
                        "action": action_name,
                        "missing_runtime_requirements": (step_missing_runtime_requirements),
                    }
                )
        if action in _PREDICTION_BACKFILL_SUPPORTED_COMMAND_ACTIONS:
            supported_command_step_count += 1
            suggested_commands = _string_list(step_raw.get("suggested_commands"))
            if (
                bool(getattr(args, "require_supported_suggested_commands", False))
                and not suggested_commands
            ):
                quality_gate_failures.append(f"missing_suggested_commands:{action_name}")
            if bool(getattr(args, "require_supported_suggested_commands", False)):
                for command in suggested_commands:
                    if not _prediction_backfill_suggested_command_supported(
                        action=action,
                        command=command,
                    ):
                        quality_gate_failures.append(f"unsupported_suggested_command:{action_name}")
                        break
                if (
                    action == "refresh_prediction_input_inventory"
                    and not _has_strict_prediction_input_inventory_verify_command(
                        suggested_commands
                    )
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:refresh_prediction_input_inventory"
                    )
                if (
                    action == "refresh_prediction_input_inventory"
                    and _has_prediction_input_inventory_archive_manifest_command(suggested_commands)
                    and not _has_prediction_input_inventory_archive_verify_command(
                        suggested_commands
                    )
                ):
                    quality_gate_failures.append(
                        "missing_suggested_archive_manifest_verify_command:"
                        "refresh_prediction_input_inventory"
                    )
                if (
                    action == "refresh_prediction_backtest"
                    and not _has_strict_prediction_backtest_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:refresh_prediction_backtest"
                    )
                if (
                    action == "refresh_prediction_backtest"
                    and _has_prediction_backtest_archive_manifest_command(suggested_commands)
                    and not _has_prediction_backtest_archive_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_archive_manifest_verify_command:"
                        "refresh_prediction_backtest"
                    )
                if (
                    action == "materialize_missing_bill_semantics"
                    and not _has_strict_bill_semantics_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:materialize_missing_bill_semantics"
                    )
                if (
                    action == "timestamp_bill_semantic_availability"
                    and not _has_strict_bill_semantics_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:timestamp_bill_semantic_availability"
                    )
                if (
                    action == "backfill_feature_source_urls"
                    and not _has_strict_source_url_audit_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:backfill_feature_source_urls"
                    )
                if (
                    action == "refresh_prediction_eval_report"
                    and not _has_strict_prediction_eval_manifest_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:refresh_prediction_eval_report"
                    )
                if (
                    action == "refresh_prediction_eval_report"
                    and _has_prediction_eval_report_archive_manifest_command(suggested_commands)
                    and not _has_prediction_eval_manifest_archive_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_archive_manifest_verify_command:"
                        "refresh_prediction_eval_report"
                    )
                if (
                    action == "load_fec_donations_and_member_crosswalks"
                    and not _has_strict_fec_inputs_verify_command(suggested_commands)
                ):
                    quality_gate_failures.append(
                        "missing_suggested_verify_command:load_fec_donations_and_member_crosswalks"
                    )
        if (
            bool(getattr(args, "require_blocker_links", False))
            and action == "materialize_missing_bill_semantics"
            and set(_string_list(step_raw.get("missing_bill_keys"))) & missing_metadata_bill_keys
            and "load_missing_bill_metadata" not in _string_list(step_raw.get("blocked_by"))
        ):
            quality_gate_failures.append("missing_blocker:materialize_missing_bill_semantics")
    missing_runtime_requirements = sorted(set(missing_runtime_requirements))
    if missing_runtime_requirements:
        quality_gate_failures.append("runtime_requirements_missing")

    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-backfill-plan",
            "artifact": str(artifact_path),
            "checked": 1 + len(steps),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "step_count": len(steps),
            "declared_step_count": expected_step_count,
            "supported_command_step_count": supported_command_step_count,
            "missing_runtime_requirements": missing_runtime_requirements,
            "missing_runtime_requirements_by_step": (missing_runtime_requirements_by_step),
            "runtime_readiness_by_step": runtime_readiness_by_step,
            "source_eval_manifest": plan.get("source_eval_manifest"),
            "source_eval_report": plan.get("source_eval_report"),
            "run_metadata": _prediction_backfill_plan_verify_run_metadata(
                args,
                artifact_path,
                source_state=_prediction_backfill_plan_verify_source_state(
                    plan=plan,
                    steps=steps,
                    supported_command_step_count=supported_command_step_count,
                    missing_runtime_requirements=missing_runtime_requirements,
                    missing_runtime_requirements_by_step=(missing_runtime_requirements_by_step),
                    runtime_readiness_by_step=runtime_readiness_by_step,
                ),
            ),
        },
    )


def _validate_prediction_backfill_plan_run_metadata(
    *,
    artifact: dict[str, Any],
    plan: dict[str, Any],
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append("run_metadata missing")
        return
    command = artifact.get("command")
    if command == "prediction-backfill-plan":
        if run_metadata.get("source_command") != "verify-prediction-benchmark":
            issues.append("run_metadata source_command mismatch")
        source_artifact_sha256 = run_metadata.get("source_artifact_sha256")
        if not isinstance(source_artifact_sha256, dict):
            issues.append("run_metadata source_artifact_sha256 missing")
        else:
            for name in (
                "inventory",
                "backtest",
                "eval_manifest",
                "bill_semantics_plan",
            ):
                value = source_artifact_sha256.get(name)
                if not isinstance(value, str) or not _is_sha256_hex(value):
                    issues.append(f"run_metadata source_artifact_sha256 invalid: {name}")
            cache_value = source_artifact_sha256.get("bill_semantics_cache_index")
            if cache_value is not None and (
                not isinstance(cache_value, str) or not _is_sha256_hex(cache_value)
            ):
                issues.append(
                    "run_metadata source_artifact_sha256 invalid: bill_semantics_cache_index"
                )
            expected_eval_manifest_sha = source_artifact_sha256.get("eval_manifest")
            source_eval_manifest = plan.get("source_eval_manifest")
            if (
                isinstance(expected_eval_manifest_sha, str)
                and isinstance(
                    source_eval_manifest,
                    str,
                )
                and _is_sha256_hex(
                    expected_eval_manifest_sha,
                )
            ):
                source_eval_manifest_path = Path(source_eval_manifest)
                if not source_eval_manifest_path.is_file():
                    issues.append("source_eval_manifest file not found")
                else:
                    source_eval_manifest_bytes = source_eval_manifest_path.read_bytes()
                    actual_eval_manifest_sha = hashlib.sha256(
                        source_eval_manifest_bytes
                    ).hexdigest()
                    if actual_eval_manifest_sha != expected_eval_manifest_sha:
                        issues.append("source_eval_manifest sha256 mismatch")
                    _validate_prediction_backfill_source_eval_report(
                        manifest_bytes=source_eval_manifest_bytes,
                        plan=plan,
                        issues=issues,
                    )
        if not isinstance(run_metadata.get("verification_flags"), dict):
            issues.append("run_metadata verification_flags missing")
        _validate_prediction_backfill_plan_source_state(
            run_metadata=run_metadata,
            plan=plan,
            issues=issues,
        )
    elif command == "verify-prediction-benchmark":
        if run_metadata.get("command") != "verify-prediction-benchmark":
            issues.append("run_metadata command mismatch")
        if not isinstance(run_metadata.get("artifact_sha256"), dict):
            issues.append("run_metadata artifact_sha256 missing")
        else:
            _validate_prediction_benchmark_run_metadata_artifact_sha256(
                artifact=artifact,
                run_metadata=run_metadata,
                issues=issues,
            )
    else:
        issues.append("artifact command must be prediction-backfill-plan")


def _validate_prediction_benchmark_run_metadata_artifact_sha256(
    *,
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
    issues: list[str],
) -> None:
    artifact_sha256 = run_metadata.get("artifact_sha256")
    if not isinstance(artifact_sha256, dict):
        return
    artifacts = artifact.get("artifacts")
    if not isinstance(artifacts, dict):
        return
    for name, reference in artifacts.items():
        if not isinstance(name, str) or not isinstance(reference, dict):
            continue
        recorded_sha = artifact_sha256.get(name)
        reference_sha = reference.get("sha256")
        if not isinstance(recorded_sha, str) or not _is_sha256_hex(recorded_sha):
            issues.append(f"run_metadata artifact_sha256 invalid: {name}")
            continue
        if isinstance(reference_sha, str) and recorded_sha != reference_sha:
            issues.append(f"run_metadata artifact_sha256 mismatch: {name}")
            continue
        path_raw = reference.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            continue
        path = Path(path_raw)
        if not path.is_file():
            issues.append(f"artifact file not found: {name}")
            continue
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != recorded_sha:
            issues.append(f"run_metadata artifact_sha256 stale: {name}")


def _validate_prediction_backfill_plan_source_state(
    *,
    run_metadata: dict[str, Any],
    plan: dict[str, Any],
    issues: list[str],
) -> None:
    source_state = run_metadata.get("source_state")
    if source_state is None:
        return
    if not isinstance(source_state, dict):
        issues.append("run_metadata source_state must be an object")
        return
    steps = plan.get("steps")
    if not isinstance(steps, list):
        steps = []
    expected = _prediction_backfill_plan_verify_source_state(
        plan=plan,
        steps=steps,
        supported_command_step_count=_prediction_backfill_supported_command_step_count(steps),
        missing_runtime_requirements=[],
        missing_runtime_requirements_by_step=[],
        runtime_readiness_by_step=[],
    )
    if source_state != expected:
        if _prediction_backfill_plan_benchmark_source_state_matches(
            source_state=source_state,
            plan=plan,
        ):
            return
        issues.append("run_metadata source_state mismatch")


def _prediction_backfill_plan_benchmark_source_state_matches(
    *,
    source_state: dict[str, Any],
    plan: dict[str, Any],
) -> bool:
    expected_step_count = plan.get("step_count")
    return (
        _is_plain_int(expected_step_count)
        and _is_plain_int(source_state.get("backfill_plan_step_count"))
        and _is_plain_int(source_state.get("backfill_recommendation_count"))
        and source_state.get("backfill_plan_step_count") == expected_step_count
        and source_state.get("backfill_recommendation_count") == expected_step_count
        and isinstance(source_state.get("component_source_states"), dict)
        and _prediction_backfill_plan_benchmark_scope_state_valid(source_state)
        and _prediction_backfill_plan_benchmark_sample_state_valid(source_state)
        and _prediction_backfill_plan_benchmark_eval_cutoff_state_valid(source_state)
        and _prediction_backfill_plan_benchmark_inventory_feature_source_state_valid(source_state)
        and _prediction_backfill_plan_benchmark_inventory_source_state_valid(source_state)
        and _prediction_backfill_plan_benchmark_source_url_audit_state_valid(source_state)
    )


def _prediction_backfill_plan_benchmark_scope_state_valid(
    source_state: dict[str, Any],
) -> bool:
    scope_pairs = (
        ("jurisdiction_count", "jurisdiction_ids"),
        ("implemented_jurisdiction_count", "implemented_jurisdiction_ids"),
        ("portable_jurisdiction_count", "portable_jurisdiction_ids"),
        ("legislative_body_count", "legislative_body_ids"),
        ("legislative_session_count", "legislative_session_ids"),
    )
    for count_key, ids_key in scope_pairs:
        has_count = count_key in source_state
        has_ids = ids_key in source_state
        if not has_count and not has_ids:
            continue
        if not has_count or not has_ids:
            return False
        count_value = source_state.get(count_key)
        ids_value = source_state.get(ids_key)
        if not _is_non_negative_plain_int(count_value):
            return False
        if not _prediction_backfill_plan_sorted_string_list(ids_value):
            return False
        if count_value != len(ids_value):
            return False
    jurisdiction_ids = source_state.get("jurisdiction_ids")
    implemented_ids = source_state.get("implemented_jurisdiction_ids")
    portable_ids = source_state.get("portable_jurisdiction_ids")
    if (
        isinstance(jurisdiction_ids, list)
        and isinstance(implemented_ids, list)
        and isinstance(portable_ids, list)
    ):
        if set(implemented_ids) | set(portable_ids) != set(jurisdiction_ids):
            return False
        if set(implemented_ids) & set(portable_ids):
            return False
    return True


def _prediction_backfill_plan_sorted_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item and item.strip() == item for item in value)
        and value == sorted(value)
        and len(set(value)) == len(value)
    )


def _prediction_backfill_plan_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item and item.strip() == item for item in value
    )


def _prediction_backfill_plan_non_negative_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_non_negative_plain_int(item) for item in value)


def _prediction_backfill_plan_benchmark_sample_state_valid(
    source_state: dict[str, Any],
) -> bool:
    sample_case_count = source_state.get("sample_case_count")
    if sample_case_count is not None and not _is_non_negative_plain_int(sample_case_count):
        return False
    sample_vote_event_ids = source_state.get("sample_vote_event_ids")
    if sample_vote_event_ids is not None:
        if not isinstance(sample_vote_event_ids, list):
            return False
        if any(not _is_non_negative_plain_int(item) for item in sample_vote_event_ids):
            return False
        if sample_case_count is not None and sample_case_count < len(sample_vote_event_ids):
            return False
    for key in (
        "sample_bill_keys",
        "sample_member_bioguide_ids",
        "sample_jurisdiction_ids",
        "sample_legislative_body_ids",
        "sample_legislative_session_ids",
        "sample_source_family_ids",
    ):
        value = source_state.get(key)
        if value is not None and not _prediction_backfill_plan_sorted_string_list(value):
            return False
    sample_jurisdiction_ids = source_state.get("sample_jurisdiction_ids")
    sample_legislative_body_ids = source_state.get("sample_legislative_body_ids")
    sample_legislative_session_ids = source_state.get("sample_legislative_session_ids")
    if not (
        sample_jurisdiction_ids is None
        and sample_legislative_body_ids is None
        and sample_legislative_session_ids is None
    ):
        if not _prediction_backfill_plan_sample_scoped_ids_valid(
            sample_jurisdiction_ids=sample_jurisdiction_ids,
            sample_legislative_body_ids=sample_legislative_body_ids,
            sample_legislative_session_ids=sample_legislative_session_ids,
        ):
            return False
    return True


def _prediction_backfill_plan_sample_scoped_ids_valid(
    *,
    sample_jurisdiction_ids: Any,
    sample_legislative_body_ids: Any,
    sample_legislative_session_ids: Any,
) -> bool:
    if sample_jurisdiction_ids is None:
        return sample_legislative_body_ids is None and sample_legislative_session_ids is None
    if not _prediction_backfill_plan_sorted_string_list(sample_jurisdiction_ids):
        return False
    jurisdiction_ids = set(sample_jurisdiction_ids)
    if sample_legislative_body_ids is None:
        return sample_legislative_session_ids is None
    if not _prediction_backfill_plan_sorted_string_list(sample_legislative_body_ids):
        return False
    legislative_body_ids = set(sample_legislative_body_ids)
    if any(
        len(parts := item.split(":")) != 2 or parts[0] not in jurisdiction_ids or not parts[1]
        for item in sample_legislative_body_ids
    ):
        return False
    if sample_legislative_session_ids is None:
        return True
    if not _prediction_backfill_plan_sorted_string_list(sample_legislative_session_ids):
        return False
    return not any(
        len(parts := item.split(":")) != 3
        or parts[0] not in jurisdiction_ids
        or f"{parts[0]}:{parts[1]}" not in legislative_body_ids
        or not parts[2]
        for item in sample_legislative_session_ids
    )


def _prediction_backfill_plan_benchmark_eval_cutoff_state_valid(
    source_state: dict[str, Any],
) -> bool:
    for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if value is not None and not _is_non_negative_plain_int(value):
            return False
    return all(
        _prediction_backfill_plan_cutoff_partition_valid(source_state, *partition)
        for partition in (
            (
                "eval_cutoff_ontology_edge_count",
                "eval_cutoff_cutoff_ontology_edge_count",
                "eval_cutoff_unknown_availability_ontology_edge_count",
                "eval_cutoff_excluded_future_ontology_edge_count",
            ),
            (
                "eval_cutoff_bill_signal_row_count",
                "eval_cutoff_cutoff_bill_signal_row_count",
                "eval_cutoff_unknown_availability_bill_signal_row_count",
                "eval_cutoff_excluded_future_bill_signal_row_count",
            ),
            (
                "eval_cutoff_training_contribution_signal_row_count",
                "eval_cutoff_cutoff_training_contribution_signal_row_count",
                "eval_cutoff_unknown_availability_training_contribution_signal_row_count",
                "eval_cutoff_excluded_future_training_contribution_signal_row_count",
            ),
            (
                "eval_cutoff_evaluation_contribution_signal_row_count",
                "eval_cutoff_cutoff_evaluation_contribution_signal_row_count",
                "eval_cutoff_unknown_availability_evaluation_contribution_signal_row_count",
                "eval_cutoff_excluded_future_evaluation_contribution_signal_row_count",
            ),
            (
                "eval_cutoff_training_statement_signal_row_count",
                "eval_cutoff_cutoff_training_statement_signal_row_count",
                "eval_cutoff_unknown_availability_training_statement_signal_row_count",
                "eval_cutoff_excluded_future_training_statement_signal_row_count",
            ),
            (
                "eval_cutoff_evaluation_statement_signal_row_count",
                "eval_cutoff_cutoff_evaluation_statement_signal_row_count",
                "eval_cutoff_unknown_availability_evaluation_statement_signal_row_count",
                "eval_cutoff_excluded_future_evaluation_statement_signal_row_count",
            ),
        )
    )


def _prediction_backfill_plan_cutoff_partition_valid(
    source_state: dict[str, Any],
    total_key: str,
    cutoff_key: str,
    unknown_key: str,
    future_key: str,
) -> bool:
    return _prediction_cutoff_partition_values_valid(
        source_state,
        total_key=total_key,
        cutoff_key=cutoff_key,
        unknown_key=unknown_key,
        future_key=future_key,
    )


def _prediction_backfill_plan_benchmark_inventory_feature_source_state_valid(
    source_state: dict[str, Any],
) -> bool:
    for key in _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS:
        value = source_state.get(key)
        if value is not None and not _is_non_negative_plain_int(value):
            return False
    for key in _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS:
        value = source_state.get(key)
        if value is not None and not _prediction_eval_source_state_rate_valid(value):
            return False
    return True


def _prediction_backfill_plan_benchmark_inventory_source_state_valid(
    source_state: dict[str, Any],
) -> bool:
    for key in _BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS:
        value = source_state.get(key)
        if value is not None and not _is_non_negative_plain_int(value):
            return False
    for key in _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS:
        value = source_state.get(key)
        if value is not None and not _prediction_eval_source_state_rate_valid(value):
            return False
    return True


def _prediction_backfill_plan_benchmark_source_url_audit_state_valid(
    source_state: dict[str, Any],
) -> bool:
    for key in _BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS:
        value = source_state.get(key)
        if value is not None and not _is_non_negative_plain_int(value):
            return False
    for key in _BENCHMARK_SOURCE_URL_AUDIT_INT_LIST_KEYS:
        value = source_state.get(key)
        if value is not None and not _prediction_backfill_plan_non_negative_int_list(value):
            return False
    for key in _BENCHMARK_SOURCE_URL_AUDIT_STRING_LIST_KEYS:
        value = source_state.get(key)
        if value is not None and not _prediction_backfill_plan_sorted_string_list(value):
            return False
        if (
            key == "source_url_audit_sample_source_family_ids"
            and value is not None
            and not _source_family_id_list(value)
        ):
            return False
    return True


def _prediction_backfill_supported_command_step_count(steps: list[Any]) -> int:
    return sum(
        1
        for step in steps
        if isinstance(step, dict)
        and step.get("action") in _PREDICTION_BACKFILL_SUPPORTED_COMMAND_ACTIONS
    )


def _validate_prediction_backfill_source_eval_report(
    *,
    manifest_bytes: bytes,
    plan: dict[str, Any],
    issues: list[str],
) -> None:
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    report_artifact = artifacts.get("report") if isinstance(artifacts, dict) else None
    if not isinstance(report_artifact, dict):
        return
    expected_path = report_artifact.get("path")
    expected_sha256 = report_artifact.get("sha256")
    source_eval_report = plan.get("source_eval_report")
    if isinstance(expected_path, str) and source_eval_report != expected_path:
        issues.append("source_eval_report path mismatch")
    if not isinstance(expected_sha256, str) or not _is_sha256_hex(expected_sha256):
        issues.append("source_eval_report sha256 invalid")
        return
    if not isinstance(source_eval_report, str):
        issues.append("source_eval_report missing")
        return
    source_eval_report_path = Path(source_eval_report)
    if not source_eval_report_path.is_file():
        issues.append("source_eval_report file not found")
        return
    actual_sha256 = hashlib.sha256(source_eval_report_path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        issues.append("source_eval_report sha256 mismatch")


def _validate_prediction_backfill_sample_cases(
    value: Any,
    *,
    sample_vote_event_ids: Any = None,
    step_index: int,
    issues: list[str],
) -> None:
    expected_vote_event_ids: list[int] | None = None
    if sample_vote_event_ids is not None:
        if not isinstance(sample_vote_event_ids, list):
            issues.append(f"steps[{step_index}].sample_vote_event_ids must be a list")
        else:
            expected_vote_event_ids = []
            for index, vote_event_id in enumerate(sample_vote_event_ids):
                if not _is_plain_int(vote_event_id):
                    issues.append(
                        f"steps[{step_index}].sample_vote_event_ids[{index}] must be an integer"
                    )
                    expected_vote_event_ids = None
                    break
                if not _is_non_negative_plain_int(vote_event_id):
                    issues.append(
                        f"steps[{step_index}].sample_vote_event_ids[{index}] must be a non-negative integer"
                    )
                    expected_vote_event_ids = None
                    break
                if vote_event_id not in expected_vote_event_ids:
                    expected_vote_event_ids.append(vote_event_id)
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(f"steps[{step_index}].sample_cases must be a list")
        return
    sample_case_vote_event_ids: list[int] = []
    seen_sample_cases: set[tuple[Any, ...]] = set()
    for case_index, case_raw in enumerate(value):
        prefix = f"steps[{step_index}].sample_cases[{case_index}]"
        if not isinstance(case_raw, dict):
            issues.append(f"{prefix} must be an object")
            continue
        if not _is_plain_int(case_raw.get("vote_event_id")):
            issues.append(f"{prefix}.vote_event_id must be an integer")
        elif not _is_non_negative_plain_int(case_raw.get("vote_event_id")):
            issues.append(f"{prefix}.vote_event_id must be a non-negative integer")
        else:
            if case_raw["vote_event_id"] not in sample_case_vote_event_ids:
                sample_case_vote_event_ids.append(case_raw["vote_event_id"])
        for key in ("event_key",):
            raw = case_raw.get(key)
            if raw is not None and not isinstance(raw, str):
                issues.append(f"{prefix}.{key} must be a string")
            elif isinstance(raw, str) and not raw.strip():
                issues.append(f"{prefix}.{key} must be a non-empty string")
            elif isinstance(raw, str) and raw != raw.strip():
                issues.append(f"{prefix}.{key} must not have surrounding whitespace")
        for key in (
            "jurisdiction_id",
            "legislative_body_id",
            "legislative_session_id",
        ):
            raw = case_raw.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str):
                issues.append(f"{prefix}.{key} must be a string")
            elif not raw.strip():
                issues.append(f"{prefix}.{key} must be a non-empty string")
        jurisdiction_id = case_raw.get("jurisdiction_id")
        if (
            isinstance(jurisdiction_id, str)
            and jurisdiction_id
            and jurisdiction_id != "us_congress"
        ):
            for key in ("legislative_body_id", "legislative_session_id"):
                raw = case_raw.get(key)
                if raw is None:
                    issues.append(f"{prefix}.{key} must be a non-empty string")
                elif not isinstance(raw, str):
                    issues.append(f"{prefix}.{key} must be a string")
                elif not raw.strip():
                    issues.append(f"{prefix}.{key} must be a non-empty string")
        for key in ("bill_key", "bill_context_key", "member_bioguide_id"):
            raw = case_raw.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str):
                issues.append(f"{prefix}.{key} must be a string")
            elif not raw.strip():
                issues.append(f"{prefix}.{key} must be a non-empty string")
            elif raw != raw.strip():
                issues.append(f"{prefix}.{key} must not have surrounding whitespace")
        case_key = (
            case_raw.get("jurisdiction_id"),
            case_raw.get("legislative_body_id"),
            case_raw.get("legislative_session_id"),
            case_raw.get("event_key"),
            case_raw.get("vote_event_id"),
            case_raw.get("bill_key"),
            case_raw.get("bill_context_key"),
            case_raw.get("member_bioguide_id"),
        )
        if case_key in seen_sample_cases:
            issues.append(f"{prefix} duplicates an earlier sample case")
        else:
            seen_sample_cases.add(case_key)
    if (
        expected_vote_event_ids is not None
        and sample_case_vote_event_ids
        and expected_vote_event_ids != sample_case_vote_event_ids
    ):
        issues.append(f"steps[{step_index}].sample_vote_event_ids mismatch")


def _prediction_backfill_plan_missing_metadata_bill_keys(steps: list[Any]) -> set[str]:
    missing_keys: set[str] = set()
    for step in steps:
        if isinstance(step, dict) and step.get("action") == "load_missing_bill_metadata":
            missing_keys.update(_string_list(step.get("missing_bill_keys")))
    return missing_keys


def _prediction_backfill_plan_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-backfill-plan",
        "verification_flags": {
            "require_source_requirements": bool(
                getattr(args, "require_source_requirements", False)
            ),
            "require_runtime_requirements": bool(
                getattr(args, "require_runtime_requirements", False)
            ),
            "check_runtime_requirements": bool(getattr(args, "check_runtime_requirements", False)),
            "require_supported_suggested_commands": bool(
                getattr(args, "require_supported_suggested_commands", False)
            ),
            "require_blocker_links": bool(getattr(args, "require_blocker_links", False)),
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_backfill_plan_verify_source_state(
    *,
    plan: dict[str, Any],
    steps: list[Any],
    supported_command_step_count: int,
    missing_runtime_requirements: list[str],
    missing_runtime_requirements_by_step: list[dict[str, Any]],
    runtime_readiness_by_step: list[dict[str, Any]],
) -> dict[str, Any]:
    action_names: list[str] = []
    runtime_requirement_count = 0
    sample_vote_event_ids: list[int] = []
    sample_bill_keys: set[str] = set()
    sample_member_bioguide_ids: set[str] = set()
    sample_jurisdiction_ids: set[str] = set()
    sample_legislative_body_ids: set[str] = set()
    sample_legislative_session_ids: set[str] = set()
    sample_source_family_ids: set[str] = set()
    sample_case_count = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if isinstance(action, str):
            action_names.append(action)
        runtime_requirements = step.get("runtime_requirements")
        if isinstance(runtime_requirements, list):
            runtime_requirement_count += len(runtime_requirements)
        for source_family_id in _string_list(step.get("sample_source_family_ids")):
            sample_source_family_ids.add(source_family_id)
        sample_cases = step.get("sample_cases")
        if isinstance(sample_cases, list):
            for sample_case in sample_cases:
                if not isinstance(sample_case, dict):
                    continue
                sample_case_count += 1
                vote_event_id = sample_case.get("vote_event_id")
                if (
                    _is_non_negative_plain_int(vote_event_id)
                    and vote_event_id not in sample_vote_event_ids
                ):
                    sample_vote_event_ids.append(vote_event_id)
                bill_key = sample_case.get("bill_context_key") or sample_case.get("bill_key")
                if isinstance(bill_key, str) and bill_key.strip() == bill_key and bill_key:
                    sample_bill_keys.add(bill_key)
                member_bioguide_id = sample_case.get("member_bioguide_id")
                if (
                    isinstance(member_bioguide_id, str)
                    and member_bioguide_id.strip() == member_bioguide_id
                    and member_bioguide_id
                ):
                    sample_member_bioguide_ids.add(member_bioguide_id)
                jurisdiction_id = sample_case.get("jurisdiction_id")
                if isinstance(jurisdiction_id, str) and jurisdiction_id.strip():
                    jurisdiction_id = jurisdiction_id.strip()
                    sample_jurisdiction_ids.add(jurisdiction_id)
                    legislative_body_id = sample_case.get("legislative_body_id")
                    if isinstance(legislative_body_id, str) and legislative_body_id.strip():
                        legislative_body_id = legislative_body_id.strip()
                        sample_legislative_body_ids.add(f"{jurisdiction_id}:{legislative_body_id}")
                        legislative_session_id = sample_case.get("legislative_session_id")
                        if (
                            isinstance(
                                legislative_session_id,
                                str,
                            )
                            and legislative_session_id.strip()
                        ):
                            sample_legislative_session_ids.add(
                                f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id.strip()}"
                            )
    return {
        "step_count": len(steps),
        "declared_step_count": plan.get("step_count"),
        "supported_command_step_count": supported_command_step_count,
        "action_names": action_names,
        "source_eval_manifest": plan.get("source_eval_manifest"),
        "source_eval_report": plan.get("source_eval_report"),
        "runtime_requirement_count": runtime_requirement_count,
        "missing_runtime_requirements": missing_runtime_requirements,
        "missing_runtime_requirement_step_count": len(missing_runtime_requirements_by_step),
        "missing_runtime_requirements_by_step": missing_runtime_requirements_by_step,
        "runtime_ready_step_count": sum(
            1
            for step in runtime_readiness_by_step
            if isinstance(step, dict) and step.get("runnable") is True
        ),
        "runtime_blocked_step_count": sum(
            1
            for step in runtime_readiness_by_step
            if isinstance(step, dict) and step.get("runnable") is False
        ),
        "runtime_readiness_by_step": runtime_readiness_by_step,
        "sample_case_count": sample_case_count,
        "sample_vote_event_ids": sample_vote_event_ids,
        "sample_bill_keys": sorted(sample_bill_keys),
        "sample_member_bioguide_ids": sorted(sample_member_bioguide_ids),
        "sample_jurisdiction_ids": sorted(sample_jurisdiction_ids),
        "sample_legislative_body_ids": sorted(sample_legislative_body_ids),
        "sample_legislative_session_ids": sorted(sample_legislative_session_ids),
        "sample_source_family_ids": sorted(sample_source_family_ids),
    }


def _missing_prediction_backfill_runtime_requirements(
    requirements: list[str],
) -> list[str]:
    missing: list[str] = []
    for requirement in requirements:
        if requirement.startswith("env:"):
            env_name = requirement.removeprefix("env:")
            if not os.environ.get(env_name):
                missing.append(requirement)
        elif requirement.startswith("file:"):
            file_path = requirement.removeprefix("file:")
            if file_path and not Path(file_path).is_file():
                missing.append(requirement)
    return missing


def _prediction_eval_manifest_ready_quality_failures(
    manifest: dict[str, Any],
) -> list[str]:
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return ["quality_missing"]
    failures: list[str] = []
    if quality.get("ok") is not True:
        failures.append("quality_not_ok")
    if quality.get("readiness_status") != "ready":
        failures.append("readiness_not_ready")
    if quality.get("readiness_blocking_reasons"):
        failures.append("readiness_blocking_reasons")
    if quality.get("readiness_warning_reasons"):
        failures.append("readiness_warning_reasons")
    gate_failures = quality.get("quality_gate_failures")
    if gate_failures:
        failures.append("quality_gate_failures")
    return failures


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _prediction_backtest_source_state(
    payload: PredictionBacktestPayload,
) -> dict[str, Any]:
    return _prediction_backtest_source_state_impl(payload)


def _validate_prediction_eval_artifact_run_metadata(
    *,
    name: str,
    artifact_path: Path,
    manifest: dict[str, Any],
    issues: list[str],
    require_run_metadata: bool = False,
    expected_source_state: dict[str, Any] | None = None,
) -> None:
    try:
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    if not isinstance(artifact_payload, dict):
        return
    run_metadata = artifact_payload.get("run_metadata")
    if run_metadata is None:
        if require_run_metadata:
            issues.append(f"{name}: run_metadata missing")
        return
    if not isinstance(run_metadata, dict):
        issues.append(f"{name}: run_metadata must be an object")
        return
    if (require_run_metadata or "command" in run_metadata) and run_metadata.get(
        "command"
    ) != "prediction-eval-report":
        issues.append(f"{name}: run_metadata mismatch: command")
    run_metadata_thresholds = run_metadata.get("thresholds")
    invalid_threshold_keys: set[str] = set()
    if "thresholds" in run_metadata and not isinstance(run_metadata_thresholds, dict):
        issues.append(f"{name}: run_metadata thresholds must be an object")
    elif isinstance(run_metadata_thresholds, dict):
        run_metadata_thresholds = _validated_prediction_eval_manifest_thresholds(
            thresholds=run_metadata_thresholds,
            issues=issues,
            issue_prefix=f"{name}: run_metadata threshold",
            invalid_keys=invalid_threshold_keys,
        )
    run_metadata_model_names = run_metadata.get("bill_semantics_model_names")
    invalid_run_metadata_keys: set[str] = set()
    if "bill_semantics_model_names" in run_metadata and not isinstance(
        run_metadata_model_names,
        list,
    ):
        issues.append(f"{name}: run_metadata bill_semantics_model_names must be a list")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and not all(
        isinstance(model_name, str) for model_name in run_metadata_model_names
    ):
        issues.append(f"{name}: run_metadata bill_semantics_model_names must be a string list")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_empty(
        run_metadata_model_names
    ):
        issues.append(f"{name}: run_metadata bill_semantics_model_names must be non-empty")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_untrimmed(
        run_metadata_model_names
    ):
        issues.append(f"{name}: run_metadata bill_semantics_model_names must be trimmed")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_duplicates(
        run_metadata_model_names
    ):
        issues.append(f"{name}: run_metadata bill_semantics_model_names must be unique")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    run_metadata_index_sha_issue = _prediction_eval_optional_sha256_issue(
        f"{name}: run_metadata bill_semantics_index_sha256",
        run_metadata.get("bill_semantics_index_sha256"),
    )
    if run_metadata_index_sha_issue is not None:
        issues.append(run_metadata_index_sha_issue)
        invalid_run_metadata_keys.add("bill_semantics_index_sha256")
    run_metadata_root_issue = _prediction_eval_optional_path_string_issue(
        f"{name}: run_metadata bill_semantics_root",
        run_metadata.get("bill_semantics_root"),
    )
    if run_metadata_root_issue is not None:
        issues.append(run_metadata_root_issue)
        invalid_run_metadata_keys.add("bill_semantics_root")
    source_state = manifest.get("source_state")
    source_state_expected = (
        source_state if isinstance(source_state, dict) else expected_source_state
    )
    if source_state_expected is None and name == "report":
        try:
            report_payload = PredictionEvalReportPayload.model_validate(artifact_payload)
        except Exception:  # noqa: BLE001
            report_payload = None
        if report_payload is not None:
            source_state_expected = _prediction_eval_source_state(report_payload)
    run_metadata_source_state = run_metadata.get("source_state")
    invalid_source_state_keys: set[str] = set()
    if isinstance(run_metadata_source_state, dict):
        invalid_source_state_keys = _validate_prediction_eval_source_state_shape(
            source_state=run_metadata_source_state,
            label=f"{name}: run_metadata source_state",
            issues=issues,
        )
    elif "source_state" in run_metadata:
        issues.append(f"{name}: run_metadata source_state must be an object")
    if source_state_expected is not None and isinstance(run_metadata_source_state, dict):
        issues.extend(
            _mapping_mismatch_key_issues(
                label=f"{name}: run_metadata source_state mismatch",
                actual=run_metadata_source_state,
                expected=source_state_expected,
                ignored_keys=invalid_source_state_keys,
            )
        )
    inputs = manifest.get("inputs")
    windows = manifest.get("windows")
    quality = manifest.get("quality")
    if not isinstance(inputs, dict):
        if require_run_metadata:
            issues.append(f"{name}: manifest inputs missing")
        return
    input_model_names = inputs.get("bill_semantics_model_names")
    invalid_input_keys: set[str] = set()
    if "bill_semantics_model_names" in inputs and not isinstance(input_model_names, list):
        issues.append("manifest inputs bill_semantics_model_names must be a list")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and not all(
        isinstance(model_name, str) for model_name in input_model_names
    ):
        issues.append("manifest inputs bill_semantics_model_names must be a string list")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_empty(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be non-empty")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_untrimmed(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be trimmed")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_duplicates(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be unique")
        invalid_input_keys.add("bill_semantics_model_names")
    input_index_sha_issue = _prediction_eval_optional_sha256_issue(
        "manifest inputs bill_semantics_index_sha256",
        inputs.get("bill_semantics_index_sha256"),
    )
    if input_index_sha_issue is not None:
        issues.append(input_index_sha_issue)
        invalid_input_keys.add("bill_semantics_index_sha256")
    input_root_issue = _prediction_eval_optional_path_string_issue(
        "manifest inputs bill_semantics_root",
        inputs.get("bill_semantics_root"),
    )
    if input_root_issue is not None:
        issues.append(input_root_issue)
        invalid_input_keys.add("bill_semantics_root")
    input_congress_archive_manifest = inputs.get("congress_archive_manifest")
    if "congress_archive_manifest" in inputs and not isinstance(
        input_congress_archive_manifest,
        dict,
    ):
        invalid_input_keys.add("congress_archive_manifest")
    expected: dict[str, Any] = {}
    if require_run_metadata or "command" in run_metadata:
        expected["command"] = "prediction-eval-report"
    if isinstance(windows, dict):
        expected.update(
            {
                "training_feature_cutoff": windows.get("training_feature_cutoff"),
                "train_start": windows.get("train_start"),
                "train_end": windows.get("train_end"),
                "feature_cutoff": windows.get("feature_cutoff"),
                "label_start": windows.get("label_start"),
                "label_end": windows.get("label_end"),
            }
        )
    elif require_run_metadata:
        issues.append(f"{name}: manifest windows missing")
    expected.update(
        {
            "bill_semantics_root": inputs.get("bill_semantics_root"),
            "bill_semantics_index_sha256": inputs.get("bill_semantics_index_sha256"),
            "bill_semantics_model_names": inputs.get("bill_semantics_model_names", []),
        }
    )
    if "congress_archive_manifest" in inputs:
        expected["congress_archive_manifest"] = input_congress_archive_manifest
    if isinstance(quality, dict) and isinstance(quality.get("thresholds"), dict):
        expected["thresholds"] = quality["thresholds"]
    elif "thresholds" in run_metadata:
        expected["thresholds"] = run_metadata_thresholds
    elif require_run_metadata:
        issues.append(f"{name}: manifest quality thresholds missing")
    if isinstance(source_state, dict):
        expected["source_state"] = source_state
    elif source_state_expected is not None and "source_state" in run_metadata:
        expected["source_state"] = source_state_expected
    elif require_run_metadata:
        issues.append(f"{name}: manifest source_state missing")
    if not _prediction_eval_run_metadata_matches_expected(
        run_metadata=run_metadata,
        expected=expected,
        ignored_run_metadata_keys=invalid_run_metadata_keys | invalid_input_keys,
        ignored_source_state_keys=invalid_source_state_keys,
        ignored_threshold_keys=invalid_threshold_keys,
        ignore_source_state_value=(
            "source_state" in run_metadata and not isinstance(run_metadata_source_state, dict)
        ),
        ignore_threshold_value=(
            "thresholds" in run_metadata and not isinstance(run_metadata_thresholds, dict)
        ),
    ):
        issues.append(f"{name}: run_metadata mismatch: expected {expected!r}, got {run_metadata!r}")


def _validate_prediction_eval_manifest_run_metadata(
    *,
    manifest: dict[str, Any],
    issues: list[str],
    require_run_metadata: bool = False,
    expected_source_state: dict[str, Any] | None = None,
) -> None:
    run_metadata = manifest.get("run_metadata")
    if run_metadata is None:
        if require_run_metadata:
            issues.append("manifest run_metadata missing")
        return
    if not isinstance(run_metadata, dict):
        issues.append("manifest run_metadata must be an object")
        return
    if (require_run_metadata or "command" in run_metadata) and run_metadata.get(
        "command"
    ) != "prediction-eval-report":
        issues.append("manifest run_metadata mismatch: command")
    run_metadata_thresholds = run_metadata.get("thresholds")
    invalid_threshold_keys: set[str] = set()
    if "thresholds" in run_metadata and not isinstance(run_metadata_thresholds, dict):
        issues.append("manifest run_metadata thresholds must be an object")
    elif isinstance(run_metadata_thresholds, dict):
        run_metadata_thresholds = _validated_prediction_eval_manifest_thresholds(
            thresholds=run_metadata_thresholds,
            issues=issues,
            issue_prefix="manifest run_metadata threshold",
            invalid_keys=invalid_threshold_keys,
        )
    run_metadata_model_names = run_metadata.get("bill_semantics_model_names")
    invalid_run_metadata_keys: set[str] = set()
    if "bill_semantics_model_names" in run_metadata and not isinstance(
        run_metadata_model_names,
        list,
    ):
        issues.append("manifest run_metadata bill_semantics_model_names must be a list")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and not all(
        isinstance(model_name, str) for model_name in run_metadata_model_names
    ):
        issues.append("manifest run_metadata bill_semantics_model_names must be a string list")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_empty(
        run_metadata_model_names
    ):
        issues.append("manifest run_metadata bill_semantics_model_names must be non-empty")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_untrimmed(
        run_metadata_model_names
    ):
        issues.append("manifest run_metadata bill_semantics_model_names must be trimmed")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    elif isinstance(run_metadata_model_names, list) and _string_list_has_duplicates(
        run_metadata_model_names
    ):
        issues.append("manifest run_metadata bill_semantics_model_names must be unique")
        invalid_run_metadata_keys.add("bill_semantics_model_names")
    run_metadata_index_sha_issue = _prediction_eval_optional_sha256_issue(
        "manifest run_metadata bill_semantics_index_sha256",
        run_metadata.get("bill_semantics_index_sha256"),
    )
    if run_metadata_index_sha_issue is not None:
        issues.append(run_metadata_index_sha_issue)
        invalid_run_metadata_keys.add("bill_semantics_index_sha256")
    run_metadata_root_issue = _prediction_eval_optional_path_string_issue(
        "manifest run_metadata bill_semantics_root",
        run_metadata.get("bill_semantics_root"),
    )
    if run_metadata_root_issue is not None:
        issues.append(run_metadata_root_issue)
        invalid_run_metadata_keys.add("bill_semantics_root")
    source_state = manifest.get("source_state")
    source_state_expected = (
        source_state if isinstance(source_state, dict) else expected_source_state
    )
    run_metadata_source_state = run_metadata.get("source_state")
    invalid_source_state_keys: set[str] = set()
    if isinstance(run_metadata_source_state, dict):
        invalid_source_state_keys = _validate_prediction_eval_source_state_shape(
            source_state=run_metadata_source_state,
            label="manifest run_metadata source_state",
            issues=issues,
        )
    elif "source_state" in run_metadata:
        issues.append("manifest run_metadata source_state must be an object")
    if source_state_expected is not None and isinstance(run_metadata_source_state, dict):
        issues.extend(
            _mapping_mismatch_key_issues(
                label="manifest run_metadata source_state mismatch",
                actual=run_metadata_source_state,
                expected=source_state_expected,
                ignored_keys=invalid_source_state_keys,
            )
        )
    inputs = manifest.get("inputs")
    windows = manifest.get("windows")
    quality = manifest.get("quality")
    if not isinstance(inputs, dict):
        if require_run_metadata:
            issues.append("manifest inputs missing")
        return
    input_model_names = inputs.get("bill_semantics_model_names")
    invalid_input_keys: set[str] = set()
    if "bill_semantics_model_names" in inputs and not isinstance(input_model_names, list):
        issues.append("manifest inputs bill_semantics_model_names must be a list")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and not all(
        isinstance(model_name, str) for model_name in input_model_names
    ):
        issues.append("manifest inputs bill_semantics_model_names must be a string list")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_empty(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be non-empty")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_untrimmed(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be trimmed")
        invalid_input_keys.add("bill_semantics_model_names")
    elif isinstance(input_model_names, list) and _string_list_has_duplicates(input_model_names):
        issues.append("manifest inputs bill_semantics_model_names must be unique")
        invalid_input_keys.add("bill_semantics_model_names")
    input_index_sha_issue = _prediction_eval_optional_sha256_issue(
        "manifest inputs bill_semantics_index_sha256",
        inputs.get("bill_semantics_index_sha256"),
    )
    if input_index_sha_issue is not None:
        issues.append(input_index_sha_issue)
        invalid_input_keys.add("bill_semantics_index_sha256")
    input_root_issue = _prediction_eval_optional_path_string_issue(
        "manifest inputs bill_semantics_root",
        inputs.get("bill_semantics_root"),
    )
    if input_root_issue is not None:
        issues.append(input_root_issue)
        invalid_input_keys.add("bill_semantics_root")
    input_congress_archive_manifest = inputs.get("congress_archive_manifest")
    if "congress_archive_manifest" in inputs and not isinstance(
        input_congress_archive_manifest,
        dict,
    ):
        invalid_input_keys.add("congress_archive_manifest")
    expected: dict[str, Any] = {}
    if require_run_metadata or "command" in run_metadata:
        expected["command"] = "prediction-eval-report"
    if isinstance(windows, dict):
        expected.update(
            {
                "training_feature_cutoff": windows.get("training_feature_cutoff"),
                "train_start": windows.get("train_start"),
                "train_end": windows.get("train_end"),
                "feature_cutoff": windows.get("feature_cutoff"),
                "label_start": windows.get("label_start"),
                "label_end": windows.get("label_end"),
            }
        )
    elif require_run_metadata:
        issues.append("manifest windows missing")
    expected.update(
        {
            "bill_semantics_root": inputs.get("bill_semantics_root"),
            "bill_semantics_index_sha256": inputs.get("bill_semantics_index_sha256"),
            "bill_semantics_model_names": inputs.get("bill_semantics_model_names", []),
        }
    )
    if "congress_archive_manifest" in inputs:
        expected["congress_archive_manifest"] = input_congress_archive_manifest
    if isinstance(quality, dict) and isinstance(quality.get("thresholds"), dict):
        expected["thresholds"] = quality["thresholds"]
    elif "thresholds" in run_metadata:
        expected["thresholds"] = run_metadata_thresholds
    elif require_run_metadata:
        issues.append("manifest quality thresholds missing")
    if isinstance(source_state, dict):
        expected["source_state"] = source_state
    elif expected_source_state is not None and "source_state" in run_metadata:
        expected["source_state"] = expected_source_state
    elif require_run_metadata:
        issues.append("manifest source_state missing")
    if not _prediction_eval_run_metadata_matches_expected(
        run_metadata=run_metadata,
        expected=expected,
        ignored_run_metadata_keys=invalid_run_metadata_keys | invalid_input_keys,
        ignored_source_state_keys=invalid_source_state_keys,
        ignored_threshold_keys=invalid_threshold_keys,
        ignore_source_state_value=(
            "source_state" in run_metadata and not isinstance(run_metadata_source_state, dict)
        ),
        ignore_threshold_value=(
            "thresholds" in run_metadata and not isinstance(run_metadata_thresholds, dict)
        ),
    ):
        issues.append(
            f"manifest run_metadata mismatch: expected {expected!r}, got {run_metadata!r}"
        )


def _prediction_eval_cutoff_audit_summary(cutoff_audit: Any) -> dict[str, Any]:
    if hasattr(cutoff_audit, "model_dump"):
        return cast(dict[str, Any], cutoff_audit.model_dump(mode="json"))
    return {key: getattr(cutoff_audit, key) for key in _PREDICTION_EVAL_CUTOFF_AUDIT_KEYS}


def _feature_source_sourced_prediction_count(source_coverage: list[Any]) -> int:
    return sum(int(row.sourced_prediction_count) for row in source_coverage)


def _feature_source_anchor_count(source_coverage: list[Any]) -> int:
    return sum(int(getattr(row, "source_anchor_count", 0)) for row in source_coverage)


def _feature_source_prediction_count(source_coverage: list[Any]) -> int:
    return sum(int(row.prediction_count) for row in source_coverage)


def _feature_source_coverage_rate(source_coverage: list[Any]) -> float | None:
    prediction_count = _feature_source_prediction_count(source_coverage)
    if prediction_count == 0:
        return None
    return _feature_source_sourced_prediction_count(source_coverage) / prediction_count


def _feature_source_url_sourced_prediction_count(source_coverage: list[Any]) -> int:
    return sum(int(getattr(row, "url_sourced_prediction_count", 0)) for row in source_coverage)


def _feature_source_url_anchor_count(source_coverage: list[Any]) -> int:
    return sum(int(getattr(row, "url_source_anchor_count", 0)) for row in source_coverage)


def _feature_source_url_coverage_rate(source_coverage: list[Any]) -> float | None:
    prediction_count = _feature_source_prediction_count(source_coverage)
    if prediction_count == 0:
        return None
    return _feature_source_url_sourced_prediction_count(source_coverage) / prediction_count


def _feature_source_official_source_sourced_prediction_count(
    source_coverage: list[Any],
) -> int:
    return sum(
        int(getattr(row, "official_source_sourced_prediction_count", 0)) for row in source_coverage
    )


def _feature_source_official_source_anchor_count(source_coverage: list[Any]) -> int:
    return sum(int(getattr(row, "official_source_anchor_count", 0)) for row in source_coverage)


def _feature_source_official_source_coverage_rate(
    source_coverage: list[Any],
) -> float | None:
    prediction_count = _feature_source_prediction_count(source_coverage)
    if prediction_count == 0:
        return None
    return (
        _feature_source_official_source_sourced_prediction_count(source_coverage) / prediction_count
    )


def _prediction_eval_quality_gate_failures(
    result: Any,
    args: Any,
    *,
    bill_semantics_model_names: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    min_training = getattr(args, "min_training_examples", None)
    if min_training is not None and result.dataset.training.label_count < int(min_training):
        failures.append("training_examples_below_minimum")

    min_evaluation = getattr(args, "min_evaluation_examples", None)
    if min_evaluation is not None and result.dataset.evaluation.label_count < int(min_evaluation):
        failures.append("evaluation_examples_below_minimum")

    min_bill_semantic_coverage = getattr(args, "min_bill_semantic_coverage_rate", None)
    if min_bill_semantic_coverage is not None and not _rate_meets_minimum(
        result.bill_semantic_coverage.coverage_rate,
        float(min_bill_semantic_coverage),
    ):
        failures.append("bill_semantic_coverage_below_minimum")

    min_bill_metadata_coverage = getattr(args, "min_bill_metadata_coverage_rate", None)
    if min_bill_metadata_coverage is not None and not _rate_meets_minimum(
        result.bill_metadata_coverage.coverage_rate,
        float(min_bill_metadata_coverage),
    ):
        failures.append("bill_metadata_coverage_below_minimum")

    min_training_bill_semantic_coverage = getattr(
        args,
        "min_training_bill_semantic_coverage_rate",
        None,
    )
    if min_training_bill_semantic_coverage is not None and not _rate_meets_minimum(
        result.training_bill_semantic_coverage.coverage_rate,
        float(min_training_bill_semantic_coverage),
    ):
        failures.append("training_bill_semantic_coverage_below_minimum")

    min_evaluation_bill_semantic_coverage = getattr(
        args,
        "min_evaluation_bill_semantic_coverage_rate",
        None,
    )
    if min_evaluation_bill_semantic_coverage is not None and not _rate_meets_minimum(
        result.evaluation_bill_semantic_coverage.coverage_rate,
        float(min_evaluation_bill_semantic_coverage),
    ):
        failures.append("evaluation_bill_semantic_coverage_below_minimum")

    min_training_feature_source_coverage = getattr(
        args,
        "min_training_feature_source_coverage_rate",
        None,
    )
    if min_training_feature_source_coverage is not None and not _rate_meets_minimum(
        _feature_source_coverage_rate(result.training_feature_source_coverage),
        float(min_training_feature_source_coverage),
    ):
        failures.append("training_feature_source_coverage_below_minimum")

    min_evaluation_feature_source_coverage = getattr(
        args,
        "min_evaluation_feature_source_coverage_rate",
        None,
    )
    if min_evaluation_feature_source_coverage is not None and not _rate_meets_minimum(
        _feature_source_coverage_rate(result.evaluation_feature_source_coverage),
        float(min_evaluation_feature_source_coverage),
    ):
        failures.append("evaluation_feature_source_coverage_below_minimum")

    min_training_feature_source_url_coverage = getattr(
        args,
        "min_training_feature_source_url_coverage_rate",
        None,
    )
    if min_training_feature_source_url_coverage is not None and not _rate_meets_minimum(
        _feature_source_url_coverage_rate(result.training_feature_source_coverage),
        float(min_training_feature_source_url_coverage),
    ):
        failures.append("training_feature_source_url_coverage_below_minimum")

    min_training_feature_official_source_coverage = getattr(
        args,
        "min_training_feature_official_source_coverage_rate",
        None,
    )
    if min_training_feature_official_source_coverage is not None and not _rate_meets_minimum(
        _feature_source_official_source_coverage_rate(result.training_feature_source_coverage),
        float(min_training_feature_official_source_coverage),
    ):
        failures.append("training_feature_official_source_coverage_below_minimum")

    min_evaluation_feature_source_url_coverage = getattr(
        args,
        "min_evaluation_feature_source_url_coverage_rate",
        None,
    )
    if min_evaluation_feature_source_url_coverage is not None and not _rate_meets_minimum(
        _feature_source_url_coverage_rate(result.evaluation_feature_source_coverage),
        float(min_evaluation_feature_source_url_coverage),
    ):
        failures.append("evaluation_feature_source_url_coverage_below_minimum")

    min_evaluation_feature_official_source_coverage = getattr(
        args,
        "min_evaluation_feature_official_source_coverage_rate",
        None,
    )
    if min_evaluation_feature_official_source_coverage is not None and not _rate_meets_minimum(
        _feature_source_official_source_coverage_rate(result.evaluation_feature_source_coverage),
        float(min_evaluation_feature_official_source_coverage),
    ):
        failures.append("evaluation_feature_official_source_coverage_below_minimum")

    min_source_url_coverage = getattr(args, "min_evaluation_source_url_coverage_rate", None)
    if min_source_url_coverage is not None and not _rate_meets_minimum(
        result.data_quality.evaluation.source_url_coverage_rate,
        float(min_source_url_coverage),
    ):
        failures.append("evaluation_source_url_coverage_below_minimum")

    if (
        getattr(args, "fail_on_unknown_bill_semantic_availability", False)
        and int(result.cutoff_audit.unknown_availability_bill_semantic_count) > 0
    ):
        failures.append("unknown_bill_semantic_availability")
    if (
        getattr(args, "fail_on_unknown_bill_signal_availability", False)
        and int(result.cutoff_audit.unknown_availability_bill_signal_row_count) > 0
    ):
        failures.append("unknown_bill_signal_availability")
    if (
        getattr(args, "fail_on_unknown_ontology_edge_availability", False)
        and int(result.cutoff_audit.unknown_availability_ontology_edge_count) > 0
    ):
        failures.append("unknown_ontology_edge_availability")
    unknown_contribution_signal_count = int(
        result.cutoff_audit.unknown_availability_training_contribution_signal_row_count
    ) + int(result.cutoff_audit.unknown_availability_evaluation_contribution_signal_row_count)
    if (
        getattr(args, "fail_on_unknown_contribution_signal_availability", False)
        and unknown_contribution_signal_count > 0
    ):
        failures.append("unknown_contribution_signal_availability")
    unknown_statement_signal_count = int(
        result.cutoff_audit.unknown_availability_training_statement_signal_row_count
    ) + int(result.cutoff_audit.unknown_availability_evaluation_statement_signal_row_count)
    if (
        getattr(args, "fail_on_unknown_statement_signal_availability", False)
        and unknown_statement_signal_count > 0
    ):
        failures.append("unknown_statement_signal_availability")
    if (
        getattr(args, "fail_on_mixed_bill_semantics_models", False)
        and len(bill_semantics_model_names or []) > 1
    ):
        failures.append("mixed_bill_semantics_models")
    return failures


def _rate_meets_minimum(value: float | None, minimum: float) -> bool:
    return value is not None and value >= minimum


COMMAND_REGISTRY: dict[str, Callable[[Any], dict[str, Any]]] = {
    "bootstrap-db": _handle_bootstrap_db,
    "runtime-env-preflight": _handle_runtime_env_preflight,
    "verify-runtime-env-preflight": _handle_verify_runtime_env_preflight,
    "status": _handle_status,
    "load-congress": _handle_load_congress,
    "materialize-fec-bulk-files": _handle_materialize_fec_bulk_files,
    "materialize-member-fec-crosswalk": _handle_materialize_member_fec_crosswalk,
    "materialize-public-statement-rss": _handle_materialize_public_statement_rss,
    "materialize-public-statement-rows": _handle_materialize_public_statement_rows,
    "load-fec-local": _handle_load_fec_local,
    "load-member-fec-crosswalk-local": _handle_load_member_fec_crosswalk_local,
    "verify-fec-inputs": _handle_verify_fec_inputs,
    "load-disclosures": _handle_load_disclosures,
    "parse-disclosures": _handle_parse_disclosures,
    "process-disclosures": _handle_process_disclosures,
    "recompute": _handle_recompute,
    "verify-public-statement-rows": _handle_verify_public_statement_rows,
    "publish": _handle_publish,
    "load-congress-local": _handle_load_congress_local,
    "process-disclosures-local": _handle_process_disclosures_local,
    "run-oracle-local": _handle_run_oracle_local,
    "plan-history-backfill": _handle_plan_history_backfill,
    "check-history-backfill-inputs": _handle_check_history_backfill_inputs,
    "write-congress-archive-manifest": _handle_write_congress_archive_manifest,
    "materialize-congress-archive": _handle_materialize_congress_archive,
    "materialize-history-backfill-inputs": _handle_materialize_history_backfill_inputs,
    "materialize-disclosures-bundle": _handle_materialize_disclosures_bundle,
    "materialize-bill-semantics": _handle_materialize_bill_semantics,
    "verify-bill-semantics": _handle_verify_bill_semantics,
    "verify-bill-semantics-plan": _handle_verify_bill_semantics_plan,
    "run-history-launch-local": _handle_run_history_launch_local,
    "run-history-backfill-local": _handle_run_history_backfill_local,
    "aggregate-history": _handle_aggregate_history,
    "prediction-backtest": _handle_prediction_backtest,
    "prediction-input-inventory": _handle_prediction_input_inventory,
    "verify-prediction-input-inventory": _handle_verify_prediction_input_inventory,
    "prediction-eval-report": _handle_prediction_eval_report,
    "prediction-eval-window-plan": _handle_prediction_eval_window_plan,
    "verify-prediction-eval-window-plan": _handle_verify_prediction_eval_window_plan,
    "prediction-eval-window-summary": _handle_prediction_eval_window_summary,
    "verify-prediction-eval-window-summary": _handle_verify_prediction_eval_window_summary,
    "verify-prediction-eval-window-run": _handle_verify_prediction_eval_window_run,
    "prediction-source-url-audit": _handle_prediction_source_url_audit,
    "verify-prediction-source-url-audit": _handle_verify_prediction_source_url_audit,
    "verify-prediction-eval-manifest": _handle_verify_prediction_eval_manifest,
    "verify-prediction-backtest": _handle_verify_prediction_backtest,
    "verify-prediction-benchmark": _handle_verify_prediction_benchmark,
    "verify-prediction-backfill-plan": _handle_verify_prediction_backfill_plan,
    "prediction-offline-readiness-summary": (_handle_prediction_offline_readiness_summary),
    "verify-prediction-offline-readiness-summary": (
        _handle_verify_prediction_offline_readiness_summary
    ),
    "verify-prediction-resume-script": _handle_verify_prediction_resume_script,
    "verify-prediction-operator-handoff": (_handle_verify_prediction_operator_handoff),
    "verify-prediction-operator-runbook": (_handle_verify_prediction_operator_runbook),
    "prediction-operator-status": _handle_prediction_operator_status,
    "verify-prediction-operator-status": _handle_verify_prediction_operator_status,
    "prediction-operator-packet-manifest": (_handle_prediction_operator_packet_manifest),
    "verify-prediction-operator-packet-manifest": (
        _handle_verify_prediction_operator_packet_manifest
    ),
    "prediction-operator-packet-export": _handle_prediction_operator_packet_export,
    "verify-prediction-operator-packet-export": (_handle_verify_prediction_operator_packet_export),
    "verify-prediction-operator-packet-directory": (
        _handle_verify_prediction_operator_packet_directory
    ),
    "prediction-operator-resume-plan": _handle_prediction_operator_resume_plan,
    "verify-prediction-operator-resume-plan": (_handle_verify_prediction_operator_resume_plan),
    "verify-prediction-operator-resume-run": _handle_verify_prediction_operator_resume_run,
    "verify-publish": _handle_verify_publish,
    "verify-publish-roundtrip": _handle_verify_publish_roundtrip,
    "verify-history-aggregate": _handle_verify_history_aggregate,
}


def dispatch_command(args: Any) -> dict[str, Any]:
    handler = COMMAND_REGISTRY.get(args.command)
    if handler is None:
        return {"ok": False, "error": f"unknown command: {args.command!r}"}
    return handler(args)
