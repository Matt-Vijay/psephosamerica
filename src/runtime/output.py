from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.db.load_report import LoadSummary, status_dict
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_artifacts import DisclosureArtifactIngestResult
from src.runtime.disclosures_bundle_process import DisclosuresBundleProcessResult
from src.runtime.disclosures_load_from_parse import DisclosuresParseLoadResult
from src.runtime.disclosures_parse import DisclosureParseRuntimeResult
from src.runtime.fec import FecLocalLoadResult
from src.runtime.member_fec_crosswalk import MemberFecCrosswalkLoadResult
from src.runtime.history_backfill import (
    LocalHistoryBackfillResult,
    build_history_backfill_report,
)
from src.runtime.history_verify_types import HistoryVerifyResult
from src.runtime.oracle_contracts import LocalOracleRunResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult

_SUMMARY_ISSUE_LIMIT = 20


def as_json(obj: Any, *, indent: int | None = None) -> str:
    return json.dumps(obj, default=str, indent=indent)


def summarize_load_result(
    result: CongressLoadResult | DisclosuresLoadRuntimeResult | FecLocalLoadResult,
) -> dict[str, Any]:
    summary: LoadSummary = result.load_summary
    out = {
        "run_id": result.run_id,
        "data_source": result.data_source.get("slug"),
        "ok": summary.ok,
        "counts": {
            "inserted": summary.total_inserted,
            "updated": summary.total_updated,
            "skipped": summary.total_skipped,
            "rejected": summary.total_rejected,
        },
        "warnings": summary.warn_error.warning_count,
        "errors": summary.warn_error.error_count,
        "tables": [
            {
                "table": r.table,
                "inserted": r.inserted,
                "updated": r.updated,
                "skipped": r.skipped,
                "rejected": r.rejected,
            }
            for r in summary.table_results
        ],
    }
    if isinstance(result, CongressLoadResult):
        out["source_state"] = _congress_load_source_state(summary)
    return out


def _congress_load_source_state(summary: LoadSummary) -> dict[str, Any]:
    table_counts: dict[str, int] = {}
    for row in summary.table_results:
        table_counts[row.table] = table_counts.get(row.table, 0) + row.inserted + row.updated
    member_row_count = table_counts.get("member", 0)
    member_term_row_count = table_counts.get("member_term", 0)
    committee_row_count = table_counts.get("committee", 0)
    bill_row_count = table_counts.get("bill", 0)
    bill_sponsor_row_count = table_counts.get("bill_sponsor", 0)
    vote_event_row_count = table_counts.get("vote_event", 0)
    vote_cast_row_count = table_counts.get("vote_cast", 0)
    source_family_ids = []
    if committee_row_count:
        source_family_ids.append("committee_membership")
    if bill_row_count or bill_sponsor_row_count:
        source_family_ids.append("congress_bill")
    if vote_event_row_count or vote_cast_row_count:
        source_family_ids.append("congress_vote")
    return {
        "source_family_ids": source_family_ids,
        "source_family_count": len(source_family_ids),
        "member_row_count": member_row_count,
        "member_term_row_count": member_term_row_count,
        "committee_row_count": committee_row_count,
        "bill_row_count": bill_row_count,
        "bill_sponsor_row_count": bill_sponsor_row_count,
        "vote_event_row_count": vote_event_row_count,
        "vote_cast_row_count": vote_cast_row_count,
        "prediction_member_inputs_available": member_row_count > 0,
        "prediction_bill_inputs_available": bill_row_count > 0,
        "prediction_vote_inputs_available": vote_event_row_count > 0 and vote_cast_row_count > 0,
    }


def summarize_fec_load_result(result: FecLocalLoadResult) -> dict[str, Any]:
    out = summarize_load_result(result)
    out["parsed_counts"] = dict(result.parsed_counts)
    out["artifact_count"] = len(result.artifact_rows)
    return out


def summarize_member_fec_crosswalk_load_result(
    result: MemberFecCrosswalkLoadResult,
) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "data_source": result.data_source.get("slug"),
        "ok": True,
        "parsed_count": result.parsed_count,
        "parsed_member_term_count": result.parsed_member_term_count,
        "updated_count": result.updated_count,
        "member_term_upserted_count": result.member_term_upserted_count,
        "skipped_count": len(result.skipped_bioguide_ids),
        "skipped_bioguide_ids": list(result.skipped_bioguide_ids),
        "skipped_member_term_count": len(result.skipped_member_term_bioguide_ids),
        "skipped_member_term_bioguide_ids": list(result.skipped_member_term_bioguide_ids),
        "source_artifact_id": result.source_artifact.get("id"),
    }


def summarize_recompute_result(result: RuntimeRecomputeResult) -> dict[str, Any]:
    recompute = result.recompute_result
    load: LoadSummary | None = recompute.load_summary
    ontology_edges = recompute.ontology_edges

    out: dict[str, Any] = {
        "run_id": result.run_id,
        "data_source": result.data_source.get("slug"),
        "rule_fires": len(recompute.rule_fires),
        "evidence_cards": len(recompute.evidence_cards),
    }
    if ontology_edges:
        edge_counts: dict[str, int] = {}
        for edge in ontology_edges:
            edge_counts[edge.edge_type] = edge_counts.get(edge.edge_type, 0) + 1
        out["ontology_edges"] = {
            "count": len(ontology_edges),
            "by_type": dict(sorted(edge_counts.items())),
        }

    unresolved_committee_matches = recompute.unresolved_committee_matches
    if load is not None or unresolved_committee_matches:
        load_out = status_dict(load) if load is not None else {}
        if unresolved_committee_matches:
            load_out["unresolved_committee_matches"] = {
                "count": len(unresolved_committee_matches),
                "items": [asdict(match) for match in unresolved_committee_matches],
            }
        out["load"] = load_out

    return out


def summarize_publish_result(result: PublishRuntimeResult) -> dict[str, Any]:
    pub = result.publish_result
    return {
        "run_id": result.run_id,
        "snapshot_id": result.snapshot_id,
        "data_source": result.data_source.get("slug"),
        "planned": pub.planned_count,
        "written": pub.written_count,
        "succeeded": pub.succeeded,
        "verification_failures": pub.verification_failures,
    }


def summarize_disclosure_artifact_ingest_result(
    result: DisclosureArtifactIngestResult,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": result.run_id,
        "data_source": result.data_source.get("slug"),
        "discovered": result.discovered_count,
        "stored": result.stored_count,
    }
    if result.local_root is not None:
        out["local_root"] = str(result.local_root)
    return out


def summarize_parse_disclosures_result(result: DisclosureParseRuntimeResult) -> dict[str, Any]:
    return {
        "processed": result.processed_count,
        "succeeded": result.succeeded_count,
        "failed": result.failed_count,
    }


def summarize_process_disclosures_result(result: DisclosuresParseLoadResult) -> dict[str, Any]:
    return {
        "parse": summarize_parse_disclosures_result(result.parse_result),
        "transformed": result.transform_count,
        "load": summarize_load_result(result.load_result),
    }


def summarize_status(status: dict[str, Any]) -> dict[str, Any]:
    ingestion_runs: list[dict[str, Any]] = status.get("ingestion_runs", [])
    latest_run: dict[str, Any] | None = ingestion_runs[0] if ingestion_runs else None
    source_artifacts: list[dict[str, Any]] = status.get("source_artifacts", [])
    latest_artifact: dict[str, Any] | None = source_artifacts[0] if source_artifacts else None

    return {
        "summary": status.get("summary", {}),
        "latest_ingestion_run": {
            "id": latest_run["id"] if latest_run else None,
            "run_type": latest_run.get("run_type") if latest_run else None,
            "status": latest_run.get("status") if latest_run else None,
            "data_source": latest_run.get("data_source_slug") if latest_run else None,
        },
        "latest_artifact": {
            "id": latest_artifact["id"] if latest_artifact else None,
            "artifact_kind": latest_artifact.get("artifact_kind") if latest_artifact else None,
            "data_source": latest_artifact.get("data_source_slug") if latest_artifact else None,
        },
    }


def summarize_disclosures_bundle_process_result(
    result: DisclosuresBundleProcessResult,
) -> dict[str, Any]:
    """Compact summary of a bundle-process disclosure pipeline run."""
    return {
        "parse": summarize_parse_disclosures_result(result.parse_result),
        "transformed": result.transform_count,
        "load": summarize_load_result(result.load_result),
    }


def summarize_local_oracle_run_result(result: LocalOracleRunResult) -> dict[str, Any]:
    """Compact summary of a completed local oracle run."""
    return {
        "snapshot_id": result.snapshot_id,
        "congress": asdict(result.congress),
        "disclosures": dict(result.disclosures),
        "recompute": dict(result.recompute),
        "publish": dict(result.publish),
        "verify": summarize_publish_verify_result(result.verify),
        "roundtrip": summarize_publish_roundtrip_result(result.roundtrip),
    }


def summarize_local_history_backfill_result(result: LocalHistoryBackfillResult) -> dict[str, Any]:
    result_out = build_history_backfill_report(result).model_dump(mode="json")
    for attempt_out in result_out["attempts"]:
        snapshot_id = attempt_out["snapshot_id"]
        oracle_result = result.snapshot_results.get(snapshot_id)
        if oracle_result is not None:
            attempt_out["oracle"] = summarize_local_oracle_run_result(oracle_result)
    if result.aggregate is not None and result_out["aggregate"] is not None:
        result_out["aggregate"]["verify"] = (
            summarize_history_verify_result(result.aggregate.verify)
            if result.aggregate.verify is not None
            else None
        )
    return result_out


def summarize_publish_verify_result(result: PublishVerifyResult) -> dict[str, Any]:
    """Compact summary of a publish verification outcome."""
    issues = _summarize_stage_issues(result.stages)
    return {
        "ok": result.ok,
        "total_checked": result.total_checked,
        "total_errors": result.total_errors,
        "total_warnings": result.total_warnings,
        "issues": issues,
        "issues_truncated": len(result.all_issues()) > len(issues),
        "stages": [
            {
                "stage": s.stage,
                "checked": s.checked,
                "ok": s.ok,
                "errors": s.error_count,
                "warnings": s.warning_count,
            }
            for s in result.stages
        ],
    }


def summarize_publish_roundtrip_result(result: PublishRoundtripResult) -> dict[str, Any]:
    """Compact summary of a publish roundtrip verification outcome."""
    issues = _summarize_stage_issues(result.stages)
    return {
        "ok": result.ok,
        "total_checked": result.total_checked,
        "total_errors": result.total_errors,
        "total_warnings": result.total_warnings,
        "issues": issues,
        "issues_truncated": len(result.all_issues()) > len(issues),
        "stages": [
            {
                "stage": s.stage,
                "checked": s.checked,
                "ok": s.ok,
                "errors": s.error_count,
                "warnings": s.warning_count,
            }
            for s in result.stages
        ],
    }


def _summarize_stage_issues(stages: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for stage in stages:
        for issue in stage.issues:
            if len(issues) >= _SUMMARY_ISSUE_LIMIT:
                return issues
            issues.append(
                {
                    "stage": issue.stage,
                    "severity": issue.severity,
                    "path": issue.path,
                    "message": issue.message,
                }
            )
    return issues


def summarize_history_verify_result(result: HistoryVerifyResult) -> dict[str, Any]:
    """Compact summary of a history aggregate verification outcome."""
    return {
        "ok": result.ok,
        "total_checked": result.total_checked,
        "total_errors": result.total_errors,
        "total_warnings": result.total_warnings,
        "stages": [
            {
                "stage": s.stage,
                "checked": s.checked,
                "ok": s.ok,
                "errors": s.error_count,
                "warnings": s.warning_count,
            }
            for s in result.stages
        ],
    }
