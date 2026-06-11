"""Generic helpers shared across operator command modules."""

from __future__ import annotations

import hashlib
import json
import math
import sys

from pathlib import Path
from src.runtime.json_artifacts import write_json_artifact as _atomic_write_json_artifact
from src.runtime.paths import local_artifact_root
from typing import Any, TypeGuard, cast
from uuid import uuid4


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


def _artifact_root_or_default(local_root: str | Path | None) -> Path:
    if local_root is None:
        return local_artifact_root()
    return Path(local_root)


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


def _write_json_artifact(path: Path, payload: Any) -> str:
    return _atomic_write_json_artifact(path, payload)


def _write_bytes_artifact(path: Path, encoded: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(encoded)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return hashlib.sha256(encoded).hexdigest()


def _congress_archive_manifest_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    manifest_path = Path(str(value))
    return {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def _required_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _is_plain_int(value: object) -> TypeGuard[int]:
    return type(value) is int


def _plain_int_or_zero(value: object) -> int:
    return value if _is_plain_int(value) else 0


_DEFAULT_RUNTIME_ENV_REQUIREMENTS = (
    "OPENPACT_POSTGRES_DSN",
    "OPENPACT_CONGRESS_API_KEY",
    "OPENAI_API_KEY",
)


def _is_non_negative_plain_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_optional_non_negative_plain_int(value: object) -> TypeGuard[int | None]:
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
        timeout = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0, "timeout must be a positive finite number"
    if not math.isfinite(timeout) or timeout <= 0.0:
        return 0.0, "timeout must be a positive finite number"
    return timeout, None


def _command_issue_result(command: str, issues: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "issues": issues,
        "issue_count": len(issues),
    }


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


def _prediction_operator_resume_plan_sorted_string_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        return False
    return value == sorted(value) and len(set(value)) == len(value)


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


def _optional_file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _sorted_string_list_or_empty(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    if not all(isinstance(item, str) and item.strip() == item and item for item in value):
        return []
    if value != sorted(value) or len(set(value)) != len(value):
        return []
    return value


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


def _is_sha256_hex(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _artifact_reference(path: Path) -> dict[str, str | None]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    }


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None
