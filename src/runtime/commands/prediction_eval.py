"""Prediction evaluation report and manifest commands."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from src.evidence.source_anchor_policy import has_official_claim_source_anchor
from src.pipeline.publish_snapshot_run import _ontology_edge_from_row
from src.prediction.backtest import REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES
from src.prediction.dataset import PredictionEvalDatasetPayload
from src.prediction.eval_report import PredictionEvalReportPayload, build_prediction_eval_report
from src.prediction.llm_semantics import load_bill_semantic_payloads
from src.query.published_rows import (
    fetch_all_ontology_edge_rows,
    fetch_vote_prediction_backtest_bill_signal_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
    fetch_vote_prediction_contribution_signal_rows,
    fetch_vote_prediction_statement_signal_rows,
)
from src.runtime.app import build_runtime
from src.runtime.bill_semantics_cache import (
    bill_semantics_cache_failures as _prediction_backtest_bill_semantics_cache_failures,
    bill_semantics_index_model_names as _bill_semantics_index_model_names,
    bill_semantics_index_sha256 as _bill_semantics_index_sha256,
)
from src.runtime.commands._shared import (
    _attach_optional_verification_output,
    _congress_archive_manifest_metadata,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _load_json_object_or_none,
    _required_string_list,
    _string_list,
    _write_json_artifact,
)
from src.runtime.commands.bill_semantics import _manifest_has_bill_semantics_cache
from src.runtime.commands.core import (
    _PREDICTION_EVAL_CUTOFF_AUDIT_KEYS,
    _PREDICTION_EVAL_INT_THRESHOLD_ARGS,
    _PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS,
    _PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS,
    _PREDICTION_EVAL_MANIFEST_FAILURE_ANALYSIS_KEYS,
    _PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS,
    _PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS,
    _PREDICTION_EVAL_MANIFEST_REQUIRED_BOOL_THRESHOLDS,
    _PREDICTION_EVAL_MANIFEST_THRESHOLD_KEYS,
    _PREDICTION_EVAL_RATE_THRESHOLD_ARGS,
    _append_issue_once,
    _json_scalar_type_mismatch,
    _manifest_failure_analysis_int,
    _mapping_mismatch_key_issues,
    _non_empty_string_list,
    _ontology_signal_requires_source_anchor,
    _rate_meets_minimum,
    _required_source_family_ids,
    _source_family_id_list,
    _string_list_has_duplicates,
    _string_list_has_empty,
    _string_list_has_untrimmed,
    _top_learned_signal_coefficients,
)
from src.runtime.commands.history import _validate_congress_archive_manifest_metadata
from src.runtime.commands.prediction_misc import _prediction_window_date_issues
from src.runtime.context import RuntimeContext, open_connection


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


def _prediction_eval_optional_rate_valid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return 0 <= value <= 1


def _prediction_window_run_metadata(args: Any) -> dict[str, str]:
    return {
        "training_feature_cutoff": args.training_feature_cutoff.isoformat(),
        "train_start": args.train_start.isoformat(),
        "train_end": args.train_end.isoformat(),
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
    if report_payload is not None:
        quality_gate_failures.extend(
            _prediction_eval_manifest_unknown_availability_failures(
                report=report_payload,
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
    required_signal_names: Collection[str],
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


def _prediction_eval_manifest_unknown_availability_failures(
    *,
    report: PredictionEvalReportPayload,
    args: Any,
) -> list[str]:
    cutoff_audit = report.cutoff_audit
    failures: list[str] = []
    checks = (
        (
            "require_fail_on_unknown_bill_semantic_availability",
            int(cutoff_audit.unknown_availability_bill_semantic_count),
            "unknown_bill_semantic_availability",
        ),
        (
            "require_fail_on_unknown_bill_signal_availability",
            int(cutoff_audit.unknown_availability_bill_signal_row_count),
            "unknown_bill_signal_availability",
        ),
        (
            "require_fail_on_unknown_ontology_edge_availability",
            int(cutoff_audit.unknown_availability_ontology_edge_count),
            "unknown_ontology_edge_availability",
        ),
        (
            "require_fail_on_unknown_contribution_signal_availability",
            int(cutoff_audit.unknown_availability_training_contribution_signal_row_count)
            + int(cutoff_audit.unknown_availability_evaluation_contribution_signal_row_count),
            "unknown_contribution_signal_availability",
        ),
        (
            "require_fail_on_unknown_statement_signal_availability",
            int(cutoff_audit.unknown_availability_training_statement_signal_row_count)
            + int(cutoff_audit.unknown_availability_evaluation_statement_signal_row_count),
            "unknown_statement_signal_availability",
        ),
    )
    for arg_name, unknown_count, failure_name in checks:
        if bool(getattr(args, arg_name, False)) and unknown_count > 0:
            failures.append(failure_name)
    return failures


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
    checked = [value for value in values if _is_non_negative_plain_int(value)]
    if len(checked) != len(values):
        return False
    total, cutoff, unknown, future = checked
    return cutoff + unknown + future == total


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
    run_metadata: dict[str, Any] = {
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
