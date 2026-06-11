# mypy: ignore-errors
# TODO(runtime-commands): pre-existing type debt carried over from the monolithic
# commands.py (where the commands.pyi stub hid it from mypy). Burn down per module.
"""Operator packet manifest/export/resume-plan commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil

from pathlib import Path
from src.runtime.prediction_operator_resume_run import verify_prediction_operator_resume_run
from types import SimpleNamespace
from typing import Any, cast

from src.runtime.commands._shared import (
    _OPERATOR_EVAL_WINDOW_RUN_GATE_SOURCE_STATE_KEYS,
    _PREDICTION_OPERATOR_RESUME_PLAN_SAMPLE_STRING_KEYS,
    _attach_optional_verification_output,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _load_json_object_or_none,
    _optional_file_sha256,
    _plain_int_or_zero,
    _prediction_operator_resume_plan_sorted_string_list,
    _string_list,
    _write_json_artifact,
)
from src.runtime.commands.runtime_env import _read_dotenv_key_presence
from src.runtime.commands.core import (
    _path_from_payload,
    _source_family_id_list,
    _write_text_artifact,
)
from src.runtime.commands.prediction_readiness import (
    _prediction_readiness_eval_window_run_summary,
    _prediction_resume_script_secret_literal_failures,
)
from src.runtime.commands.operator_reports import (
    _load_operator_handoff_payload,
    _operator_bill_sponsor_availability_core,
    _operator_bill_sponsor_availability_line,
    _operator_bill_sponsor_availability_source_state,
    _operator_bill_sponsor_availability_source_state_issues,
    _operator_congress_load_core,
    _operator_congress_load_source_state,
    _operator_cutoff_audit_core,
    _operator_cutoff_audit_source_state,
    _operator_cutoff_audit_source_state_count_issues,
    _operator_eval_window_run_archive_lines,
    _operator_eval_window_run_archive_source_state,
    _operator_eval_window_run_archive_source_state_issues,
    _operator_eval_window_run_label_gate_source_state,
    _operator_eval_window_run_strict_gate_lines,
    _operator_handoff_check_env_preflight_source,
    _operator_handoff_check_resume_links,
    _operator_handoff_load_readiness_summary,
    _operator_handoff_plan,
    _operator_handoff_resume_batch_contract_issues,
    _operator_handoff_secret_literal_failures,
    _operator_jurisdiction_topology_core,
    _operator_jurisdiction_topology_lines,
    _operator_jurisdiction_topology_source_state,
    _operator_jurisdiction_topology_source_state_issues,
    _operator_runbook_secret_literal_failures,
    _operator_runbook_verified_artifact_missing,
    _operator_source_url_audit_lines,
    _operator_source_url_audit_source_state,
    _operator_source_url_audit_source_state_issues,
    _operator_status_canonical_transitive_source_state,
    _operator_status_core,
    _operator_status_count_issues,
    _operator_status_plan_from_handoff,
    _operator_status_sample_source_state_issues,
    _operator_status_sample_source_state_keys,
    _operator_status_source_state_count_issues,
    _operator_status_source_state_mismatch_issues,
    _prediction_operator_sample_scoped_id_issues,
)


def _handle_verify_prediction_operator_resume_run(args: Any) -> dict[str, Any]:
    return verify_prediction_operator_resume_run(args)


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
