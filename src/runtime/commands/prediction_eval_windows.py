"""Prediction evaluation window plan/summary/run commands."""

from __future__ import annotations

import datetime as dt
import json

from pathlib import Path
from src.prediction.eval_report import PredictionEvalReportPayload
from src.prediction.window_plan import (
    PredictionEvalWindowPlanPayload,
    build_prediction_eval_window_plan,
)
from typing import Any, cast

from src.runtime.commands._shared import (
    _attach_optional_verification_output,
    _command_issue_result,
    _congress_archive_manifest_metadata,
    _is_non_negative_plain_int,
    _is_plain_int,
    _is_sha256_hex,
    _plain_int_or_zero,
    _positive_int_arg_issues,
    _write_json_artifact,
)
from src.runtime.commands.core import (
    _file_sha256,
    _float_option_value_from_command,
    _int_option_value_from_command,
    _numeric_or_none_matches,
    _option_value_from_command,
    _option_values_from_command,
)
from src.runtime.commands.prediction_eval import _prediction_eval_optional_rate_valid


def run_prediction_eval_window_plan_command(
    *,
    start_label_year: int,
    end_label_year: int,
    train_years: int = 2,
    label_years: int = 1,
    max_feature_cutoff: dt.date | None = None,
    output_dir: str = "out",
    bill_semantics_root: str | None = None,
    congress_archive_manifest: str | None = None,
    plan_artifact_path: str | None = None,
) -> PredictionEvalWindowPlanPayload:
    return build_prediction_eval_window_plan(
        start_label_year=start_label_year,
        end_label_year=end_label_year,
        train_years=train_years,
        label_years=label_years,
        max_feature_cutoff=max_feature_cutoff,
        output_dir=output_dir,
        bill_semantics_root=bill_semantics_root,
        congress_archive_manifest=congress_archive_manifest,
        plan_artifact_path=plan_artifact_path,
    )


def _handle_prediction_eval_window_plan(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("start_label_year", "end_label_year", "train_years", "label_years"),
    )
    if issues:
        return _command_issue_result("prediction-eval-window-plan", issues)

    result = run_prediction_eval_window_plan_command(
        start_label_year=args.start_label_year,
        end_label_year=args.end_label_year,
        train_years=args.train_years,
        label_years=args.label_years,
        max_feature_cutoff=args.max_feature_cutoff,
        output_dir=args.output_dir,
        bill_semantics_root=args.bill_semantics_root,
        congress_archive_manifest=getattr(args, "congress_archive_manifest", None),
        plan_artifact_path=args.output,
    )
    run_metadata = {
        "command": "prediction-eval-window-plan",
        "start_label_year": args.start_label_year,
        "end_label_year": args.end_label_year,
        "train_years": args.train_years,
        "label_years": args.label_years,
        "max_feature_cutoff": (
            args.max_feature_cutoff.isoformat() if args.max_feature_cutoff is not None else None
        ),
        "output_dir": args.output_dir,
        "bill_semantics_root": args.bill_semantics_root,
        "congress_archive_manifest": getattr(args, "congress_archive_manifest", None),
    }
    payload = result.model_dump(mode="json")
    payload["command"] = "prediction-eval-window-plan"
    payload["run_metadata"] = run_metadata
    output = Path(args.output) if args.output is not None else None
    output_sha256: str | None = None
    if output is not None:
        output_sha256 = _write_json_artifact(output, payload)
    return {
        "ok": True,
        "command": "prediction-eval-window-plan",
        "plan_version": result.plan_version,
        "window_count": result.window_count,
        "start_label_year": result.start_label_year,
        "end_label_year": result.end_label_year,
        "train_years": result.train_years,
        "label_years": result.label_years,
        "max_feature_cutoff": (
            result.max_feature_cutoff.isoformat() if result.max_feature_cutoff is not None else None
        ),
        "windows": [window.model_dump(mode="json") for window in result.windows],
        "summary_command": result.summary_command,
        "verify_summary_command": result.verify_summary_command,
        "verify_run_command": result.verify_run_command,
        "run_metadata": run_metadata,
        "output": str(output) if output is not None else None,
        "output_sha256": output_sha256,
    }


def _handle_verify_prediction_eval_window_plan(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-plan",
                "artifact": str(artifact_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_plan_verify_run_metadata(
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

    issues: list[str] = []
    if artifact.get("command") not in (None, "prediction-eval-window-plan"):
        issues.append("artifact command must be prediction-eval-window-plan")
    plan_payload = {
        key: value for key, value in artifact.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"invalid window plan: {exc}"])

    if bool(getattr(args, "require_run_metadata", False)):
        _validate_prediction_eval_window_plan_run_metadata(
            artifact=artifact,
            plan=plan,
            issues=issues,
        )

    quality_gate_failures: list[str] = []
    min_window_count = getattr(args, "min_window_count", None)
    if min_window_count is not None:
        if not _is_plain_int(min_window_count):
            issues.append("min_window_count must be an integer")
        elif min_window_count < 0:
            issues.append("min_window_count must be a non-negative integer")
        elif plan.window_count < min_window_count:
            quality_gate_failures.append("window_count_below_minimum")

    require_plan_congress_archive_manifest = bool(
        _prediction_eval_window_plan_run_metadata_value(
            artifact,
            "congress_archive_manifest",
        )
    )
    command_issues = _prediction_eval_window_plan_command_issues(
        plan,
        require_congress_archive_manifest=require_plan_congress_archive_manifest,
    )
    if bool(getattr(args, "require_commands", False)):
        issues.extend(command_issues)

    source_state = _prediction_eval_window_plan_source_state(plan, command_issues)
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-eval-window-plan",
            "artifact": str(artifact_path),
            "checked": 1 + plan.window_count,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "plan_version": plan.plan_version,
            "window_count": plan.window_count,
            "start_label_year": plan.start_label_year,
            "end_label_year": plan.end_label_year,
            "min_window_count": min_window_count,
            "command_issue_count": len(command_issues),
            "command_issues": command_issues,
            "run_metadata": _prediction_eval_window_plan_verify_run_metadata(
                args,
                artifact_path,
                source_state=source_state,
            ),
        },
    )


def _prediction_eval_window_plan_run_metadata_value(
    artifact: dict[str, Any],
    key: str,
) -> Any:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None
    return run_metadata.get(key)


def _validate_prediction_eval_window_plan_run_metadata(
    *,
    artifact: dict[str, Any],
    plan: PredictionEvalWindowPlanPayload,
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append("run_metadata missing")
        return
    expected = {
        "command": "prediction-eval-window-plan",
        "start_label_year": plan.start_label_year,
        "end_label_year": plan.end_label_year,
        "train_years": plan.train_years,
        "label_years": plan.label_years,
        "max_feature_cutoff": (
            plan.max_feature_cutoff.isoformat() if plan.max_feature_cutoff is not None else None
        ),
    }
    for key, expected_value in expected.items():
        if run_metadata.get(key) != expected_value:
            issues.append(f"run_metadata {key} mismatch")
    for key in ("output_dir", "bill_semantics_root", "congress_archive_manifest"):
        if key not in run_metadata:
            issues.append(f"run_metadata {key} missing")


def _prediction_eval_window_plan_command_issues(
    plan: PredictionEvalWindowPlanPayload,
    *,
    require_congress_archive_manifest: bool = False,
) -> list[str]:
    issues: list[str] = []
    verifier_output_paths: list[tuple[str, str]] = []
    if not plan.summary_command.startswith(
        "python3 -m src.runtime.main prediction-eval-window-summary "
    ):
        issues.append("summary_command unsupported")
    if plan.window_count and "--report " not in plan.summary_command:
        issues.append("summary_command missing --report")
    if "--require-report-run-metadata" not in plan.summary_command:
        issues.append("summary_command missing --require-report-run-metadata")
    if "--require-non-overlapping-label-windows" not in plan.summary_command:
        issues.append("summary_command missing --require-non-overlapping-label-windows")
    if "--output " not in plan.summary_command:
        issues.append("summary_command missing --output")
    if not plan.verify_summary_command.startswith(
        "python3 -m src.runtime.main verify-prediction-eval-window-summary "
    ):
        issues.append("verify_summary_command unsupported")
    if not plan.verify_run_command.startswith(
        "python3 -m src.runtime.main verify-prediction-eval-window-run "
    ):
        issues.append("verify_run_command unsupported")
    for fragment in (
        "--artifact ",
        "--plan ",
        "--require-plan-match",
        "--require-run-metadata",
        "--require-current-report-hashes",
        "--require-non-overlapping-label-windows",
        f"--min-window-count {plan.window_count}",
        "--output ",
    ):
        if fragment not in plan.verify_summary_command:
            issues.append(f"verify_summary_command missing {fragment.strip()}")
    for fragment in (
        "--plan ",
        "--summary-verify ",
        "--require-window-verifiers",
        "--require-summary-verify",
        "--output ",
    ):
        if fragment not in plan.verify_run_command:
            issues.append(f"verify_run_command missing {fragment.strip()}")
    for index, window in enumerate(plan.windows):
        eval_command = window.eval_report_command
        if not eval_command.startswith("python3 -m src.runtime.main prediction-eval-report "):
            issues.append(f"windows[{index}].eval_report_command unsupported")
        inventory_command = window.input_inventory_command
        if not inventory_command.startswith(
            "python3 -m src.runtime.main prediction-input-inventory "
        ):
            issues.append(f"windows[{index}].input_inventory_command unsupported")
        inventory_verify_command = window.input_inventory_verify_command
        if not inventory_verify_command.startswith(
            "python3 -m src.runtime.main verify-prediction-input-inventory "
        ):
            issues.append(f"windows[{index}].input_inventory_verify_command unsupported")
        else:
            verifier_output_paths.append(
                (
                    f"windows[{index}].input_inventory_verify_command",
                    _option_value_from_command(inventory_verify_command, "--output") or "",
                )
            )
        verify_command = window.eval_manifest_verify_command
        if not verify_command.startswith(
            "python3 -m src.runtime.main verify-prediction-eval-manifest "
        ):
            issues.append(f"windows[{index}].eval_manifest_verify_command unsupported")
        else:
            verifier_output_paths.append(
                (
                    f"windows[{index}].eval_manifest_verify_command",
                    _option_value_from_command(verify_command, "--output") or "",
                )
            )
        expected_fragments = _prediction_eval_window_expected_command_fragments(window)
        for fragment in expected_fragments:
            if fragment not in eval_command:
                issues.append(f"windows[{index}].eval_report_command missing {fragment}")
            if fragment not in inventory_command:
                issues.append(f"windows[{index}].input_inventory_command missing {fragment}")
        if require_congress_archive_manifest:
            if "--congress-archive-manifest " not in eval_command:
                issues.append(
                    f"windows[{index}].eval_report_command missing --congress-archive-manifest"
                )
            if "--congress-archive-manifest " not in inventory_command:
                issues.append(
                    f"windows[{index}].input_inventory_command missing --congress-archive-manifest"
                )
        if "--dataset-output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --dataset-output")
        if "--manifest-output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --manifest-output")
        if "--output " not in eval_command:
            issues.append(f"windows[{index}].eval_report_command missing --output")
        if "--output " not in inventory_command:
            issues.append(f"windows[{index}].input_inventory_command missing --output")
        for fragment in (
            "--artifact ",
            "--require-run-metadata",
            *(
                ("--require-congress-archive-manifest",)
                if require_congress_archive_manifest
                else ()
            ),
            "--require-portable-jurisdiction-ids",
            "--require-portable-body-ids",
            "--require-portable-session-ids",
            "--require-source-family congress_vote",
            "--require-source-family congress_bill",
            "--min-training-labels 1",
            "--min-evaluation-labels 1",
            "--min-training-label-official-source-url-coverage-rate 1",
            "--min-evaluation-label-official-source-url-coverage-rate 1",
            "--min-bill-official-source-url-coverage-rate 1",
            "--min-bill-sponsor-availability-rate 1",
            "--min-ontology-official-source-anchor-coverage-rate 1",
            "--min-fec-contributions 1",
            "--min-member-attributed-fec-contributions 1",
            "--min-members-with-fec-candidate-id 1",
            "--min-public-statement-signals 1",
            "--min-members-with-public-statement-signals 1",
            "--output ",
        ):
            if fragment not in inventory_verify_command:
                issues.append(
                    f"windows[{index}].input_inventory_verify_command missing {fragment.strip()}"
                )
        for fragment in (
            "--manifest ",
            "--require-artifact-run-metadata",
            *(
                ("--require-congress-archive-manifest",)
                if require_congress_archive_manifest
                else ()
            ),
            "--require-model-name member_vote_rate_baseline",
            "--require-model-name ontology_signal_model",
            "--require-model-name learned_signal_logistic",
            "--require-ontology-feature-signals",
            "--min-training-feature-source-url-coverage-rate 1",
            "--min-training-feature-official-source-coverage-rate 1",
            "--min-evaluation-feature-source-url-coverage-rate 1",
            "--min-evaluation-feature-official-source-coverage-rate 1",
            "--min-evaluation-source-url-coverage-rate 1",
            "--require-fail-on-unknown-bill-semantic-availability",
            "--require-fail-on-unknown-bill-signal-availability",
            "--require-fail-on-unknown-ontology-edge-availability",
            "--require-fail-on-unknown-contribution-signal-availability",
            "--require-fail-on-unknown-statement-signal-availability",
            "--output ",
        ):
            if fragment not in verify_command:
                issues.append(
                    f"windows[{index}].eval_manifest_verify_command missing {fragment.strip()}"
                )
    seen_verifier_outputs: dict[str, str] = {}
    for label, output_path in verifier_output_paths:
        if not output_path:
            issues.append(f"{label} missing --output value")
            continue
        previous = seen_verifier_outputs.get(output_path)
        if previous is None:
            seen_verifier_outputs[output_path] = label
        else:
            issues.append(f"{label} duplicates verifier output: {previous}")
    return issues


def _prediction_eval_window_expected_command_fragments(
    window: Any,
) -> list[str]:
    return [
        f"--training-feature-cutoff {window.training_feature_cutoff.isoformat()}",
        f"--train-start {window.train_start.isoformat()}",
        f"--train-end {window.train_end.isoformat()}",
        f"--feature-cutoff {window.feature_cutoff.isoformat()}",
        f"--label-start {window.label_start.isoformat()}",
        f"--label-end {window.label_end.isoformat()}",
    ]


def _prediction_eval_window_plan_source_state(
    plan: PredictionEvalWindowPlanPayload,
    command_issues: list[str],
) -> dict[str, Any]:
    return {
        "plan_version": plan.plan_version,
        "window_count": plan.window_count,
        "window_ids": [window.window_id for window in plan.windows],
        "first_feature_cutoff": (
            plan.windows[0].feature_cutoff.isoformat() if plan.windows else None
        ),
        "last_feature_cutoff": (
            plan.windows[-1].feature_cutoff.isoformat() if plan.windows else None
        ),
        "first_label_start": (plan.windows[0].label_start.isoformat() if plan.windows else None),
        "last_label_end": plan.windows[-1].label_end.isoformat() if plan.windows else None,
        "command_issue_count": len(command_issues),
        "has_summary_command": bool(plan.summary_command),
        "has_verify_summary_command": bool(plan.verify_summary_command),
        "has_verify_run_command": bool(plan.verify_run_command),
        "requires_training_labels": all(
            "--min-training-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        "requires_evaluation_labels": all(
            "--min-evaluation-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
    }


def _prediction_eval_window_plan_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-plan",
        "artifact": str(artifact_path),
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_commands": bool(getattr(args, "require_commands", False)),
            "min_window_count": getattr(args, "min_window_count", None),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _handle_prediction_eval_window_summary(args: Any) -> dict[str, Any]:
    report_paths = [Path(path) for path in getattr(args, "report", [])]
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    reports: list[tuple[Path, dict[str, Any], PredictionEvalReportPayload]] = []

    if not report_paths:
        issues.append("at least one --report is required")
    if len({str(path) for path in report_paths}) != len(report_paths):
        issues.append("report paths must be unique")

    for path in report_paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{path}: failed to load report: {exc}")
            continue
        if not isinstance(raw, dict):
            issues.append(f"{path}: report must be an object")
            continue
        try:
            report = PredictionEvalReportPayload.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{path}: invalid prediction eval report: {exc}")
            continue
        reports.append((path, raw, report))

    if bool(getattr(args, "require_report_run_metadata", False)):
        for path, raw, report in reports:
            _validate_prediction_eval_window_summary_report_metadata(
                path=path,
                raw=raw,
                report=report,
                issues=issues,
            )

    if bool(getattr(args, "require_ready_reports", False)):
        for path, _, report in reports:
            if report.readiness.status != "ready" or not report.readiness.ok:
                quality_gate_failures.append(f"{path}: report_not_ready")

    sorted_reports = sorted(reports, key=lambda item: (item[2].label_start, item[2].label_end))
    if bool(getattr(args, "require_non_overlapping_label_windows", False)):
        for previous, current in zip(sorted_reports, sorted_reports[1:]):
            previous_report = previous[2]
            current_report = current[2]
            if previous_report.label_end >= current_report.label_start:
                issues.append(
                    "label windows overlap: "
                    f"{previous[0]} {previous_report.label_start.isoformat()}.."
                    f"{previous_report.label_end.isoformat()} and "
                    f"{current[0]} {current_report.label_start.isoformat()}.."
                    f"{current_report.label_end.isoformat()}"
                )

    windows = [
        _prediction_eval_window_summary_window(path, report) for path, _, report in sorted_reports
    ]
    model_summaries = _prediction_eval_window_summary_models([report for _, _, report in reports])
    source_state = {
        "report_count": len(reports),
        "report_paths": [str(path) for path, _, _ in reports],
        "window_count": len(windows),
        "total_evaluation_label_count": sum(
            report.evaluation_label_count for _, _, report in reports
        ),
        "model_names": sorted(model_summaries),
    }
    result: dict[str, Any] = {
        "ok": bool(reports) and not issues and not quality_gate_failures,
        "command": "prediction-eval-window-summary",
        "report_count": len(reports),
        "checked": len(report_paths),
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "window_count": len(windows),
        "windows": windows,
        "total_evaluation_label_count": source_state["total_evaluation_label_count"],
        "model_count": len(model_summaries),
        "model_names": sorted(model_summaries),
        "models": [model_summaries[name] for name in sorted(model_summaries)],
        "run_metadata": _prediction_eval_window_summary_run_metadata(
            args,
            report_paths,
            source_state=source_state,
        ),
    }
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        output_sha256 = _write_json_artifact(output, result)
        result["output"] = str(output)
        result["output_sha256"] = output_sha256
    return result


def _validate_prediction_eval_window_summary_report_metadata(
    *,
    path: Path,
    raw: dict[str, Any],
    report: PredictionEvalReportPayload,
    issues: list[str],
) -> None:
    run_metadata = raw.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append(f"{path}: run_metadata missing")
        return
    if run_metadata.get("command") != "prediction-eval-report":
        issues.append(f"{path}: run_metadata command mismatch")
    for key, expected in {
        "training_feature_cutoff": report.training_feature_cutoff.isoformat(),
        "train_start": report.train_start.isoformat(),
        "train_end": report.train_end.isoformat(),
        "feature_cutoff": report.feature_cutoff.isoformat(),
        "label_start": report.label_start.isoformat(),
        "label_end": report.label_end.isoformat(),
    }.items():
        if run_metadata.get(key) != expected:
            issues.append(f"{path}: run_metadata {key} mismatch")


def _prediction_eval_window_summary_window(
    path: Path,
    report: PredictionEvalReportPayload,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "training_feature_cutoff": report.training_feature_cutoff.isoformat(),
        "train_start": report.train_start.isoformat(),
        "train_end": report.train_end.isoformat(),
        "feature_cutoff": report.feature_cutoff.isoformat(),
        "label_start": report.label_start.isoformat(),
        "label_end": report.label_end.isoformat(),
        "readiness_status": report.readiness.status,
        "training_example_count": report.training_example_count,
        "evaluation_label_count": report.evaluation_label_count,
        "model_names": [model.model_name for model in report.models],
    }


def _prediction_eval_window_summary_models(
    reports: list[PredictionEvalReportPayload],
) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    brier_weights: dict[str, int] = {}
    brier_weighted_sums: dict[str, float] = {}
    log_loss_weights: dict[str, int] = {}
    log_loss_weighted_sums: dict[str, float] = {}
    for report in reports:
        for model in report.models:
            metrics = model.metrics
            item = totals.setdefault(
                model.model_name,
                {
                    "model_name": model.model_name,
                    "window_count": 0,
                    "label_count": 0,
                    "evaluated_count": 0,
                    "correct_count": 0,
                    "skipped_count": 0,
                    "accuracy": None,
                    "coverage_rate": None,
                    "weighted_brier_score": None,
                    "weighted_log_loss": None,
                    "brier_score_window_count": 0,
                    "log_loss_window_count": 0,
                    "skip_reason_counts": {},
                },
            )
            item["window_count"] += 1
            item["label_count"] += metrics.label_count
            item["evaluated_count"] += metrics.evaluated_count
            item["correct_count"] += metrics.correct_count
            item["skipped_count"] += metrics.skipped_count
            skip_counts = cast(dict[str, int], item["skip_reason_counts"])
            for reason, count in model.skip_reason_counts.items():
                skip_counts[reason] = skip_counts.get(reason, 0) + count
            if metrics.brier_score is not None and metrics.evaluated_count:
                brier_weights[model.model_name] = (
                    brier_weights.get(model.model_name, 0) + metrics.evaluated_count
                )
                brier_weighted_sums[model.model_name] = brier_weighted_sums.get(
                    model.model_name,
                    0.0,
                ) + (metrics.brier_score * metrics.evaluated_count)
                item["brier_score_window_count"] += 1
            if metrics.log_loss is not None and metrics.evaluated_count:
                log_loss_weights[model.model_name] = (
                    log_loss_weights.get(model.model_name, 0) + metrics.evaluated_count
                )
                log_loss_weighted_sums[model.model_name] = log_loss_weighted_sums.get(
                    model.model_name,
                    0.0,
                ) + (metrics.log_loss * metrics.evaluated_count)
                item["log_loss_window_count"] += 1
    for model_name, item in totals.items():
        evaluated_count = int(item["evaluated_count"])
        label_count = int(item["label_count"])
        correct_count = int(item["correct_count"])
        item["accuracy"] = correct_count / evaluated_count if evaluated_count else None
        item["coverage_rate"] = evaluated_count / label_count if label_count else None
        if brier_weights.get(model_name):
            item["weighted_brier_score"] = (
                brier_weighted_sums[model_name] / brier_weights[model_name]
            )
        if log_loss_weights.get(model_name):
            item["weighted_log_loss"] = (
                log_loss_weighted_sums[model_name] / log_loss_weights[model_name]
            )
    return totals


def _prediction_eval_window_summary_run_metadata(
    args: Any,
    report_paths: list[Path],
    *,
    source_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": "prediction-eval-window-summary",
        "report_paths": [str(path) for path in report_paths],
        "report_sha256": {str(path): _file_sha256(path) for path in report_paths},
        "verification_flags": {
            "require_report_run_metadata": bool(
                getattr(args, "require_report_run_metadata", False)
            ),
            "require_ready_reports": bool(getattr(args, "require_ready_reports", False)),
            "require_non_overlapping_label_windows": bool(
                getattr(args, "require_non_overlapping_label_windows", False)
            ),
        },
        "source_state": source_state,
    }


def _handle_verify_prediction_eval_window_summary(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-summary",
                "artifact": str(artifact_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_summary_verify_run_metadata(
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

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    if artifact.get("command") != "prediction-eval-window-summary":
        issues.append("artifact command must be prediction-eval-window-summary")

    windows = artifact.get("windows")
    if not isinstance(windows, list):
        issues.append("windows must be a list")
        windows = []
    models = artifact.get("models")
    if not isinstance(models, list):
        issues.append("models must be a list")
        models = []

    _validate_prediction_eval_window_summary_counts(
        artifact=artifact,
        windows=windows,
        models=models,
        issues=issues,
    )
    if bool(getattr(args, "require_run_metadata", False)):
        _validate_prediction_eval_window_summary_run_metadata(
            artifact=artifact,
            issues=issues,
        )
    if bool(getattr(args, "require_current_report_hashes", False)):
        _validate_prediction_eval_window_summary_current_hashes(
            artifact=artifact,
            windows=windows,
            issues=issues,
        )
    if bool(getattr(args, "require_non_overlapping_label_windows", False)):
        _validate_prediction_eval_window_summary_non_overlapping_windows(
            windows,
            issues=issues,
        )
    plan_path_raw = getattr(args, "plan", None)
    require_plan_match = bool(getattr(args, "require_plan_match", False))
    plan_state: dict[str, Any] | None = None
    if require_plan_match and plan_path_raw is None:
        issues.append("plan required for require_plan_match")
    if plan_path_raw is not None:
        plan_state = _validate_prediction_eval_window_summary_plan_match(
            plan_path=Path(plan_path_raw),
            windows=windows,
            issues=issues,
            require_match=require_plan_match,
        )

    min_window_count = getattr(args, "min_window_count", None)
    if min_window_count is not None:
        if not _is_plain_int(min_window_count):
            issues.append("min_window_count must be an integer")
        elif min_window_count < 0:
            issues.append("min_window_count must be a non-negative integer")
        elif len(windows) < min_window_count:
            quality_gate_failures.append("window_count_below_minimum")
    min_total_labels = getattr(args, "min_total_evaluation_labels", None)
    total_labels = _plain_int_or_zero(artifact.get("total_evaluation_label_count"))
    if min_total_labels is not None:
        if not _is_plain_int(min_total_labels):
            issues.append("min_total_evaluation_labels must be an integer")
        elif min_total_labels < 0:
            issues.append("min_total_evaluation_labels must be a non-negative integer")
        elif total_labels < min_total_labels:
            quality_gate_failures.append("total_evaluation_label_count_below_minimum")

    source_state = _prediction_eval_window_summary_verify_source_state(
        artifact=artifact,
        windows=windows,
        models=models,
        plan_state=plan_state,
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-eval-window-summary",
            "artifact": str(artifact_path),
            "checked": 1 + len(windows) + len(models),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "window_count": len(windows),
            "model_count": len(models),
            "total_evaluation_label_count": total_labels,
            "plan": plan_path_raw,
            "plan_matched": plan_state.get("matched") if plan_state is not None else None,
            "run_metadata": _prediction_eval_window_summary_verify_run_metadata(
                args,
                artifact_path,
                source_state=source_state,
            ),
        },
    )


def _validate_prediction_eval_window_summary_counts(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    models: list[Any],
    issues: list[str],
) -> None:
    if not _is_plain_int(artifact.get("window_count")):
        issues.append("window_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("window_count")):
        issues.append("window_count must be a non-negative integer")
    elif artifact["window_count"] != len(windows):
        issues.append("window_count mismatch")
    if not _is_plain_int(artifact.get("report_count")):
        issues.append("report_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("report_count")):
        issues.append("report_count must be a non-negative integer")
    elif artifact["report_count"] != len(windows):
        issues.append("report_count mismatch")
    if not _is_plain_int(artifact.get("model_count")):
        issues.append("model_count must be an integer")
    elif not _is_non_negative_plain_int(artifact.get("model_count")):
        issues.append("model_count must be a non-negative integer")
    elif artifact["model_count"] != len(models):
        issues.append("model_count mismatch")
    declared_total = artifact.get("total_evaluation_label_count")
    if not _is_plain_int(declared_total):
        issues.append("total_evaluation_label_count must be an integer")
    elif not _is_non_negative_plain_int(declared_total):
        issues.append("total_evaluation_label_count must be a non-negative integer")
    else:
        window_total = sum(
            count
            for window in windows
            if isinstance(window, dict)
            for count in (window.get("evaluation_label_count"),)
            if _is_non_negative_plain_int(count)
        )
        if declared_total != window_total:
            issues.append("total_evaluation_label_count mismatch")
    model_names = artifact.get("model_names")
    if not isinstance(model_names, list) or not all(isinstance(name, str) for name in model_names):
        issues.append("model_names must be a string list")
    else:
        actual_model_names = sorted(
            str(model.get("model_name"))
            for model in models
            if isinstance(model, dict) and isinstance(model.get("model_name"), str)
        )
        if model_names != actual_model_names:
            issues.append("model_names mismatch")
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            issues.append(f"windows[{index}] must be an object")
            continue
        for key in ("path", "label_start", "label_end"):
            if not isinstance(window.get(key), str) or not window.get(key):
                issues.append(f"windows[{index}].{key} missing")
        _validate_prediction_eval_window_summary_window_dates(window, index=index, issues=issues)
        sha_raw = window.get("sha256")
        if sha_raw is None or sha_raw == "":
            issues.append(f"windows[{index}].sha256 missing")
        elif not isinstance(sha_raw, str) or not _is_sha256_hex(sha_raw):
            issues.append(f"windows[{index}].sha256 invalid")
        if not _is_plain_int(window.get("evaluation_label_count")):
            issues.append(f"windows[{index}].evaluation_label_count must be an integer")
        elif not _is_non_negative_plain_int(window.get("evaluation_label_count")):
            issues.append(f"windows[{index}].evaluation_label_count must be a non-negative integer")
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            issues.append(f"models[{index}] must be an object")
            continue
        _validate_prediction_eval_window_summary_model(model, index=index, issues=issues)


def _validate_prediction_eval_window_summary_window_dates(
    window: dict[str, Any],
    *,
    index: int,
    issues: list[str],
) -> None:
    parsed: dict[str, dt.date] = {}
    for key in (
        "training_feature_cutoff",
        "train_start",
        "train_end",
        "feature_cutoff",
        "label_start",
        "label_end",
    ):
        value = window.get(key)
        if not isinstance(value, str) or not value:
            issues.append(f"windows[{index}].{key} missing")
            continue
        try:
            parsed[key] = dt.date.fromisoformat(value)
        except ValueError:
            issues.append(f"windows[{index}].{key} must be an ISO date")
    if {"train_start", "train_end"} <= parsed.keys() and parsed["train_start"] > parsed[
        "train_end"
    ]:
        issues.append(f"windows[{index}].train_start must be on or before train_end")
    if {"label_start", "label_end"} <= parsed.keys() and parsed["label_start"] > parsed[
        "label_end"
    ]:
        issues.append(f"windows[{index}].label_start must be on or before label_end")
    if {"training_feature_cutoff", "train_start"} <= parsed.keys() and parsed[
        "training_feature_cutoff"
    ] >= parsed["train_start"]:
        issues.append(f"windows[{index}].training_feature_cutoff must be before train_start")
    if {"train_end", "feature_cutoff"} <= parsed.keys() and parsed["train_end"] > parsed[
        "feature_cutoff"
    ]:
        issues.append(f"windows[{index}].train_end must be on or before feature_cutoff")
    if {"feature_cutoff", "label_start"} <= parsed.keys() and parsed["feature_cutoff"] >= parsed[
        "label_start"
    ]:
        issues.append(f"windows[{index}].feature_cutoff must be before label_start")


def _validate_prediction_eval_window_summary_model(
    model: dict[str, Any],
    *,
    index: int,
    issues: list[str],
) -> None:
    if not isinstance(model.get("model_name"), str) or not model.get("model_name"):
        issues.append(f"models[{index}].model_name missing")
    for key in (
        "window_count",
        "label_count",
        "evaluated_count",
        "correct_count",
        "skipped_count",
    ):
        if not _is_plain_int(model.get(key)):
            issues.append(f"models[{index}].{key} must be an integer")
        elif not _is_non_negative_plain_int(model.get(key)):
            issues.append(f"models[{index}].{key} must be a non-negative integer")
    if (
        _is_non_negative_plain_int(model.get("evaluated_count"))
        and _is_non_negative_plain_int(model.get("skipped_count"))
        and _is_non_negative_plain_int(model.get("label_count"))
        and model["evaluated_count"] + model["skipped_count"] != model["label_count"]
    ):
        issues.append(f"models[{index}].label_count mismatch")
    if (
        _is_non_negative_plain_int(model.get("correct_count"))
        and _is_non_negative_plain_int(model.get("evaluated_count"))
        and model["correct_count"] > model["evaluated_count"]
    ):
        issues.append(f"models[{index}].correct_count exceeds evaluated_count")
    if _is_non_negative_plain_int(model.get("evaluated_count")):
        expected_accuracy = (
            model["correct_count"] / model["evaluated_count"]
            if _is_non_negative_plain_int(model.get("correct_count")) and model["evaluated_count"]
            else None
        )
        if not _prediction_eval_optional_rate_valid(model.get("accuracy")):
            issues.append(f"models[{index}].accuracy must be a number between 0 and 1")
        elif not _numeric_or_none_matches(model.get("accuracy"), expected_accuracy):
            issues.append(f"models[{index}].accuracy mismatch")
    if _is_non_negative_plain_int(model.get("label_count")):
        expected_coverage = (
            model["evaluated_count"] / model["label_count"]
            if _is_non_negative_plain_int(model.get("evaluated_count")) and model["label_count"]
            else None
        )
        if not _prediction_eval_optional_rate_valid(model.get("coverage_rate")):
            issues.append(f"models[{index}].coverage_rate must be a number between 0 and 1")
        elif not _numeric_or_none_matches(model.get("coverage_rate"), expected_coverage):
            issues.append(f"models[{index}].coverage_rate mismatch")


def _validate_prediction_eval_window_summary_run_metadata(
    *,
    artifact: dict[str, Any],
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        issues.append("run_metadata missing")
        return
    if run_metadata.get("command") != "prediction-eval-window-summary":
        issues.append("run_metadata command mismatch")
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        issues.append("run_metadata source_state missing")
        return
    expected_source_state_keys = {
        "model_names",
        "report_count",
        "report_paths",
        "window_count",
        "total_evaluation_label_count",
    }
    for key in sorted(set(source_state) - expected_source_state_keys):
        issues.append(f"run_metadata source_state unexpected: {key}")
    for key in ("report_count", "window_count", "total_evaluation_label_count"):
        if not _is_plain_int(source_state.get(key)):
            issues.append(f"run_metadata source_state {key} must be an integer")
            continue
        if not _is_non_negative_plain_int(source_state.get(key)):
            issues.append(f"run_metadata source_state {key} must be a non-negative integer")
            continue
        if source_state.get(key) != artifact.get(key):
            issues.append(f"run_metadata source_state {key} mismatch")


def _validate_prediction_eval_window_summary_current_hashes(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    issues: list[str],
) -> None:
    run_metadata = artifact.get("run_metadata")
    metadata_hashes = run_metadata.get("report_sha256") if isinstance(run_metadata, dict) else None
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            continue
        path_raw = window.get("path")
        sha_raw = window.get("sha256")
        if not isinstance(path_raw, str) or not path_raw:
            continue
        path = Path(path_raw)
        try:
            current_sha = _file_sha256(path)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"windows[{index}].path unreadable: {exc}")
            continue
        if isinstance(sha_raw, str) and _is_sha256_hex(sha_raw) and current_sha != sha_raw:
            issues.append(f"windows[{index}].sha256 mismatch")
        if isinstance(metadata_hashes, dict) and path_raw in metadata_hashes:
            metadata_sha = metadata_hashes.get(path_raw)
            if not isinstance(metadata_sha, str) or not _is_sha256_hex(metadata_sha):
                issues.append(f"run_metadata report_sha256 invalid: {path_raw}")
            elif metadata_sha != current_sha:
                issues.append(f"run_metadata report_sha256 mismatch: {path_raw}")


def _validate_prediction_eval_window_summary_non_overlapping_windows(
    windows: list[Any],
    *,
    issues: list[str],
) -> None:
    parsed_windows: list[tuple[str, dt.date, dt.date]] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            continue
        try:
            label_start = dt.date.fromisoformat(str(window.get("label_start")))
            label_end = dt.date.fromisoformat(str(window.get("label_end")))
        except ValueError:
            issues.append(f"windows[{index}].label window invalid")
            continue
        if label_start > label_end:
            issues.append(f"windows[{index}].label window reversed")
            continue
        parsed_windows.append((str(window.get("path")), label_start, label_end))
    for previous, current in zip(
        sorted(parsed_windows, key=lambda item: (item[1], item[2])),
        sorted(parsed_windows, key=lambda item: (item[1], item[2]))[1:],
    ):
        if previous[2] >= current[1]:
            issues.append(
                "label windows overlap: "
                f"{previous[0]} {previous[1].isoformat()}..{previous[2].isoformat()} "
                f"and {current[0]} {current[1].isoformat()}..{current[2].isoformat()}"
            )


def _validate_prediction_eval_window_summary_plan_match(
    *,
    plan_path: Path,
    windows: list[Any],
    issues: list[str],
    require_match: bool,
) -> dict[str, Any]:
    try:
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"plan failed to load: {exc}")
        return {"path": str(plan_path), "matched": False, "issue": "load_failed"}
    if not isinstance(raw_plan, dict):
        issues.append("plan must be an object")
        return {"path": str(plan_path), "matched": False, "issue": "not_object"}
    plan_payload = {
        key: value for key, value in raw_plan.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"invalid plan: {exc}")
        return {"path": str(plan_path), "matched": False, "issue": "invalid_plan"}
    expected_paths = _prediction_eval_window_plan_expected_report_paths(plan)
    actual_paths = [
        window.get("path")
        for window in windows
        if isinstance(window, dict) and isinstance(window.get("path"), str)
    ]
    plan_issues: list[str] = []
    if actual_paths != expected_paths:
        plan_issues.append("summary report paths do not match plan")
    expected_windows = [
        {
            "path": path,
            "feature_cutoff": window.feature_cutoff.isoformat(),
            "label_start": window.label_start.isoformat(),
            "label_end": window.label_end.isoformat(),
        }
        for path, window in zip(expected_paths, plan.windows)
    ]
    actual_windows = [
        {
            "path": window.get("path"),
            "feature_cutoff": window.get("feature_cutoff"),
            "label_start": window.get("label_start"),
            "label_end": window.get("label_end"),
        }
        for window in windows
        if isinstance(window, dict)
    ]
    if actual_windows != expected_windows:
        plan_issues.append("summary windows do not match plan")
    if require_match:
        issues.extend(plan_issues)
    return {
        "path": str(plan_path),
        "sha256": _file_sha256(plan_path),
        "matched": not plan_issues,
        "issue_count": len(plan_issues),
        "issues": plan_issues,
        "expected_report_paths": expected_paths,
        "actual_report_paths": actual_paths,
    }


def _prediction_eval_window_plan_expected_report_paths(
    plan: PredictionEvalWindowPlanPayload,
) -> list[str]:
    paths: list[str] = []
    for window in plan.windows:
        parsed = _option_value_from_command(window.eval_report_command, "--output")
        paths.append(parsed if parsed is not None else "")
    return paths


def _prediction_eval_window_summary_verify_source_state(
    *,
    artifact: dict[str, Any],
    windows: list[Any],
    models: list[Any],
    plan_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_state: dict[str, Any] = {
        "report_count": artifact.get("report_count"),
        "window_count": len(windows),
        "total_evaluation_label_count": artifact.get("total_evaluation_label_count"),
        "model_names": [
            model.get("model_name")
            for model in models
            if isinstance(model, dict) and isinstance(model.get("model_name"), str)
        ],
        "window_paths": [
            window.get("path")
            for window in windows
            if isinstance(window, dict) and isinstance(window.get("path"), str)
        ],
        "windows": [
            _prediction_eval_window_summary_source_state_window(window)
            for window in windows
            if isinstance(window, dict)
        ],
    }
    if plan_state is not None:
        source_state["plan"] = plan_state
    return source_state


def _prediction_eval_window_summary_source_state_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": window.get("path"),
        "training_feature_cutoff": window.get("training_feature_cutoff"),
        "train_start": window.get("train_start"),
        "train_end": window.get("train_end"),
        "feature_cutoff": window.get("feature_cutoff"),
        "label_start": window.get("label_start"),
        "label_end": window.get("label_end"),
        "evaluation_label_count": window.get("evaluation_label_count"),
    }


def _prediction_eval_window_summary_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-summary",
        "artifact": str(artifact_path),
        "artifact_sha256": _file_sha256(artifact_path) if artifact_path.exists() else None,
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_current_report_hashes": bool(
                getattr(args, "require_current_report_hashes", False)
            ),
            "require_non_overlapping_label_windows": bool(
                getattr(args, "require_non_overlapping_label_windows", False)
            ),
            "plan": getattr(args, "plan", None),
            "require_plan_match": bool(getattr(args, "require_plan_match", False)),
            "min_window_count": getattr(args, "min_window_count", None),
            "min_total_evaluation_labels": getattr(
                args,
                "min_total_evaluation_labels",
                None,
            ),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _handle_verify_prediction_eval_window_run(args: Any) -> dict[str, Any]:
    plan_path = Path(args.plan)

    def failure_result(*, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-eval-window-run",
                "plan": str(plan_path),
                "checked": 0,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_eval_window_run_verify_run_metadata(args, plan_path),
            },
        )

    try:
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"failed to load plan: {exc}"])
    if not isinstance(raw_plan, dict):
        return failure_result(issues=["plan must be an object"])
    plan_payload = {
        key: value for key, value in raw_plan.items() if key not in {"command", "run_metadata"}
    }
    try:
        plan = PredictionEvalWindowPlanPayload.model_validate(plan_payload)
    except Exception as exc:  # noqa: BLE001
        return failure_result(issues=[f"invalid window plan: {exc}"])

    issues: list[str] = []
    congress_archive_manifest, congress_archive_manifest_issues = (
        _prediction_eval_window_run_plan_congress_archive_manifest(raw_plan)
    )
    issues.extend(congress_archive_manifest_issues)
    expected_window_verifiers = _prediction_eval_window_run_expected_window_verifiers(
        plan,
        congress_archive_manifest=congress_archive_manifest,
    )
    expected_summary_verify = _prediction_eval_window_run_expected_summary_verify(
        args,
        plan,
        plan_path=plan_path,
        issues=issues,
    )
    verifier_results = _prediction_eval_window_run_verifier_results(
        expected_window_verifiers,
        require=bool(getattr(args, "require_window_verifiers", False)),
        issues=issues,
    )
    summary_result = None
    if expected_summary_verify is not None:
        summary_result = _prediction_eval_window_run_verifier_result(
            expected_summary_verify,
            require=bool(getattr(args, "require_summary_verify", False)),
            issues=issues,
        )
    elif bool(getattr(args, "require_summary_verify", False)):
        issues.append("summary verifier path missing")

    loaded_verifier_count = sum(1 for item in verifier_results if item.get("loaded"))
    if summary_result is not None and summary_result.get("loaded"):
        loaded_verifier_count += 1
    missing_verifier_count = sum(1 for item in verifier_results if item.get("missing"))
    if summary_result is not None and summary_result.get("missing"):
        missing_verifier_count += 1
    failing_verifier_count = sum(1 for item in verifier_results if item.get("status") == "failed")
    if summary_result is not None and summary_result.get("status") == "failed":
        failing_verifier_count += 1
    source_state = _prediction_eval_window_run_verify_source_state(
        plan=plan,
        expected_window_verifiers=expected_window_verifiers,
        expected_summary_verify=expected_summary_verify,
        verifier_results=verifier_results,
        summary_result=summary_result,
        loaded_verifier_count=loaded_verifier_count,
        missing_verifier_count=missing_verifier_count,
        failing_verifier_count=failing_verifier_count,
        congress_archive_manifest=congress_archive_manifest,
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues,
            "command": "verify-prediction-eval-window-run",
            "plan": str(plan_path),
            "plan_sha256": _file_sha256(plan_path),
            "summary_verify": (
                str(expected_summary_verify["path"])
                if expected_summary_verify is not None
                else None
            ),
            "checked": len(verifier_results) + (1 if summary_result is not None else 0),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": [],
            "quality_gate_failure_count": 0,
            "window_count": plan.window_count,
            "expected_window_verifier_count": len(expected_window_verifiers),
            "loaded_verifier_count": loaded_verifier_count,
            "missing_verifier_count": missing_verifier_count,
            "failing_verifier_count": failing_verifier_count,
            "window_verifiers": verifier_results,
            "summary_verifier": summary_result,
            "run_metadata": _prediction_eval_window_run_verify_run_metadata(
                args,
                plan_path,
                source_state=source_state,
            ),
        },
    )


def _prediction_eval_window_run_expected_window_verifiers(
    plan: PredictionEvalWindowPlanPayload,
    *,
    congress_archive_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    verifiers: list[dict[str, Any]] = []
    for window in plan.windows:
        for kind, command, expected_command in (
            (
                "input_inventory",
                window.input_inventory_verify_command,
                "verify-prediction-input-inventory",
            ),
            (
                "eval_manifest",
                window.eval_manifest_verify_command,
                "verify-prediction-eval-manifest",
            ),
        ):
            output = _option_value_from_command(command, "--output")
            verifiers.append(
                {
                    "kind": kind,
                    "window_id": window.window_id,
                    "path": Path(output) if output is not None else None,
                    "expected_command": expected_command,
                    "expected_congress_archive_manifest": congress_archive_manifest,
                    "expected_verification_flags": (
                        _prediction_eval_window_run_expected_verification_flags(
                            kind=kind,
                            command=command,
                        )
                    ),
                }
            )
    return verifiers


def _prediction_eval_window_run_expected_verification_flags(
    *,
    kind: str,
    command: str,
) -> dict[str, Any]:
    if kind == "input_inventory":
        return {
            "require_run_metadata": "--require-run-metadata" in command,
            "require_congress_archive_manifest": ("--require-congress-archive-manifest" in command),
            "require_portable_jurisdiction_ids": ("--require-portable-jurisdiction-ids" in command),
            "require_portable_body_ids": "--require-portable-body-ids" in command,
            "require_portable_session_ids": "--require-portable-session-ids" in command,
            "require_source_families": _option_values_from_command(
                command,
                "--require-source-family",
            ),
            "min_training_labels": _int_option_value_from_command(
                command,
                "--min-training-labels",
            ),
            "min_evaluation_labels": _int_option_value_from_command(
                command,
                "--min-evaluation-labels",
            ),
            "min_training_feature_vote_history_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-vote-history-source-coverage-rate",
                )
            ),
            "min_evaluation_feature_vote_history_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-vote-history-source-coverage-rate",
                )
            ),
            "min_training_label_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-label-official-source-url-coverage-rate",
                )
            ),
            "min_evaluation_label_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-label-official-source-url-coverage-rate",
                )
            ),
            "min_bill_official_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-bill-official-source-url-coverage-rate",
                )
            ),
            "min_bill_sponsor_availability_rate": _float_option_value_from_command(
                command,
                "--min-bill-sponsor-availability-rate",
            ),
            "min_ontology_official_source_anchor_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-ontology-official-source-anchor-coverage-rate",
                )
            ),
            "min_fec_contributions": _int_option_value_from_command(
                command,
                "--min-fec-contributions",
            ),
            "min_member_attributed_fec_contributions": _int_option_value_from_command(
                command,
                "--min-member-attributed-fec-contributions",
            ),
            "min_members_with_fec_candidate_id": _int_option_value_from_command(
                command,
                "--min-members-with-fec-candidate-id",
            ),
            "min_public_statement_signals": _int_option_value_from_command(
                command,
                "--min-public-statement-signals",
            ),
            "min_members_with_public_statement_signals": _int_option_value_from_command(
                command,
                "--min-members-with-public-statement-signals",
            ),
        }
    if kind == "eval_manifest":
        return {
            "require_artifact_run_metadata": "--require-artifact-run-metadata" in command,
            "require_congress_archive_manifest": ("--require-congress-archive-manifest" in command),
            "require_model_names": _option_values_from_command(
                command,
                "--require-model-name",
            ),
            "require_ontology_feature_signals": ("--require-ontology-feature-signals" in command),
            "min_training_feature_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-source-url-coverage-rate",
                )
            ),
            "min_training_feature_official_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-training-feature-official-source-coverage-rate",
                )
            ),
            "min_evaluation_feature_source_url_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-source-url-coverage-rate",
                )
            ),
            "min_evaluation_feature_official_source_coverage_rate": (
                _float_option_value_from_command(
                    command,
                    "--min-evaluation-feature-official-source-coverage-rate",
                )
            ),
            "min_evaluation_source_url_coverage_rate": _float_option_value_from_command(
                command,
                "--min-evaluation-source-url-coverage-rate",
            ),
            "require_fail_on_unknown_bill_semantic_availability": (
                "--require-fail-on-unknown-bill-semantic-availability" in command
            ),
            "require_fail_on_unknown_bill_signal_availability": (
                "--require-fail-on-unknown-bill-signal-availability" in command
            ),
            "require_fail_on_unknown_ontology_edge_availability": (
                "--require-fail-on-unknown-ontology-edge-availability" in command
            ),
            "require_fail_on_unknown_contribution_signal_availability": (
                "--require-fail-on-unknown-contribution-signal-availability" in command
            ),
            "require_fail_on_unknown_statement_signal_availability": (
                "--require-fail-on-unknown-statement-signal-availability" in command
            ),
        }
    return {}


def _prediction_eval_window_run_plan_congress_archive_manifest(
    raw_plan: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    run_metadata = raw_plan.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None, []
    congress_archive_manifest = run_metadata.get("congress_archive_manifest")
    if congress_archive_manifest is None:
        return None, []
    if isinstance(congress_archive_manifest, dict):
        return congress_archive_manifest, []
    if isinstance(congress_archive_manifest, str) and congress_archive_manifest:
        manifest_path = Path(congress_archive_manifest)
        if not manifest_path.is_file():
            return None, [
                f"plan congress_archive_manifest file not found: {manifest_path}",
            ]
        return _congress_archive_manifest_metadata(manifest_path), []
    return None, ["plan congress_archive_manifest must be a path string or metadata object"]


def _prediction_eval_window_run_expected_summary_verify(
    args: Any,
    plan: PredictionEvalWindowPlanPayload,
    *,
    plan_path: Path,
    issues: list[str],
) -> dict[str, Any] | None:
    derived = _option_value_from_command(plan.verify_summary_command, "--output")
    provided = getattr(args, "summary_verify", None)
    path_raw = provided if provided is not None else derived
    if path_raw is None:
        return None
    if (
        bool(getattr(args, "require_summary_verify", False))
        and provided is not None
        and derived is not None
        and provided != derived
    ):
        issues.append("summary_verify does not match plan verify_summary_command output")
    return {
        "kind": "summary",
        "window_id": None,
        "path": Path(path_raw),
        "expected_command": "verify-prediction-eval-window-summary",
        "require_plan_matched": True,
        "expected_plan": str(plan_path),
        "expected_plan_sha256": _file_sha256(plan_path),
        "expected_windows": _prediction_eval_window_run_expected_summary_windows(plan),
    }


def _prediction_eval_window_run_expected_summary_windows(
    plan: PredictionEvalWindowPlanPayload,
) -> list[dict[str, Any]]:
    report_paths = _prediction_eval_window_plan_expected_report_paths(plan)
    return [
        {
            "path": path,
            "training_feature_cutoff": window.training_feature_cutoff.isoformat(),
            "train_start": window.train_start.isoformat(),
            "train_end": window.train_end.isoformat(),
            "feature_cutoff": window.feature_cutoff.isoformat(),
            "label_start": window.label_start.isoformat(),
            "label_end": window.label_end.isoformat(),
        }
        for path, window in zip(report_paths, plan.windows)
    ]


def _prediction_eval_window_run_verifier_results(
    expected_verifiers: list[dict[str, Any]],
    *,
    require: bool,
    issues: list[str],
) -> list[dict[str, Any]]:
    return [
        _prediction_eval_window_run_verifier_result(verifier, require=require, issues=issues)
        for verifier in expected_verifiers
    ]


def _prediction_eval_window_run_verifier_result(
    expected_verifier: dict[str, Any],
    *,
    require: bool,
    issues: list[str],
) -> dict[str, Any]:
    path = expected_verifier.get("path")
    path_text = str(path) if path is not None else None
    result = {
        "kind": expected_verifier.get("kind"),
        "window_id": expected_verifier.get("window_id"),
        "path": path_text,
        "expected_command": expected_verifier.get("expected_command"),
        "loaded": False,
        "missing": False,
        "status": "unchecked",
        "ok": None,
        "issue_count": None,
        "quality_gate_failure_count": None,
        "sha256": None,
    }
    if not isinstance(path, Path):
        result["missing"] = True
        result["status"] = "missing"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(expected_verifier, "output missing")
            )
        return result
    if not path.exists():
        result["missing"] = True
        result["status"] = "missing"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"artifact missing: {path}",
                )
            )
        return result
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        result["sha256"] = _file_sha256(path)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"failed to load artifact: {exc}",
                )
            )
        return result
    if not isinstance(artifact, dict):
        result["status"] = "failed"
        if require:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "artifact must be an object",
                )
            )
        return result
    result["loaded"] = True
    result["ok"] = artifact.get("ok")
    issue_count = artifact.get("issue_count")
    quality_gate_failure_count = artifact.get("quality_gate_failure_count")
    result["issue_count"] = issue_count if _is_plain_int(issue_count) else None
    result["quality_gate_failure_count"] = (
        quality_gate_failure_count if _is_plain_int(quality_gate_failure_count) else None
    )
    verifier_issues = _prediction_eval_window_run_artifact_issues(expected_verifier, artifact)
    if verifier_issues:
        result["status"] = "failed"
        if require:
            issues.extend(verifier_issues)
    else:
        result["status"] = "passed"
    return result


def _prediction_eval_window_run_artifact_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    expected_command = expected_verifier.get("expected_command")
    if artifact.get("command") != expected_command:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                f"command mismatch: expected {expected_command}",
            )
        )
    if artifact.get("ok") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(expected_verifier, "ok is not true")
        )
    issue_count = artifact.get("issue_count")
    if not _is_plain_int(issue_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "issue_count must be an integer",
            )
        )
    elif not _is_non_negative_plain_int(issue_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "issue_count must be a non-negative integer",
            )
        )
    elif issue_count != 0:
        issues.append(
            _prediction_eval_window_run_verifier_issue(expected_verifier, "issue_count is not zero")
        )
    quality_gate_failure_count = artifact.get("quality_gate_failure_count")
    if not _is_plain_int(quality_gate_failure_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count must be an integer",
            )
        )
    elif not _is_non_negative_plain_int(quality_gate_failure_count):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count must be a non-negative integer",
            )
        )
    elif quality_gate_failure_count != 0:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "quality_gate_failure_count is not zero",
            )
        )
    if expected_verifier.get("require_plan_matched") and artifact.get("plan_matched") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "plan_matched is not true",
            )
        )
    if expected_verifier.get("require_plan_matched"):
        issues.extend(
            _prediction_eval_window_run_summary_plan_metadata_issues(
                expected_verifier,
                artifact,
            )
        )
    issues.extend(
        _prediction_eval_window_run_verification_flags_issues(
            expected_verifier,
            artifact,
        )
    )
    issues.extend(
        _prediction_eval_window_run_congress_archive_manifest_issues(
            expected_verifier,
            artifact,
        )
    )
    return issues


def _prediction_eval_window_run_verification_flags_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    expected_flags = expected_verifier.get("expected_verification_flags")
    if not isinstance(expected_flags, dict) or not expected_flags:
        return []
    run_metadata = artifact.get("run_metadata")
    verification_flags = (
        run_metadata.get("verification_flags") if isinstance(run_metadata, dict) else None
    )
    if not isinstance(verification_flags, dict):
        return [
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        ]
    issues: list[str] = []
    for key, expected_value in expected_flags.items():
        actual_value = verification_flags.get(key)
        if actual_value != expected_value:
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"run_metadata verification_flags mismatch: {key}",
                )
            )
    return issues


def _prediction_eval_window_run_congress_archive_manifest_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    expected = expected_verifier.get("expected_congress_archive_manifest")
    if expected is None:
        return []
    issues: list[str] = []
    run_metadata = artifact.get("run_metadata")
    verification_flags = (
        run_metadata.get("verification_flags") if isinstance(run_metadata, dict) else None
    )
    if not isinstance(verification_flags, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        )
    elif verification_flags.get("require_congress_archive_manifest") is not True:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata require_congress_archive_manifest is not true",
            )
        )
    source_artifact_path = _prediction_eval_window_run_verified_source_artifact_path(
        expected_verifier,
        artifact,
    )
    if source_artifact_path is None:
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "verified source artifact path missing",
            )
        )
        return issues
    try:
        source_artifact = json.loads(source_artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                f"failed to load verified source artifact: {exc}",
            )
        )
        return issues
    if not isinstance(source_artifact, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "verified source artifact must be an object",
            )
        )
        return issues
    actual = _prediction_eval_window_run_source_congress_archive_manifest(
        expected_verifier,
        source_artifact,
    )
    if not isinstance(actual, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "congress_archive_manifest missing",
            )
        )
        return issues
    for key in ("path", "sha256"):
        if actual.get(key) != expected.get(key):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    f"congress_archive_manifest {key} mismatch",
                )
            )
    return issues


def _prediction_eval_window_run_verified_source_artifact_path(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> Path | None:
    if expected_verifier.get("kind") == "input_inventory":
        path = artifact.get("artifact")
    elif expected_verifier.get("kind") == "eval_manifest":
        path = artifact.get("manifest")
    else:
        path = None
    if not isinstance(path, str) or not path:
        return None
    return Path(path)


def _prediction_eval_window_run_source_congress_archive_manifest(
    expected_verifier: dict[str, Any],
    source_artifact: dict[str, Any],
) -> Any:
    if expected_verifier.get("kind") == "input_inventory":
        run_metadata = source_artifact.get("run_metadata")
        if isinstance(run_metadata, dict):
            return run_metadata.get("congress_archive_manifest")
        return None
    if expected_verifier.get("kind") == "eval_manifest":
        inputs = source_artifact.get("inputs")
        if isinstance(inputs, dict):
            return inputs.get("congress_archive_manifest")
        return None
    return None


def _prediction_eval_window_run_summary_plan_metadata_issues(
    expected_verifier: dict[str, Any],
    artifact: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    run_metadata = artifact.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return [
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata missing",
            )
        ]
    verification_flags = run_metadata.get("verification_flags")
    if not isinstance(verification_flags, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata verification_flags missing",
            )
        )
    else:
        if verification_flags.get("plan") != expected_verifier.get("expected_plan"):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata plan mismatch",
                )
            )
        for flag in (
            "require_plan_match",
            "require_current_report_hashes",
            "require_non_overlapping_label_windows",
        ):
            if verification_flags.get(flag) is not True:
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        f"run_metadata {flag} is not true",
                    )
                )
    source_state = run_metadata.get("source_state")
    plan_state = source_state.get("plan") if isinstance(source_state, dict) else None
    if not isinstance(plan_state, dict):
        issues.append(
            _prediction_eval_window_run_verifier_issue(
                expected_verifier,
                "run_metadata source_state plan missing",
            )
        )
    else:
        plan_sha256 = plan_state.get("sha256")
        if not isinstance(plan_sha256, str) or not _is_sha256_hex(plan_sha256):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata source_state plan sha256 invalid",
                )
            )
        elif plan_sha256 != expected_verifier.get("expected_plan_sha256"):
            issues.append(
                _prediction_eval_window_run_verifier_issue(
                    expected_verifier,
                    "run_metadata source_state plan sha256 mismatch",
                )
            )
        expected_windows = expected_verifier.get("expected_windows")
        actual_windows = source_state.get("windows") if isinstance(source_state, dict) else None
        if expected_windows is not None:
            if not isinstance(actual_windows, list):
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        "run_metadata source_state windows missing",
                    )
                )
            elif [
                _prediction_eval_window_run_summary_window_projection(window)
                for window in actual_windows
                if isinstance(window, dict)
            ] != expected_windows:
                issues.append(
                    _prediction_eval_window_run_verifier_issue(
                        expected_verifier,
                        "run_metadata source_state windows mismatch",
                    )
                )
    return issues


def _prediction_eval_window_run_summary_window_projection(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": window.get("path"),
        "training_feature_cutoff": window.get("training_feature_cutoff"),
        "train_start": window.get("train_start"),
        "train_end": window.get("train_end"),
        "feature_cutoff": window.get("feature_cutoff"),
        "label_start": window.get("label_start"),
        "label_end": window.get("label_end"),
    }


def _prediction_eval_window_run_verifier_issue(
    expected_verifier: dict[str, Any],
    message: str,
) -> str:
    kind = expected_verifier.get("kind")
    window_id = expected_verifier.get("window_id")
    if isinstance(window_id, str) and window_id:
        return f"{kind} verifier {window_id}: {message}"
    return f"{kind} verifier: {message}"


def _prediction_eval_window_run_verify_source_state(
    *,
    plan: PredictionEvalWindowPlanPayload,
    expected_window_verifiers: list[dict[str, Any]],
    expected_summary_verify: dict[str, Any] | None,
    verifier_results: list[dict[str, Any]],
    summary_result: dict[str, Any] | None,
    loaded_verifier_count: int,
    missing_verifier_count: int,
    failing_verifier_count: int,
    congress_archive_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_results = [result for result in verifier_results if result.get("loaded")]
    if summary_result is not None and summary_result.get("loaded"):
        loaded_results.append(summary_result)
    expected_verifier_hash_count = len(expected_window_verifiers) + (
        1 if expected_summary_verify is not None else 0
    )
    verifier_sha256 = {
        str(result["path"]): result["sha256"]
        for result in loaded_results
        if isinstance(result.get("path"), str) and isinstance(result.get("sha256"), str)
    }
    strict_flags = _prediction_eval_window_run_strict_flag_state(expected_window_verifiers)
    return {
        "plan_version": plan.plan_version,
        "window_count": plan.window_count,
        "window_ids": [window.window_id for window in plan.windows],
        "expected_window_verifier_paths": [
            str(verifier["path"])
            for verifier in expected_window_verifiers
            if verifier.get("path") is not None
        ],
        "expected_summary_verify_path": (
            str(expected_summary_verify["path"]) if expected_summary_verify is not None else None
        ),
        "loaded_verifier_count": loaded_verifier_count,
        "missing_verifier_count": missing_verifier_count,
        "failing_verifier_count": failing_verifier_count,
        "expected_verifier_hash_count": expected_verifier_hash_count,
        "verifier_hash_count": len(verifier_sha256),
        "verifier_hash_coverage_complete": (len(verifier_sha256) == expected_verifier_hash_count),
        "verifier_sha256": verifier_sha256,
        "requires_training_labels": all(
            "--min-training-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        "requires_evaluation_labels": all(
            "--min-evaluation-labels 1" in window.input_inventory_verify_command
            for window in plan.windows
        ),
        **strict_flags,
        "congress_archive_manifest": congress_archive_manifest,
    }


def _prediction_eval_window_run_strict_flag_state(
    expected_window_verifiers: list[dict[str, Any]],
) -> dict[str, bool]:
    input_flags = [
        flags
        for verifier in expected_window_verifiers
        if verifier.get("kind") == "input_inventory"
        and isinstance((flags := verifier.get("expected_verification_flags")), dict)
    ]
    eval_flags = [
        flags
        for verifier in expected_window_verifiers
        if verifier.get("kind") == "eval_manifest"
        and isinstance((flags := verifier.get("expected_verification_flags")), dict)
    ]
    input_portable = all(
        flags.get("require_portable_jurisdiction_ids") is True
        and flags.get("require_portable_body_ids") is True
        and flags.get("require_portable_session_ids") is True
        for flags in input_flags
    )
    input_source_families = all(
        flags.get("require_source_families") == ["congress_vote", "congress_bill"]
        for flags in input_flags
    )
    input_official_thresholds = all(
        flags.get("min_training_label_official_source_url_coverage_rate") is not None
        and flags.get("min_evaluation_label_official_source_url_coverage_rate") is not None
        and flags.get("min_bill_official_source_url_coverage_rate") is not None
        and flags.get("min_bill_sponsor_availability_rate") is not None
        and flags.get("min_ontology_official_source_anchor_coverage_rate") is not None
        for flags in input_flags
    )
    input_optional_evidence = all(
        flags.get("min_fec_contributions") == 1
        and flags.get("min_member_attributed_fec_contributions") == 1
        and flags.get("min_members_with_fec_candidate_id") == 1
        and flags.get("min_public_statement_signals") == 1
        and flags.get("min_members_with_public_statement_signals") == 1
        for flags in input_flags
    )
    eval_model_suite = all(
        flags.get("require_model_names")
        == [
            "member_vote_rate_baseline",
            "ontology_signal_model",
            "learned_signal_logistic",
        ]
        for flags in eval_flags
    )
    eval_official_thresholds = all(
        flags.get("min_training_feature_source_url_coverage_rate") is not None
        and flags.get("min_training_feature_official_source_coverage_rate") is not None
        and flags.get("min_evaluation_feature_source_url_coverage_rate") is not None
        and flags.get("min_evaluation_feature_official_source_coverage_rate") is not None
        and flags.get("min_evaluation_source_url_coverage_rate") is not None
        for flags in eval_flags
    )
    eval_unknown_availability = all(
        flags.get("require_fail_on_unknown_bill_semantic_availability") is True
        and flags.get("require_fail_on_unknown_bill_signal_availability") is True
        and flags.get("require_fail_on_unknown_ontology_edge_availability") is True
        and flags.get("require_fail_on_unknown_contribution_signal_availability") is True
        and flags.get("require_fail_on_unknown_statement_signal_availability") is True
        for flags in eval_flags
    )
    eval_ontology_features = all(
        flags.get("require_ontology_feature_signals") is True for flags in eval_flags
    )
    return {
        "requires_input_inventory_portable_ids": bool(input_flags) and input_portable,
        "requires_input_inventory_congress_source_families": (
            bool(input_flags) and input_source_families
        ),
        "requires_input_inventory_official_source_thresholds": (
            bool(input_flags) and input_official_thresholds
        ),
        "requires_input_inventory_optional_evidence": (
            bool(input_flags) and input_optional_evidence
        ),
        "requires_eval_manifest_model_suite": bool(eval_flags) and eval_model_suite,
        "requires_eval_manifest_official_source_thresholds": (
            bool(eval_flags) and eval_official_thresholds
        ),
        "requires_eval_manifest_unknown_availability_failures": (
            bool(eval_flags) and eval_unknown_availability
        ),
        "requires_eval_manifest_ontology_feature_signals": (
            bool(eval_flags) and eval_ontology_features
        ),
    }


def _prediction_eval_window_run_verify_run_metadata(
    args: Any,
    plan_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-eval-window-run",
        "plan": str(plan_path),
        "plan_sha256": _file_sha256(plan_path) if plan_path.exists() else None,
        "verification_flags": {
            "summary_verify": getattr(args, "summary_verify", None),
            "require_window_verifiers": bool(getattr(args, "require_window_verifiers", False)),
            "require_summary_verify": bool(getattr(args, "require_summary_verify", False)),
        },
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _missing_prediction_eval_window_run_verify_result() -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-prediction-eval-window-run",
        "artifact": None,
        "checked": 0,
        "issues": [],
        "issue_count": 0,
        "quality_gate_failures": ["eval_window_run_verify_missing"],
        "quality_gate_failure_count": 1,
        "run_metadata": {
            "command": "verify-prediction-eval-window-run",
            "artifact": None,
            "source_state": {"artifact_loaded": False},
        },
    }
