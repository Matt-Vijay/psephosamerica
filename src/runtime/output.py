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
from src.runtime.oracle_contracts import LocalOracleRunResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult


def as_json(obj: Any, *, indent: int | None = None) -> str:
    return json.dumps(obj, default=str, indent=indent)


def summarize_load_result(result: CongressLoadResult | DisclosuresLoadRuntimeResult) -> dict[str, Any]:
    summary: LoadSummary = result.load_summary
    return {
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


def summarize_recompute_result(result: RuntimeRecomputeResult) -> dict[str, Any]:
    recompute = result.recompute_result
    load: LoadSummary | None = recompute.load_summary

    out: dict[str, Any] = {
        "run_id": result.run_id,
        "data_source": result.data_source.get("slug"),
        "rule_fires": len(recompute.rule_fires),
        "evidence_cards": len(recompute.evidence_cards),
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


def summarize_publish_verify_result(result: PublishVerifyResult) -> dict[str, Any]:
    """Compact summary of a publish verification outcome."""
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


def summarize_publish_roundtrip_result(result: PublishRoundtripResult) -> dict[str, Any]:
    """Compact summary of a publish roundtrip verification outcome."""
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
