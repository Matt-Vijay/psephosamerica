from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from src.prediction.input_inventory import (
    PredictionInputInventoryPayload,
    build_prediction_input_inventory,
)
from src.query.prediction_inventory import fetch_prediction_fec_inventory
from src.query.published_rows import (
    fetch_all_ontology_edge_rows,
    fetch_bill_semantic_input_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
)
from src.runtime.app import build_runtime
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.json_artifacts import (
    attach_optional_verification_output as _attach_optional_verification_output,
)
from src.runtime.json_artifacts import write_json_artifact as _write_json_artifact

_TRAINING_FEATURE_TERM_WINDOW_WARNING = "training_feature_members_excluded_by_cutoff_or_term_window"
_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def run_prediction_input_inventory_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
) -> PredictionInputInventoryPayload:
    """Open a connection and report temporal prediction input inventory."""
    conn = open_connection(ctx)
    return build_prediction_input_inventory(
        training_feature_cutoff=training_feature_cutoff,
        train_start=train_start,
        train_end=train_end,
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        training_feature_rows=fetch_vote_prediction_backtest_feature_rows(
            conn,
            training_feature_cutoff,
        ),
        evaluation_feature_rows=fetch_vote_prediction_backtest_feature_rows(
            conn,
            feature_cutoff,
        ),
        training_label_rows=fetch_vote_prediction_backtest_label_rows(
            conn,
            train_start,
            train_end,
        ),
        evaluation_label_rows=fetch_vote_prediction_backtest_label_rows(
            conn,
            label_start,
            label_end,
        ),
        bill_rows=fetch_bill_semantic_input_rows(
            conn,
            feature_cutoff=feature_cutoff,
        ),
        ontology_edge_rows=fetch_all_ontology_edge_rows(conn),
        fec_inventory=fetch_prediction_fec_inventory(
            conn,
            feature_cutoff=feature_cutoff,
        ),
    )


def handle_prediction_input_inventory_command(args: Any) -> dict[str, Any]:
    window_issues = _prediction_window_date_issues(args)
    if window_issues:
        return _command_issue_result("prediction-input-inventory", window_issues)

    runtime = build_runtime()
    result = run_prediction_input_inventory_command(
        runtime.context,
        training_feature_cutoff=args.training_feature_cutoff,
        train_start=args.train_start,
        train_end=args.train_end,
        feature_cutoff=args.feature_cutoff,
        label_start=args.label_start,
        label_end=args.label_end,
    )
    payload = result.model_dump(mode="json")
    payload["run_metadata"] = {
        "command": "prediction-input-inventory",
        **_prediction_window_run_metadata(args),
        "source_state": prediction_input_inventory_source_state(result),
    }
    congress_archive_manifest = _congress_archive_manifest_metadata(
        getattr(args, "congress_archive_manifest", None)
    )
    if congress_archive_manifest is not None:
        payload["run_metadata"]["congress_archive_manifest"] = congress_archive_manifest
    output = Path(args.output) if args.output is not None else None
    output_sha256: str | None = None
    if output is not None:
        output_sha256 = _write_json_artifact(output, payload)
    return {
        "ok": result.ok,
        "command": "prediction-input-inventory",
        "training_feature_member_count": result.training_feature_member_count,
        "evaluation_feature_member_count": result.evaluation_feature_member_count,
        "training_feature_vote_history_member_count": (
            result.training_feature_vote_history_member_count
        ),
        "evaluation_feature_vote_history_member_count": (
            result.evaluation_feature_vote_history_member_count
        ),
        "training_feature_vote_history_source_member_count": (
            result.training_feature_vote_history_source_member_count
        ),
        "evaluation_feature_vote_history_source_member_count": (
            result.evaluation_feature_vote_history_source_member_count
        ),
        "training_feature_vote_history_source_coverage_rate": (
            result.training_feature_vote_history_source_coverage_rate
        ),
        "evaluation_feature_vote_history_source_coverage_rate": (
            result.evaluation_feature_vote_history_source_coverage_rate
        ),
        "training_label_count": result.training_label_count,
        "evaluation_label_count": result.evaluation_label_count,
        "training_labels_missing_feature_member_count": (
            result.training_labels_missing_feature_member_count
        ),
        "evaluation_labels_missing_feature_member_count": (
            result.evaluation_labels_missing_feature_member_count
        ),
        "training_label_source_url_coverage_rate": (result.training_label_source_url_coverage_rate),
        "evaluation_label_source_url_coverage_rate": (
            result.evaluation_label_source_url_coverage_rate
        ),
        "training_label_official_source_url_coverage_rate": (
            result.training_label_official_source_url_coverage_rate
        ),
        "evaluation_label_official_source_url_coverage_rate": (
            result.evaluation_label_official_source_url_coverage_rate
        ),
        "bill_count": result.bill_count,
        "bill_source_url_coverage_rate": result.bill_source_url_coverage_rate,
        "bill_official_source_url_coverage_rate": (result.bill_official_source_url_coverage_rate),
        "bill_sponsor_availability_rate": result.bill_sponsor_availability_rate,
        "bill_primary_sponsor_introduced_date_fallback_count": (
            result.bill_primary_sponsor_introduced_date_fallback_count
        ),
        "ontology_edge_count": result.ontology_edge_count,
        "ontology_source_anchor_coverage_rate": (result.ontology_source_anchor_coverage_rate),
        "ontology_official_source_anchor_coverage_rate": (
            result.ontology_official_source_anchor_coverage_rate
        ),
        "fec_contribution_count": result.fec_contribution_count,
        "member_attributed_fec_contribution_count": (
            result.member_attributed_fec_contribution_count
        ),
        "fec_member_attribution_rate": result.fec_member_attribution_rate,
        "members_with_fec_candidate_id_count": result.members_with_fec_candidate_id_count,
        "public_statement_signal_count": result.public_statement_signal_count,
        "members_with_public_statement_signal_count": (
            result.members_with_public_statement_signal_count
        ),
        "public_statement_ontology_edge_count": result.public_statement_ontology_edge_count,
        "public_statement_prediction_member_overlap_count": (
            result.public_statement_prediction_member_overlap_count
        ),
        "jurisdiction_count": result.jurisdiction_count,
        "implemented_jurisdiction_count": result.implemented_jurisdiction_count,
        "portable_jurisdiction_count": result.portable_jurisdiction_count,
        "portable_rows_missing_body_id_count": result.portable_rows_missing_body_id_count,
        "portable_rows_missing_session_id_count": (result.portable_rows_missing_session_id_count),
        "legislative_body_count": result.legislative_body_count,
        "legislative_session_count": result.legislative_session_count,
        "source_family_count": result.source_family_count,
        "source_family_ids": list(result.source_family_ids),
        "blocking_reasons": list(result.blocking_reasons),
        "warning_reasons": list(result.warning_reasons),
        "output": str(output) if output is not None else None,
        "output_sha256": output_sha256,
    }


def _prediction_window_date_issues(args: Any) -> list[str]:
    issues: list[str] = []
    if args.train_start > args.train_end:
        issues.append("train_start must be on or before train_end")
    if args.label_start > args.label_end:
        issues.append("label_start must be on or before label_end")
    if args.training_feature_cutoff >= args.train_start:
        issues.append("training_feature_cutoff must be before train_start")
    if args.feature_cutoff >= args.label_start:
        issues.append("feature_cutoff must be before label_start")
    return issues


def _command_issue_result(command: str, issues: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "issues": issues,
        "issue_count": len(issues),
    }


def verify_prediction_input_inventory_command(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    checked = 0

    def failure_result(*, checked: int, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-input-inventory",
                "artifact": str(artifact_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_input_inventory_verify_run_metadata(
                    args,
                    artifact_path,
                ),
            },
        )

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return failure_result(
            checked=0,
            issues=[f"failed to load artifact: {exc}"],
        )
    if not isinstance(artifact, dict):
        return failure_result(checked=0, issues=["artifact must be an object"])
    try:
        payload = PredictionInputInventoryPayload.model_validate(artifact)
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"artifact schema: {exc}"])
    checked += 1
    _validate_prediction_input_inventory_derived_rates(payload, issues)
    run_metadata = artifact.get("run_metadata")
    require_run_metadata = bool(getattr(args, "require_run_metadata", False))
    quality_gate_failures: list[str] = []
    if run_metadata is None:
        if require_run_metadata:
            issues.append("run_metadata missing")
        if bool(getattr(args, "require_congress_archive_manifest", False)):
            issues.append("run_metadata congress_archive_manifest missing")
    elif not isinstance(run_metadata, dict):
        issues.append("run_metadata must be an object")
    else:
        checked += 1
        if require_run_metadata:
            _validate_prediction_input_inventory_required_official_source_metadata(
                artifact=artifact,
                run_metadata=run_metadata,
                issues=issues,
            )
        _validate_prediction_input_inventory_run_metadata(
            payload=payload,
            run_metadata=run_metadata,
            issues=issues,
            require_source_state=require_run_metadata,
        )
        _validate_prediction_input_inventory_congress_archive_manifest_metadata(
            run_metadata=run_metadata,
            issues=issues,
            require_manifest=bool(getattr(args, "require_congress_archive_manifest", False)),
        )
    if bool(getattr(args, "require_clean_inventory", False)):
        quality_gate_failures.extend(_prediction_input_inventory_clean_failures(payload))
    if (
        bool(getattr(args, "require_portable_jurisdiction_ids", False))
        and payload.portable_rows_missing_jurisdiction_id_count
    ):
        quality_gate_failures.append("portable_source_rows_missing_jurisdiction_ids")
    if (
        bool(getattr(args, "require_portable_session_ids", False))
        and payload.portable_rows_missing_session_id_count
    ):
        quality_gate_failures.append("portable_jurisdiction_rows_missing_session_ids")
    if (
        bool(getattr(args, "require_portable_body_ids", False))
        and payload.portable_rows_missing_body_id_count
    ):
        quality_gate_failures.append("portable_jurisdiction_rows_missing_body_ids")
    if (
        bool(getattr(args, "require_no_bill_sponsor_introduced_date_fallbacks", False))
        and payload.bill_primary_sponsor_introduced_date_fallback_count
    ):
        quality_gate_failures.append("bill_sponsor_introduced_date_fallbacks_present")
    quality_gate_failures.extend(
        _prediction_input_inventory_required_source_family_failures(payload, args, issues)
    )
    invalid_thresholds = _prediction_input_inventory_invalid_thresholds(args=args, issues=issues)
    quality_gate_failures.extend(
        _prediction_input_inventory_label_minimum_failures(
            payload,
            args,
            invalid_thresholds=invalid_thresholds,
        )
    )
    quality_gate_failures.extend(
        _prediction_input_inventory_optional_evidence_minimum_failures(
            payload,
            args,
            invalid_thresholds=invalid_thresholds,
        )
    )
    quality_gate_failures.extend(
        _prediction_input_inventory_coverage_threshold_failures(
            payload,
            args,
            invalid_thresholds=invalid_thresholds,
        )
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-input-inventory",
            "artifact": str(artifact_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "training_label_count": payload.training_label_count,
            "evaluation_label_count": payload.evaluation_label_count,
            "training_label_source_url_coverage_rate": (
                payload.training_label_source_url_coverage_rate
            ),
            "evaluation_label_source_url_coverage_rate": (
                payload.evaluation_label_source_url_coverage_rate
            ),
            "training_label_official_source_url_coverage_rate": (
                payload.training_label_official_source_url_coverage_rate
            ),
            "evaluation_label_official_source_url_coverage_rate": (
                payload.evaluation_label_official_source_url_coverage_rate
            ),
            "bill_source_url_coverage_rate": payload.bill_source_url_coverage_rate,
            "bill_official_source_url_coverage_rate": (
                payload.bill_official_source_url_coverage_rate
            ),
            "bill_sponsor_availability_rate": payload.bill_sponsor_availability_rate,
            "bill_primary_sponsor_introduced_date_fallback_count": (
                payload.bill_primary_sponsor_introduced_date_fallback_count
            ),
            "ontology_source_anchor_coverage_rate": (payload.ontology_source_anchor_coverage_rate),
            "ontology_official_source_anchor_coverage_rate": (
                payload.ontology_official_source_anchor_coverage_rate
            ),
            "fec_member_attribution_rate": payload.fec_member_attribution_rate,
            "jurisdiction_count": payload.jurisdiction_count,
            "portable_rows_missing_jurisdiction_id_count": (
                payload.portable_rows_missing_jurisdiction_id_count
            ),
            "portable_rows_missing_body_id_count": (payload.portable_rows_missing_body_id_count),
            "portable_rows_missing_session_id_count": (
                payload.portable_rows_missing_session_id_count
            ),
            "legislative_body_count": payload.legislative_body_count,
            "legislative_session_count": payload.legislative_session_count,
            "source_family_count": payload.source_family_count,
            "warning_reasons": list(payload.warning_reasons),
            "blocking_reasons": list(payload.blocking_reasons),
            "run_metadata": _prediction_input_inventory_verify_run_metadata(
                args,
                artifact_path,
                source_state=prediction_input_inventory_source_state(payload),
            ),
        },
    )


def prediction_input_inventory_source_state(
    payload: PredictionInputInventoryPayload,
) -> dict[str, Any]:
    source_state: dict[str, Any] = {
        "training_feature_member_count": payload.training_feature_member_count,
        "evaluation_feature_member_count": payload.evaluation_feature_member_count,
        "training_label_count": payload.training_label_count,
        "evaluation_label_count": payload.evaluation_label_count,
        "training_labels_missing_feature_member_count": (
            payload.training_labels_missing_feature_member_count
        ),
        "evaluation_labels_missing_feature_member_count": (
            payload.evaluation_labels_missing_feature_member_count
        ),
        "training_label_source_url_count": payload.training_label_source_url_count,
        "evaluation_label_source_url_count": payload.evaluation_label_source_url_count,
        "training_label_source_url_coverage_rate": (
            payload.training_label_source_url_coverage_rate
        ),
        "evaluation_label_source_url_coverage_rate": (
            payload.evaluation_label_source_url_coverage_rate
        ),
        "bill_count": payload.bill_count,
        "bill_source_url_count": payload.bill_source_url_count,
        "bill_source_url_coverage_rate": payload.bill_source_url_coverage_rate,
        "bill_sponsor_count": payload.bill_sponsor_count,
        "bill_available_sponsor_count": payload.bill_available_sponsor_count,
        "bill_sponsor_availability_rate": payload.bill_sponsor_availability_rate,
        "bill_primary_sponsor_introduced_date_fallback_count": (
            payload.bill_primary_sponsor_introduced_date_fallback_count
        ),
        "ontology_edge_count": payload.ontology_edge_count,
        "sourced_ontology_edge_count": payload.sourced_ontology_edge_count,
        "ontology_source_anchor_coverage_rate": payload.ontology_source_anchor_coverage_rate,
        "fec_contribution_count": payload.fec_contribution_count,
        "member_attributed_fec_contribution_count": (
            payload.member_attributed_fec_contribution_count
        ),
        "fec_member_attribution_rate": payload.fec_member_attribution_rate,
        "members_with_fec_candidate_id_count": (payload.members_with_fec_candidate_id_count),
        "public_statement_signal_count": payload.public_statement_signal_count,
        "members_with_public_statement_signal_count": (
            payload.members_with_public_statement_signal_count
        ),
        "public_statement_ontology_edge_count": payload.public_statement_ontology_edge_count,
        "public_statement_prediction_member_overlap_count": (
            payload.public_statement_prediction_member_overlap_count
        ),
    }
    if payload.jurisdiction_count:
        source_state["jurisdiction_count"] = payload.jurisdiction_count
        source_state["jurisdiction_ids"] = list(payload.jurisdiction_ids)
    if payload.implemented_jurisdiction_count:
        source_state["implemented_jurisdiction_count"] = payload.implemented_jurisdiction_count
        source_state["implemented_jurisdiction_ids"] = list(payload.implemented_jurisdiction_ids)
    if payload.portable_jurisdiction_count:
        source_state["portable_jurisdiction_count"] = payload.portable_jurisdiction_count
        source_state["portable_jurisdiction_ids"] = list(payload.portable_jurisdiction_ids)
    has_portable_context = bool(
        payload.portable_jurisdiction_count
        or payload.portable_rows_missing_jurisdiction_id_count
        or payload.portable_rows_missing_body_id_count
        or payload.portable_rows_missing_session_id_count
    )
    if has_portable_context:
        source_state["portable_rows_missing_jurisdiction_id_count"] = (
            payload.portable_rows_missing_jurisdiction_id_count
        )
        source_state["portable_rows_missing_body_id_count"] = (
            payload.portable_rows_missing_body_id_count
        )
        source_state["portable_rows_missing_session_id_count"] = (
            payload.portable_rows_missing_session_id_count
        )
    if payload.legislative_body_count:
        source_state["legislative_body_count"] = payload.legislative_body_count
        source_state["legislative_body_ids"] = list(payload.legislative_body_ids)
    if payload.legislative_session_count:
        source_state["legislative_session_count"] = payload.legislative_session_count
        source_state["legislative_session_ids"] = list(payload.legislative_session_ids)
    if payload.source_family_count:
        source_state["source_family_count"] = payload.source_family_count
        source_state["source_family_ids"] = list(payload.source_family_ids)
    optional_counts = {
        "training_feature_vote_history_member_count": (
            payload.training_feature_vote_history_member_count
        ),
        "evaluation_feature_vote_history_member_count": (
            payload.evaluation_feature_vote_history_member_count
        ),
        "training_feature_vote_history_source_member_count": (
            payload.training_feature_vote_history_source_member_count
        ),
        "evaluation_feature_vote_history_source_member_count": (
            payload.evaluation_feature_vote_history_source_member_count
        ),
        "training_feature_vote_history_source_coverage_rate": (
            payload.training_feature_vote_history_source_coverage_rate
        ),
        "evaluation_feature_vote_history_source_coverage_rate": (
            payload.evaluation_feature_vote_history_source_coverage_rate
        ),
        "official_training_label_source_url_count": (
            payload.official_training_label_source_url_count
        ),
        "training_label_official_source_url_coverage_rate": (
            payload.training_label_official_source_url_coverage_rate
        ),
        "official_evaluation_label_source_url_count": (
            payload.official_evaluation_label_source_url_count
        ),
        "evaluation_label_official_source_url_coverage_rate": (
            payload.evaluation_label_official_source_url_coverage_rate
        ),
        "official_bill_source_url_count": payload.official_bill_source_url_count,
        "bill_official_source_url_coverage_rate": payload.bill_official_source_url_coverage_rate,
        "official_sourced_ontology_edge_count": (payload.official_sourced_ontology_edge_count),
        "ontology_official_source_anchor_coverage_rate": (
            payload.ontology_official_source_anchor_coverage_rate
        ),
    }
    for key, value in optional_counts.items():
        if value is not None:
            source_state[key] = value
    if _TRAINING_FEATURE_TERM_WINDOW_WARNING in payload.warning_reasons:
        source_state["training_feature_cutoff_or_term_window_warning_count"] = 1
    if payload.blocking_reasons:
        source_state["blocking_reason_count"] = len(payload.blocking_reasons)
        source_state["blocking_reasons"] = sorted(payload.blocking_reasons)
    if payload.warning_reasons:
        source_state["warning_reason_count"] = len(payload.warning_reasons)
        source_state["warning_reasons"] = sorted(payload.warning_reasons)
    return source_state


def _prediction_window_run_metadata(args: Any) -> dict[str, str]:
    return {
        "training_feature_cutoff": args.training_feature_cutoff.isoformat(),
        "train_start": args.train_start.isoformat(),
        "train_end": args.train_end.isoformat(),
        "feature_cutoff": args.feature_cutoff.isoformat(),
        "label_start": args.label_start.isoformat(),
        "label_end": args.label_end.isoformat(),
    }


def _congress_archive_manifest_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    manifest_path = Path(str(value))
    return {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def _prediction_input_inventory_clean_failures(
    payload: PredictionInputInventoryPayload,
) -> list[str]:
    failures: list[str] = []
    if not payload.ok:
        failures.append("not_ok")
    if payload.blocking_reasons:
        failures.append("blocking_reasons")
    if payload.warning_reasons:
        failures.append("warning_reasons")
    return failures


def _prediction_input_inventory_required_source_family_failures(
    payload: PredictionInputInventoryPayload,
    args: Any,
    issues: list[str],
) -> list[str]:
    required = getattr(args, "require_source_families", None) or []
    if not isinstance(required, list):
        issues.append("require_source_families must be a list")
        return []
    normalized_required: list[str] = []
    for family in required:
        if not isinstance(family, str) or _SOURCE_FAMILY_ID_RE.fullmatch(family) is None:
            issues.append("require_source_families must contain normalized source family ids")
            return []
        normalized_required.append(family)
    available = set(payload.source_family_ids)
    return [
        f"missing_required_source_family:{family}"
        for family in sorted(set(normalized_required))
        if family not in available
    ]


def _prediction_input_inventory_label_minimum_failures(
    payload: PredictionInputInventoryPayload,
    args: Any,
    *,
    invalid_thresholds: set[str] | None = None,
) -> list[str]:
    invalid_thresholds = invalid_thresholds or set()
    failures: list[str] = []
    min_training_labels = getattr(args, "min_training_labels", None)
    if (
        "min_training_labels" not in invalid_thresholds
        and min_training_labels is not None
        and payload.training_label_count < int(min_training_labels)
    ):
        failures.append("training_label_count_below_min")
    min_evaluation_labels = getattr(args, "min_evaluation_labels", None)
    if (
        "min_evaluation_labels" not in invalid_thresholds
        and min_evaluation_labels is not None
        and payload.evaluation_label_count < int(min_evaluation_labels)
    ):
        failures.append("evaluation_label_count_below_min")
    return failures


def _prediction_input_inventory_optional_evidence_minimum_failures(
    payload: PredictionInputInventoryPayload,
    args: Any,
    *,
    invalid_thresholds: set[str] | None = None,
) -> list[str]:
    invalid_thresholds = invalid_thresholds or set()
    checks = (
        (
            "min_fec_contributions",
            payload.fec_contribution_count,
            "fec_contribution_count_below_min",
        ),
        (
            "min_member_attributed_fec_contributions",
            payload.member_attributed_fec_contribution_count,
            "member_attributed_fec_contribution_count_below_min",
        ),
        (
            "min_members_with_fec_candidate_id",
            payload.members_with_fec_candidate_id_count,
            "members_with_fec_candidate_id_count_below_min",
        ),
        (
            "min_public_statement_signals",
            payload.public_statement_signal_count,
            "public_statement_signal_count_below_min",
        ),
        (
            "min_members_with_public_statement_signals",
            payload.members_with_public_statement_signal_count,
            "members_with_public_statement_signal_count_below_min",
        ),
    )
    failures: list[str] = []
    for arg_name, actual_count, failure_name in checks:
        if arg_name in invalid_thresholds:
            continue
        minimum = getattr(args, arg_name, None)
        if minimum is not None and actual_count < int(minimum):
            failures.append(failure_name)
    return failures


def _prediction_input_inventory_coverage_threshold_failures(
    payload: PredictionInputInventoryPayload,
    args: Any,
    *,
    invalid_thresholds: set[str] | None = None,
) -> list[str]:
    invalid_thresholds = invalid_thresholds or set()
    checks = (
        (
            "min_training_feature_vote_history_source_coverage_rate",
            payload.training_feature_vote_history_source_coverage_rate,
            "training_feature_vote_history_source_coverage_below_min",
        ),
        (
            "min_evaluation_feature_vote_history_source_coverage_rate",
            payload.evaluation_feature_vote_history_source_coverage_rate,
            "evaluation_feature_vote_history_source_coverage_below_min",
        ),
        (
            "min_training_label_source_url_coverage_rate",
            payload.training_label_source_url_coverage_rate,
            "training_label_source_url_coverage_below_min",
        ),
        (
            "min_evaluation_label_source_url_coverage_rate",
            payload.evaluation_label_source_url_coverage_rate,
            "evaluation_label_source_url_coverage_below_min",
        ),
        (
            "min_training_label_official_source_url_coverage_rate",
            payload.training_label_official_source_url_coverage_rate,
            "training_label_official_source_url_coverage_below_min",
        ),
        (
            "min_evaluation_label_official_source_url_coverage_rate",
            payload.evaluation_label_official_source_url_coverage_rate,
            "evaluation_label_official_source_url_coverage_below_min",
        ),
        (
            "min_bill_source_url_coverage_rate",
            payload.bill_source_url_coverage_rate,
            "bill_source_url_coverage_below_min",
        ),
        (
            "min_bill_official_source_url_coverage_rate",
            payload.bill_official_source_url_coverage_rate,
            "bill_official_source_url_coverage_below_min",
        ),
        (
            "min_bill_sponsor_availability_rate",
            payload.bill_sponsor_availability_rate,
            "bill_sponsor_availability_below_min",
        ),
        (
            "min_ontology_source_anchor_coverage_rate",
            payload.ontology_source_anchor_coverage_rate,
            "ontology_source_anchor_coverage_below_min",
        ),
        (
            "min_ontology_official_source_anchor_coverage_rate",
            payload.ontology_official_source_anchor_coverage_rate,
            "ontology_official_source_anchor_coverage_below_min",
        ),
        (
            "min_fec_member_attribution_rate",
            payload.fec_member_attribution_rate,
            "fec_member_attribution_below_min",
        ),
    )
    failures: list[str] = []
    for arg_name, actual_rate, failure_name in checks:
        if arg_name in invalid_thresholds:
            continue
        minimum = getattr(args, arg_name, None)
        if minimum is not None and (actual_rate is None or actual_rate < float(minimum)):
            failures.append(failure_name)
    return failures


def _prediction_input_inventory_invalid_thresholds(
    *,
    args: Any,
    issues: list[str],
) -> set[str]:
    invalid: set[str] = set()
    for arg_name in _PREDICTION_INPUT_INVENTORY_INT_THRESHOLDS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            invalid.add(arg_name)
            issues.append(f"{arg_name} must be a non-negative integer")
    for arg_name in _PREDICTION_INPUT_INVENTORY_RATE_THRESHOLDS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 <= float(value) <= 1
        ):
            invalid.add(arg_name)
            issues.append(f"{arg_name} must be a number between 0 and 1")
    return invalid


_PREDICTION_INPUT_INVENTORY_INT_THRESHOLDS = (
    "min_training_labels",
    "min_evaluation_labels",
    "min_fec_contributions",
    "min_member_attributed_fec_contributions",
    "min_members_with_fec_candidate_id",
    "min_public_statement_signals",
    "min_members_with_public_statement_signals",
)


_PREDICTION_INPUT_INVENTORY_RATE_THRESHOLDS = (
    "min_training_feature_vote_history_source_coverage_rate",
    "min_evaluation_feature_vote_history_source_coverage_rate",
    "min_training_label_source_url_coverage_rate",
    "min_evaluation_label_source_url_coverage_rate",
    "min_training_label_official_source_url_coverage_rate",
    "min_evaluation_label_official_source_url_coverage_rate",
    "min_bill_source_url_coverage_rate",
    "min_bill_official_source_url_coverage_rate",
    "min_bill_sponsor_availability_rate",
    "min_ontology_source_anchor_coverage_rate",
    "min_ontology_official_source_anchor_coverage_rate",
    "min_fec_member_attribution_rate",
)


def _validate_prediction_input_inventory_derived_rates(
    payload: PredictionInputInventoryPayload,
    issues: list[str],
) -> None:
    checks = [
        (
            "training_feature_vote_history_source_member_count",
            payload.training_feature_vote_history_source_member_count,
            payload.training_feature_vote_history_member_count,
            "training_feature_vote_history_source_coverage_rate",
            payload.training_feature_vote_history_source_coverage_rate,
        ),
        (
            "evaluation_feature_vote_history_source_member_count",
            payload.evaluation_feature_vote_history_source_member_count,
            payload.evaluation_feature_vote_history_member_count,
            "evaluation_feature_vote_history_source_coverage_rate",
            payload.evaluation_feature_vote_history_source_coverage_rate,
        ),
        (
            "training_label_source_url_count",
            payload.training_label_source_url_count,
            payload.training_label_count,
            "training_label_source_url_coverage_rate",
            payload.training_label_source_url_coverage_rate,
        ),
        (
            "evaluation_label_source_url_count",
            payload.evaluation_label_source_url_count,
            payload.evaluation_label_count,
            "evaluation_label_source_url_coverage_rate",
            payload.evaluation_label_source_url_coverage_rate,
        ),
        (
            "bill_source_url_count",
            payload.bill_source_url_count,
            payload.bill_count,
            "bill_source_url_coverage_rate",
            payload.bill_source_url_coverage_rate,
        ),
        (
            "official_training_label_source_url_count",
            payload.official_training_label_source_url_count,
            payload.training_label_count,
            "training_label_official_source_url_coverage_rate",
            payload.training_label_official_source_url_coverage_rate,
        ),
        (
            "official_evaluation_label_source_url_count",
            payload.official_evaluation_label_source_url_count,
            payload.evaluation_label_count,
            "evaluation_label_official_source_url_coverage_rate",
            payload.evaluation_label_official_source_url_coverage_rate,
        ),
        (
            "official_bill_source_url_count",
            payload.official_bill_source_url_count,
            payload.bill_count,
            "bill_official_source_url_coverage_rate",
            payload.bill_official_source_url_coverage_rate,
        ),
        (
            "bill_available_sponsor_count",
            payload.bill_available_sponsor_count,
            payload.bill_sponsor_count,
            "bill_sponsor_availability_rate",
            payload.bill_sponsor_availability_rate,
        ),
        (
            "sourced_ontology_edge_count",
            payload.sourced_ontology_edge_count,
            payload.ontology_edge_count,
            "ontology_source_anchor_coverage_rate",
            payload.ontology_source_anchor_coverage_rate,
        ),
        (
            "official_sourced_ontology_edge_count",
            payload.official_sourced_ontology_edge_count,
            payload.ontology_edge_count,
            "ontology_official_source_anchor_coverage_rate",
            payload.ontology_official_source_anchor_coverage_rate,
        ),
        (
            "member_attributed_fec_contribution_count",
            payload.member_attributed_fec_contribution_count,
            payload.fec_contribution_count,
            "fec_member_attribution_rate",
            payload.fec_member_attribution_rate,
        ),
    ]
    for count_key, numerator, denominator, rate_key, actual_rate in checks:
        if numerator is None or denominator is None:
            continue
        if numerator > denominator:
            issues.append(f"coverage count exceeds total: {count_key}")
        expected_rate = None if denominator == 0 else numerator / denominator
        if actual_rate != expected_rate:
            issues.append(f"coverage mismatch: {rate_key}")
    official_source_count_checks = (
        (
            "official_training_label_source_url_count",
            payload.official_training_label_source_url_count,
            payload.training_label_source_url_count,
        ),
        (
            "official_evaluation_label_source_url_count",
            payload.official_evaluation_label_source_url_count,
            payload.evaluation_label_source_url_count,
        ),
        (
            "official_bill_source_url_count",
            payload.official_bill_source_url_count,
            payload.bill_source_url_count,
        ),
        (
            "official_sourced_ontology_edge_count",
            payload.official_sourced_ontology_edge_count,
            payload.sourced_ontology_edge_count,
        ),
    )
    for count_key, official_count, source_count in official_source_count_checks:
        if official_count is not None and official_count > source_count:
            issues.append(f"official coverage count exceeds source count: {count_key}")
    if (
        payload.bill_primary_sponsor_introduced_date_fallback_count
        > payload.bill_available_sponsor_count
    ):
        issues.append("primary sponsor introduced-date fallback count exceeds available sponsors")


def _validate_prediction_input_inventory_run_metadata(
    *,
    payload: PredictionInputInventoryPayload,
    run_metadata: dict[Any, Any],
    issues: list[str],
    require_source_state: bool = False,
) -> None:
    if (require_source_state or "command" in run_metadata) and run_metadata.get(
        "command"
    ) != "prediction-input-inventory":
        issues.append("run_metadata mismatch: command")
    expected = {
        "training_feature_cutoff": payload.training_feature_cutoff.isoformat(),
        "train_start": payload.train_start.isoformat(),
        "train_end": payload.train_end.isoformat(),
        "feature_cutoff": payload.feature_cutoff.isoformat(),
        "label_start": payload.label_start.isoformat(),
        "label_end": payload.label_end.isoformat(),
    }
    for key, expected_window_value in expected.items():
        actual_value = run_metadata.get(key)
        if actual_value != expected_window_value:
            issues.append(f"run_metadata mismatch: {key}")
    source_state = run_metadata.get("source_state")
    if source_state is None:
        if require_source_state:
            issues.append("run_metadata source_state missing")
        return
    if not isinstance(source_state, dict):
        issues.append("run_metadata source_state must be an object")
        return
    expected_source_state = prediction_input_inventory_source_state(payload)
    for key in sorted(set(source_state) - set(expected_source_state)):
        issues.append(f"run_metadata source_state unexpected: {key}")
    for key, expected_count_value in expected_source_state.items():
        if key not in source_state and key.endswith("_rate"):
            continue
        actual_count_value = source_state.get(key)
        if isinstance(expected_count_value, list):
            if not _is_sorted_string_list(actual_count_value):
                issues.append(f"run_metadata source_state invalid type: {key}")
                continue
            actual_count_values = cast(list[str], actual_count_value)
            _validate_prediction_input_inventory_source_state_scoped_ids(
                key,
                actual_count_values,
                source_state,
                issues,
            )
        elif key.endswith("_rate"):
            if actual_count_value is not None and not _is_rate(actual_count_value):
                issues.append(f"run_metadata source_state invalid type: {key}")
                continue
        elif not _is_non_negative_int(actual_count_value):
            issues.append(f"run_metadata source_state invalid type: {key}")
            continue
        if actual_count_value != expected_count_value:
            issues.append(f"run_metadata source_state mismatch: {key}")


def _validate_prediction_input_inventory_source_state_scoped_ids(
    key: str,
    value: list[Any],
    source_state: dict[Any, Any],
    issues: list[str],
) -> None:
    if key == "legislative_body_ids":
        jurisdiction_ids = set(source_state.get("jurisdiction_ids", []))
        if any(
            not isinstance(item, str)
            or len(item.split(":")) != 2
            or item.split(":")[0] not in jurisdiction_ids
            or not item.split(":")[1]
            for item in value
        ):
            issues.append("run_metadata source_state invalid scoped ids: legislative_body_ids")
    if key == "legislative_session_ids":
        jurisdiction_ids = set(source_state.get("jurisdiction_ids", []))
        body_ids = set(source_state.get("legislative_body_ids", []))
        if any(
            not isinstance(item, str)
            or len(item.split(":")) != 3
            or item.split(":")[0] not in jurisdiction_ids
            or f"{item.split(':')[0]}:{item.split(':')[1]}" not in body_ids
            or not item.split(":")[2]
            for item in value
        ):
            issues.append("run_metadata source_state invalid scoped ids: legislative_session_ids")
    if key == "source_family_ids" and any(
        not isinstance(item, str) or _SOURCE_FAMILY_ID_RE.fullmatch(item) is None for item in value
    ):
        issues.append("run_metadata source_state invalid source_family_ids")


_REQUIRED_PREDICTION_INPUT_INVENTORY_OFFICIAL_FIELDS = (
    "official_training_label_source_url_count",
    "official_evaluation_label_source_url_count",
    "training_label_official_source_url_coverage_rate",
    "evaluation_label_official_source_url_coverage_rate",
    "official_bill_source_url_count",
    "bill_official_source_url_coverage_rate",
    "official_sourced_ontology_edge_count",
    "ontology_official_source_anchor_coverage_rate",
)

_REQUIRED_PREDICTION_INPUT_INVENTORY_OFFICIAL_SOURCE_STATE_FIELDS = (
    "official_training_label_source_url_count",
    "official_evaluation_label_source_url_count",
    "official_bill_source_url_count",
    "official_sourced_ontology_edge_count",
)


def _validate_prediction_input_inventory_required_official_source_metadata(
    *,
    artifact: dict[Any, Any],
    run_metadata: dict[Any, Any],
    issues: list[str],
) -> None:
    for field in _REQUIRED_PREDICTION_INPUT_INVENTORY_OFFICIAL_FIELDS:
        if field not in artifact:
            issues.append(f"required official source field missing: {field}")
    source_state = run_metadata.get("source_state")
    if not isinstance(source_state, dict):
        return
    for field in _REQUIRED_PREDICTION_INPUT_INVENTORY_OFFICIAL_SOURCE_STATE_FIELDS:
        if source_state.get(field) is None:
            issues.append(f"run_metadata source_state missing: {field}")


def _validate_prediction_input_inventory_congress_archive_manifest_metadata(
    *,
    run_metadata: dict[Any, Any],
    issues: list[str],
    require_manifest: bool,
) -> None:
    manifest = run_metadata.get("congress_archive_manifest")
    if manifest is None:
        if require_manifest:
            issues.append("run_metadata congress_archive_manifest missing")
        return
    if not isinstance(manifest, dict):
        issues.append("run_metadata congress_archive_manifest must be an object")
        return
    manifest_path_raw = manifest.get("path")
    expected_sha = manifest.get("sha256")
    if not isinstance(manifest_path_raw, str) or not manifest_path_raw:
        issues.append("run_metadata congress_archive_manifest path missing")
        return
    if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
        issues.append("run_metadata congress_archive_manifest sha256 invalid")
        return
    manifest_path = Path(manifest_path_raw)
    if not manifest_path.is_file():
        issues.append(f"run_metadata congress_archive_manifest file not found: {manifest_path}")
        return
    actual_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        issues.append(
            "run_metadata congress_archive_manifest sha256 mismatch: "
            f"expected {expected_sha}, got {actual_sha}"
        )


def _prediction_input_inventory_verify_run_metadata(
    args: Any,
    artifact_path: Path,
    *,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_metadata: dict[str, Any] = {
        "command": "verify-prediction-input-inventory",
        "verification_flags": {
            "require_run_metadata": bool(getattr(args, "require_run_metadata", False)),
            "require_congress_archive_manifest": bool(
                getattr(args, "require_congress_archive_manifest", False)
            ),
            "require_clean_inventory": bool(getattr(args, "require_clean_inventory", False)),
            "require_portable_jurisdiction_ids": bool(
                getattr(args, "require_portable_jurisdiction_ids", False)
            ),
            "require_portable_body_ids": bool(getattr(args, "require_portable_body_ids", False)),
            "require_portable_session_ids": bool(
                getattr(args, "require_portable_session_ids", False)
            ),
            "require_source_families": list(getattr(args, "require_source_families", None) or []),
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
        },
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if artifact_path.is_file()
        else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_rate(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _is_sorted_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() == item and item for item in value)
        and len(set(value)) == len(value)
        and value == sorted(value)
    )
