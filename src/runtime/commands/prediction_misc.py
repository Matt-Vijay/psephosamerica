"""Prediction backtest / input-inventory / source-url-audit command handlers."""

from __future__ import annotations

import datetime as dt
import hashlib

from pathlib import Path
from src.prediction.backtest import PredictionBacktestPayload
from src.runtime.context import RuntimeContext
from src.runtime.prediction_backtest import (
    handle_prediction_backtest_command,
    prediction_backtest_source_state as _prediction_backtest_source_state_impl,
    run_prediction_backtest_command as _run_prediction_backtest_command,
    verify_prediction_backtest_command,
)
from src.runtime.prediction_input_inventory import (
    handle_prediction_input_inventory_command,
    prediction_input_inventory_source_state as _prediction_input_inventory_source_state_impl,
    run_prediction_input_inventory_command as _run_prediction_input_inventory_command,
    verify_prediction_input_inventory_command,
)
from src.runtime.prediction_source_url_audit import (
    run_prediction_source_url_audit_command,
    verify_prediction_source_url_audit_command,
)
from typing import Any

from src.runtime.commands._shared import _load_json_object_or_none, _required_string_list


def run_prediction_backtest_command(
    ctx: RuntimeContext,
    *,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    model: str = "baseline",
    bill_semantics_root: Path | None = None,
) -> PredictionBacktestPayload:
    """Compatibility wrapper for the extracted prediction backtest runner."""
    return _run_prediction_backtest_command(
        ctx,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        model=model,
        bill_semantics_root=bill_semantics_root,
    )


def run_prediction_input_inventory_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
) -> Any:
    """Compatibility wrapper for the extracted prediction input inventory runner."""
    return _run_prediction_input_inventory_command(
        ctx,
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
    )


def _handle_prediction_backtest(args: Any) -> dict[str, Any]:
    return handle_prediction_backtest_command(args)


def _handle_prediction_input_inventory(args: Any) -> dict[str, Any]:
    return handle_prediction_input_inventory_command(args)


def _handle_verify_prediction_input_inventory(args: Any) -> dict[str, Any]:
    return verify_prediction_input_inventory_command(args)


def _prediction_input_inventory_source_state(payload: Any) -> dict[str, Any]:
    return _prediction_input_inventory_source_state_impl(payload)


def _prediction_backtest_run_metadata(args: Any) -> dict[str, str]:
    return {
        "feature_cutoff": args.feature_cutoff.isoformat(),
        "label_start": args.label_start.isoformat(),
        "label_end": args.label_end.isoformat(),
    }


def _handle_prediction_source_url_audit(args: Any) -> dict[str, Any]:
    return run_prediction_source_url_audit_command(args)


def _handle_verify_prediction_source_url_audit(args: Any) -> dict[str, Any]:
    return verify_prediction_source_url_audit_command(args)


def _handle_verify_prediction_backtest(args: Any) -> dict[str, Any]:
    return verify_prediction_backtest_command(args)


def _prediction_window_date_issues(args: Any) -> list[str]:
    issues: list[str] = []
    if args.train_start > args.train_end:
        issues.append("train_start must be on or before train_end")
    if args.label_start > args.label_end:
        issues.append("label_start must be on or before label_end")
    if args.training_feature_cutoff >= args.train_start:
        issues.append("training_feature_cutoff must be before train_start")
    if args.train_end > args.feature_cutoff:
        issues.append("train_end must be on or before feature_cutoff")
    if args.feature_cutoff >= args.label_start:
        issues.append("feature_cutoff must be before label_start")
    return issues


def _missing_prediction_source_url_audit_result() -> dict[str, Any]:
    return {
        "ok": False,
        "command": "verify-prediction-source-url-audit",
        "artifact": None,
        "checked": 0,
        "issues": ["source URL audit artifact missing"],
        "quality_gate_failures": ["source_url_audit_missing"],
        "gap_count": 0,
        "missing_url_sourced_prediction_count": 0,
        "missing_official_source_prediction_count": 0,
        "run_metadata": {
            "command": "verify-prediction-source-url-audit",
            "source_state": {
                "artifact": None,
                "artifact_sha256": None,
                "eval_report_path": None,
                "eval_report_sha256": None,
                "gap_count": 0,
                "missing_url_sourced_prediction_count": 0,
                "missing_official_source_prediction_count": 0,
            },
        },
    }


def _prediction_backtest_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata = {
        "command": "verify-prediction-backtest",
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_evaluated_predictions": bool(
                getattr(args, "require_evaluated_predictions", False)
            ),
            "require_prediction_source_urls": bool(
                getattr(args, "require_prediction_source_urls", False)
            ),
            "require_official_prediction_source_urls": bool(
                getattr(args, "require_official_prediction_source_urls", False)
            ),
            "require_model_name": getattr(args, "require_model_name", None),
            "require_bill_semantics_cache": (
                bool(getattr(args, "require_bill_semantics_cache", False))
                or bool(
                    _required_string_list(getattr(args, "require_bill_semantics_model_name", None))
                )
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
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _prediction_input_inventory_refresh_commands(inventory_path: Path) -> list[str]:
    artifact = _load_json_object_or_none(inventory_path)
    if artifact is None:
        return []
    required_dates = [
        "training_feature_cutoff",
        "train_start",
        "train_end",
        "feature_cutoff",
        "label_start",
        "label_end",
    ]
    if not all(isinstance(artifact.get(key), str) for key in required_dates):
        return []
    run_metadata = artifact.get("run_metadata")
    congress_archive_manifest = (
        run_metadata.get("congress_archive_manifest") if isinstance(run_metadata, dict) else None
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
        " --require-congress-archive-manifest" if congress_archive_manifest_arg else ""
    )
    return [
        "python3 -m src.runtime.main prediction-input-inventory "
        f"--training-feature-cutoff {artifact['training_feature_cutoff']} "
        f"--train-start {artifact['train_start']} "
        f"--train-end {artifact['train_end']} "
        f"--feature-cutoff {artifact['feature_cutoff']} "
        f"--label-start {artifact['label_start']} "
        f"--label-end {artifact['label_end']} "
        f"{congress_archive_manifest_arg}"
        f"--output {inventory_path}",
        "python3 -m src.runtime.main verify-prediction-input-inventory "
        f"--artifact {inventory_path} --require-run-metadata "
        f"{require_congress_archive_manifest_arg}"
        "--require-clean-inventory "
        "--require-portable-jurisdiction-ids "
        "--require-portable-body-ids "
        "--require-portable-session-ids "
        "--require-source-family congress_vote "
        "--require-source-family congress_bill "
        "--min-training-label-official-source-url-coverage-rate 0.95 "
        "--min-evaluation-label-official-source-url-coverage-rate 0.95 "
        "--min-bill-official-source-url-coverage-rate 0.95 "
        "--min-bill-sponsor-availability-rate 0.95 "
        "--min-ontology-official-source-anchor-coverage-rate 0.95 "
        "--min-fec-contributions 1 "
        "--min-member-attributed-fec-contributions 1 "
        "--min-members-with-fec-candidate-id 1 "
        "--min-public-statement-signals 1 "
        "--min-members-with-public-statement-signals 1",
    ]


def _prediction_backtest_refresh_commands(
    backtest_path: Path | None,
    required_source_family_ids: list[str],
) -> list[str]:
    if backtest_path is None:
        return []
    artifact = _load_json_object_or_none(backtest_path)
    if artifact is None:
        return []
    required_dates = ["feature_cutoff", "label_start", "label_end"]
    if not all(isinstance(artifact.get(key), str) for key in required_dates):
        return []
    model_arg = "ontology" if artifact.get("model_name") == "ontology_signal_model" else "baseline"
    run_metadata = artifact.get("run_metadata")
    bill_semantics_root = (
        run_metadata.get("bill_semantics_root") if isinstance(run_metadata, dict) else None
    )
    bill_semantics_arg = (
        f" --bill-semantics-root {bill_semantics_root}"
        if model_arg == "ontology" and isinstance(bill_semantics_root, str) and bill_semantics_root
        else ""
    )
    congress_archive_manifest = (
        run_metadata.get("congress_archive_manifest") if isinstance(run_metadata, dict) else None
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
        f" --require-source-family {source_family_id}"
        for source_family_id in required_source_family_ids
    )
    return [
        "python3 -m src.runtime.main prediction-backtest "
        f"--model {model_arg} "
        f"--feature-cutoff {artifact['feature_cutoff']} "
        f"--label-start {artifact['label_start']} "
        f"--label-end {artifact['label_end']}"
        f"{bill_semantics_arg} "
        f"{congress_archive_manifest_arg}"
        f"--output {backtest_path}",
        "python3 -m src.runtime.main verify-prediction-backtest "
        f"--artifact {backtest_path} "
        "--require-run-metadata "
        "--require-evaluated-predictions "
        "--require-prediction-source-urls "
        "--require-official-prediction-source-urls"
        f"{require_congress_archive_manifest_arg}"
        f"{required_family_args}",
    ]


def _has_prediction_input_inventory_archive_manifest_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main prediction-input-inventory ")
        and "--congress-archive-manifest " in command
        for command in commands
    )


def _has_prediction_input_inventory_archive_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-input-inventory ")
        and "--require-congress-archive-manifest" in command
        for command in commands
    )


def _has_strict_prediction_input_inventory_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--require-run-metadata",
        "--require-clean-inventory",
        "--require-portable-jurisdiction-ids",
        "--require-portable-body-ids",
        "--require-portable-session-ids",
        "--require-source-family congress_vote",
        "--require-source-family congress_bill",
        "--min-training-label-official-source-url-coverage-rate 0.95",
        "--min-evaluation-label-official-source-url-coverage-rate 0.95",
        "--min-bill-official-source-url-coverage-rate 0.95",
        "--min-bill-sponsor-availability-rate 0.95",
        "--min-ontology-official-source-anchor-coverage-rate 0.95",
        "--min-fec-contributions 1",
        "--min-member-attributed-fec-contributions 1",
        "--min-members-with-fec-candidate-id 1",
        "--min-public-statement-signals 1",
        "--min-members-with-public-statement-signals 1",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-input-inventory ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _has_prediction_backtest_archive_manifest_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main prediction-backtest ")
        and "--congress-archive-manifest " in command
        for command in commands
    )


def _has_prediction_backtest_archive_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-backtest ")
        and "--require-congress-archive-manifest" in command
        for command in commands
    )


def _has_strict_prediction_backtest_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--require-run-metadata",
        "--require-evaluated-predictions",
        "--require-prediction-source-urls",
        "--require-official-prediction-source-urls",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-backtest ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )


def _prediction_backtest_source_state(
    payload: PredictionBacktestPayload,
) -> dict[str, Any]:
    return _prediction_backtest_source_state_impl(payload)
