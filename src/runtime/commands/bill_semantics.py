"""Bill-semantics materialization, plan, and verification commands."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, cast

from src.core.files import sha256_file
from src.evidence.source_anchor_policy import is_official_source_url
from src.prediction.llm_semantics import (
    BillSemanticIndexPayload,
    OpenAIBillSemanticExtractor,
    bill_semantic_input_from_row,
    load_bill_semantic_payloads,
    materialize_bill_semantics,
)
from src.query.published_rows import fetch_bill_semantic_input_rows
from src.runtime.app import build_runtime
from src.runtime.bill_semantics_cache import (
    bill_semantics_index_object as _bill_semantics_index_object,
    bill_semantics_index_payload as _bill_semantics_index_payload,
    bill_semantics_index_run_metadata_failures as _bill_semantics_index_run_metadata_failures,
)
from src.runtime.commands._shared import (
    _attach_optional_verification_output,
    _command_issue_result,
    _is_non_negative_plain_int,
    _is_optional_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _plain_int_or_zero,
    _required_string_list,
    _string_list,
    _write_json_artifact,
)
from src.runtime.commands.core import (
    _date_from_iso_string,
    _is_official_plan_source_anchor,
    _is_present_invalid_iso_date,
    _string_list_from_plan,
)
from src.runtime.context import open_connection


def _handle_materialize_bill_semantics(args: Any) -> dict[str, Any]:
    if not _is_optional_non_negative_plain_int(getattr(args, "limit", None)):
        return _command_issue_result(
            "materialize-bill-semantics",
            ["limit must be a non-negative integer"],
        )

    runtime = build_runtime()
    conn = open_connection(runtime.context)
    targeting_requested = _materialize_bill_semantic_targeting_requested(args)
    target_bill_keys = _materialize_bill_semantic_target_keys(args)
    unsupported_target_bill_keys = _unsupported_bill_semantic_target_keys(target_bill_keys)
    feature_cutoff = getattr(args, "feature_cutoff", None)
    bill_rows = fetch_bill_semantic_input_rows(conn, feature_cutoff=feature_cutoff)
    matched_bill_keys: list[str] = []
    if targeting_requested:
        bill_rows_by_key = [(_materialize_bill_semantic_row_key(row), row) for row in bill_rows]
        target_key_set = set(target_bill_keys)
        matched_rows_by_key = [
            (bill_key, row)
            for bill_key, row in bill_rows_by_key
            if bill_key is not None and bill_key in target_key_set
        ]
        bill_rows = [row for _, row in matched_rows_by_key]
        matched_bill_keys = [bill_key for bill_key, _ in matched_rows_by_key]
    unmatched_bill_keys = sorted(set(target_bill_keys) - set(matched_bill_keys))
    quality_gate_failures: list[str] = []
    if getattr(args, "fail_on_unmatched_targets", False) and unmatched_bill_keys:
        quality_gate_failures.append("unmatched_target_bill_keys")
    if getattr(args, "dry_run", False):
        selected_bill_rows = bill_rows[: args.limit] if args.limit is not None else bill_rows
        plan_payload = _bill_semantic_materialization_plan(
            args=args,
            selected_bill_rows=selected_bill_rows,
            target_bill_keys=target_bill_keys,
            matched_bill_keys=matched_bill_keys,
        )
        plan_output = (
            Path(args.plan_output) if getattr(args, "plan_output", None) is not None else None
        )
        plan_output_sha256: str | None = None
        if plan_output is not None:
            plan_output_sha256 = _write_json_artifact(plan_output, plan_payload)
        return _attach_materialize_bill_semantics_summary_output(
            args,
            {
                "ok": not quality_gate_failures,
                "command": "materialize-bill-semantics",
                "dry_run": True,
                "quality_gate_failures": quality_gate_failures,
                "output_root": str(Path(args.output_root)),
                "model": args.model,
                "requested_count": len(selected_bill_rows),
                "written_count": 0,
                "cached_count": 0,
                "target_bill_keys": target_bill_keys,
                "matched_bill_keys": sorted(set(matched_bill_keys)),
                "unmatched_bill_keys": unmatched_bill_keys,
                "unsupported_target_bill_keys": unsupported_target_bill_keys,
                "plan_output": str(plan_output) if plan_output is not None else None,
                "plan_output_sha256": plan_output_sha256,
            },
            index_sha256=None,
        )
    if quality_gate_failures:
        return _attach_materialize_bill_semantics_summary_output(
            args,
            {
                "ok": False,
                "command": "materialize-bill-semantics",
                "dry_run": False,
                "quality_gate_failures": quality_gate_failures,
                "output_root": str(Path(args.output_root)),
                "model": args.model,
                "requested_count": 0,
                "written_count": 0,
                "cached_count": 0,
                "target_bill_keys": target_bill_keys,
                "matched_bill_keys": sorted(set(matched_bill_keys)),
                "unmatched_bill_keys": unmatched_bill_keys,
                "unsupported_target_bill_keys": unsupported_target_bill_keys,
                "index_path": None,
                "index_sha256": None,
            },
            index_sha256=None,
        )
    extractor = (
        OpenAIBillSemanticExtractor.from_env(model=args.model)
        if bill_rows
        else cast(OpenAIBillSemanticExtractor, _NoopBillSemanticExtractor())
    )
    result = materialize_bill_semantics(
        bill_rows=bill_rows,
        output_root=Path(args.output_root),
        extractor=extractor,
        limit=args.limit,
        overwrite=args.overwrite,
    )
    index_sha256 = sha256_file(result.index_path)
    return _attach_materialize_bill_semantics_summary_output(
        args,
        {
            "ok": True,
            "command": "materialize-bill-semantics",
            "dry_run": False,
            "quality_gate_failures": [],
            "output_root": str(result.output_root),
            "model": args.model,
            "requested_count": result.requested_count,
            "written_count": result.written_count,
            "cached_count": result.cached_count,
            "target_bill_keys": target_bill_keys,
            "matched_bill_keys": sorted(set(matched_bill_keys)),
            "unmatched_bill_keys": unmatched_bill_keys,
            "unsupported_target_bill_keys": unsupported_target_bill_keys,
            "index_path": str(result.index_path),
            "index_sha256": index_sha256,
        },
        index_sha256=index_sha256,
    )


def _attach_materialize_bill_semantics_summary_output(
    args: Any,
    summary: dict[str, Any],
    *,
    index_sha256: str | None,
) -> dict[str, Any]:
    source_report_metadata = _materialize_bill_semantics_source_report_metadata(args)
    summary["run_metadata"] = {
        "command": "materialize-bill-semantics",
        "dry_run": bool(summary.get("dry_run")),
        "model": str(getattr(args, "model", "")),
        "output_root": str(Path(args.output_root)),
        "feature_cutoff": (
            args.feature_cutoff.isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "limit": getattr(args, "limit", None),
        "overwrite": bool(getattr(args, "overwrite", False)),
        "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
        "target_bill_keys": list(summary.get("target_bill_keys", [])),
        "matched_bill_keys": list(summary.get("matched_bill_keys", [])),
        "unmatched_bill_keys": list(summary.get("unmatched_bill_keys", [])),
        "index_sha256": index_sha256,
        "source_state": _materialize_bill_semantics_source_state(
            args=args,
            summary=summary,
            index_sha256=index_sha256,
            source_report_metadata=source_report_metadata,
        ),
    }
    if source_report_metadata:
        summary["run_metadata"]["source_report"] = source_report_metadata
    summary_output_arg = getattr(args, "summary_output", None)
    if summary_output_arg is None:
        return summary
    summary_output = Path(summary_output_arg)
    summary_output_sha256 = _write_json_artifact(summary_output, summary)
    summary["summary_output"] = str(summary_output)
    summary["summary_output_sha256"] = summary_output_sha256
    return summary


def _materialize_bill_semantics_source_state(
    *,
    args: Any,
    summary: dict[str, Any],
    index_sha256: str | None,
    source_report_metadata: dict[str, Any],
) -> dict[str, Any]:
    source_state: dict[str, Any] = {
        "dry_run": bool(summary.get("dry_run")),
        "model": str(getattr(args, "model", "")),
        "output_root": str(Path(args.output_root)),
        "feature_cutoff": (
            args.feature_cutoff.isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "requested_count": _plain_int_or_zero(summary.get("requested_count")),
        "written_count": _plain_int_or_zero(summary.get("written_count")),
        "cached_count": _plain_int_or_zero(summary.get("cached_count")),
        "target_bill_count": len(list(summary.get("target_bill_keys", []))),
        "matched_bill_count": len(list(summary.get("matched_bill_keys", []))),
        "unmatched_bill_count": len(list(summary.get("unmatched_bill_keys", []))),
        "index_path": summary.get("index_path"),
        "index_sha256": index_sha256,
    }
    plan_output = summary.get("plan_output")
    if plan_output is not None:
        source_state["plan_output"] = str(plan_output)
    plan_output_sha256 = summary.get("plan_output_sha256")
    if plan_output_sha256 is not None:
        source_state["plan_output_sha256"] = str(plan_output_sha256)
    unsupported_target_bill_keys = _string_list(summary.get("unsupported_target_bill_keys"))
    if unsupported_target_bill_keys:
        source_state["unsupported_target_bill_count"] = len(unsupported_target_bill_keys)
        source_state["unsupported_target_bill_keys"] = unsupported_target_bill_keys
    if source_report_metadata:
        source_state["source_report"] = source_report_metadata
    return source_state


def _materialize_bill_semantics_source_report_metadata(args: Any) -> dict[str, Any]:
    missing_from_report = getattr(args, "missing_from_report", None)
    if not missing_from_report:
        return {}
    report_path = Path(str(missing_from_report))
    metadata: dict[str, Any] = {"path": str(report_path)}
    if report_path.is_file():
        metadata["sha256"] = sha256_file(report_path)
        report_metadata = _bill_semantic_plan_report_metadata(report_path)
        metadata["missing_bill_keys"] = report_metadata.get(
            "missing_from_report_missing_bill_keys",
            [],
        )
        metadata["cutoff_ineligible_bill_keys"] = report_metadata.get(
            "missing_from_report_cutoff_ineligible_bill_keys",
            [],
        )
        metadata["run_metadata"] = report_metadata.get("missing_from_report_run_metadata")
    return metadata


def _bill_semantic_materialization_plan(
    *,
    args: Any,
    selected_bill_rows: list[dict[str, Any]],
    target_bill_keys: list[str],
    matched_bill_keys: list[str],
) -> dict[str, Any]:
    matched_bills: list[dict[str, Any]] = []
    for row in selected_bill_rows:
        bill_input = bill_semantic_input_from_row(row)
        matched_bills.append(
            {
                **bill_input.model_dump(mode="json"),
                "source_anchor_count": len(bill_input.source_anchors),
            }
        )
    missing_from_report = getattr(args, "missing_from_report", None)
    missing_from_report_path = Path(str(missing_from_report)) if missing_from_report else None
    missing_from_report_metadata = (
        _bill_semantic_plan_report_metadata(missing_from_report_path)
        if missing_from_report_path is not None and missing_from_report_path.is_file()
        else {}
    )
    inputs = {
        "bill_keys": [str(key) for key in getattr(args, "bill_key", None) or []],
        "missing_from_report": (
            str(missing_from_report_path) if missing_from_report_path is not None else None
        ),
        "missing_from_report_sha256": (
            sha256_file(missing_from_report_path)
            if missing_from_report_path is not None and missing_from_report_path.is_file()
            else None
        ),
        **missing_from_report_metadata,
    }
    sorted_matched_bill_keys = sorted(set(matched_bill_keys))
    unmatched_bill_keys = sorted(set(target_bill_keys) - set(matched_bill_keys))
    unsupported_target_bill_keys = _unsupported_bill_semantic_target_keys(target_bill_keys)
    requested_count = len(selected_bill_rows)
    return {
        "command": "materialize-bill-semantics",
        "dry_run": True,
        "output_root": str(Path(args.output_root)),
        "model": args.model,
        "feature_cutoff": (
            args.feature_cutoff.isoformat()
            if getattr(args, "feature_cutoff", None) is not None
            else None
        ),
        "limit": args.limit,
        "overwrite": args.overwrite,
        "fail_on_unmatched_targets": getattr(args, "fail_on_unmatched_targets", False),
        "inputs": inputs,
        "run_metadata": {
            "command": "materialize-bill-semantics",
            "dry_run": True,
            "model": str(getattr(args, "model", "")),
            "output_root": str(Path(args.output_root)),
            "feature_cutoff": (
                args.feature_cutoff.isoformat()
                if getattr(args, "feature_cutoff", None) is not None
                else None
            ),
            "limit": getattr(args, "limit", None),
            "overwrite": bool(getattr(args, "overwrite", False)),
            "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
            "source_state": _bill_semantics_plan_verify_source_state(
                feature_cutoff=(
                    args.feature_cutoff.isoformat()
                    if getattr(args, "feature_cutoff", None) is not None
                    else None
                ),
                target_bill_keys=target_bill_keys,
                matched_bill_keys=sorted_matched_bill_keys,
                unmatched_bill_keys=unmatched_bill_keys,
                unsupported_target_bill_keys=unsupported_target_bill_keys,
                matched_bills=matched_bills,
                requested_count=requested_count,
                inputs=inputs,
            ),
        },
        "target_bill_keys": target_bill_keys,
        "matched_bill_keys": sorted_matched_bill_keys,
        "unmatched_bill_keys": unmatched_bill_keys,
        "unsupported_target_bill_keys": unsupported_target_bill_keys,
        "requested_count": requested_count,
        "matched_bills": matched_bills,
    }


class _NoopBillSemanticExtractor:
    def extract(self, bill_input: Any) -> Any:
        raise RuntimeError(f"unexpected semantic extraction for {bill_input!r}")


def _materialize_bill_semantic_targeting_requested(args: Any) -> bool:
    return (
        bool(getattr(args, "bill_key", None))
        or getattr(args, "missing_from_report", None) is not None
    )


def _materialize_bill_semantic_row_key(row: dict[str, Any]) -> str | None:
    congress = row.get("congress")
    bill_type = row.get("bill_type")
    bill_number = row.get("bill_number")
    if isinstance(congress, bool) or isinstance(bill_number, bool):
        return None
    if not isinstance(congress, int) or not isinstance(bill_number, int):
        return None
    if not isinstance(bill_type, str) or not bill_type.strip():
        return None
    return f"{congress}-{bill_type.strip().lower()}-{bill_number}"


def _materialize_bill_semantic_target_keys(args: Any) -> list[str]:
    explicit_keys = [str(key) for key in getattr(args, "bill_key", None) or []]
    report_path = getattr(args, "missing_from_report", None)
    report_keys = (
        _bill_semantic_missing_keys_from_report(Path(report_path))
        if report_path is not None
        else []
    )
    return sorted(set(explicit_keys + report_keys))


def _unsupported_bill_semantic_target_keys(bill_keys: list[str]) -> list[str]:
    return sorted(
        {bill_key for bill_key in bill_keys if not re.fullmatch(r"\d+-[a-z]+-\d+", bill_key)}
    )


def _bill_semantic_missing_keys_from_report(path: Path) -> list[str]:
    return _bill_semantic_report_keys_from_coverage(path, "missing_bill_keys")


def _bill_semantic_cutoff_ineligible_keys_from_report(path: Path) -> list[str]:
    return _bill_semantic_report_keys_from_coverage(path, "cutoff_ineligible_bill_keys")


def _bill_semantic_report_keys_from_coverage(path: Path, key_name: str) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("prediction eval report must be a JSON object")

    coverage_fields = [
        "bill_semantic_coverage",
        "training_bill_semantic_coverage",
        "evaluation_bill_semantic_coverage",
    ]
    keys: list[str] = []
    seen_coverage = False
    for field in coverage_fields:
        coverage = data.get(field)
        if coverage is None:
            continue
        if not isinstance(coverage, dict):
            raise ValueError(f"prediction eval report {field} must be a JSON object")
        seen_coverage = True
        field_keys = coverage.get(key_name, [])
        if not isinstance(field_keys, list):
            raise ValueError(f"prediction eval report {field}.{key_name} must be a list")
        keys.extend(str(key) for key in field_keys)
    if not seen_coverage:
        raise ValueError("prediction eval report missing bill semantic coverage")
    return sorted(set(keys))


def _bill_semantic_plan_report_metadata(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    run_metadata = data.get("run_metadata") if isinstance(data, dict) else None
    return {
        "missing_from_report_missing_bill_keys": _bill_semantic_missing_keys_from_report(path),
        "missing_from_report_cutoff_ineligible_bill_keys": (
            _bill_semantic_cutoff_ineligible_keys_from_report(path)
        ),
        "missing_from_report_run_metadata": (
            run_metadata if isinstance(run_metadata, dict) else None
        ),
    }


def _handle_verify_bill_semantics_plan(args: Any) -> dict[str, Any]:
    plan_path = Path(args.plan)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    checked = 0

    def failure_result(
        *,
        checked: int,
        issues: list[str],
        quality_gate_failures: list[str] | None = None,
    ) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-bill-semantics-plan",
                "plan": str(plan_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": quality_gate_failures or [],
                "quality_gate_failure_count": len(quality_gate_failures or []),
                "run_metadata": _bill_semantics_plan_verify_run_metadata(
                    args,
                    plan_path,
                ),
            },
        )

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"failed to load plan: {exc}"])
    if not isinstance(plan, dict):
        return failure_result(checked=0, issues=["plan must be an object"])
    checked += 1
    if plan.get("command") != "materialize-bill-semantics":
        issues.append("plan command must be materialize-bill-semantics")
    if plan.get("dry_run") is not True:
        issues.append("plan dry_run must be true")

    target_bill_keys = _string_list_from_plan(plan, "target_bill_keys", issues)
    matched_bill_keys = _string_list_from_plan(plan, "matched_bill_keys", issues)
    unmatched_bill_keys = _string_list_from_plan(plan, "unmatched_bill_keys", issues)
    unsupported_target_bill_keys = _string_list_from_plan(
        plan,
        "unsupported_target_bill_keys",
        issues,
        optional=True,
    )
    expected_unsupported = _unsupported_bill_semantic_target_keys(target_bill_keys)
    if unsupported_target_bill_keys != expected_unsupported:
        issues.append(
            "unsupported_target_bill_keys mismatch: "
            f"expected {expected_unsupported!r}, got {unsupported_target_bill_keys!r}"
        )
    expected_unmatched = sorted(set(target_bill_keys) - set(matched_bill_keys))
    if unmatched_bill_keys != expected_unmatched:
        issues.append(
            "unmatched_bill_keys mismatch: "
            f"expected {expected_unmatched!r}, got {unmatched_bill_keys!r}"
        )
    fail_on_unmatched_targets = bool(
        getattr(args, "fail_on_unmatched_targets", False)
        or plan.get("fail_on_unmatched_targets") is True
    )
    if fail_on_unmatched_targets and unmatched_bill_keys:
        quality_gate_failures.append("unmatched_target_bill_keys")

    matched_bills = plan.get("matched_bills")
    if not isinstance(matched_bills, list):
        issues.append("matched_bills must be a list")
        matched_bills = []
    else:
        require_matched_source_anchors = bool(
            getattr(args, "require_matched_source_anchors", False)
        )
        feature_cutoff = _date_from_iso_string(plan.get("feature_cutoff"))
        if _is_present_invalid_iso_date(plan.get("feature_cutoff")):
            issues.append("feature_cutoff must be an ISO date")
        _validate_bill_semantics_plan_matched_bills(
            matched_bills=matched_bills,
            matched_bill_keys=matched_bill_keys,
            issues=issues,
            quality_gate_failures=quality_gate_failures,
            require_matched_source_anchors=require_matched_source_anchors,
            feature_cutoff=feature_cutoff,
        )
    requested_count = plan.get("requested_count")
    if not _is_plain_int(requested_count) or requested_count < 0:
        issues.append("requested_count must be a non-negative integer")
    elif requested_count != len(matched_bills):
        issues.append(
            f"requested_count mismatch: expected {len(matched_bills)}, got {requested_count}"
        )

    inputs = plan.get("inputs")
    require_source_report = bool(getattr(args, "require_source_report", False))
    if inputs is None:
        if require_source_report:
            issues.append("inputs missing")
    else:
        if not isinstance(inputs, dict):
            issues.append("inputs must be an object")
        else:
            checked += _validate_bill_semantics_plan_inputs(
                inputs,
                issues,
                require_source_report=require_source_report,
            )
    plan_source_state = _bill_semantics_plan_verify_source_state(
        feature_cutoff=plan.get("feature_cutoff"),
        target_bill_keys=target_bill_keys,
        matched_bill_keys=matched_bill_keys,
        unmatched_bill_keys=unmatched_bill_keys,
        unsupported_target_bill_keys=unsupported_target_bill_keys,
        matched_bills=matched_bills,
        requested_count=requested_count if _is_plain_int(requested_count) else None,
        inputs=inputs if isinstance(inputs, dict) else None,
    )
    plan_run_metadata = plan.get("run_metadata")
    if plan_run_metadata is not None:
        if not isinstance(plan_run_metadata, dict):
            issues.append("plan run_metadata must be an object")
        else:
            checked += 1
            _validate_bill_semantics_plan_run_metadata(
                run_metadata=plan_run_metadata,
                expected_source_state=plan_source_state,
                issues=issues,
            )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-bill-semantics-plan",
            "plan": str(plan_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "target_bill_keys": target_bill_keys,
            "matched_bill_keys": matched_bill_keys,
            "unmatched_bill_keys": unmatched_bill_keys,
            "unsupported_target_bill_keys": unsupported_target_bill_keys,
            "fail_on_unmatched_targets": fail_on_unmatched_targets,
            "require_source_report": require_source_report,
            "matched_bill_count": len(matched_bills),
            "run_metadata": _bill_semantics_plan_verify_run_metadata(
                args,
                plan_path,
                source_state=plan_source_state,
            ),
        },
    )


def _validate_bill_semantics_plan_matched_bills(
    *,
    matched_bills: list[Any],
    matched_bill_keys: list[str],
    issues: list[str],
    quality_gate_failures: list[str],
    require_matched_source_anchors: bool = False,
    feature_cutoff: dt.date | None = None,
) -> None:
    seen_keys: list[str] = []
    missing_source_anchor = False
    future_available_at = False
    for index, item in enumerate(matched_bills):
        if not isinstance(item, dict):
            issues.append(f"matched_bills[{index}] must be an object")
            continue
        bill_key = item.get("bill_key")
        if bill_key is None:
            issues.append(f"matched_bills[{index}].bill_key is required")
            continue
        seen_keys.append(str(bill_key))
        if _is_present_invalid_iso_date(item.get("available_at")):
            issues.append(f"matched_bills[{index}].available_at must be an ISO date")
        available_at = _date_from_iso_string(item.get("available_at"))
        if (
            feature_cutoff is not None
            and available_at is not None
            and available_at > feature_cutoff
        ):
            future_available_at = True
        source_anchors = item.get("source_anchors")
        if source_anchors is not None and not isinstance(source_anchors, list):
            issues.append(f"matched_bills[{index}].source_anchors must be a list")
        elif require_matched_source_anchors:
            if not source_anchors:
                missing_source_anchor = True
            elif any(not _is_official_plan_source_anchor(anchor) for anchor in source_anchors):
                missing_source_anchor = True
        source_anchor_count = item.get("source_anchor_count")
        if source_anchor_count is not None:
            if not _is_plain_int(source_anchor_count) or source_anchor_count < 0:
                issues.append(f"matched_bills[{index}].source_anchor_count must be non-negative")
            elif isinstance(source_anchors, list) and source_anchor_count != len(source_anchors):
                issues.append(
                    f"matched_bills[{index}].source_anchor_count mismatch: "
                    f"expected {len(source_anchors)}, got {source_anchor_count}"
                )
        elif require_matched_source_anchors:
            missing_source_anchor = True
    unknown_keys = sorted(set(seen_keys) - set(matched_bill_keys))
    if unknown_keys:
        issues.append(f"matched_bills contains keys outside matched_bill_keys: {unknown_keys!r}")
    if missing_source_anchor:
        quality_gate_failures.append("matched_bill_source_anchors_missing")
    if future_available_at:
        quality_gate_failures.append("matched_bill_available_after_feature_cutoff")


def _validate_bill_semantics_plan_inputs(
    inputs: dict[str, Any],
    issues: list[str],
    *,
    require_source_report: bool = False,
) -> int:
    report_path_raw = inputs.get("missing_from_report")
    expected_report_sha = inputs.get("missing_from_report_sha256")
    if require_source_report:
        if not report_path_raw:
            issues.append("missing_from_report: missing path")
        if not expected_report_sha:
            issues.append("missing_from_report: missing sha256")
        if "missing_from_report_missing_bill_keys" not in inputs:
            issues.append("missing_from_report: missing bill keys missing")
        if "missing_from_report_run_metadata" not in inputs:
            issues.append("missing_from_report: run_metadata missing")
    if report_path_raw and not expected_report_sha:
        issues.append("missing_from_report: missing sha256")
        return 0
    if expected_report_sha and not report_path_raw:
        issues.append("missing_from_report: missing path")
        return 0
    if not report_path_raw and not expected_report_sha:
        return 0
    if not isinstance(expected_report_sha, str) or not _is_sha256_hex(expected_report_sha):
        issues.append("missing_from_report: sha256 invalid")
        return 0
    report_path = Path(str(report_path_raw))
    if not report_path.is_file():
        issues.append(f"missing_from_report: file not found: {report_path}")
        return 1
    actual_report_sha = sha256_file(report_path)
    if actual_report_sha != expected_report_sha:
        issues.append(
            "missing_from_report: sha256 mismatch: "
            f"expected {expected_report_sha}, got {actual_report_sha}"
        )
    expected_missing_keys = inputs.get("missing_from_report_missing_bill_keys")
    if expected_missing_keys is not None:
        if not isinstance(expected_missing_keys, list):
            issues.append("missing_from_report: missing bill keys must be a list")
        else:
            actual_missing_keys = _bill_semantic_missing_keys_from_report(report_path)
            normalized_expected_missing_keys = sorted(str(key) for key in expected_missing_keys)
            if normalized_expected_missing_keys != actual_missing_keys:
                issues.append("missing_from_report: missing bill keys mismatch")
    expected_cutoff_ineligible_keys = inputs.get("missing_from_report_cutoff_ineligible_bill_keys")
    if expected_cutoff_ineligible_keys is not None:
        if not isinstance(expected_cutoff_ineligible_keys, list):
            issues.append("missing_from_report: cutoff-ineligible bill keys must be a list")
        else:
            actual_cutoff_ineligible_keys = _bill_semantic_cutoff_ineligible_keys_from_report(
                report_path
            )
            normalized_expected_cutoff_keys = sorted(
                str(key) for key in expected_cutoff_ineligible_keys
            )
            if normalized_expected_cutoff_keys != actual_cutoff_ineligible_keys:
                issues.append("missing_from_report: cutoff-ineligible bill keys mismatch")
    expected_run_metadata = inputs.get("missing_from_report_run_metadata")
    if expected_run_metadata is not None:
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        actual_run_metadata = (
            report_data.get("run_metadata") if isinstance(report_data, dict) else None
        )
        if expected_run_metadata != actual_run_metadata:
            issues.append("missing_from_report: run_metadata mismatch")
    return 1


def _validate_bill_semantics_plan_run_metadata(
    *,
    run_metadata: dict[Any, Any],
    expected_source_state: dict[str, Any],
    issues: list[str],
) -> None:
    if run_metadata.get("command") != "materialize-bill-semantics":
        issues.append("plan run_metadata mismatch: command")
    if run_metadata.get("dry_run") is not True:
        issues.append("plan run_metadata mismatch: dry_run")
    source_state = run_metadata.get("source_state")
    if source_state is None:
        issues.append("plan run_metadata source_state missing")
        return
    if not isinstance(source_state, dict):
        issues.append("plan run_metadata source_state must be an object")
        return
    invalid_count_keys = _bill_semantics_plan_invalid_source_state_count_keys(source_state)
    issues.extend(
        f"plan run_metadata source_state {issue}"
        for issue in _bill_semantics_plan_source_state_count_issues(
            source_state,
            invalid_count_keys=invalid_count_keys,
        )
    )
    for key in sorted(set(source_state) - set(expected_source_state)):
        issues.append(f"plan run_metadata source_state unexpected: {key}")
    for key, expected_value in expected_source_state.items():
        if key in invalid_count_keys:
            continue
        if source_state.get(key) != expected_value:
            issues.append(f"plan run_metadata source_state mismatch: {key}")


def _bill_semantics_plan_invalid_source_state_count_keys(
    source_state: dict[str, Any],
) -> set[str]:
    keys = (
        "target_bill_count",
        "matched_bill_count",
        "unmatched_bill_count",
        "unsupported_target_bill_count",
        "matched_source_anchor_count",
        "matched_bill_source_anchor_count",
        "requested_count",
    )
    return {
        key
        for key in keys
        if key in source_state and not _is_non_negative_plain_int(source_state.get(key))
    }


def _bill_semantics_plan_source_state_count_issues(
    source_state: dict[str, Any],
    *,
    invalid_count_keys: set[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    invalid_keys = (
        invalid_count_keys
        if invalid_count_keys is not None
        else _bill_semantics_plan_invalid_source_state_count_keys(source_state)
    )
    for key in (
        "target_bill_count",
        "matched_bill_count",
        "unmatched_bill_count",
        "unsupported_target_bill_count",
        "matched_source_anchor_count",
        "matched_bill_source_anchor_count",
        "requested_count",
    ):
        if key in source_state and not _is_plain_int(source_state.get(key)):
            issues.append(f"{key} must be an integer")
        elif key in invalid_keys:
            issues.append(f"{key} must be a non-negative integer")
    return issues


def _handle_verify_bill_semantics(args: Any) -> dict[str, Any]:
    root = Path(args.root)
    index_path = root / "index.json"
    required_model_names = _required_string_list(getattr(args, "require_model_name", None))
    index_sha256 = sha256_file(index_path) if index_path.is_file() else None
    try:
        payloads = load_bill_semantic_payloads(root)
    except Exception as exc:  # noqa: BLE001
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-bill-semantics",
                "root": str(root),
                "index_path": str(index_path),
                "index_sha256": index_sha256,
                "bill_count": 0,
                "issues": [str(exc)],
                "issue_count": 1,
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _bill_semantics_verify_run_metadata(
                    args,
                    root,
                    index_sha256,
                    source_state=_bill_semantics_unavailable_source_state(
                        root=root,
                        index_path=index_path,
                        index_sha256=index_sha256,
                    ),
                ),
            },
        )
    index_payload = _bill_semantics_index_payload(root)
    index_object = _bill_semantics_index_object(root)
    model_names = sorted(
        {payload.model_name for payload in payloads if payload.model_name is not None}
    )
    unofficial_source_anchor_count = _bill_semantics_unofficial_source_anchor_count(payloads)
    quality_gate_failures = [
        f"missing_model_name:{model_name}"
        for model_name in required_model_names
        if model_name not in model_names
    ]
    if index_payload.source_inputs_sha256 is not None and not _is_sha256_hex(
        index_payload.source_inputs_sha256
    ):
        quality_gate_failures.append("source_inputs_sha256_invalid")
    elif bool(getattr(args, "require_source_inputs_sha256", False)):
        if index_payload.source_inputs_sha256 is None:
            quality_gate_failures.append("source_inputs_sha256_missing")
        if "source_bill_count" not in index_object:
            quality_gate_failures.append("source_bill_count_missing")
        if "source_bill_keys" not in index_object:
            quality_gate_failures.append("source_bill_keys_missing")
        quality_gate_failures.extend(
            _bill_semantics_index_run_metadata_failures(
                index_payload=index_payload,
                index_object=index_object,
            )
        )
        if unofficial_source_anchor_count:
            quality_gate_failures.append("unofficial_source_anchors")
    return _attach_optional_verification_output(
        args,
        {
            "ok": not quality_gate_failures,
            "command": "verify-bill-semantics",
            "root": str(root),
            "index_path": str(index_path),
            "index_sha256": index_sha256,
            "bill_count": len(payloads),
            "bill_keys": [payload.bill_key for payload in payloads],
            "model_names": model_names,
            "required_model_names": required_model_names,
            "source_bill_count": index_payload.source_bill_count,
            "source_bill_keys": list(index_payload.source_bill_keys),
            "source_inputs_sha256": index_payload.source_inputs_sha256,
            "unofficial_source_anchor_count": unofficial_source_anchor_count,
            "issues": [],
            "issue_count": 0,
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "run_metadata": _bill_semantics_verify_run_metadata(
                args,
                root,
                index_sha256,
                source_state=_bill_semantics_verify_source_state(
                    payloads=payloads,
                    index_payload=index_payload,
                    unofficial_source_anchor_count=unofficial_source_anchor_count,
                ),
            ),
        },
    )


def _bill_semantics_unofficial_source_anchor_count(
    payloads: list[Any],
) -> int:
    return sum(
        1
        for payload in payloads
        for anchor in payload.source_anchors
        if not is_official_source_url(anchor.source_type, anchor.url)
    )


def _manifest_has_bill_semantics_cache(inputs: dict[Any, Any]) -> bool:
    root = inputs.get("bill_semantics_root")
    index_sha = inputs.get("bill_semantics_index_sha256")
    return bool(
        isinstance(root, str)
        and root == root.strip()
        and root.strip()
        and isinstance(index_sha, str)
        and _is_sha256_hex(index_sha)
    )


def _missing_bill_semantics_cache_result(
    required_model_names: list[str],
    *,
    require_source_inputs_sha256: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-bill-semantics",
        "root": None,
        "index_path": None,
        "index_sha256": None,
        "bill_count": 0,
        "issues": [],
        "quality_gate_failures": ["bill_semantics_root_missing"],
        "run_metadata": {
            "command": "verify-bill-semantics",
            "verification_flags": {
                "require_model_names": required_model_names,
                "require_source_inputs_sha256": require_source_inputs_sha256,
            },
            "root": None,
            "index_sha256": None,
            "source_state": _bill_semantics_unavailable_source_state(),
        },
    }


def _bill_semantics_verify_run_metadata(
    args: Any,
    root: Path,
    index_sha256: str | None,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-bill-semantics",
        "verification_flags": {
            "require_model_names": _required_string_list(getattr(args, "require_model_name", None)),
            "require_source_inputs_sha256": bool(
                getattr(args, "require_source_inputs_sha256", False)
            ),
        },
        "root": str(root),
        "index_sha256": index_sha256,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _bill_semantics_verify_source_state(
    *,
    payloads: list[Any],
    index_payload: BillSemanticIndexPayload,
    unofficial_source_anchor_count: int,
) -> dict[str, Any]:
    return {
        "bill_count": len(payloads),
        "bill_keys": [payload.bill_key for payload in payloads],
        "model_names": sorted(
            {payload.model_name for payload in payloads if payload.model_name is not None}
        ),
        "source_bill_count": index_payload.source_bill_count,
        "source_bill_keys": list(index_payload.source_bill_keys),
        "source_inputs_sha256": index_payload.source_inputs_sha256,
        "unofficial_source_anchor_count": unofficial_source_anchor_count,
    }


def _bill_semantics_unavailable_source_state(
    *,
    root: Path | None = None,
    index_path: Path | None = None,
    index_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "root": str(root) if root is not None else None,
        "index_path": str(index_path) if index_path is not None else None,
        "index_sha256": index_sha256,
        "bill_count": 0,
        "bill_keys": [],
        "model_names": [],
        "source_bill_count": None,
        "source_bill_keys": [],
        "source_inputs_sha256": None,
        "unofficial_source_anchor_count": None,
    }


def _bill_semantics_plan_verify_run_metadata(
    args: Any,
    plan_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-bill-semantics-plan",
        "verification_flags": {
            "fail_on_unmatched_targets": bool(getattr(args, "fail_on_unmatched_targets", False)),
            "require_source_report": bool(getattr(args, "require_source_report", False)),
            "require_matched_source_anchors": bool(
                getattr(args, "require_matched_source_anchors", False)
            ),
        },
        "artifact_sha256": sha256_file(plan_path) if plan_path.is_file() else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _bill_semantics_plan_verify_source_state(
    *,
    feature_cutoff: Any | None = None,
    target_bill_keys: list[str],
    matched_bill_keys: list[str],
    unmatched_bill_keys: list[str],
    unsupported_target_bill_keys: list[str] | None = None,
    matched_bills: list[Any],
    requested_count: int | None,
    inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    matched_source_anchor_count = 0
    matched_bill_source_anchor_count = 0
    for bill in matched_bills:
        if not isinstance(bill, dict):
            continue
        source_anchors = bill.get("source_anchors")
        if isinstance(source_anchors, list):
            matched_source_anchor_count += len(source_anchors)
            if source_anchors:
                matched_bill_source_anchor_count += 1
    source_state = {
        "feature_cutoff": feature_cutoff,
        "target_bill_count": len(target_bill_keys),
        "matched_bill_count": len(matched_bill_keys),
        "unmatched_bill_count": len(unmatched_bill_keys),
        "target_bill_keys": target_bill_keys,
        "matched_bill_keys": matched_bill_keys,
        "unmatched_bill_keys": unmatched_bill_keys,
        "matched_source_anchor_count": matched_source_anchor_count,
        "matched_bill_source_anchor_count": matched_bill_source_anchor_count,
        "requested_count": requested_count,
        "missing_from_report": inputs.get("missing_from_report") if inputs else None,
        "missing_from_report_sha256": (
            inputs.get("missing_from_report_sha256") if inputs else None
        ),
    }
    unsupported_target_bill_keys = unsupported_target_bill_keys or []
    if unsupported_target_bill_keys:
        source_state["unsupported_target_bill_count"] = len(unsupported_target_bill_keys)
        source_state["unsupported_target_bill_keys"] = unsupported_target_bill_keys
    return source_state


def _has_strict_bill_semantics_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-bill-semantics ")
        and "--require-source-inputs-sha256" in command
        and "--require-model-name " in command
        for command in commands
    )
