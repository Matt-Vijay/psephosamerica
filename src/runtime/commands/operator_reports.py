# mypy: ignore-errors
# TODO(runtime-commands): pre-existing type debt carried over from the monolithic
# commands.py (where the commands.pyi stub hid it from mypy). Burn down per module.
"""Operator handoff, runbook, and status commands."""

from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path
from typing import Any

from src.runtime.commands._shared import (
    _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_KEYS,
    _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS,
    _PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _optional_file_sha256,
    _plain_int_or_zero,
    _prediction_operator_resume_plan_sorted_string_list,
    _sorted_string_list_or_empty,
    _string_list,
)
from src.runtime.commands.core import _source_family_id_list
from src.runtime.commands.prediction_readiness import (
    _prediction_readiness_eval_window_run_summary,
    _prediction_resume_script_batch_contract_issues,
    _with_prediction_readiness_provenance,
)


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
