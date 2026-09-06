"""Prediction offline-readiness summary and resume-script commands."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from src.runtime.commands._shared import (
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_COUNT_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_INT_LIST_KEYS,
    _BENCHMARK_SOURCE_URL_AUDIT_STRING_LIST_KEYS,
    _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS,
    _artifact_reference,
    _attach_optional_verification_output,
    _dict_or_empty,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _plain_int_or_zero,
    _sorted_string_list_or_empty,
    _string_list,
    _write_bytes_artifact,
    _write_json_artifact,
)
from src.runtime.commands.core import (
    _BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_BACKTEST_SOURCE_COVERAGE_KEYS,
    _BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS,
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_KEYS,
    _OFFLINE_READINESS_BENCHMARK_EVAL_CUTOFF_AUDIT_KEYS,
    _PREDICTION_OFFLINE_READINESS_SOURCE_STATE_STRING_LIST_KEYS,
    _SOURCE_FAMILY_ID_RE,
    _source_family_id_list,
)
from src.runtime.commands.prediction_benchmark import (
    _prediction_backfill_action_order,
    _prediction_backfill_suggested_command_supported,
)
from src.runtime.commands.runtime_env import _runtime_env_next_actions_by_env_summary


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
    benchmark = _dict_or_empty(payload.get("benchmark"))
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
        _dict_or_empty(congress_load),
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
    benchmark = _dict_or_empty(payload.get("benchmark"))
    runtime = _dict_or_empty(payload.get("runtime"))
    env_preflight = _dict_or_empty(payload.get("env_preflight"))
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
    benchmark = _dict_or_empty(payload.get("benchmark"))
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
    jurisdiction_ids_list = _sorted_string_list_or_empty(jurisdiction_ids_value)
    if not (
        jurisdiction_ids_list
        and isinstance(legislative_body_ids_value, list)
        and all(isinstance(item, str) for item in legislative_body_ids_value)
    ):
        return issues
    jurisdiction_ids = set(jurisdiction_ids_list)
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
    jurisdiction_ids_list = _sorted_string_list_or_empty(jurisdiction_ids)
    if not jurisdiction_ids_list:
        return []
    jurisdiction_id_set = set(jurisdiction_ids_list)
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
        r"^\s*(OPENAI_API_KEY|PSEPHOS_POSTGRES_DSN|PSEPHOS_CONGRESS_API_KEY)="
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
    current_result = _dict_or_empty(component.get("current_result"))
    run_metadata = _dict_or_empty(component.get("run_metadata"))
    source_state = _dict_or_empty(run_metadata.get("source_state"))
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
    grouped: dict[str, list[str]] = {
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
