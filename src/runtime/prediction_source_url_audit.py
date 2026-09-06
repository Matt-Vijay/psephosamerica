from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.prediction.source_url_audit import build_prediction_source_url_audit
from src.runtime.json_artifacts import (
    attach_optional_verification_output as _attach_optional_verification_output,
    write_json_artifact as _write_json_artifact,
)

_PREDICTION_SOURCE_URL_AUDIT_QUALITY_GATES = {
    "feature_source_url_gaps",
    "feature_source_official_url_gaps",
    "portable_sample_missing_body_id_gaps",
    "portable_sample_missing_session_id_gaps",
}
_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def run_prediction_source_url_audit_command(args: Any) -> dict[str, Any]:
    """Run the prediction source URL audit command and optionally write its artifact."""
    try:
        audit = build_prediction_source_url_audit(Path(args.eval_report))
    except Exception as exc:  # noqa: BLE001
        return _command_issue_result(
            "prediction-source-url-audit",
            [f"failed to load eval_report: {exc}"],
        )
    quality_gate_failures: list[str] = []
    if bool(getattr(args, "fail_on_gaps", False)):
        if audit.missing_url_sourced_prediction_count:
            quality_gate_failures.append("feature_source_url_gaps")
        if audit.missing_official_source_prediction_count:
            quality_gate_failures.append("feature_source_official_url_gaps")
        if audit.portable_sample_missing_body_id_count:
            quality_gate_failures.append("portable_sample_missing_body_id_gaps")
        if audit.portable_sample_missing_session_id_count:
            quality_gate_failures.append("portable_sample_missing_session_id_gaps")
    result: dict[str, Any] = {
        "ok": not quality_gate_failures,
        "command": "prediction-source-url-audit",
        **asdict(audit),
        "quality_gate_failures": quality_gate_failures,
        "run_metadata": {
            "command": "prediction-source-url-audit",
            "verification_flags": {
                "fail_on_gaps": bool(getattr(args, "fail_on_gaps", False)),
            },
            "artifact_sha256": audit.eval_report_sha256,
            "source_artifact_sha256": {"eval_report": audit.eval_report_sha256},
            "source_state": {
                **_prediction_source_url_audit_source_state(audit),
                **_prediction_source_url_audit_quality_gate_source_state(quality_gate_failures),
            },
        },
    }
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        result["output_sha256"] = _write_json_artifact(output, result)
        result["output"] = str(output)
    return result


def _command_issue_result(command: str, issues: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "issues": issues,
        "issue_count": len(issues),
    }


def verify_prediction_source_url_audit_command(args: Any) -> dict[str, Any]:
    """Verify a detached prediction source URL audit artifact."""
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    checked = 0

    def failure_result(*, checked: int, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-source-url-audit",
                "artifact": str(artifact_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_source_url_audit_verify_run_metadata(
                    args,
                    artifact_path,
                ),
            },
        )

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"failed to load artifact: {exc}"])
    if not isinstance(artifact, dict):
        return failure_result(checked=0, issues=["artifact must be an object"])
    checked += 1
    _validate_prediction_source_url_audit_artifact(artifact, issues)
    raw_eval_report_path = artifact.get("eval_report_path")
    expected_eval_report_sha = artifact.get("eval_report_sha256")
    expected_eval_report_sha_is_valid = isinstance(
        expected_eval_report_sha, str
    ) and _is_sha256_hex(expected_eval_report_sha)
    if isinstance(raw_eval_report_path, str) and raw_eval_report_path:
        eval_report_path = Path(raw_eval_report_path)
        if not eval_report_path.is_file():
            issues.append(f"eval_report not found: {eval_report_path}")
        else:
            checked += 1
            actual_eval_report_sha = hashlib.sha256(eval_report_path.read_bytes()).hexdigest()
            if (
                expected_eval_report_sha_is_valid
                and expected_eval_report_sha != actual_eval_report_sha
            ):
                issues.append("eval_report_sha256 mismatch")
    source_state = _prediction_source_url_audit_artifact_source_state(artifact)
    run_metadata = artifact.get("run_metadata")
    require_run_metadata = bool(getattr(args, "require_run_metadata", False))
    if run_metadata is None:
        if require_run_metadata:
            issues.append("run_metadata missing")
    elif not isinstance(run_metadata, dict):
        issues.append("run_metadata must be an object")
    else:
        checked += 1
        _validate_prediction_source_url_audit_run_metadata(
            run_metadata=run_metadata,
            quality_gate_failures=artifact.get("quality_gate_failures"),
            expected_source_state=source_state,
            issues=issues,
            require_source_state=require_run_metadata,
        )
    quality_gate_failures: list[str] = []
    if (
        bool(getattr(args, "require_no_gaps", False))
        and _artifact_int_or_zero(artifact, "missing_url_sourced_prediction_count") > 0
    ):
        quality_gate_failures.append("feature_source_url_gaps")
    if (
        bool(getattr(args, "require_no_official_source_gaps", False))
        and _artifact_int_or_zero(
            artifact,
            "missing_official_source_prediction_count",
        )
        > 0
    ):
        quality_gate_failures.append("feature_source_official_url_gaps")
    if (
        bool(getattr(args, "require_no_gaps", False))
        and _artifact_int_or_zero(artifact, "portable_sample_missing_body_id_count") > 0
    ):
        quality_gate_failures.append("portable_sample_missing_body_id_gaps")
    if (
        bool(getattr(args, "require_no_gaps", False))
        and _artifact_int_or_zero(artifact, "portable_sample_missing_session_id_count") > 0
    ):
        quality_gate_failures.append("portable_sample_missing_session_id_gaps")
    if bool(getattr(args, "require_no_portable_context_gaps", False)):
        if _artifact_int_or_zero(artifact, "portable_sample_missing_body_id_count") > 0:
            quality_gate_failures.append("portable_sample_missing_body_id_gaps")
        if _artifact_int_or_zero(artifact, "portable_sample_missing_session_id_count") > 0:
            quality_gate_failures.append("portable_sample_missing_session_id_gaps")
    quality_gate_failures = list(dict.fromkeys(quality_gate_failures))
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-source-url-audit",
            "artifact": str(artifact_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "gap_count": _artifact_int_or_zero(artifact, "gap_count"),
            "missing_url_sourced_prediction_count": _artifact_int_or_zero(
                artifact,
                "missing_url_sourced_prediction_count",
            ),
            "missing_official_source_prediction_count": _artifact_int_or_zero(
                artifact,
                "missing_official_source_prediction_count",
            ),
            "run_metadata": _prediction_source_url_audit_verify_run_metadata(
                args,
                artifact_path,
                source_state={
                    "artifact": str(artifact_path),
                    "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    **source_state,
                },
            ),
        },
    )


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _prediction_source_url_audit_source_state(audit: Any) -> dict[str, Any]:
    source_state = {
        "eval_report_path": audit.eval_report_path,
        "eval_report_sha256": audit.eval_report_sha256,
        "gap_count": audit.gap_count,
        "missing_url_sourced_prediction_count": (audit.missing_url_sourced_prediction_count),
        "missing_official_source_prediction_count": (
            audit.missing_official_source_prediction_count
        ),
    }
    jurisdiction_count = _audit_int_attr(audit, "jurisdiction_count")
    implemented_jurisdiction_count = _audit_int_attr(audit, "implemented_jurisdiction_count")
    portable_jurisdiction_count = _audit_int_attr(audit, "portable_jurisdiction_count")
    portable_sample_missing_body_id_count = _audit_int_attr(
        audit,
        "portable_sample_missing_body_id_count",
    )
    portable_sample_missing_session_id_count = _audit_int_attr(
        audit,
        "portable_sample_missing_session_id_count",
    )
    legislative_body_count = _audit_int_attr(audit, "legislative_body_count")
    legislative_session_count = _audit_int_attr(audit, "legislative_session_count")
    jurisdiction_ids = _audit_sorted_string_attr(audit, "jurisdiction_ids")
    implemented_jurisdiction_ids = _audit_sorted_string_attr(
        audit,
        "implemented_jurisdiction_ids",
    )
    portable_jurisdiction_ids = _audit_sorted_string_attr(audit, "portable_jurisdiction_ids")
    legislative_body_ids = _audit_sorted_string_attr(audit, "legislative_body_ids")
    legislative_session_ids = _audit_sorted_string_attr(audit, "legislative_session_ids")
    if jurisdiction_count:
        source_state["jurisdiction_count"] = jurisdiction_count
        source_state["jurisdiction_ids"] = jurisdiction_ids
    if implemented_jurisdiction_count:
        source_state["implemented_jurisdiction_count"] = implemented_jurisdiction_count
        source_state["implemented_jurisdiction_ids"] = implemented_jurisdiction_ids
    if portable_jurisdiction_count:
        source_state["portable_jurisdiction_count"] = portable_jurisdiction_count
        source_state["portable_jurisdiction_ids"] = portable_jurisdiction_ids
    if portable_jurisdiction_count or portable_sample_missing_body_id_count:
        source_state["portable_sample_missing_body_id_count"] = (
            portable_sample_missing_body_id_count
        )
    if portable_jurisdiction_count or portable_sample_missing_session_id_count:
        source_state["portable_sample_missing_session_id_count"] = (
            portable_sample_missing_session_id_count
        )
    if legislative_body_count:
        source_state["legislative_body_count"] = legislative_body_count
        source_state["legislative_body_ids"] = legislative_body_ids
    if legislative_session_count:
        source_state["legislative_session_count"] = legislative_session_count
        source_state["legislative_session_ids"] = legislative_session_ids
    source_family_ids = list(getattr(audit, "source_family_ids", []) or [])
    if source_family_ids:
        source_state["source_family_count"] = len(source_family_ids)
        source_state["source_family_ids"] = source_family_ids
    source_state.update(_audit_sample_identity(audit.gaps))
    return source_state


def _prediction_source_url_audit_quality_gate_source_state(
    quality_gate_failures: Any,
) -> dict[str, Any]:
    if not isinstance(quality_gate_failures, list) or not quality_gate_failures:
        return {}
    if any(
        not isinstance(item, str) or not item or item.strip() != item
        for item in quality_gate_failures
    ):
        return {}
    return {
        "quality_gate_failure_count": len(quality_gate_failures),
        "quality_gate_failures": sorted(quality_gate_failures),
    }


def _audit_int_attr(audit: Any, name: str) -> int:
    value = getattr(audit, name, 0)
    return value if type(value) is int and value >= 0 else 0


def _audit_sorted_string_attr(audit: Any, name: str) -> list[str]:
    value = getattr(audit, name, [])
    return sorted(item for item in value if isinstance(item, str) and item)


def _validate_prediction_source_url_audit_artifact(
    artifact: dict[str, Any],
    issues: list[str],
) -> None:
    if artifact.get("command") != "prediction-source-url-audit":
        issues.append("artifact command must be prediction-source-url-audit")
    if type(artifact.get("ok")) is not bool:
        issues.append("ok must be a boolean")
    gaps = artifact.get("gaps")
    if not isinstance(gaps, list):
        issues.append("gaps must be a list")
        gaps = []
    _validate_prediction_source_url_audit_quality_gate_failures(artifact, issues)
    checked = artifact.get("checked")
    if not _is_non_negative_int(checked):
        issues.append("checked must be a non-negative integer")
    gap_count = artifact.get("gap_count")
    if not _is_non_negative_int(gap_count):
        issues.append("gap_count must be a non-negative integer")
    elif gap_count != len(gaps):
        issues.append(f"gap_count mismatch: expected {len(gaps)}, got {gap_count}")
    for index, gap in enumerate(gaps):
        _validate_prediction_source_url_audit_gap(index, gap, issues)
    _validate_prediction_source_url_audit_scope_counts(artifact, gaps, issues)
    missing_url_count = artifact.get("missing_url_sourced_prediction_count")
    if not _is_non_negative_int(missing_url_count):
        issues.append("missing_url_sourced_prediction_count must be non-negative")
    else:
        actual_missing_url_count = sum(
            _plain_non_negative_int_or_zero(gap.get("missing_url_sourced_prediction_count"))
            for gap in gaps
            if isinstance(gap, dict)
        )
        if missing_url_count != actual_missing_url_count:
            issues.append(
                "missing_url_sourced_prediction_count mismatch: "
                f"expected {actual_missing_url_count}, got {missing_url_count}"
            )
    missing_official_count = artifact.get("missing_official_source_prediction_count")
    if not _is_non_negative_int(missing_official_count):
        issues.append("missing_official_source_prediction_count must be non-negative")
    else:
        actual_missing_official_count = sum(
            _plain_non_negative_int_or_zero(gap.get("missing_official_source_prediction_count"))
            for gap in gaps
            if isinstance(gap, dict)
        )
        if missing_official_count != actual_missing_official_count:
            issues.append(
                "missing_official_source_prediction_count mismatch: "
                f"expected {actual_missing_official_count}, got {missing_official_count}"
            )
    eval_report_path = artifact.get("eval_report_path")
    if not eval_report_path:
        issues.append("eval_report_path missing")
    elif not isinstance(eval_report_path, str):
        issues.append("eval_report_path invalid")
    eval_report_sha256 = artifact.get("eval_report_sha256")
    if not eval_report_sha256:
        issues.append("eval_report_sha256 missing")
    elif not isinstance(eval_report_sha256, str) or not _is_sha256_hex(eval_report_sha256):
        issues.append("eval_report_sha256 invalid")


def _validate_prediction_source_url_audit_scope_counts(
    artifact: dict[str, Any],
    gaps: list[Any],
    issues: list[str],
) -> None:
    for key in (
        "jurisdiction_count",
        "implemented_jurisdiction_count",
        "portable_jurisdiction_count",
        "portable_sample_missing_body_id_count",
        "portable_sample_missing_session_id_count",
        "legislative_body_count",
        "legislative_session_count",
        "source_family_count",
    ):
        if key not in artifact:
            continue
        value = artifact.get(key)
        if not _is_non_negative_int(value):
            issues.append(f"{key} must be a non-negative integer")
    for count_key, ids_key in (
        ("jurisdiction_count", "jurisdiction_ids"),
        ("implemented_jurisdiction_count", "implemented_jurisdiction_ids"),
        ("portable_jurisdiction_count", "portable_jurisdiction_ids"),
        ("legislative_body_count", "legislative_body_ids"),
        ("legislative_session_count", "legislative_session_ids"),
        ("source_family_count", "source_family_ids"),
    ):
        count_value = artifact.get(count_key)
        if (
            count_key in artifact
            and _is_non_negative_int(count_value)
            and count_value
            and ids_key not in artifact
        ):
            issues.append(f"{ids_key} missing for {count_key}")
    for count_key, ids_key in (
        ("jurisdiction_count", "jurisdiction_ids"),
        ("implemented_jurisdiction_count", "implemented_jurisdiction_ids"),
        ("portable_jurisdiction_count", "portable_jurisdiction_ids"),
        ("legislative_body_count", "legislative_body_ids"),
        ("legislative_session_count", "legislative_session_ids"),
        ("source_family_count", "source_family_ids"),
    ):
        key = ids_key
        if key not in artifact:
            continue
        value = artifact.get(key)
        if not _is_sorted_string_list(value):
            issues.append(f"{key} must be a sorted string list")
        elif key == "source_family_ids" and not _is_source_family_id_list(value):
            issues.append("source_family_ids must be normalized source family ids")
        else:
            count_value = artifact.get(count_key)
            if not _is_non_negative_int(count_value):
                continue
            ids = _string_list(value)
            if count_value == len(ids):
                continue
            issues.append(f"{count_key} mismatch: expected {len(ids)}, got {count_value}")
    _validate_prediction_source_url_audit_portable_missing_counts(
        artifact,
        gaps,
        issues,
    )
    _validate_prediction_source_url_audit_gap_scope_fields(artifact, gaps, issues)
    _validate_prediction_source_url_audit_scope_sets(artifact, issues)


def _validate_prediction_source_url_audit_gap_scope_fields(
    artifact: dict[str, Any],
    gaps: list[Any],
    issues: list[str],
) -> None:
    (
        jurisdiction_count,
        implemented_jurisdiction_count,
        portable_jurisdiction_count,
        _portable_sample_missing_body_id_count,
        _portable_sample_missing_session_id_count,
        legislative_body_count,
        legislative_session_count,
        _jurisdiction_ids,
        _implemented_jurisdiction_ids,
        _portable_jurisdiction_ids,
        _legislative_body_ids,
        _legislative_session_ids,
    ) = _audit_sample_context(gaps)
    for count_key, ids_key, expected_count in (
        ("jurisdiction_count", "jurisdiction_ids", jurisdiction_count),
        (
            "implemented_jurisdiction_count",
            "implemented_jurisdiction_ids",
            implemented_jurisdiction_count,
        ),
        (
            "portable_jurisdiction_count",
            "portable_jurisdiction_ids",
            portable_jurisdiction_count,
        ),
        ("legislative_body_count", "legislative_body_ids", legislative_body_count),
        (
            "legislative_session_count",
            "legislative_session_ids",
            legislative_session_count,
        ),
    ):
        if not expected_count or count_key in artifact:
            continue
        issues.append(f"{count_key} missing")
        issues.append(f"{ids_key} missing for {count_key}")


def _validate_prediction_source_url_audit_portable_missing_counts(
    artifact: dict[str, Any],
    gaps: list[Any],
    issues: list[str],
) -> None:
    (
        _jurisdiction_count,
        _implemented_jurisdiction_count,
        _portable_jurisdiction_count,
        portable_sample_missing_body_id_count,
        portable_sample_missing_session_id_count,
        _legislative_body_count,
        _legislative_session_count,
        _jurisdiction_ids,
        _implemented_jurisdiction_ids,
        _portable_jurisdiction_ids,
        _legislative_body_ids,
        _legislative_session_ids,
    ) = _audit_sample_context(gaps)
    for key, expected in (
        ("portable_sample_missing_body_id_count", portable_sample_missing_body_id_count),
        (
            "portable_sample_missing_session_id_count",
            portable_sample_missing_session_id_count,
        ),
    ):
        if key not in artifact:
            continue
        value = artifact.get(key)
        if _is_non_negative_int(value) and value != expected:
            issues.append(f"{key} mismatch: expected {expected}, got {value}")


def _validate_prediction_source_url_audit_quality_gate_failures(
    artifact: dict[str, Any],
    issues: list[str],
) -> None:
    quality_gate_failures = artifact.get("quality_gate_failures")
    if quality_gate_failures is None:
        return
    if not isinstance(quality_gate_failures, list):
        issues.append("quality_gate_failures must be a list")
        return
    if artifact.get("ok") is True and quality_gate_failures:
        issues.append("ok mismatch: quality_gate_failures present")
    seen_failures: set[str] = set()
    for index, failure in enumerate(quality_gate_failures):
        if not isinstance(failure, str) or not failure:
            issues.append(f"quality_gate_failures[{index}] must be a non-empty string")
        elif failure not in _PREDICTION_SOURCE_URL_AUDIT_QUALITY_GATES:
            issues.append(f"quality_gate_failures[{index}] unknown: {failure}")
        elif failure in seen_failures:
            issues.append(f"quality_gate_failures[{index}] duplicate: {failure}")
        else:
            seen_failures.add(failure)


def _validate_prediction_source_url_audit_scope_sets(
    artifact: dict[str, Any],
    issues: list[str],
) -> None:
    jurisdiction_ids = artifact.get("jurisdiction_ids")
    implemented_ids = artifact.get("implemented_jurisdiction_ids")
    portable_ids = artifact.get("portable_jurisdiction_ids")
    if (
        _is_sorted_string_list(jurisdiction_ids)
        and _is_sorted_string_list(implemented_ids)
        and _is_sorted_string_list(portable_ids)
    ):
        jurisdiction_ids_list = _string_list(jurisdiction_ids)
        implemented_ids_list = _string_list(implemented_ids)
        portable_ids_list = _string_list(portable_ids)
        implemented_set = set(implemented_ids_list)
        portable_set = set(portable_ids_list)
        if implemented_set | portable_set != set(jurisdiction_ids_list):
            issues.append("jurisdiction status ids must cover jurisdiction_ids")
        if implemented_set & portable_set:
            issues.append("jurisdiction status ids must not overlap")
    body_ids = artifact.get("legislative_body_ids")
    if _is_sorted_string_list(jurisdiction_ids) and _is_sorted_string_list(body_ids):
        jurisdiction_set = set(_string_list(jurisdiction_ids))
        for body_id in _string_list(body_ids):
            parts = body_id.split(":")
            if len(parts) != 2 or parts[0] not in jurisdiction_set or not parts[1]:
                issues.append("legislative_body_ids must be jurisdiction scoped")
                break
    session_ids = artifact.get("legislative_session_ids")
    if (
        _is_sorted_string_list(jurisdiction_ids)
        and _is_sorted_string_list(body_ids)
        and _is_sorted_string_list(session_ids)
    ):
        jurisdiction_set = set(_string_list(jurisdiction_ids))
        body_set = set(_string_list(body_ids))
        for session_id in _string_list(session_ids):
            parts = session_id.split(":")
            if (
                len(parts) != 3
                or parts[0] not in jurisdiction_set
                or f"{parts[0]}:{parts[1]}" not in body_set
                or not parts[2]
            ):
                issues.append("legislative_session_ids must be body scoped")
                break


def _validate_prediction_source_url_audit_gap(
    index: int,
    gap: Any,
    issues: list[str],
) -> None:
    prefix = f"gaps[{index}]"
    if not isinstance(gap, dict):
        issues.append(f"{prefix} must be an object")
        return
    for key in ("split", "model_name", "signal_name"):
        if not isinstance(gap.get(key), str) or not gap.get(key):
            issues.append(f"{prefix}.{key} must be a non-empty string")
    _validate_prediction_source_url_audit_gap_samples(gap, prefix, issues)
    prediction_count = _gap_non_negative_int(gap, "prediction_count", prefix, issues)
    url_sourced_count = _gap_non_negative_int(
        gap,
        "url_sourced_prediction_count",
        prefix,
        issues,
    )
    missing_url_count = _gap_non_negative_int(
        gap,
        "missing_url_sourced_prediction_count",
        prefix,
        issues,
    )
    official_sourced_count = _gap_non_negative_int(
        gap,
        "official_source_sourced_prediction_count",
        prefix,
        issues,
    )
    missing_official_count = _gap_non_negative_int(
        gap,
        "missing_official_source_prediction_count",
        prefix,
        issues,
    )
    if (
        prediction_count is None
        or url_sourced_count is None
        or missing_url_count is None
        or official_sourced_count is None
        or missing_official_count is None
    ):
        return
    if url_sourced_count > prediction_count:
        issues.append(f"{prefix}.url_sourced_prediction_count cannot exceed prediction_count")
    if official_sourced_count > prediction_count:
        issues.append(
            f"{prefix}.official_source_sourced_prediction_count cannot exceed prediction_count"
        )
    expected_missing_url_count = max(0, prediction_count - url_sourced_count)
    if missing_url_count != expected_missing_url_count:
        issues.append(
            f"{prefix}.missing_url_sourced_prediction_count mismatch: "
            f"expected {expected_missing_url_count}, got {missing_url_count}"
        )
    expected_missing_official_count = max(0, prediction_count - official_sourced_count)
    if missing_official_count != expected_missing_official_count:
        issues.append(
            f"{prefix}.missing_official_source_prediction_count mismatch: "
            f"expected {expected_missing_official_count}, got {missing_official_count}"
        )
    _validate_prediction_source_url_audit_gap_rate(
        gap,
        "url_source_coverage_rate",
        numerator=url_sourced_count,
        denominator=prediction_count,
        prefix=prefix,
        issues=issues,
    )
    _validate_prediction_source_url_audit_gap_rate(
        gap,
        "official_source_coverage_rate",
        numerator=official_sourced_count,
        denominator=prediction_count,
        prefix=prefix,
        issues=issues,
    )


def _gap_non_negative_int(
    gap: dict[str, Any],
    key: str,
    prefix: str,
    issues: list[str],
) -> int | None:
    value = gap.get(key)
    if type(value) is not int or value < 0:
        issues.append(f"{prefix}.{key} must be a non-negative integer")
        return None
    return value


def _plain_non_negative_int_or_zero(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _validate_prediction_source_url_audit_gap_rate(
    gap: dict[str, Any],
    key: str,
    *,
    numerator: int,
    denominator: int,
    prefix: str,
    issues: list[str],
) -> None:
    if key not in gap:
        return
    value = gap.get(key)
    if type(value) is int:
        rate_value = float(value)
    elif type(value) is float:
        rate_value = value
    else:
        issues.append(f"{prefix}.{key} must be numeric when present")
        return
    if denominator == 0:
        issues.append(f"{prefix}.{key} must be omitted when prediction_count is zero")
        return
    expected = numerator / denominator
    if abs(rate_value - expected) > 1e-9:
        issues.append(f"{prefix}.{key} mismatch: expected {expected}, got {value}")


def _validate_prediction_source_url_audit_gap_samples(
    gap: dict[str, Any],
    prefix: str,
    issues: list[str],
) -> None:
    _validate_gap_sample_int_list(gap, "sample_vote_event_ids", prefix, issues)
    _validate_gap_sample_str_list(gap, "sample_bill_keys", prefix, issues)
    _validate_gap_sample_str_list(gap, "sample_member_bioguide_ids", prefix, issues)
    _validate_gap_sorted_str_list(gap, "source_family_ids", prefix, issues)
    sample_cases = gap.get("sample_cases")
    if sample_cases is None:
        return
    if not isinstance(sample_cases, list):
        issues.append(f"{prefix}.sample_cases must be a list")
        return
    expected_vote_event_ids: list[int] = []
    expected_bill_keys: list[str] = []
    expected_member_bioguide_ids: list[str] = []
    seen_sample_cases: set[tuple[object, ...]] = set()
    for index, sample_case in enumerate(sample_cases):
        sample_prefix = f"{prefix}.sample_cases[{index}]"
        if not isinstance(sample_case, dict):
            issues.append(f"{sample_prefix} must be an object")
            continue
        vote_event_id = sample_case.get("vote_event_id")
        if vote_event_id is not None and type(vote_event_id) is not int:
            issues.append(f"{sample_prefix}.vote_event_id must be an integer")
        elif type(vote_event_id) is int and vote_event_id <= 0:
            issues.append(f"{sample_prefix}.vote_event_id must be a positive integer")
        elif type(vote_event_id) is int and vote_event_id not in expected_vote_event_ids:
            expected_vote_event_ids.append(vote_event_id)
        for key in (
            "event_key",
            "jurisdiction_id",
            "legislative_body_id",
            "legislative_session_id",
        ):
            _validate_optional_non_empty_sample_case_string(sample_case, key, sample_prefix, issues)
        _validate_congress_sample_event_key(sample_case, sample_prefix, issues)
        bill_key = sample_case.get("bill_key")
        if bill_key is not None and not isinstance(bill_key, str):
            issues.append(f"{sample_prefix}.bill_key must be a string")
        elif isinstance(bill_key, str):
            if not bill_key.strip():
                issues.append(f"{sample_prefix}.bill_key must be a non-empty string")
            elif bill_key != bill_key.strip():
                issues.append(f"{sample_prefix}.bill_key must not have surrounding whitespace")
        bill_context_key = sample_case.get("bill_context_key")
        if bill_context_key is not None and not isinstance(bill_context_key, str):
            issues.append(f"{sample_prefix}.bill_context_key must be a string")
        elif isinstance(bill_context_key, str) and not bill_context_key.strip():
            issues.append(f"{sample_prefix}.bill_context_key must be a non-empty string")
        elif isinstance(bill_context_key, str) and bill_context_key != bill_context_key.strip():
            issues.append(f"{sample_prefix}.bill_context_key must not have surrounding whitespace")
        sample_bill_key = bill_context_key if isinstance(bill_context_key, str) else bill_key
        if isinstance(sample_bill_key, str) and sample_bill_key.strip():
            if sample_bill_key not in expected_bill_keys:
                expected_bill_keys.append(sample_bill_key)
        member_bioguide_id = sample_case.get("member_bioguide_id")
        if member_bioguide_id is not None and not isinstance(member_bioguide_id, str):
            issues.append(f"{sample_prefix}.member_bioguide_id must be a string")
        elif isinstance(member_bioguide_id, str):
            if not member_bioguide_id.strip():
                issues.append(f"{sample_prefix}.member_bioguide_id must be a non-empty string")
            elif member_bioguide_id != member_bioguide_id.strip():
                issues.append(
                    f"{sample_prefix}.member_bioguide_id must not have surrounding whitespace"
                )
            elif member_bioguide_id not in expected_member_bioguide_ids:
                expected_member_bioguide_ids.append(member_bioguide_id)
        case_key = (
            sample_case.get("jurisdiction_id"),
            sample_case.get("legislative_body_id"),
            sample_case.get("legislative_session_id"),
            sample_case.get("event_key"),
            sample_case.get("vote_event_id"),
            sample_case.get("bill_key"),
            sample_case.get("bill_context_key"),
            sample_case.get("member_bioguide_id"),
        )
        if case_key in seen_sample_cases:
            issues.append(f"{sample_prefix} duplicates an earlier sample case")
        else:
            seen_sample_cases.add(case_key)
    _validate_gap_sample_index_matches_cases(
        gap,
        "sample_vote_event_ids",
        expected_vote_event_ids,
        prefix,
        issues,
    )
    _validate_gap_sample_index_matches_cases(
        gap,
        "sample_bill_keys",
        expected_bill_keys,
        prefix,
        issues,
    )
    _validate_gap_sample_index_matches_cases(
        gap,
        "sample_member_bioguide_ids",
        expected_member_bioguide_ids,
        prefix,
        issues,
    )


def _validate_optional_non_empty_sample_case_string(
    sample_case: dict[str, Any],
    key: str,
    sample_prefix: str,
    issues: list[str],
) -> None:
    value = sample_case.get(key)
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(f"{sample_prefix}.{key} must be a string")
    elif not value.strip():
        issues.append(f"{sample_prefix}.{key} must be a non-empty string")
    elif value != value.strip():
        issues.append(f"{sample_prefix}.{key} must not have surrounding whitespace")


def _validate_congress_sample_event_key(
    sample_case: dict[str, Any],
    sample_prefix: str,
    issues: list[str],
) -> None:
    if sample_case.get("jurisdiction_id") != "us_congress":
        return
    event_key = sample_case.get("event_key")
    if event_key is None:
        return
    if not isinstance(event_key, str) or event_key != event_key.strip():
        return
    parts = event_key.split("-")
    if len(parts) != 4 or parts[0] not in {"house", "senate"}:
        issues.append(f"{sample_prefix}.event_key must be chamber-congress-session-roll")
        return
    if not all(part.isdigit() and int(part) > 0 for part in parts[1:]):
        issues.append(f"{sample_prefix}.event_key must use positive numeric Congress vote ids")


def _validate_gap_sample_index_matches_cases(
    gap: dict[str, Any],
    key: str,
    expected: list[int] | list[str],
    prefix: str,
    issues: list[str],
) -> None:
    values = gap.get(key)
    if key == "sample_vote_event_ids" and isinstance(values, list):
        if any(type(value) is not int or value <= 0 for value in values):
            return
    if isinstance(values, list) and values != expected:
        issues.append(f"{prefix}.{key} mismatch")


def _validate_gap_sample_int_list(
    gap: dict[str, Any],
    key: str,
    prefix: str,
    issues: list[str],
) -> None:
    values = gap.get(key)
    if values is None:
        return
    if not isinstance(values, list):
        issues.append(f"{prefix}.{key} must be a list")
        return
    for index, value in enumerate(values):
        if type(value) is not int:
            issues.append(f"{prefix}.{key}[{index}] must be an integer")
        elif value <= 0:
            issues.append(f"{prefix}.{key}[{index}] must be a positive integer")


def _validate_gap_sample_str_list(
    gap: dict[str, Any],
    key: str,
    prefix: str,
    issues: list[str],
) -> None:
    values = gap.get(key)
    if values is None:
        return
    if not isinstance(values, list):
        issues.append(f"{prefix}.{key} must be a list")
        return
    for index, value in enumerate(values):
        if not isinstance(value, str):
            issues.append(f"{prefix}.{key}[{index}] must be a string")
        elif not value.strip():
            issues.append(f"{prefix}.{key}[{index}] must be a non-empty string")
        elif value != value.strip():
            issues.append(f"{prefix}.{key}[{index}] must not have surrounding whitespace")


def _validate_gap_sorted_str_list(
    gap: dict[str, Any],
    key: str,
    prefix: str,
    issues: list[str],
) -> None:
    values = gap.get(key)
    if values is None:
        return
    if not _is_sorted_string_list(values):
        issues.append(f"{prefix}.{key} must be a sorted unique string list")
    elif key == "source_family_ids" and not _is_source_family_id_list(values):
        issues.append(f"{prefix}.{key} must contain normalized source family ids")


def _prediction_source_url_audit_artifact_source_state(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    source_state = {
        "eval_report_path": str(artifact.get("eval_report_path", "")),
        "eval_report_sha256": artifact.get("eval_report_sha256"),
        "gap_count": _artifact_int_or_zero(artifact, "gap_count"),
        "missing_url_sourced_prediction_count": _artifact_int_or_zero(
            artifact,
            "missing_url_sourced_prediction_count",
        ),
        "missing_official_source_prediction_count": _artifact_int_or_zero(
            artifact,
            "missing_official_source_prediction_count",
        ),
    }
    gaps = artifact.get("gaps")
    jurisdiction_count = _artifact_int_or_zero(artifact, "jurisdiction_count")
    implemented_jurisdiction_count = _artifact_int_or_zero(
        artifact,
        "implemented_jurisdiction_count",
    )
    portable_jurisdiction_count = _artifact_int_or_zero(
        artifact,
        "portable_jurisdiction_count",
    )
    portable_sample_missing_body_id_count = _artifact_int_or_zero(
        artifact,
        "portable_sample_missing_body_id_count",
    )
    portable_sample_missing_session_id_count = _artifact_int_or_zero(
        artifact,
        "portable_sample_missing_session_id_count",
    )
    legislative_body_count = _artifact_int_or_zero(artifact, "legislative_body_count")
    legislative_session_count = _artifact_int_or_zero(
        artifact,
        "legislative_session_count",
    )
    jurisdiction_ids = _artifact_sorted_string_list_or_empty(artifact, "jurisdiction_ids")
    implemented_jurisdiction_ids = _artifact_sorted_string_list_or_empty(
        artifact,
        "implemented_jurisdiction_ids",
    )
    portable_jurisdiction_ids = _artifact_sorted_string_list_or_empty(
        artifact,
        "portable_jurisdiction_ids",
    )
    legislative_body_ids = _artifact_sorted_string_list_or_empty(
        artifact,
        "legislative_body_ids",
    )
    legislative_session_ids = _artifact_sorted_string_list_or_empty(
        artifact,
        "legislative_session_ids",
    )
    if jurisdiction_count:
        source_state["jurisdiction_count"] = jurisdiction_count
        source_state["jurisdiction_ids"] = jurisdiction_ids
    if implemented_jurisdiction_count:
        source_state["implemented_jurisdiction_count"] = implemented_jurisdiction_count
        source_state["implemented_jurisdiction_ids"] = implemented_jurisdiction_ids
    if portable_jurisdiction_count:
        source_state["portable_jurisdiction_count"] = portable_jurisdiction_count
        source_state["portable_jurisdiction_ids"] = portable_jurisdiction_ids
    if portable_jurisdiction_count or portable_sample_missing_body_id_count:
        source_state["portable_sample_missing_body_id_count"] = (
            portable_sample_missing_body_id_count
        )
    if portable_jurisdiction_count or portable_sample_missing_session_id_count:
        source_state["portable_sample_missing_session_id_count"] = (
            portable_sample_missing_session_id_count
        )
    if legislative_body_count:
        source_state["legislative_body_count"] = legislative_body_count
        source_state["legislative_body_ids"] = legislative_body_ids
    if legislative_session_count:
        source_state["legislative_session_count"] = legislative_session_count
        source_state["legislative_session_ids"] = legislative_session_ids
    source_family_ids = artifact.get("source_family_ids")
    if _is_sorted_string_list(source_family_ids) and source_family_ids:
        source_state["source_family_count"] = len(source_family_ids)
        source_state["source_family_ids"] = source_family_ids
    source_state.update(_audit_sample_identity(gaps if isinstance(gaps, list) else []))
    source_state.update(
        _prediction_source_url_audit_quality_gate_source_state(
            artifact.get("quality_gate_failures")
        )
    )
    return source_state


def _audit_sample_identity(gaps: Any) -> dict[str, Any]:
    sample_case_count = 0
    vote_event_ids: list[int] = []
    bill_keys: list[str] = []
    member_bioguide_ids: list[str] = []
    source_family_ids: set[str] = set()
    jurisdiction_ids: set[str] = set()
    legislative_body_ids: set[str] = set()
    legislative_session_ids: set[str] = set()
    if not isinstance(gaps, list | tuple):
        return {}
    for gap in gaps:
        gap_source_family_ids = _gap_source_family_ids(gap)
        source_family_ids.update(gap_source_family_ids)
        sample_cases = _gap_sample_cases(gap)
        if not isinstance(sample_cases, list | tuple):
            continue
        for sample_case in sample_cases:
            if not isinstance(sample_case, dict):
                continue
            sample_case_count += 1
            vote_event_id = sample_case.get("vote_event_id")
            if (
                type(vote_event_id) is int
                and vote_event_id > 0
                and vote_event_id not in vote_event_ids
            ):
                vote_event_ids.append(vote_event_id)
            bill_key = sample_case.get("bill_context_key") or sample_case.get("bill_key")
            if isinstance(bill_key, str) and bill_key not in bill_keys:
                bill_keys.append(bill_key)
            member_bioguide_id = sample_case.get("member_bioguide_id")
            if (
                isinstance(member_bioguide_id, str)
                and member_bioguide_id not in member_bioguide_ids
            ):
                member_bioguide_ids.append(member_bioguide_id)
            jurisdiction_id = sample_case.get("jurisdiction_id")
            if not isinstance(jurisdiction_id, str) or not jurisdiction_id.strip():
                continue
            jurisdiction_id = jurisdiction_id.strip()
            jurisdiction_ids.add(jurisdiction_id)
            legislative_body_id = sample_case.get("legislative_body_id")
            if not isinstance(legislative_body_id, str) or not legislative_body_id.strip():
                continue
            legislative_body_id = legislative_body_id.strip()
            legislative_body_ids.add(f"{jurisdiction_id}:{legislative_body_id}")
            legislative_session_id = _audit_sample_case_legislative_session_id(sample_case)
            if legislative_session_id is not None:
                legislative_session_ids.add(
                    f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id}"
                )
    if not sample_case_count:
        return {}
    return {
        "sample_case_count": sample_case_count,
        "sample_vote_event_ids": vote_event_ids,
        "sample_bill_keys": sorted(bill_keys),
        "sample_member_bioguide_ids": sorted(member_bioguide_ids),
        "sample_source_family_ids": sorted(source_family_ids),
        "sample_jurisdiction_ids": sorted(jurisdiction_ids),
        "sample_legislative_body_ids": sorted(legislative_body_ids),
        "sample_legislative_session_ids": sorted(legislative_session_ids),
    }


def _audit_sample_context(
    gaps: Any,
) -> tuple[
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
]:
    jurisdictions: set[str] = set()
    legislative_bodies: set[str] = set()
    legislative_sessions: set[str] = set()
    portable_sample_missing_body_id_count = 0
    portable_sample_missing_session_id_count = 0
    if not isinstance(gaps, list | tuple):
        return 0, 0, 0, 0, 0, 0, 0, [], [], [], [], []
    for gap in gaps:
        sample_cases = _gap_sample_cases(gap)
        for sample_case in sample_cases:
            if not isinstance(sample_case, dict):
                continue
            jurisdiction_id = sample_case.get("jurisdiction_id")
            if not isinstance(jurisdiction_id, str) or not jurisdiction_id.strip():
                continue
            jurisdiction_id = jurisdiction_id.strip()
            jurisdictions.add(jurisdiction_id)
            legislative_body_id = sample_case.get("legislative_body_id")
            if not isinstance(legislative_body_id, str) or not legislative_body_id.strip():
                if jurisdiction_id != "us_congress":
                    portable_sample_missing_body_id_count += 1
                continue
            legislative_body_id = legislative_body_id.strip()
            legislative_bodies.add(f"{jurisdiction_id}:{legislative_body_id}")
            legislative_session_id = _audit_sample_case_legislative_session_id(sample_case)
            if legislative_session_id is not None:
                legislative_sessions.add(
                    f"{jurisdiction_id}:{legislative_body_id}:{legislative_session_id}"
                )
            elif jurisdiction_id != "us_congress":
                portable_sample_missing_session_id_count += 1
    implemented_jurisdictions = {
        jurisdiction for jurisdiction in jurisdictions if jurisdiction == "us_congress"
    }
    portable_jurisdictions = jurisdictions - implemented_jurisdictions
    jurisdiction_ids = sorted(jurisdictions)
    implemented_jurisdiction_ids = sorted(implemented_jurisdictions)
    portable_jurisdiction_ids = sorted(portable_jurisdictions)
    legislative_body_ids = sorted(legislative_bodies)
    legislative_session_ids = sorted(legislative_sessions)
    return (
        len(jurisdiction_ids),
        len(implemented_jurisdiction_ids),
        len(portable_jurisdiction_ids),
        portable_sample_missing_body_id_count,
        portable_sample_missing_session_id_count,
        len(legislative_body_ids),
        len(legislative_session_ids),
        jurisdiction_ids,
        implemented_jurisdiction_ids,
        portable_jurisdiction_ids,
        legislative_body_ids,
        legislative_session_ids,
    )


def _gap_source_family_ids(gap: Any) -> list[str]:
    if isinstance(gap, dict):
        source_family_ids = gap.get("source_family_ids")
    else:
        source_family_ids = getattr(gap, "source_family_ids", [])
    if not isinstance(source_family_ids, list | tuple):
        return []
    return sorted(
        {
            source_family_id
            for source_family_id in source_family_ids
            if isinstance(source_family_id, str) and source_family_id.strip() == source_family_id
        }
    )


def _audit_sample_case_legislative_session_id(sample_case: dict[str, Any]) -> str | None:
    legislative_session_id = sample_case.get("legislative_session_id")
    if isinstance(legislative_session_id, str) and legislative_session_id.strip():
        return legislative_session_id.strip()
    jurisdiction_id = sample_case.get("jurisdiction_id")
    event_key = sample_case.get("event_key")
    if jurisdiction_id == "us_congress" and isinstance(event_key, str):
        event_key_parts = event_key.split("-")
        if (
            len(event_key_parts) >= 4
            and event_key_parts[1].isdigit()
            and event_key_parts[2].isdigit()
        ):
            return f"congress_{event_key_parts[1]}_session_{event_key_parts[2]}"
    return None


def _gap_sample_cases(gap: Any) -> Any:
    if isinstance(gap, dict):
        return gap.get("sample_cases", [])
    return getattr(gap, "sample_cases", [])


def _artifact_int_or_zero(artifact: dict[str, Any], key: str) -> int:
    value = artifact.get(key)
    return value if type(value) is int and value >= 0 else 0


def _artifact_sorted_string_list_or_empty(
    artifact: dict[str, Any],
    key: str,
) -> list[str]:
    value = artifact.get(key)
    return _string_list(value) if _is_sorted_string_list(value) else []


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _is_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _validate_prediction_source_url_audit_run_metadata(
    *,
    run_metadata: dict[Any, Any],
    quality_gate_failures: Any,
    expected_source_state: dict[str, Any],
    issues: list[str],
    require_source_state: bool = False,
) -> None:
    if (require_source_state or "command" in run_metadata) and run_metadata.get(
        "command"
    ) != "prediction-source-url-audit":
        issues.append("run_metadata mismatch: command")
    artifact_sha256 = run_metadata.get("artifact_sha256")
    expected_artifact_sha256 = expected_source_state.get("eval_report_sha256")
    if artifact_sha256 is not None:
        if not isinstance(artifact_sha256, str) or not _is_sha256_hex(artifact_sha256):
            issues.append("run_metadata artifact_sha256 invalid")
        elif artifact_sha256 != expected_artifact_sha256:
            issues.append("run_metadata artifact_sha256 mismatch")
    source_artifact_sha256 = run_metadata.get("source_artifact_sha256")
    if source_artifact_sha256 is None:
        if require_source_state:
            issues.append("run_metadata source_artifact_sha256 missing: eval_report")
    elif not isinstance(source_artifact_sha256, dict):
        issues.append("run_metadata source_artifact_sha256 must be an object")
    else:
        eval_report_sha256 = source_artifact_sha256.get("eval_report")
        if not isinstance(eval_report_sha256, str) or not _is_sha256_hex(eval_report_sha256):
            issues.append("run_metadata source_artifact_sha256 invalid: eval_report")
        elif eval_report_sha256 != expected_artifact_sha256:
            issues.append("run_metadata source_artifact_sha256 mismatch: eval_report")
    verification_flags = run_metadata.get("verification_flags")
    if verification_flags is None:
        if require_source_state:
            issues.append("run_metadata verification_flags missing")
    elif not isinstance(verification_flags, dict):
        issues.append("run_metadata verification_flags must be an object")
    elif "fail_on_gaps" not in verification_flags and require_source_state:
        issues.append("run_metadata verification_flags missing: fail_on_gaps")
    elif "fail_on_gaps" in verification_flags and not isinstance(
        verification_flags.get("fail_on_gaps"),
        bool,
    ):
        issues.append("run_metadata verification_flags invalid type: fail_on_gaps")
    elif (
        verification_flags.get("fail_on_gaps") is False
        and isinstance(quality_gate_failures, list)
        and quality_gate_failures
    ):
        issues.append("run_metadata verification_flags mismatch: fail_on_gaps")
    elif verification_flags.get("fail_on_gaps") is True:
        expected_failures = _prediction_source_url_audit_expected_gap_failures(
            expected_source_state
        )
        if quality_gate_failures != expected_failures:
            issues.append(
                "run_metadata verification_flags mismatch: fail_on_gaps quality_gate_failures"
            )
    source_state = run_metadata.get("source_state")
    if source_state is None:
        if require_source_state:
            issues.append("run_metadata source_state missing")
        return
    if not isinstance(source_state, dict):
        issues.append("run_metadata source_state must be an object")
        return
    for key in sorted(set(source_state) - set(expected_source_state)):
        issues.append(f"run_metadata source_state unexpected: {key}")
    for key, expected_value in expected_source_state.items():
        actual_value = source_state.get(key)
        if type(expected_value) is int and not _is_non_negative_int(actual_value):
            issues.append(f"run_metadata source_state invalid type: {key}")
            continue
        if key == "sample_vote_event_ids":
            if not _is_non_negative_int_list(actual_value):
                issues.append(f"run_metadata source_state invalid type: {key}")
                continue
        elif key in {"sample_bill_keys", "sample_member_bioguide_ids"}:
            if not _is_sorted_string_list(actual_value):
                issues.append(f"run_metadata source_state invalid type: {key}")
                continue
        elif isinstance(expected_value, list):
            if not _is_sorted_string_list(actual_value):
                issues.append(f"run_metadata source_state invalid type: {key}")
                continue
        if key == "eval_report_sha256" and (
            not isinstance(actual_value, str) or not _is_sha256_hex(actual_value)
        ):
            issues.append(f"run_metadata source_state invalid hash: {key}")
            continue
        if isinstance(expected_value, str) and not isinstance(actual_value, str):
            issues.append(f"run_metadata source_state invalid type: {key}")
            continue
        if actual_value != expected_value:
            issues.append(f"run_metadata source_state mismatch: {key}")


def _prediction_source_url_audit_expected_gap_failures(
    source_state: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if _plain_non_negative_int_or_zero(source_state.get("missing_url_sourced_prediction_count")):
        failures.append("feature_source_url_gaps")
    if _plain_non_negative_int_or_zero(
        source_state.get("missing_official_source_prediction_count")
    ):
        failures.append("feature_source_official_url_gaps")
    if _plain_non_negative_int_or_zero(source_state.get("portable_sample_missing_body_id_count")):
        failures.append("portable_sample_missing_body_id_gaps")
    if _plain_non_negative_int_or_zero(
        source_state.get("portable_sample_missing_session_id_count")
    ):
        failures.append("portable_sample_missing_session_id_gaps")
    return failures


def _prediction_source_url_audit_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-source-url-audit",
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_no_gaps": bool(getattr(args, "require_no_gaps", False)),
            "require_no_official_source_gaps": bool(
                getattr(args, "require_no_official_source_gaps", False)
            ),
            "require_no_portable_context_gaps": bool(
                getattr(args, "require_no_portable_context_gaps", False)
            ),
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _is_sorted_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() == item and item for item in value)
        and len(set(value)) == len(value)
        and value == sorted(value)
    )


def _is_source_family_id_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None for item in value
    )


def _is_non_negative_int_list(value: object) -> bool:
    return isinstance(value, list) and all(type(item) is int and item > 0 for item in value)
