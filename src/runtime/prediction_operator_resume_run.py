"""Verification for saved prediction operator packet resume-run artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeGuard

from src.core.files import sha256_file
from src.runtime.json_artifacts import (
    attach_optional_verification_output as _attach_optional_output,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_EXPECTED_SOURCE_HASH_KEYS = frozenset(
    {
        "packet_export_manifest",
        "checksums",
        "resume_plan",
        "run_resume",
        "resume_script",
    }
)
_OPTIONAL_SOURCE_HASH_KEYS = frozenset({"dotenv"})
_OPTIONAL_SAMPLE_INT_LIST_SOURCE_STATE_KEYS = frozenset({"selected_sample_vote_event_ids"})
_OPTIONAL_SAMPLE_STRING_LIST_SOURCE_STATE_KEYS = frozenset(
    {
        "selected_sample_bill_keys",
        "selected_sample_member_bioguide_ids",
        "selected_sample_jurisdiction_ids",
        "selected_sample_legislative_body_ids",
        "selected_sample_legislative_session_ids",
        "selected_sample_source_family_ids",
    }
)
_OPTIONAL_CONGRESS_BOOL_SOURCE_STATE_KEYS = frozenset(
    {
        "congress_load_required",
        "congress_load_present",
        "congress_load_ok",
        "congress_load_prediction_member_inputs_available",
        "congress_load_prediction_bill_inputs_available",
        "congress_load_prediction_vote_inputs_available",
    }
)
_OPTIONAL_CONGRESS_STRING_LIST_SOURCE_STATE_KEYS = frozenset({"congress_load_source_family_ids"})
_OPTIONAL_CONGRESS_COUNT_SOURCE_STATE_KEYS = frozenset(
    {
        "congress_load_source_family_count",
        "congress_load_member_row_count",
        "congress_load_bill_row_count",
        "congress_load_vote_event_row_count",
        "congress_load_vote_cast_row_count",
    }
)
_REQUIRED_CONGRESS_PREDICTION_SOURCE_FAMILY_IDS = frozenset(
    {"committee_membership", "congress_bill", "congress_vote"}
)
_OPTIONAL_EVAL_WINDOW_BOOL_SOURCE_STATE_KEYS = frozenset(
    {
        "eval_window_run_requires_input_inventory_portable_ids",
        "eval_window_run_requires_input_inventory_congress_source_families",
        "eval_window_run_requires_input_inventory_official_source_thresholds",
        "eval_window_run_requires_input_inventory_optional_evidence",
        "eval_window_run_requires_eval_manifest_model_suite",
        "eval_window_run_requires_eval_manifest_official_source_thresholds",
        "eval_window_run_requires_eval_manifest_unknown_availability_failures",
        "eval_window_run_requires_eval_manifest_ontology_feature_signals",
    }
)


def verify_prediction_operator_resume_run(args: Any) -> dict[str, Any]:
    """Verify a packet-local run_resume.py --output artifact without running it."""
    packet_dir = Path(args.packet_dir)
    artifact_path = Path(args.artifact)
    dotenv_path = Path(args.dotenv) if getattr(args, "dotenv", None) is not None else None
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    artifact = _load_json_payload(artifact_path, "resume_run", issues)
    if artifact.get("command") != "run_resume.py":
        issues.append("artifact_command_mismatch")

    raw_run_metadata = artifact.get("run_metadata")
    run_metadata: dict[str, Any] = raw_run_metadata if isinstance(raw_run_metadata, dict) else {}
    run_metadata_present = bool(run_metadata)
    if bool(getattr(args, "require_run_metadata", False)) and not run_metadata_present:
        quality_gate_failures.append("run_metadata_missing")
    if run_metadata_present and run_metadata.get("command") != "run_resume.py":
        issues.append("run_metadata_command_mismatch")

    source_hashes, source_hash_invalid = _source_hashes(
        run_metadata,
        issues,
        require=run_metadata_present,
    )
    resume_script_path = _packet_resume_script_path(packet_dir)
    source_hash_mismatches: list[str] = []
    source_hash_missing: list[str] = []
    source_file_missing: list[str] = []
    dotenv_hash_matches: bool | None = None
    if run_metadata_present:
        source_hash_mismatches, source_hash_missing, source_file_missing = _check_source_hashes(
            packet_dir=packet_dir,
            resume_script_path=resume_script_path,
            source_hashes=source_hashes,
            source_hash_invalid=source_hash_invalid,
            issues=issues,
        )
        dotenv_hash_matches = _check_dotenv_hash(
            dotenv_path=dotenv_path,
            source_hashes=source_hashes,
            source_hash_invalid=source_hash_invalid,
            issues=issues,
        )

    artifact_ok = bool(artifact.get("ok"))
    if bool(getattr(args, "require_ok", False)) and not artifact_ok:
        quality_gate_failures.append("artifact_not_ok")
    artifact_dry_run = bool(artifact.get("dry_run"))
    if bool(getattr(args, "require_dry_run", False)) and not artifact_dry_run:
        quality_gate_failures.append("artifact_not_dry_run")
    env_validation = artifact.get("env_validation")
    if env_validation != "presence_only":
        issues.append("env_validation_missing_or_unknown")
    selected_count_issues = _selected_count_issues(artifact)
    issues.extend(selected_count_issues)
    selected_counts_match = not selected_count_issues
    selected_provenance_issues = _selected_provenance_issues(artifact)
    issues.extend(selected_provenance_issues)
    selected_provenance_matches = not selected_provenance_issues
    issues.extend(_selected_source_artifact_hash_issues(artifact, run_metadata))
    source_state_issues = _source_state_issues(artifact, run_metadata)
    issues.extend(source_state_issues)
    source_state_matches = not source_state_issues
    verification_flags_issues = _verification_flags_issues(artifact, run_metadata)
    issues.extend(verification_flags_issues)
    verification_flags_match = not verification_flags_issues

    secret_failures: list[str] = []
    if bool(getattr(args, "require_no_secret_literals", False)):
        secret_failures = _secret_literal_failures(artifact)
        quality_gate_failures.extend(secret_failures)

    congress_prediction_inputs_ready = _congress_prediction_inputs_ready(run_metadata)
    if (
        bool(getattr(args, "require_congress_prediction_inputs", False))
        and not congress_prediction_inputs_ready
    ):
        quality_gate_failures.append("congress_prediction_inputs_not_ready")
    strict_eval_window_run_ready = _strict_eval_window_run_ready(run_metadata)
    if (
        bool(getattr(args, "require_strict_eval_window_run", False))
        and not strict_eval_window_run_ready
    ):
        quality_gate_failures.append("strict_eval_window_run_not_ready")

    selected_source_artifact_count = len(_string_list(artifact.get("selected_source_artifacts")))
    selected_source_artifact_hash_count = _selected_source_artifact_hash_count(
        artifact,
        run_metadata,
    )
    selected_source_artifact_hash_missing_count = max(
        0,
        selected_source_artifact_count - selected_source_artifact_hash_count,
    )
    selected_source_artifact_hash_coverage_complete = (
        selected_source_artifact_hash_missing_count == 0
    )
    if (
        bool(getattr(args, "require_selected_source_artifact_hashes", False))
        and not selected_source_artifact_hash_coverage_complete
    ):
        quality_gate_failures.append("selected_source_artifact_hashes_incomplete")
    source_state = {
        "artifact_ok": artifact_ok,
        "artifact_dry_run": artifact_dry_run,
        "env_validation": env_validation if isinstance(env_validation, str) else None,
        "run_metadata_present": run_metadata_present,
        "source_hash_missing_count": len(source_hash_missing),
        "source_hash_invalid_count": len(source_hash_invalid),
        "source_file_missing_count": len(source_file_missing),
        "source_hash_mismatch_count": len(source_hash_mismatches),
        "dotenv_hash_matches": dotenv_hash_matches,
        "selected_counts_match": selected_counts_match,
        "selected_provenance_matches": selected_provenance_matches,
        "source_state_matches": source_state_matches,
        "verification_flags_match": verification_flags_match,
        "selected_source_artifact_count": selected_source_artifact_count,
        "selected_source_artifact_hash_count": selected_source_artifact_hash_count,
        "selected_source_artifact_hash_missing_count": (
            selected_source_artifact_hash_missing_count
        ),
        "selected_source_artifact_hash_coverage_complete": (
            selected_source_artifact_hash_coverage_complete
        ),
        "selected_additional_reason_count": len(
            _string_list(artifact.get("selected_additional_reasons"))
        ),
        "secret_literal_count": len(secret_failures),
        "congress_prediction_inputs_ready": congress_prediction_inputs_ready,
        "strict_eval_window_run_ready": strict_eval_window_run_ready,
    }
    source_state.update(_canonical_optional_sample_source_state(run_metadata))
    source_state.update(_canonical_optional_congress_source_state(run_metadata))
    source_state.update(_canonical_optional_eval_window_source_state(run_metadata))
    result = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-prediction-operator-resume-run",
        "packet_dir": str(packet_dir),
        "artifact": str(artifact_path),
        "artifact_sha256": _optional_file_sha256(artifact_path),
        "dotenv": str(dotenv_path) if dotenv_path is not None else None,
        "artifact_ok": artifact_ok,
        "artifact_dry_run": artifact_dry_run,
        "env_validation": env_validation if isinstance(env_validation, str) else None,
        "run_metadata_present": run_metadata_present,
        "source_hash_missing": sorted(source_hash_missing),
        "source_hash_missing_count": len(source_hash_missing),
        "source_hash_invalid": sorted(source_hash_invalid),
        "source_hash_invalid_count": len(source_hash_invalid),
        "source_file_missing": sorted(source_file_missing),
        "source_file_missing_count": len(source_file_missing),
        "source_hash_mismatches": sorted(source_hash_mismatches),
        "source_hash_mismatch_count": len(source_hash_mismatches),
        "dotenv_hash_matches": dotenv_hash_matches,
        "selected_counts_match": selected_counts_match,
        "selected_provenance_matches": selected_provenance_matches,
        "source_state_matches": source_state_matches,
        "verification_flags_match": verification_flags_match,
        "secret_literal_count": len(secret_failures),
        "issues": sorted(set(issues)),
        "issue_count": len(set(issues)),
        "quality_gate_failures": sorted(set(quality_gate_failures)),
        "quality_gate_failure_count": len(set(quality_gate_failures)),
        "run_metadata": {
            "command": "verify-prediction-operator-resume-run",
            "verification_flags": {
                "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
                "require_ok": bool(getattr(args, "require_ok", False)),
                "require_dry_run": bool(getattr(args, "require_dry_run", False)),
                "require_no_secret_literals": bool(
                    getattr(args, "require_no_secret_literals", False)
                ),
                "require_congress_prediction_inputs": bool(
                    getattr(args, "require_congress_prediction_inputs", False)
                ),
                "require_strict_eval_window_run": bool(
                    getattr(args, "require_strict_eval_window_run", False)
                ),
                "require_selected_source_artifact_hashes": bool(
                    getattr(args, "require_selected_source_artifact_hashes", False)
                ),
                "dotenv": str(dotenv_path) if dotenv_path is not None else None,
            },
            "source_artifact_sha256": {
                "artifact": _optional_file_sha256(artifact_path),
                "dotenv": _optional_file_sha256(dotenv_path) if dotenv_path is not None else None,
            },
            "source_state": source_state,
        },
    }
    return _attach_optional_output(args, result)


def _load_json_payload(
    path: Path,
    label: str,
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
    return payload


def _source_hashes(
    run_metadata: dict[str, Any],
    issues: list[str],
    *,
    require: bool,
) -> tuple[dict[str, str], list[str]]:
    raw_hashes = (
        run_metadata.get("source_artifact_sha256")
        if isinstance(run_metadata.get("source_artifact_sha256"), dict)
        else {}
    )
    if not isinstance(raw_hashes, dict):
        raw_hashes = {}
    source_hashes: dict[str, str] = {}
    source_hash_invalid: list[str] = []
    for key, value in raw_hashes.items():
        if not isinstance(key, str):
            continue
        if key not in _EXPECTED_SOURCE_HASH_KEYS and key not in _OPTIONAL_SOURCE_HASH_KEYS:
            issues.append(f"source_hash_unexpected:{key}")
            continue
        if not isinstance(value, str):
            source_hash_invalid.append(key)
            issues.append(
                "dotenv_hash_invalid" if key == "dotenv" else f"source_hash_invalid:{key}"
            )
            continue
        if not _is_sha256_hex(value):
            source_hash_invalid.append(key)
            issues.append(
                "dotenv_hash_invalid" if key == "dotenv" else f"source_hash_invalid:{key}"
            )
            continue
        source_hashes[key] = value
    if require and not source_hashes:
        issues.append("source_artifact_sha256_missing")
    return source_hashes, source_hash_invalid


def _check_source_hashes(
    *,
    packet_dir: Path,
    resume_script_path: Path | None,
    source_hashes: dict[str, str],
    source_hash_invalid: list[str],
    issues: list[str],
) -> tuple[list[str], list[str], list[str]]:
    expected_sources: dict[str, Path | None] = {
        key: path
        for key, path in {
            "packet_export_manifest": packet_dir / "packet-export-manifest.json",
            "checksums": packet_dir / "SHA256SUMS",
            "resume_plan": packet_dir / "resume_plan.py",
            "run_resume": packet_dir / "run_resume.py",
            "resume_script": resume_script_path,
        }.items()
    }
    source_hash_mismatches: list[str] = []
    source_hash_missing: list[str] = []
    source_file_missing: list[str] = []
    invalid_hash_keys = set(source_hash_invalid)
    for key, path in expected_sources.items():
        if key in invalid_hash_keys:
            continue
        expected_hash = source_hashes.get(key)
        if expected_hash is None:
            source_hash_missing.append(key)
            issues.append(f"source_hash_missing:{key}")
            continue
        if path is None or not path.is_file():
            source_file_missing.append(key)
            issues.append(f"source_file_missing:{key}")
            continue
        actual_hash = _optional_file_sha256(path)
        if actual_hash != expected_hash:
            source_hash_mismatches.append(key)
            issues.append(f"source_hash_mismatch:{key}")
    return source_hash_mismatches, source_hash_missing, source_file_missing


def _check_dotenv_hash(
    *,
    dotenv_path: Path | None,
    source_hashes: dict[str, str],
    source_hash_invalid: list[str],
    issues: list[str],
) -> bool | None:
    if dotenv_path is None:
        return None
    if "dotenv" in source_hash_invalid:
        return False
    expected_dotenv_hash = source_hashes.get("dotenv")
    if expected_dotenv_hash is None:
        issues.append("dotenv_hash_missing")
        return False
    if not dotenv_path.is_file():
        issues.append("dotenv_missing")
        return False
    dotenv_hash_matches = _optional_file_sha256(dotenv_path) == expected_dotenv_hash
    if not dotenv_hash_matches:
        issues.append("dotenv_hash_mismatch")
    return dotenv_hash_matches


def _selected_provenance_issues(artifact: dict[str, Any]) -> list[str]:
    selected_phases = _selected_phase_rows(artifact)
    expected_source_artifacts = _phase_values(selected_phases, "source_artifacts")
    expected_additional_reasons = _phase_values(selected_phases, "additional_reasons")
    issues: list[str] = []
    issues.extend(
        _non_empty_string_list_issue(
            artifact.get("selected_source_artifacts"),
            "selected_source_artifacts",
        )
    )
    issues.extend(
        _non_empty_string_list_issue(
            artifact.get("selected_additional_reasons"),
            "selected_additional_reasons",
        )
    )
    issues.extend(
        _non_empty_string_list_issue(
            artifact.get("selected_missing_env"),
            "selected_missing_env",
        )
    )
    issues.extend(_phase_string_list_issues(selected_phases))
    if _string_list(artifact.get("selected_source_artifacts")) != expected_source_artifacts:
        issues.append("selected_source_artifacts_mismatch")
    if _string_list(artifact.get("selected_additional_reasons")) != expected_additional_reasons:
        issues.append("selected_additional_reasons_mismatch")
    return issues


def _selected_source_artifact_hash_issues(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> list[str]:
    if not run_metadata:
        return []
    raw_hashes = run_metadata.get("selected_source_artifact_sha256")
    if raw_hashes is None:
        return []
    if not isinstance(raw_hashes, dict):
        return ["selected_source_artifact_sha256_must_be_object"]
    issues: list[str] = []
    hashes: dict[str, Any] = {}
    for key, value in raw_hashes.items():
        if not isinstance(key, str) or not key or key.strip() != key:
            issues.append("selected_source_artifact_hash_key_invalid")
            continue
        hashes[key] = value
    artifact_names = set(_string_list(artifact.get("selected_source_artifacts")))
    for artifact_name in sorted(set(hashes) - artifact_names):
        issues.append(f"selected_source_artifact_hash_unexpected:{artifact_name}")
    for artifact_name in sorted(artifact_names):
        value = hashes.get(artifact_name)
        if value is None:
            issues.append(f"selected_source_artifact_missing_hash:{artifact_name}")
        elif not isinstance(value, str) or not _is_sha256_hex(value):
            issues.append(f"selected_source_artifact_hash_invalid:{artifact_name}")
        elif _selected_source_artifact_sha256(artifact, artifact_name) not in (None, value):
            issues.append(f"selected_source_artifact_hash_mismatch:{artifact_name}")
    return issues


def _selected_source_artifact_hash_count(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> int:
    raw_hashes = run_metadata.get("selected_source_artifact_sha256")
    if not isinstance(raw_hashes, dict):
        return 0
    selected_names = set(_string_list(artifact.get("selected_source_artifacts")))
    return sum(
        1
        for key, value in raw_hashes.items()
        if isinstance(key, str)
        and key in selected_names
        and isinstance(value, str)
        and _is_sha256_hex(value)
    )


def _selected_source_artifact_sha256(
    artifact: dict[str, Any],
    artifact_name: str,
) -> str | None:
    repo_root = artifact.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root:
        return None
    artifact_path = Path(artifact_name)
    path = artifact_path if artifact_path.is_absolute() else Path(repo_root) / artifact_path
    return _optional_file_sha256(path)


def _phase_string_list_issues(phases: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for key in ("source_artifacts", "additional_reasons", "missing_env"):
        if any(_has_blank_string(phase.get(key)) for phase in phases):
            issues.append(f"plan phases {key} must contain non-empty strings")
    return issues


def _non_empty_string_list_issue(value: Any, label: str) -> list[str]:
    if _has_blank_string(value):
        return [f"{label} must contain non-empty strings"]
    return []


def _has_blank_string(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, str) and not item.strip() for item in value
    )


def _selected_count_issues(artifact: dict[str, Any]) -> list[str]:
    selected_phases = _selected_phase_rows(artifact)
    issues: list[str] = []
    issues.extend(_selected_phases_shape_issues(artifact.get("selected_phases")))
    selected_phase_count = artifact.get("selected_phase_count")
    selected_command_count = artifact.get("selected_command_count")
    if not isinstance(selected_phase_count, int) or isinstance(selected_phase_count, bool):
        issues.append("selected_phase_count must be an integer")
    elif selected_phase_count < 0:
        issues.append("selected_phase_count must be a non-negative integer")
    elif selected_phase_count != len(selected_phases):
        issues.append("selected_phase_count_mismatch")
    if not isinstance(selected_command_count, int) or isinstance(selected_command_count, bool):
        issues.append("selected_command_count must be an integer")
    elif selected_command_count < 0:
        issues.append("selected_command_count must be a non-negative integer")
    elif selected_command_count != _phase_value_count(
        selected_phases,
        "commands",
    ):
        issues.append("selected_command_count_mismatch")
    expected_missing_env = sorted(set(_phase_values(selected_phases, "missing_env")))
    if _string_list(artifact.get("selected_missing_env")) != expected_missing_env:
        issues.append("selected_missing_env_mismatch")
    return issues


def _selected_phases_shape_issues(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not _is_plain_int(item) for item in value):
        return ["selected_phases must be a list of integers"]
    return []


def _source_state_issues(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> list[str]:
    if not run_metadata:
        return []
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return ["run_metadata_source_state_mismatch"]
    expected = _expected_source_state(artifact)
    shape_issues = _source_state_count_shape_issues(source_state)
    shape_issues.extend(_optional_sample_source_state_shape_issues(source_state))
    shape_issues.extend(_optional_congress_source_state_shape_issues(source_state))
    shape_issues.extend(_optional_eval_window_source_state_shape_issues(source_state))
    if shape_issues:
        return shape_issues
    optional_keys = _valid_optional_source_state_keys(source_state, expected)
    optional_keys.update(_valid_optional_sample_source_state_keys(source_state))
    optional_keys.update(_valid_optional_congress_source_state_keys(source_state))
    optional_keys.update(_valid_optional_eval_window_source_state_keys(source_state))
    unexpected_keys = sorted(set(source_state) - set(expected) - optional_keys)
    if unexpected_keys:
        return [f"run_metadata source_state unexpected: {key}" for key in unexpected_keys]
    for key, value in expected.items():
        if source_state.get(key) != value:
            return ["run_metadata_source_state_mismatch"]
    return []


def _valid_optional_source_state_keys(
    source_state: dict[str, Any],
    expected: dict[str, Any],
) -> set[str]:
    if "loaded_env_names" not in source_state:
        return set()
    loaded_env_names = source_state.get("loaded_env_names")
    loaded_key_count = expected.get("loaded_key_count")
    if not isinstance(loaded_env_names, list):
        return set()
    if not all(
        isinstance(name, str) and name and name.strip() == name for name in loaded_env_names
    ):
        return set()
    if loaded_env_names != sorted(loaded_env_names) or len(set(loaded_env_names)) != len(
        loaded_env_names
    ):
        return set()
    if _is_plain_int(loaded_key_count) and len(loaded_env_names) != loaded_key_count:
        return set()
    return {"loaded_env_names"}


def _valid_optional_sample_source_state_keys(source_state: dict[str, Any]) -> set[str]:
    valid_keys: set[str] = set()
    scoped_sample_issues = _sample_scoped_id_issues(source_state)
    for key in _OPTIONAL_SAMPLE_INT_LIST_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if _is_non_negative_int_list(value):
            valid_keys.add(key)
    for key in _OPTIONAL_SAMPLE_STRING_LIST_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if _is_sorted_unique_non_empty_string_list(value) and (
            key != "selected_sample_source_family_ids" or _is_source_family_id_list(value)
        ):
            valid_keys.add(key)
    if scoped_sample_issues:
        valid_keys.discard("selected_sample_legislative_body_ids")
        valid_keys.discard("selected_sample_legislative_session_ids")
    return valid_keys


def _valid_optional_congress_source_state_keys(source_state: dict[str, Any]) -> set[str]:
    valid_keys: set[str] = set()
    for key in _OPTIONAL_CONGRESS_BOOL_SOURCE_STATE_KEYS:
        if isinstance(source_state.get(key), bool):
            valid_keys.add(key)
    for key in _OPTIONAL_CONGRESS_STRING_LIST_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if _is_sorted_unique_non_empty_string_list(value) and _is_source_family_id_list(value):
            valid_keys.add(key)
    for key in _OPTIONAL_CONGRESS_COUNT_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            valid_keys.add(key)
    return valid_keys


def _valid_optional_eval_window_source_state_keys(source_state: dict[str, Any]) -> set[str]:
    valid_keys: set[str] = set()
    for key in _OPTIONAL_EVAL_WINDOW_BOOL_SOURCE_STATE_KEYS:
        if isinstance(source_state.get(key), bool):
            valid_keys.add(key)
    return valid_keys


def _optional_sample_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in _OPTIONAL_SAMPLE_INT_LIST_SOURCE_STATE_KEYS:
        if key in source_state and not _is_non_negative_int_list(source_state.get(key)):
            issues.append(
                f"run_metadata source_state {key} must be a list of non-negative integers"
            )
    for key in _OPTIONAL_SAMPLE_STRING_LIST_SOURCE_STATE_KEYS:
        if key in source_state and not _is_sorted_unique_non_empty_string_list(
            source_state.get(key)
        ):
            issues.append(
                f"run_metadata source_state {key} must be a sorted unique list of non-empty strings"
            )
        elif (
            key == "selected_sample_source_family_ids"
            and key in source_state
            and not _is_source_family_id_list(source_state.get(key))
        ):
            issues.append(
                f"run_metadata source_state {key} must contain normalized source family ids"
            )
    issues.extend(_sample_scoped_id_issues(source_state))
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
    jurisdiction_id_values = jurisdiction_ids
    jurisdiction_id_set = set(jurisdiction_id_values)
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


def _optional_congress_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in sorted(_OPTIONAL_CONGRESS_BOOL_SOURCE_STATE_KEYS):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"run_metadata source_state {key} must be a boolean")
    for key in sorted(_OPTIONAL_CONGRESS_STRING_LIST_SOURCE_STATE_KEYS):
        if key in source_state and not _is_sorted_unique_non_empty_string_list(
            source_state.get(key)
        ):
            issues.append(
                f"run_metadata source_state {key} must be a sorted unique list of non-empty strings"
            )
        elif key in source_state and not _is_source_family_id_list(source_state.get(key)):
            issues.append(
                f"run_metadata source_state {key} must contain normalized source family ids"
            )
    for key in sorted(_OPTIONAL_CONGRESS_COUNT_SOURCE_STATE_KEYS):
        if key not in source_state:
            continue
        value = source_state.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            issues.append(f"run_metadata source_state {key} must be an integer")
        elif value < 0:
            issues.append(f"run_metadata source_state {key} must be a non-negative integer")
    return issues


def _optional_eval_window_source_state_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in sorted(_OPTIONAL_EVAL_WINDOW_BOOL_SOURCE_STATE_KEYS):
        if key in source_state and not isinstance(source_state.get(key), bool):
            issues.append(f"run_metadata source_state {key} must be a boolean")
    return issues


def _canonical_optional_sample_source_state(run_metadata: dict[str, Any]) -> dict[str, Any]:
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return {}
    canonical: dict[str, Any] = {}
    for key in sorted(_OPTIONAL_SAMPLE_INT_LIST_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if _is_non_negative_int_list(value):
            canonical[key] = value
    for key in sorted(_OPTIONAL_SAMPLE_STRING_LIST_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if _is_sorted_unique_non_empty_string_list(value) and (
            key != "selected_sample_source_family_ids" or _is_source_family_id_list(value)
        ):
            canonical[key] = value
    return canonical


def _canonical_optional_congress_source_state(run_metadata: dict[str, Any]) -> dict[str, Any]:
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return {}
    canonical: dict[str, Any] = {}
    for key in sorted(_OPTIONAL_CONGRESS_BOOL_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if isinstance(value, bool):
            canonical[key] = value
    for key in sorted(_OPTIONAL_CONGRESS_STRING_LIST_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if _is_sorted_unique_non_empty_string_list(value) and _is_source_family_id_list(value):
            canonical[key] = value
    for key in sorted(_OPTIONAL_CONGRESS_COUNT_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            canonical[key] = value
    return canonical


def _canonical_optional_eval_window_source_state(run_metadata: dict[str, Any]) -> dict[str, Any]:
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return {}
    canonical: dict[str, Any] = {}
    for key in sorted(_OPTIONAL_EVAL_WINDOW_BOOL_SOURCE_STATE_KEYS):
        value = source_state.get(key)
        if isinstance(value, bool):
            canonical[key] = value
    return canonical


def _congress_prediction_inputs_ready(run_metadata: dict[str, Any]) -> bool:
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return False
    booleans_ready = all(
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
    if not booleans_ready:
        return False
    source_family_ids = source_state.get("congress_load_source_family_ids")
    if not _is_sorted_unique_non_empty_string_list(
        source_family_ids
    ) or not _is_source_family_id_list(source_family_ids):
        return False
    if not _REQUIRED_CONGRESS_PREDICTION_SOURCE_FAMILY_IDS.issubset(set(source_family_ids)):
        return False
    if source_state.get("congress_load_source_family_count") != len(source_family_ids):
        return False
    for key in _OPTIONAL_CONGRESS_COUNT_SOURCE_STATE_KEYS:
        value = source_state.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return False
    return True


def _strict_eval_window_run_ready(run_metadata: dict[str, Any]) -> bool:
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return False
    return all(
        source_state.get(key) is True for key in _OPTIONAL_EVAL_WINDOW_BOOL_SOURCE_STATE_KEYS
    )


def _verification_flags_issues(
    artifact: dict[str, Any],
    run_metadata: dict[str, Any],
) -> list[str]:
    if not run_metadata:
        return []
    flags = run_metadata.get("verification_flags")
    if not isinstance(flags, dict):
        return ["run_metadata_verification_flags_mismatch"]
    expected = _expected_verification_flags(artifact)
    for key, value in expected.items():
        if flags.get(key) != value:
            return ["run_metadata_verification_flags_mismatch"]
    return []


def _expected_verification_flags(artifact: dict[str, Any]) -> dict[str, Any]:
    raw_dotenv = artifact.get("dotenv")
    dotenv = raw_dotenv if isinstance(raw_dotenv, dict) else {}
    dotenv_path = dotenv.get("path")
    output = artifact.get("output")
    return {
        "dry_run": bool(artifact.get("dry_run")),
        "selected_phases": _plain_int_list(artifact.get("selected_phases")),
        "dotenv": dotenv_path if isinstance(dotenv_path, str) else None,
        "output": output if isinstance(output, str) else None,
    }


def _expected_source_state(artifact: dict[str, Any]) -> dict[str, Any]:
    raw_plan = artifact.get("plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    raw_dotenv = artifact.get("dotenv")
    dotenv = raw_dotenv if isinstance(raw_dotenv, dict) else {}
    missing_env = plan.get("missing_env")
    loaded_key_count = dotenv.get("loaded_key_count")
    return {
        "packet_verified": bool(plan.get("packet_verified")),
        "plan_launch_ready": bool(plan.get("launch_ready")),
        "plan_missing_env_count": len(missing_env) if isinstance(missing_env, list) else None,
        "loaded_key_count": loaded_key_count if _is_plain_int(loaded_key_count) else None,
        "selected_phase_count": _plain_int_or_none(artifact.get("selected_phase_count")),
        "selected_command_count": _plain_int_or_none(artifact.get("selected_command_count")),
        "selected_source_artifact_count": len(
            _string_list(artifact.get("selected_source_artifacts"))
        ),
        "selected_additional_reason_count": len(
            _string_list(artifact.get("selected_additional_reasons"))
        ),
        "selected_missing_env_count": len(_string_list(artifact.get("selected_missing_env"))),
        "env_validation": artifact.get("env_validation")
        if isinstance(artifact.get("env_validation"), str)
        else None,
    }


def _invalid_source_state_count_keys(source_state: dict[str, Any]) -> set[str]:
    invalid_keys: set[str] = set()
    for key in (
        "loaded_key_count",
        "selected_phase_count",
        "selected_command_count",
        "selected_source_artifact_count",
        "selected_additional_reason_count",
        "selected_missing_env_count",
    ):
        value = source_state.get(key)
        if value is not None and (not _is_plain_int(value) or value < 0):
            invalid_keys.add(key)
    return invalid_keys


def _source_state_count_shape_issues(source_state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in _invalid_source_state_count_keys(source_state):
        value = source_state.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            issues.append(f"run_metadata source_state {key} must be an integer")
        elif value < 0:
            issues.append(f"run_metadata source_state {key} must be a non-negative integer")
    return issues


def _selected_phase_rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    raw_plan = artifact.get("plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    phases = [phase for phase in plan.get("phases", []) if isinstance(phase, dict)]
    selected_phase_numbers = [
        value for value in artifact.get("selected_phases", []) if _is_plain_int(value)
    ]
    if not selected_phase_numbers:
        return phases
    selected = set(selected_phase_numbers)
    return [phase for phase in phases if phase.get("phase") in selected]


def _phase_values(phases: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for phase in phases:
        for value in _string_list(phase.get(key)):
            if value not in values:
                values.append(value)
    return values


def _phase_value_count(phases: list[dict[str, Any]], key: str) -> int:
    return sum(len(_string_list(phase.get(key))) for phase in phases)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plain_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_plain_int(item)]


def _plain_int_or_none(value: Any) -> int | None:
    return value if _is_plain_int(value) else None


def _is_non_negative_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_plain_int(item) and item >= 0 for item in value)


def _is_sorted_unique_non_empty_string_list(value: Any) -> TypeGuard[list[str]]:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


def _is_source_family_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None for item in value
    )


def _packet_resume_script_path(packet_dir: Path) -> Path | None:
    manifest = _load_json_object_or_none(packet_dir / "packet-export-manifest.json")
    if isinstance(manifest, dict):
        for entry in manifest.get("exported_files", []):
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


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def _secret_literal_failures(payload: dict[str, Any]) -> list[str]:
    serialized = json.dumps(payload, sort_keys=True)
    failures: list[str] = []
    if "sk-" in serialized:
        failures.append("secret_literal:openai_token")
    if re.search(r"\bpostgres(?:ql)?://\S+", serialized):
        failures.append("secret_literal:postgres_dsn")
    return sorted(set(failures))


def _optional_file_sha256(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _is_sha256_hex(value: str) -> bool:
    return _SHA256_HEX_RE.fullmatch(value) is not None
