"""Prediction benchmark verification and backfill-plan commands."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeGuard

from src.runtime.commands._shared import (
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_INT_LIST_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_STRING_LIST_KEYS,
    _artifact_reference,
    _attach_optional_verification_output,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _load_json_object_or_none,
    _optional_file_sha256,
    _plain_int_or_zero,
    _required_string_list,
    _sorted_string_list_or_empty,
    _string_list,
    _write_json_artifact,
)
from src.runtime.commands.bill_semantics import (
    _handle_verify_bill_semantics,
    _handle_verify_bill_semantics_plan,
    _has_strict_bill_semantics_verify_command,
    _missing_bill_semantics_cache_result,
    _unsupported_bill_semantic_target_keys,
)
from src.runtime.commands.core import (
    _BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS,
    _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS,
    _JURISDICTION_SEMANTIC_ADAPTER_SOURCE_REQUIREMENT,
    _PREDICTION_BACKFILL_DATA_ACTION_ORDER,
    _PREDICTION_BACKFILL_REFRESH_ACTION_ORDER,
    _PREDICTION_BACKFILL_SUGGESTED_COMMAND_PREFIXES,
    _PREDICTION_BACKFILL_SUPPORTED_COMMAND_ACTIONS,
    _append_unique_objects,
    _append_unique_strings,
    _congresses_from_bill_keys,
    _has_strict_source_url_audit_verify_command,
    _merge_int_count_dicts,
    _missing_required_source_family_ids,
    _normalized_source_family_ids,
    _object_list,
    _required_model_names,
    _run_metadata_source_state,
    _source_family_id_list,
    _strict_int_count_dict,
    _strict_non_negative_int_list,
    _strict_nonblank_string,
    _strict_object_list,
    _strict_string_list,
    _strict_string_values,
)
from src.runtime.commands.fec import _has_strict_fec_inputs_verify_command
from src.runtime.commands.prediction_eval import (
    _handle_verify_prediction_eval_manifest,
    _has_prediction_eval_manifest_archive_verify_command,
    _has_prediction_eval_report_archive_manifest_command,
    _has_strict_prediction_eval_manifest_verify_command,
    _load_prediction_eval_report_from_artifacts,
    _prediction_cutoff_partition_values_valid,
    _prediction_eval_report_refresh_commands,
    _prediction_eval_source_state_rate_valid,
)
from src.runtime.commands.prediction_eval_windows import (
    _handle_verify_prediction_eval_window_run,
    _missing_prediction_eval_window_run_verify_result,
)
from src.runtime.commands.prediction_misc import (
    _handle_verify_prediction_backtest,
    _handle_verify_prediction_input_inventory,
    _handle_verify_prediction_source_url_audit,
    _has_prediction_backtest_archive_manifest_command,
    _has_prediction_backtest_archive_verify_command,
    _has_prediction_input_inventory_archive_manifest_command,
    _has_prediction_input_inventory_archive_verify_command,
    _has_strict_prediction_backtest_verify_command,
    _has_strict_prediction_input_inventory_verify_command,
    _missing_prediction_source_url_audit_result,
    _prediction_backtest_refresh_commands,
    _prediction_input_inventory_refresh_commands,
)
from src.runtime.congress_options import current_congress_for_date


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
            "env:PSEPHOS_POSTGRES_DSN",
            "env:PSEPHOS_CONGRESS_API_KEY",
        ],
        "materialize_missing_bill_semantics": [
            "env:PSEPHOS_POSTGRES_DSN",
            "env:OPENAI_API_KEY",
            f"file:{report_path}" if report_path is not None else "file:<eval-report>",
        ],
        "backfill_feature_source_urls": ["env:PSEPHOS_POSTGRES_DSN"],
        "backfill_cutoff_feature_members": ["env:PSEPHOS_POSTGRES_DSN"],
        "load_source_backed_public_statement_signals": [
            "env:PSEPHOS_POSTGRES_DSN",
            "file:data/taxonomy/sectors.yaml",
        ],
        "load_fec_donations_and_member_crosswalks": [
            "env:PSEPHOS_POSTGRES_DSN",
            "file:data/crosswalks/member_fec.csv",
            "file:data/fec/cm.txt",
            "file:data/fec/ccl.txt",
            "file:data/fec/itcont.txt",
        ],
        "timestamp_bill_semantic_availability": [
            "env:PSEPHOS_POSTGRES_DSN",
            "env:OPENAI_API_KEY",
            f"file:{report_path}" if report_path is not None else "file:<eval-report>",
        ],
        "timestamp_bill_signal_availability": [
            "env:PSEPHOS_POSTGRES_DSN",
            "env:PSEPHOS_CONGRESS_API_KEY",
        ],
        "timestamp_contribution_signal_availability": [
            "env:PSEPHOS_POSTGRES_DSN",
            "file:data/fec/cm.txt",
            "file:data/fec/ccl.txt",
            "file:data/fec/itcont.txt",
            "file:data/crosswalks/member_fec.csv",
        ],
        "timestamp_ontology_edge_availability": ["env:PSEPHOS_POSTGRES_DSN"],
        "timestamp_statement_signal_availability": [
            "env:PSEPHOS_POSTGRES_DSN",
            "file:data/prepared/public-statement-sector-rows.jsonl",
        ],
        "refresh_prediction_input_inventory": ["env:PSEPHOS_POSTGRES_DSN"],
        "refresh_prediction_backtest": ["env:PSEPHOS_POSTGRES_DSN"],
        "refresh_prediction_eval_report": ["env:PSEPHOS_POSTGRES_DSN"],
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


def _prediction_backfill_suggested_command_supported(
    *,
    action: str,
    command: str,
) -> bool:
    prefixes = _PREDICTION_BACKFILL_SUGGESTED_COMMAND_PREFIXES.get(action)
    if not prefixes:
        return False
    return any(command.startswith(prefix) for prefix in prefixes)


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


def _prediction_backfill_plan_sorted_string_list(value: Any) -> TypeGuard[list[str]]:
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
