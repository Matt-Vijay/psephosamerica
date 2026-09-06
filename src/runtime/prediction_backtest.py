from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, TypeGuard

from src.core.files import sha256_file
from src.evidence.source_anchor_policy import (
    SOURCE_TYPES_REQUIRING_URL,
    anchor_source_type,
    anchor_url,
    is_official_source_url,
)
from src.ingest.congress.archive_manifest import (
    manifest_file_metadata as _congress_archive_manifest_metadata,
    validate_manifest_file_metadata as _validate_prediction_backtest_congress_archive_manifest_metadata,
)
from src.pipeline.publish_snapshot_run import _ontology_edge_from_row
from src.prediction.backtest import (
    OPTIONAL_ONTOLOGY_FEATURE_SIGNAL_NAMES,
    REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES,
    PredictionBacktestPayload,
    build_vote_baseline_backtest,
    build_vote_ontology_backtest,
)
from src.prediction.llm_semantics import load_bill_semantic_payloads
from src.query.published_rows import (
    fetch_all_ontology_edge_rows,
    fetch_vote_prediction_backtest_bill_signal_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
    fetch_vote_prediction_contribution_signal_rows,
    fetch_vote_prediction_statement_signal_rows,
)
from src.runtime.app import build_runtime
from src.runtime.bill_semantics_cache import (
    bill_semantics_cache_failures as _prediction_backtest_bill_semantics_cache_failures,
    bill_semantics_index_model_names as _bill_semantics_index_model_names,
    bill_semantics_index_sha256 as _bill_semantics_index_sha256,
    has_bill_semantics_cache as _prediction_backtest_has_bill_semantics_cache,
    validate_bill_semantics_run_metadata as _validate_bill_semantics_run_metadata,
)
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.json_artifacts import (
    attach_optional_verification_output as _attach_optional_verification_output,
    write_json_artifact as _write_json_artifact,
)

_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def run_prediction_backtest_command(
    ctx: RuntimeContext,
    *,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    model: str = "baseline",
    bill_semantics_root: Path | None = None,
) -> PredictionBacktestPayload:
    """Open a connection and run a no-leakage vote prediction backtest."""
    conn = open_connection(ctx)
    feature_rows = fetch_vote_prediction_backtest_feature_rows(conn, feature_cutoff)
    label_rows = fetch_vote_prediction_backtest_label_rows(conn, label_start, label_end)
    if model == "ontology":
        ontology_edges = [
            _ontology_edge_from_row(row) for row in fetch_all_ontology_edge_rows(conn)
        ]
        return build_vote_ontology_backtest(
            feature_cutoff=feature_cutoff,
            label_start=label_start,
            label_end=label_end,
            feature_rows=feature_rows,
            label_rows=label_rows,
            ontology_edges=ontology_edges,
            bill_signal_rows=fetch_vote_prediction_backtest_bill_signal_rows(
                conn,
                feature_cutoff,
            ),
            bill_semantics=(
                load_bill_semantic_payloads(bill_semantics_root)
                if bill_semantics_root is not None
                else None
            ),
            contribution_signal_rows=fetch_vote_prediction_contribution_signal_rows(
                conn,
                feature_cutoff,
            ),
            statement_signal_rows=fetch_vote_prediction_statement_signal_rows(
                conn,
                feature_cutoff,
            ),
        )
    if model != "baseline":
        raise ValueError(f"unsupported prediction backtest model: {model}")
    return build_vote_baseline_backtest(
        feature_cutoff=feature_cutoff,
        label_start=label_start,
        label_end=label_end,
        feature_rows=feature_rows,
        label_rows=label_rows,
    )


def handle_prediction_backtest_command(args: Any) -> dict[str, Any]:
    window_issues = _prediction_backtest_window_issues(args)
    if window_issues:
        return _command_issue_result("prediction-backtest", window_issues)

    runtime = build_runtime()
    bill_semantics_root = (
        Path(args.bill_semantics_root) if args.bill_semantics_root is not None else None
    )
    bill_semantics_index_sha256 = _bill_semantics_index_sha256(bill_semantics_root)
    bill_semantics_model_names = _bill_semantics_index_model_names(bill_semantics_root)
    result = run_prediction_backtest_command(
        runtime.context,
        feature_cutoff=args.feature_cutoff,
        label_start=args.label_start,
        label_end=args.label_end,
        model=args.model,
        bill_semantics_root=bill_semantics_root,
    )
    warning_reasons: list[str] = []
    if args.model == "ontology" and bill_semantics_root is None:
        warning_reasons.append("missing_bill_semantics_root")
    congress_archive_manifest = _congress_archive_manifest_metadata(
        getattr(args, "congress_archive_manifest", None)
    )
    source_state = prediction_backtest_source_state(result)
    if warning_reasons:
        source_state["warning_reason_count"] = len(warning_reasons)
        source_state["warning_reasons"] = warning_reasons
    run_metadata: dict[str, Any] = {
        "command": "prediction-backtest",
        **_prediction_backtest_run_metadata(args),
        "source_state": source_state,
        "bill_semantics_root": (
            str(bill_semantics_root) if bill_semantics_root is not None else None
        ),
        "bill_semantics_index_sha256": bill_semantics_index_sha256,
        "bill_semantics_model_names": bill_semantics_model_names,
    }
    if warning_reasons:
        run_metadata["warning_reasons"] = warning_reasons
    if congress_archive_manifest is not None:
        run_metadata["congress_archive_manifest"] = congress_archive_manifest
    payload = result.model_dump(mode="json")
    payload["run_metadata"] = run_metadata
    output = Path(args.output) if args.output is not None else None
    output_sha256: str | None = None
    if output is not None:
        output_sha256 = _write_json_artifact(output, payload)
    quality_gate_failures: list[str] = []
    if (
        getattr(args, "fail_on_mixed_bill_semantics_models", False)
        and len(bill_semantics_model_names) > 1
    ):
        quality_gate_failures.append("mixed_bill_semantics_models")
    if (
        getattr(args, "fail_on_missing_bill_semantics_root", False)
        and "missing_bill_semantics_root" in warning_reasons
    ):
        quality_gate_failures.append("missing_bill_semantics_root")
    return {
        "ok": not quality_gate_failures,
        "command": "prediction-backtest",
        "quality_gate_failures": quality_gate_failures,
        "warning_reasons": warning_reasons,
        "model_name": result.model_name,
        "feature_cutoff": result.feature_cutoff.isoformat(),
        "label_start": result.label_start.isoformat(),
        "label_end": result.label_end.isoformat(),
        "feature_vote_event_count": result.feature_vote_event_count,
        "label_vote_event_count": result.label_vote_event_count,
        "member_count": result.member_count,
        "metrics": result.metrics.model_dump(mode="json"),
        "bill_semantics_root": (
            str(bill_semantics_root) if bill_semantics_root is not None else None
        ),
        "bill_semantics_index_sha256": bill_semantics_index_sha256,
        "bill_semantics_model_names": bill_semantics_model_names,
        "output": str(output) if output is not None else None,
        "output_sha256": output_sha256,
    }


def _prediction_backtest_window_issues(args: Any) -> list[str]:
    issues: list[str] = []
    if args.label_start > args.label_end:
        issues.append("label_start must be on or before label_end")
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


def verify_prediction_backtest_command(args: Any) -> dict[str, Any]:
    artifact_path = Path(args.artifact)
    issues: list[str] = []
    checked = 0

    def failure_result(*, checked: int, issues: list[str]) -> dict[str, Any]:
        return _attach_optional_verification_output(
            args,
            {
                "ok": False,
                "command": "verify-prediction-backtest",
                "artifact": str(artifact_path),
                "checked": checked,
                "issues": issues,
                "issue_count": len(issues),
                "quality_gate_failures": [],
                "quality_gate_failure_count": 0,
                "run_metadata": _prediction_backtest_verify_run_metadata(
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
        payload = PredictionBacktestPayload.model_validate(artifact)
    except Exception as exc:  # noqa: BLE001
        return failure_result(checked=0, issues=[f"artifact schema: {exc}"])
    checked += 1
    run_metadata = artifact.get("run_metadata")
    require_run_metadata = bool(getattr(args, "require_run_metadata", False))
    required_bill_semantics_model_names = _required_model_names(
        getattr(args, "require_bill_semantics_model_name", None),
        issues=issues,
        label="require_bill_semantics_model_name",
    )
    required_source_families = _required_source_family_ids(
        getattr(args, "require_source_families", None),
        issues=issues,
    )
    require_bill_semantics_source_inputs_sha256 = bool(
        getattr(args, "require_bill_semantics_source_inputs_sha256", False)
    )
    require_bill_semantics_cache = (
        bool(getattr(args, "require_bill_semantics_cache", False))
        or bool(required_bill_semantics_model_names)
        or require_bill_semantics_source_inputs_sha256
    )
    quality_gate_failures: list[str] = []
    if run_metadata is None:
        if require_run_metadata:
            issues.append("run_metadata missing")
    elif not isinstance(run_metadata, dict):
        issues.append("run_metadata must be an object")
    else:
        checked += _validate_prediction_backtest_run_metadata(
            payload=payload,
            run_metadata=run_metadata,
            issues=issues,
            require_source_state=require_run_metadata,
        )
        checked += _validate_bill_semantics_run_metadata(
            run_metadata=run_metadata,
            issues=issues,
            require_cache_state=require_run_metadata,
        )
        _validate_prediction_backtest_congress_archive_manifest_metadata(
            run_metadata=run_metadata,
            issues=issues,
            require_manifest=bool(getattr(args, "require_congress_archive_manifest", False)),
        )
    if (
        bool(getattr(args, "require_evaluated_predictions", False))
        and payload.metrics.evaluated_count == 0
    ):
        quality_gate_failures.append("no_evaluated_predictions")
    _validate_prediction_event_keys(payload, issues)
    prediction_source_url_count = sum(
        1 for prediction in payload.predictions if prediction.source_url
    )
    missing_prediction_source_url_count = len(payload.predictions) - prediction_source_url_count
    official_prediction_source_url_count = sum(
        1 for prediction in payload.predictions if _prediction_has_official_source_url(prediction)
    )
    unofficial_prediction_source_url_count = (
        prediction_source_url_count - official_prediction_source_url_count
    )
    if (
        bool(getattr(args, "require_prediction_source_urls", False))
        and missing_prediction_source_url_count
    ):
        quality_gate_failures.append("missing_prediction_source_urls")
    if bool(getattr(args, "require_official_prediction_source_urls", False)):
        if (
            missing_prediction_source_url_count
            and "missing_prediction_source_urls" not in quality_gate_failures
        ):
            quality_gate_failures.append("missing_prediction_source_urls")
        if unofficial_prediction_source_url_count:
            quality_gate_failures.append("unofficial_prediction_source_urls")
    if require_bill_semantics_cache:
        if not _prediction_backtest_has_bill_semantics_cache(run_metadata):
            quality_gate_failures.append("bill_semantics_cache_missing")
        elif isinstance(run_metadata, dict):
            quality_gate_failures.extend(
                _prediction_backtest_bill_semantics_cache_failures(
                    run_metadata=run_metadata,
                    required_model_names=required_bill_semantics_model_names,
                    require_source_inputs_sha256=(require_bill_semantics_source_inputs_sha256),
                    issues=issues,
                )
            )
    required_model_name = getattr(args, "require_model_name", None)
    if required_model_name is not None and payload.model_name != str(required_model_name):
        quality_gate_failures.append("model_name_mismatch")
    source_family_ids = _prediction_source_family_ids(payload)
    missing_required_source_family_ids = [
        source_family_id
        for source_family_id in required_source_families
        if source_family_id not in source_family_ids
    ]
    quality_gate_failures.extend(
        f"missing_required_source_family:{source_family_id}"
        for source_family_id in missing_required_source_family_ids
    )
    ontology_feature_signal_state = ontology_feature_signal_state_from_backtest(payload)
    if (
        bool(getattr(args, "require_ontology_feature_signals", False))
        and ontology_feature_signal_state["missing_prediction_count"]
    ):
        quality_gate_failures.append("missing_ontology_feature_signals")
    if (
        bool(getattr(args, "require_ontology_feature_signals", False))
        and not ontology_feature_signal_state["missing_prediction_count"]
        and ontology_feature_signal_state["missing_source_anchor_prediction_count"]
    ):
        quality_gate_failures.append("missing_ontology_feature_source_anchors")
    verify_source_state = _prediction_backtest_verify_source_state(
        payload=payload,
        run_metadata=run_metadata,
        prediction_source_url_count=prediction_source_url_count,
        missing_prediction_source_url_count=missing_prediction_source_url_count,
        official_prediction_source_url_count=official_prediction_source_url_count,
        unofficial_prediction_source_url_count=unofficial_prediction_source_url_count,
    )
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-prediction-backtest",
            "artifact": str(artifact_path),
            "checked": checked,
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "required_model_name": str(required_model_name)
            if required_model_name is not None
            else None,
            "source_family_ids": source_family_ids,
            "required_source_family_ids": required_source_families,
            "missing_required_source_family_ids": missing_required_source_family_ids,
            "model_name": payload.model_name,
            "ontology_feature_signal_state": ontology_feature_signal_state,
            "prediction_count": len(payload.predictions),
            "prediction_source_url_count": prediction_source_url_count,
            "missing_prediction_source_url_count": missing_prediction_source_url_count,
            "official_prediction_source_url_count": (official_prediction_source_url_count),
            "unofficial_prediction_source_url_count": (unofficial_prediction_source_url_count),
            "metric_evaluated_count": payload.metrics.evaluated_count,
            "run_metadata": _prediction_backtest_verify_run_metadata(
                args,
                artifact_path,
                source_state=verify_source_state,
            ),
        },
    )


def prediction_backtest_source_state(
    payload: PredictionBacktestPayload,
) -> dict[str, Any]:
    prediction_source_url_count = sum(
        1 for prediction in payload.predictions if prediction.source_url
    )
    official_prediction_source_url_count = sum(
        1 for prediction in payload.predictions if _prediction_has_official_source_url(prediction)
    )
    member_vote_history_source_anchor_count = _feature_source_anchor_count(
        payload,
        "member_vote_history",
    )
    official_member_vote_history_source_anchor_count = _official_feature_source_anchor_count(
        payload,
        "member_vote_history",
    )
    feature_source_anchor_counts = _feature_source_anchor_counts(payload)
    official_feature_source_anchor_counts = _official_feature_source_anchor_counts(payload)
    feature_source_backed_prediction_count = _feature_source_backed_prediction_count(payload)
    official_feature_source_backed_prediction_count = (
        _official_feature_source_backed_prediction_count(payload)
    )
    legislative_feature_source_anchor_count = _legislative_feature_source_anchor_count(payload)
    legislative_feature_source_anchor_context_count = (
        _legislative_feature_source_anchor_context_count(payload)
    )
    source_family_ids = _prediction_source_family_ids(payload)
    source_state = {
        "model_name": payload.model_name,
        "feature_vote_event_count": payload.feature_vote_event_count,
        "label_vote_event_count": payload.label_vote_event_count,
        "member_count": payload.member_count,
        "prediction_count": len(payload.predictions),
        "metrics_label_count": payload.metrics.label_count,
        "metrics_evaluated_count": payload.metrics.evaluated_count,
        "metrics_skipped_count": payload.metrics.skipped_count,
        "prediction_source_url_count": prediction_source_url_count,
        "missing_prediction_source_url_count": (
            len(payload.predictions) - prediction_source_url_count
        ),
        "official_prediction_source_url_count": official_prediction_source_url_count,
        "unofficial_prediction_source_url_count": (
            prediction_source_url_count - official_prediction_source_url_count
        ),
    }
    if source_family_ids:
        source_state["source_family_count"] = len(source_family_ids)
        source_state["source_family_ids"] = source_family_ids
    if member_vote_history_source_anchor_count:
        source_state["member_vote_history_source_anchor_count"] = (
            member_vote_history_source_anchor_count
        )
        source_state["official_member_vote_history_source_anchor_count"] = (
            official_member_vote_history_source_anchor_count
        )
    if feature_source_anchor_counts:
        source_state["feature_source_anchor_counts"] = feature_source_anchor_counts
        source_state["official_feature_source_anchor_counts"] = (
            official_feature_source_anchor_counts
        )
        source_state["feature_source_backed_prediction_count"] = (
            feature_source_backed_prediction_count
        )
        source_state["feature_source_missing_prediction_count"] = (
            payload.metrics.evaluated_count - feature_source_backed_prediction_count
        )
        source_state["feature_source_coverage_rate"] = (
            feature_source_backed_prediction_count / payload.metrics.evaluated_count
            if payload.metrics.evaluated_count
            else None
        )
        source_state["official_feature_source_backed_prediction_count"] = (
            official_feature_source_backed_prediction_count
        )
        source_state["unofficial_feature_source_backed_prediction_count"] = (
            feature_source_backed_prediction_count - official_feature_source_backed_prediction_count
        )
        source_state["official_feature_source_coverage_rate"] = (
            official_feature_source_backed_prediction_count / payload.metrics.evaluated_count
            if payload.metrics.evaluated_count
            else None
        )
        if legislative_feature_source_anchor_count:
            source_state["legislative_feature_source_anchor_count"] = (
                legislative_feature_source_anchor_count
            )
            source_state["legislative_feature_source_anchor_context_count"] = (
                legislative_feature_source_anchor_context_count
            )
            source_state["legislative_feature_source_anchor_missing_context_count"] = (
                legislative_feature_source_anchor_count
                - legislative_feature_source_anchor_context_count
            )
    jurisdiction_count = _prediction_jurisdiction_count(payload)
    implemented_jurisdiction_count = _prediction_implemented_jurisdiction_count(payload)
    portable_jurisdiction_count = _prediction_portable_jurisdiction_count(payload)
    legislative_body_count = _prediction_legislative_body_count(payload)
    legislative_session_count = _prediction_legislative_session_count(payload)
    jurisdiction_ids = _prediction_jurisdiction_ids(payload)
    implemented_jurisdiction_ids = _prediction_implemented_jurisdiction_ids(payload)
    portable_jurisdiction_ids = _prediction_portable_jurisdiction_ids(payload)
    legislative_body_ids = _prediction_legislative_body_ids(payload)
    legislative_session_ids = _prediction_legislative_session_ids(payload)
    if jurisdiction_count:
        source_state["jurisdiction_count"] = jurisdiction_count
        source_state["jurisdiction_ids"] = jurisdiction_ids
    if implemented_jurisdiction_count:
        source_state["implemented_jurisdiction_count"] = implemented_jurisdiction_count
        source_state["implemented_jurisdiction_ids"] = implemented_jurisdiction_ids
    if portable_jurisdiction_count:
        source_state["portable_jurisdiction_count"] = portable_jurisdiction_count
        source_state["portable_jurisdiction_ids"] = portable_jurisdiction_ids
    if legislative_body_count:
        source_state["legislative_body_count"] = legislative_body_count
        source_state["legislative_body_ids"] = legislative_body_ids
    if legislative_session_count:
        source_state["legislative_session_count"] = legislative_session_count
        source_state["legislative_session_ids"] = legislative_session_ids
    return source_state


def _prediction_has_official_source_url(prediction: Any) -> bool:
    source_type = (
        "vote_event" if prediction.jurisdiction_id == "us_congress" else "legislative_vote"
    )
    return is_official_source_url(source_type, prediction.source_url)


def _prediction_source_family_ids(payload: PredictionBacktestPayload) -> list[str]:
    families: set[str] = set()
    for prediction in payload.predictions:
        if prediction.source_url:
            source_type = (
                "vote_event" if prediction.jurisdiction_id == "us_congress" else "legislative_vote"
            )
            families.add(_source_family_id(source_type))
        for anchors in prediction.feature_source_anchors.values():
            for anchor in anchors:
                families.add(_source_family_id(str(anchor.source_type), prediction=prediction))
    return sorted(families)


def _source_family_id(source_type: str, *, prediction: Any | None = None) -> str:
    source_type = source_type.strip()
    if source_type == "legislative_vote":
        return "legislative_vote"
    if source_type in {"vote_event", "congress_vote"}:
        jurisdiction_id = getattr(prediction, "jurisdiction_id", None)
        return "congress_vote" if jurisdiction_id in (None, "us_congress") else "legislative_vote"
    return source_type


def _feature_source_anchor_count(payload: PredictionBacktestPayload, signal_name: str) -> int:
    return sum(
        len(prediction.feature_source_anchors.get(signal_name, []))
        for prediction in payload.predictions
        if prediction.skipped_reason is None
    )


def _feature_source_anchor_counts(payload: PredictionBacktestPayload) -> dict[str, int]:
    signal_names = {
        signal_name
        for prediction in payload.predictions
        if prediction.skipped_reason is None
        for signal_name in prediction.feature_source_anchors
    }
    return {
        signal_name: count
        for signal_name in sorted(signal_names)
        if (count := _feature_source_anchor_count(payload, signal_name)) > 0
    }


def _feature_source_backed_prediction_count(payload: PredictionBacktestPayload) -> int:
    return sum(
        1
        for prediction in payload.predictions
        if prediction.skipped_reason is None and any(prediction.feature_source_anchors.values())
    )


def _official_feature_source_backed_prediction_count(
    payload: PredictionBacktestPayload,
) -> int:
    return sum(
        1
        for prediction in payload.predictions
        if prediction.skipped_reason is None
        and any(
            _has_official_feature_source_anchor(anchors, prediction=prediction)
            for anchors in prediction.feature_source_anchors.values()
        )
    )


def _official_feature_source_anchor_count(
    payload: PredictionBacktestPayload,
    signal_name: str,
) -> int:
    return sum(
        1
        for prediction in payload.predictions
        if prediction.skipped_reason is None
        for anchor in prediction.feature_source_anchors.get(signal_name, [])
        if _feature_source_anchor_has_official_url(anchor, prediction=prediction)
    )


def _official_feature_source_anchor_counts(payload: PredictionBacktestPayload) -> dict[str, int]:
    return {
        signal_name: _official_feature_source_anchor_count(payload, signal_name)
        for signal_name in _feature_source_anchor_counts(payload)
    }


def _has_official_feature_source_anchor(
    anchors: list[Any],
    *,
    prediction: Any,
) -> bool:
    return any(
        _feature_source_anchor_has_official_url(anchor, prediction=prediction) for anchor in anchors
    )


def _feature_source_anchor_has_official_url(anchor: Any, *, prediction: Any) -> bool:
    source_type = anchor_source_type(anchor)
    if source_type not in SOURCE_TYPES_REQUIRING_URL:
        return False
    normalized_source_type = _source_family_id(source_type, prediction=prediction)
    return is_official_source_url(normalized_source_type, anchor_url(anchor))


def _legislative_feature_source_anchor_count(payload: PredictionBacktestPayload) -> int:
    return sum(
        1
        for prediction in payload.predictions
        if prediction.skipped_reason is None
        for anchors in prediction.feature_source_anchors.values()
        for anchor in anchors
        if _source_family_id(str(anchor.source_type), prediction=prediction)
        in {"legislative_bill", "legislative_vote"}
    )


def _legislative_feature_source_anchor_context_count(
    payload: PredictionBacktestPayload,
) -> int:
    return sum(
        1
        for prediction in payload.predictions
        if prediction.skipped_reason is None
        for anchors in prediction.feature_source_anchors.values()
        for anchor in anchors
        if _source_family_id(str(anchor.source_type), prediction=prediction)
        in {"legislative_bill", "legislative_vote"}
        and _source_anchor_has_legislative_context(anchor)
    )


def _source_anchor_has_legislative_context(anchor: Any) -> bool:
    return all(
        isinstance(getattr(anchor, field_name, None), str)
        and bool(getattr(anchor, field_name).strip())
        for field_name in (
            "jurisdiction_id",
            "legislative_body_id",
            "legislative_session_id",
        )
    )


def _prediction_jurisdiction_count(payload: PredictionBacktestPayload) -> int:
    return len(_prediction_jurisdiction_ids(payload))


def _prediction_jurisdiction_ids(payload: PredictionBacktestPayload) -> list[str]:
    return sorted({prediction.jurisdiction_id for prediction in payload.predictions})


def _prediction_implemented_jurisdiction_count(payload: PredictionBacktestPayload) -> int:
    return len(_prediction_implemented_jurisdiction_ids(payload))


def _prediction_implemented_jurisdiction_ids(
    payload: PredictionBacktestPayload,
) -> list[str]:
    return sorted(
        {
            prediction.jurisdiction_id
            for prediction in payload.predictions
            if prediction.jurisdiction_id == "us_congress"
        }
    )


def _prediction_portable_jurisdiction_count(payload: PredictionBacktestPayload) -> int:
    return len(_prediction_portable_jurisdiction_ids(payload))


def _prediction_portable_jurisdiction_ids(payload: PredictionBacktestPayload) -> list[str]:
    return sorted(
        {
            prediction.jurisdiction_id
            for prediction in payload.predictions
            if prediction.jurisdiction_id != "us_congress"
        }
    )


def _prediction_legislative_body_count(payload: PredictionBacktestPayload) -> int:
    return len(_prediction_legislative_body_ids(payload))


def _prediction_legislative_body_ids(payload: PredictionBacktestPayload) -> list[str]:
    return sorted(
        {
            f"{prediction.jurisdiction_id}:{_prediction_legislative_body_id(prediction)}"
            for prediction in payload.predictions
        }
    )


def _prediction_legislative_session_count(payload: PredictionBacktestPayload) -> int:
    return len(_prediction_legislative_session_ids(payload))


def _prediction_legislative_session_ids(payload: PredictionBacktestPayload) -> list[str]:
    return sorted(
        {
            (
                f"{prediction.jurisdiction_id}:"
                f"{_prediction_legislative_body_id(prediction)}:"
                f"{prediction.legislative_session_id}"
            )
            for prediction in payload.predictions
        }
    )


def _prediction_legislative_body_id(prediction: Any) -> str:
    if prediction.legislative_body_id:
        return str(prediction.legislative_body_id)
    if prediction.jurisdiction_id == "us_congress":
        return f"us_congress_{prediction.chamber}"
    return str(prediction.chamber)


def _prediction_backtest_run_metadata(args: Any) -> dict[str, str]:
    return {
        "feature_cutoff": args.feature_cutoff.isoformat(),
        "label_start": args.label_start.isoformat(),
        "label_end": args.label_end.isoformat(),
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
            "require_congress_archive_manifest": bool(
                getattr(args, "require_congress_archive_manifest", False)
            ),
            "require_model_name": getattr(args, "require_model_name", None),
            "require_ontology_feature_signals": bool(
                getattr(args, "require_ontology_feature_signals", False)
            ),
            "require_source_families": _required_source_family_ids(
                getattr(args, "require_source_families", None),
                issues=None,
            ),
            "require_bill_semantics_cache": (
                bool(getattr(args, "require_bill_semantics_cache", False))
                or bool(
                    _required_model_names(
                        getattr(args, "require_bill_semantics_model_name", None),
                        issues=None,
                        label="require_bill_semantics_model_name",
                    )
                )
                or bool(
                    getattr(
                        args,
                        "require_bill_semantics_source_inputs_sha256",
                        False,
                    )
                )
            ),
            "require_bill_semantics_model_names": _required_model_names(
                getattr(args, "require_bill_semantics_model_name", None),
                issues=None,
                label="require_bill_semantics_model_name",
            ),
            "require_bill_semantics_source_inputs_sha256": bool(
                getattr(
                    args,
                    "require_bill_semantics_source_inputs_sha256",
                    False,
                )
            ),
        },
        "artifact_sha256": sha256_file(artifact_path) if artifact_path.is_file() else None,
    }
    if source_state is not None:
        run_metadata["source_state"] = source_state
    return run_metadata


def _validate_prediction_backtest_run_metadata(
    *,
    payload: PredictionBacktestPayload,
    run_metadata: dict[Any, Any],
    issues: list[str],
    require_source_state: bool = False,
) -> int:
    if (require_source_state or "command" in run_metadata) and run_metadata.get(
        "command"
    ) != "prediction-backtest":
        issues.append("run_metadata mismatch: command")
    expected = {
        "feature_cutoff": payload.feature_cutoff.isoformat(),
        "label_start": payload.label_start.isoformat(),
        "label_end": payload.label_end.isoformat(),
    }
    if not any(key in run_metadata for key in expected):
        if require_source_state:
            for key in expected:
                issues.append(f"run_metadata mismatch: {key}")
        checked = 1 if "source_state" in run_metadata else 0
    else:
        for key, expected_value in expected.items():
            if run_metadata.get(key) != expected_value:
                issues.append(f"run_metadata mismatch: {key}")
        checked = 1
    source_state = run_metadata.get("source_state")
    if source_state is None:
        if require_source_state:
            issues.append("run_metadata source_state missing")
    elif not isinstance(source_state, dict):
        issues.append("run_metadata source_state must be an object")
    else:
        expected_source_state = prediction_backtest_source_state(payload)
        warning_reasons = _prediction_backtest_run_metadata_warning_reasons(
            run_metadata,
            issues=issues,
        )
        if warning_reasons:
            expected_source_state["warning_reason_count"] = len(warning_reasons)
            expected_source_state["warning_reasons"] = warning_reasons
        _validate_prediction_backtest_source_state_scope_ids(source_state, issues)
        for key in sorted(set(source_state) - set(expected_source_state)):
            issues.append(f"run_metadata source_state unexpected: {key}")
        for key, expected_count_value in expected_source_state.items():
            actual_count_value = source_state.get(key)
            if isinstance(expected_count_value, dict):
                if _validate_prediction_backtest_source_state_count_map(
                    key,
                    expected_count_value,
                    actual_count_value,
                    issues,
                ):
                    continue
            if isinstance(expected_count_value, int) and not isinstance(
                expected_count_value,
                bool,
            ):
                if not _is_non_negative_int(actual_count_value):
                    issues.append(f"run_metadata source_state invalid type: {key}")
                    continue
            if isinstance(expected_count_value, float):
                if not _is_rate(actual_count_value):
                    issues.append(f"run_metadata source_state invalid type: {key}")
                    continue
            if isinstance(expected_count_value, list):
                if not _is_sorted_string_list(actual_count_value):
                    issues.append(f"run_metadata source_state invalid type: {key}")
                    continue
            if actual_count_value != expected_count_value:
                issues.append(f"run_metadata source_state mismatch: {key}")
    return checked


def _prediction_backtest_run_metadata_warning_reasons(
    run_metadata: dict[Any, Any],
    *,
    issues: list[str] | None = None,
) -> list[str]:
    value = run_metadata.get("warning_reasons")
    if not isinstance(value, list):
        if value is not None and issues is not None:
            issues.append("run_metadata warning_reasons must be a sorted unique string list")
        return []
    if any(not isinstance(item, str) or not item or item.strip() != item for item in value):
        if issues is not None:
            issues.append("run_metadata warning_reasons must be a sorted unique string list")
        return []
    if value != sorted(value) or len(set(value)) != len(value):
        if issues is not None:
            issues.append("run_metadata warning_reasons must be a sorted unique string list")
        return []
    return value


def _validate_prediction_backtest_source_state_count_map(
    key: str,
    expected_count_map: dict[Any, Any],
    actual_count_map: Any,
    issues: list[str],
) -> bool:
    if not all(
        isinstance(expected_key, str)
        and isinstance(expected_value, int)
        and not isinstance(expected_value, bool)
        and expected_value >= 0
        for expected_key, expected_value in expected_count_map.items()
    ):
        return False
    if not isinstance(actual_count_map, dict):
        issues.append(f"run_metadata source_state invalid type: {key}")
        return True
    invalid_entry = False
    for signal_name, actual_value in actual_count_map.items():
        if not isinstance(signal_name, str) or not _is_non_negative_int(actual_value):
            issues.append(f"run_metadata source_state invalid type: {key}.{signal_name}")
            invalid_entry = True
    if invalid_entry:
        return True
    if actual_count_map != expected_count_map:
        issues.append(f"run_metadata source_state mismatch: {key}")
    return True


def _validate_prediction_backtest_source_state_scope_ids(
    source_state: dict[Any, Any],
    issues: list[str],
) -> None:
    jurisdiction_ids = source_state.get("jurisdiction_ids")
    legislative_body_ids = source_state.get("legislative_body_ids")
    legislative_session_ids = source_state.get("legislative_session_ids")
    if not _is_sorted_string_list(jurisdiction_ids):
        return
    jurisdiction_id_set = set(jurisdiction_ids)
    body_id_set: set[str] = set()
    if isinstance(legislative_body_ids, list):
        for body_id in legislative_body_ids:
            if not isinstance(body_id, str):
                continue
            parts = body_id.split(":")
            if len(parts) != 2 or parts[0] not in jurisdiction_id_set or not parts[1]:
                issues.append(
                    "run_metadata source_state legislative_body_ids must be jurisdiction scoped"
                )
                break
            body_id_set.add(body_id)
    if isinstance(legislative_session_ids, list):
        for session_id in legislative_session_ids:
            if not isinstance(session_id, str):
                continue
            parts = session_id.split(":")
            if (
                len(parts) != 3
                or parts[0] not in jurisdiction_id_set
                or f"{parts[0]}:{parts[1]}" not in body_id_set
                or not parts[2]
            ):
                issues.append(
                    "run_metadata source_state legislative_session_ids must be body scoped"
                )
                break


def _prediction_backtest_verify_source_state(
    *,
    payload: PredictionBacktestPayload,
    run_metadata: Any,
    prediction_source_url_count: int,
    missing_prediction_source_url_count: int,
    official_prediction_source_url_count: int,
    unofficial_prediction_source_url_count: int,
) -> dict[str, Any]:
    source_state = {
        **prediction_backtest_source_state(payload),
        "ontology_feature_signal_state": ontology_feature_signal_state_from_backtest(payload),
        "prediction_source_url_count": prediction_source_url_count,
        "missing_prediction_source_url_count": missing_prediction_source_url_count,
        "official_prediction_source_url_count": official_prediction_source_url_count,
        "unofficial_prediction_source_url_count": unofficial_prediction_source_url_count,
        "bill_semantics_root": None,
        "bill_semantics_index_sha256": None,
        "bill_semantics_model_names": [],
    }
    if not isinstance(run_metadata, dict):
        return source_state
    source_state["bill_semantics_root"] = run_metadata.get("bill_semantics_root")
    source_state["bill_semantics_index_sha256"] = run_metadata.get("bill_semantics_index_sha256")
    model_names = run_metadata.get("bill_semantics_model_names")
    if isinstance(model_names, list):
        source_state["bill_semantics_model_names"] = [str(model_name) for model_name in model_names]
    return source_state


def _validate_prediction_event_keys(
    payload: PredictionBacktestPayload,
    issues: list[str],
) -> None:
    for index, prediction in enumerate(payload.predictions):
        expected = _expected_prediction_event_key(prediction)
        if prediction.event_key != expected:
            issues.append(f"predictions[{index}].event_key mismatch: expected {expected}")


def _expected_prediction_event_key(prediction: Any) -> str:
    if prediction.jurisdiction_id == "us_congress":
        suffix = f"{prediction.congress}-{prediction.session_number}-{prediction.roll_call_number}"
        return f"{prediction.chamber}-{suffix}"
    legislative_body_id = prediction.legislative_body_id or prediction.chamber
    legislative_session_id = (
        prediction.legislative_session_id or f"{prediction.congress}-{prediction.session_number}"
    )
    return (
        f"{prediction.jurisdiction_id}-{legislative_body_id}-"
        f"{legislative_session_id}-{prediction.roll_call_number}"
    )


def ontology_feature_signal_state_from_backtest(
    payload: PredictionBacktestPayload,
) -> dict[str, Any]:
    required = set(REQUIRED_ONTOLOGY_FEATURE_SIGNAL_NAMES)
    optional = set(OPTIONAL_ONTOLOGY_FEATURE_SIGNAL_NAMES)
    missing_by_prediction: list[dict[str, Any]] = []
    missing_source_anchors_by_prediction: list[dict[str, Any]] = []
    missing_signal_names: set[str] = set()
    missing_source_anchor_signal_names: set[str] = set()
    checked_prediction_count = 0
    for index, prediction in enumerate(payload.predictions):
        if prediction.skipped_reason is not None:
            continue
        checked_prediction_count += 1
        present = set(prediction.feature_signals)
        unavailable = set(prediction.unavailable_signals)
        missing = sorted((required - present) | (optional - present - unavailable))
        if not missing:
            continue
        missing_signal_names.update(missing)
        missing_by_prediction.append(
            {
                "index": index,
                "vote_event_id": prediction.vote_event_id,
                "member_bioguide_id": prediction.member_bioguide_id,
                "missing_signal_names": missing,
            }
        )
        continue
    for index, prediction in enumerate(payload.predictions):
        if prediction.skipped_reason is not None:
            continue
        present = set(prediction.feature_signals)
        missing_sources = sorted(
            signal_name
            for signal_name in required
            if signal_name in present
            and _ontology_signal_requires_source_anchor(
                signal_name,
                prediction.feature_signals.get(signal_name),
            )
            and not _has_official_feature_source_anchor(
                prediction.feature_source_anchors.get(signal_name, []),
                prediction=prediction,
            )
        )
        if not missing_sources:
            continue
        missing_source_anchor_signal_names.update(missing_sources)
        missing_source_anchors_by_prediction.append(
            {
                "index": index,
                "vote_event_id": prediction.vote_event_id,
                "member_bioguide_id": prediction.member_bioguide_id,
                "missing_source_anchor_signal_names": missing_sources,
            }
        )
    return {
        "required_signal_names": sorted(required),
        "optional_signal_names": sorted(optional),
        "checked_prediction_count": checked_prediction_count,
        "missing_signal_names": sorted(missing_signal_names),
        "missing_prediction_count": len(missing_by_prediction),
        "missing_by_prediction": missing_by_prediction[:25],
        "missing_by_prediction_truncated": len(missing_by_prediction) > 25,
        "missing_source_anchor_signal_names": sorted(missing_source_anchor_signal_names),
        "missing_source_anchor_prediction_count": len(missing_source_anchors_by_prediction),
        "missing_source_anchors_by_prediction": missing_source_anchors_by_prediction[:25],
        "missing_source_anchors_by_prediction_truncated": (
            len(missing_source_anchors_by_prediction) > 25
        ),
    }


def _ontology_signal_requires_source_anchor(signal_name: str, value: float | None) -> bool:
    if value is None:
        return False
    if signal_name == "sponsor_cosponsor_alignment":
        return abs(value - 0.5) > 1e-9
    return abs(value) > 1e-9


def _required_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _required_model_names(
    value: Any,
    *,
    issues: list[str] | None,
    label: str,
) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    valid: list[str] = []
    malformed = False
    for item in values:
        if not isinstance(item, str):
            malformed = True
            continue
        if not item or item != item.strip():
            malformed = True
            continue
        valid.append(item)
    if len(set(valid)) != len(valid):
        malformed = True
    if malformed and issues is not None:
        issues.append(f"{label} must contain unique trimmed non-empty strings")
    return sorted(set(valid))


def _required_source_family_ids(value: Any, *, issues: list[str] | None) -> list[str]:
    source_family_ids = sorted(set(_required_string_list(value)))
    malformed = [
        source_family_id
        for source_family_id in source_family_ids
        if _SOURCE_FAMILY_ID_RE.fullmatch(source_family_id) is None
    ]
    if malformed and issues is not None:
        issues.append("require_source_families must contain normalized source family ids")
    return [
        source_family_id
        for source_family_id in source_family_ids
        if source_family_id not in malformed
    ]


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sorted_string_list(value: object) -> TypeGuard[list[str]]:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() == item and item for item in value)
        and len(set(value)) == len(value)
        and value == sorted(value)
    )


def _is_rate(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return 0 <= value <= 1


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
