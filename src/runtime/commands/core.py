"""Core operator commands: bootstrap, status, load, recompute, publish, oracle."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from src.evidence.source_anchor_policy import is_official_source_url
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.app import PsephosAmericaRuntime, build_runtime, open_runtime_connection
from src.runtime.bootstrap import bootstrap_database, describe_bootstrap_plan
from src.runtime.commands._shared import (
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
    _BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS,
    _command_issue_result,
    _emit_verification_summary,
    _is_plain_int,
    _optional_limit_issue,
    _positive_int_arg_issues,
    _required_string_list,
    _write_bytes_artifact,
)
from src.runtime.commands.statements import _load_statement_rows, _statement_rows_source_metadata
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_live_full import run_live_congress_load_full
from src.runtime.congress_options import CongressLoadOptions, current_congress_for_date
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures_bundle import DisclosuresBundle, load_disclosures_bundle
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.output import (
    summarize_load_result,
    summarize_local_oracle_run_result,
    summarize_publish_result,
    summarize_publish_roundtrip_result,
    summarize_publish_verify_result,
    summarize_recompute_result,
)
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.publish_roundtrip import verify_roundtrip as _verify_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify import verify_local_publish as _verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime
from src.runtime.status_command import get_runtime_status
from src.runtime.zip_bundle import load_zip_bundle

_SOURCE_FAMILY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def load_congress(
    ctx: RuntimeContext,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a live Congress load."""
    conn = open_connection(ctx)
    return run_live_congress_load_full(
        conn,
        ctx.settings,
        congress=options.congress,
        include_votes=options.include_votes,
        house_vote_year=options.house_vote_year,
        senate_session=options.senate_session,
    )


def load_congress_local(
    ctx: RuntimeContext,
    archive: Path,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Open a connection and run a Congress load from a local archive bundle."""
    conn = open_connection(ctx)
    return run_congress_archive_load(conn, archive, options)


def recompute_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    *,
    statement_rows: list[dict[str, Any]] | None = None,
    statement_rows_source: dict[str, Any] | None = None,
) -> RuntimeRecomputeResult:
    """Open a connection and run a full conflict-of-interest recompute."""
    conn = open_connection(ctx)
    return run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=ctx.taxonomy,
        issuer_sector_resolver=ctx.issuer_sector_resolver,
        contribution_sector_resolver=ctx.contribution_sector_resolver,
        statement_rows=statement_rows,
        statement_rows_source=statement_rows_source,
    )


def publish_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
) -> PublishRuntimeResult:
    """Open a connection and publish an immutable snapshot."""
    conn = open_connection(ctx)
    return run_publish_runtime(conn, snapshot_date, target_dir, zip_bundle_inputs)


def verify_publish_local(publish_root: Path) -> PublishVerifyResult:
    """Verify a local publish tree without a database connection."""
    result = _verify_local_publish(publish_root)
    _emit_verification_summary("verify-publish", publish_root, result)
    return result


def verify_publish_roundtrip_local(
    ctx: RuntimeContext,
    publish_root: Path,
) -> PublishRoundtripResult:
    """Verify a local publish tree using the DB as the source of truth."""
    conn = open_connection(ctx)
    result = _verify_roundtrip(conn, publish_root)
    _emit_verification_summary("verify-publish-roundtrip", publish_root, result)
    return result


def run_oracle_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> LocalOracleRunResult:
    """Open a connection and run the full local oracle pipeline."""
    conn = open_connection(ctx)
    return run_oracle_local(conn, congress_archive, disclosures_bundle, options)


def _snapshot_date_or_today(snapshot_date: dt.date | None) -> dt.date:
    return snapshot_date if snapshot_date is not None else dt.date.today()


def _publish_target_or_default(
    target_dir: str | Path | None, runtime: PsephosAmericaRuntime
) -> Path:
    if target_dir is None:
        return runtime.publish_root
    return Path(target_dir)


def _current_congress(today: dt.date | None = None) -> int:
    return current_congress_for_date(today)


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    ingestion_runs: list[dict[str, Any]] = status.get("ingestion_runs", [])
    parse_runs: list[dict[str, Any]] = status.get("parse_runs", [])
    source_artifacts: list[dict[str, Any]] = status.get("source_artifacts", [])
    data_sources: list[dict[str, Any]] = status.get("data_sources", [])

    latest_run = ingestion_runs[0] if ingestion_runs else None
    latest_parse = parse_runs[0] if parse_runs else None
    latest_artifact = source_artifacts[0] if source_artifacts else None

    return {
        "summary": status.get("summary", {}),
        "latest_ingestion_run": {
            "id": latest_run.get("id") if latest_run else None,
            "run_type": latest_run.get("run_type") if latest_run else None,
            "status": latest_run.get("status") if latest_run else None,
            "data_source": latest_run.get("data_source_slug") if latest_run else None,
        },
        "latest_parse_run": {
            "id": latest_parse.get("id") if latest_parse else None,
            "parser_name": latest_parse.get("parser_name") if latest_parse else None,
            "status": latest_parse.get("status") if latest_parse else None,
        },
        "latest_artifact": {
            "id": latest_artifact.get("id") if latest_artifact else None,
            "artifact_kind": latest_artifact.get("artifact_kind") if latest_artifact else None,
            "data_source": latest_artifact.get("data_source_slug") if latest_artifact else None,
        },
        "active_data_sources": [
            {
                "slug": row.get("slug"),
                "name": row.get("name"),
                "source_kind": row.get("source_kind"),
            }
            for row in data_sources
        ],
    }


def _publish_summary_ok(summary: dict[str, Any]) -> bool:
    return bool(summary.get("succeeded", True))


def _oracle_summary_ok(summary: dict[str, Any]) -> bool:
    congress_ok = bool(summary.get("congress", {}).get("load_ok", True))
    disclosures_ok = bool(summary.get("disclosures", {}).get("load_ok", True))
    publish_ok = bool(summary.get("publish", {}).get("succeeded", True))
    verify_ok = bool(summary.get("verify", {}).get("ok", True))
    roundtrip_ok = bool(summary.get("roundtrip", {}).get("ok", True))
    return congress_ok and disclosures_ok and publish_ok and verify_ok and roundtrip_ok


def _handle_bootstrap_db(args: Any) -> dict[str, Any]:
    plan = describe_bootstrap_plan()
    if args.dry_run:
        return {
            "ok": True,
            "command": "bootstrap-db",
            "dry_run": True,
            **plan,
        }

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    bootstrap_database(conn)
    return {"ok": True, "command": "bootstrap-db", "dry_run": False, **plan}


def _handle_status(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    return {
        "ok": True,
        "command": "status",
        **_compact_status(get_runtime_status(conn, limit=args.limit)),
    }


def _handle_load_congress(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    if issues:
        return _command_issue_result("load-congress", issues)

    runtime = build_runtime()
    ctx = runtime.context
    if args.api_key:
        ctx = replace(
            ctx, settings=ctx.settings.model_copy(update={"congress_api_key": args.api_key})
        )

    include_votes = (
        args.include_votes or args.house_vote_year is not None or args.senate_session is not None
    )
    options = CongressLoadOptions(
        congress=args.congress if args.congress is not None else _current_congress(),
        include_votes=include_votes,
        house_vote_year=args.house_vote_year,
        senate_session=args.senate_session,
    )
    result = load_congress(ctx, options)
    return {"ok": True, "command": "load-congress", **summarize_load_result(result)}


def _handle_recompute(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    statement_rows_path = (
        Path(args.statement_rows) if getattr(args, "statement_rows", None) is not None else None
    )
    statement_rows = (
        _load_statement_rows(statement_rows_path) if statement_rows_path is not None else None
    )
    statement_rows_source = (
        _statement_rows_source_metadata(statement_rows_path, len(statement_rows))
        if statement_rows_path is not None and statement_rows is not None
        else None
    )
    snapshot_date = _snapshot_date_or_today(args.snapshot_date)
    result = (
        recompute_snapshot(
            runtime.context,
            snapshot_date,
            statement_rows=statement_rows,
            statement_rows_source=statement_rows_source,
        )
        if statement_rows is not None
        else recompute_snapshot(runtime.context, snapshot_date)
    )
    summary = summarize_recompute_result(result)
    if statement_rows is not None:
        summary["statement_rows_loaded"] = len(statement_rows)
        summary["statement_rows_source"] = statement_rows_source
    return {"ok": True, "command": "recompute", **summary}


def _handle_publish(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    zip_bundle_path = getattr(args, "zip_bundle", None)
    if zip_bundle_path is None:
        raise ValueError("publish requires --zip-bundle")

    zip_bundle_inputs = load_zip_bundle(Path(zip_bundle_path))
    result = publish_snapshot(
        runtime.context,
        _snapshot_date_or_today(args.snapshot_date),
        _publish_target_or_default(args.out_dir, runtime),
        zip_bundle_inputs,
    )
    summary = summarize_publish_result(result)
    return {"ok": _publish_summary_ok(summary), "command": "publish", **summary}


def _handle_load_congress_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("load-congress-local", issues)

    runtime = build_runtime()
    options = CongressLoadOptions(
        congress=args.congress,
        include_votes=False,
        house_vote_year=None,
        senate_session=None,
    )
    result = load_congress_local(runtime.context, Path(args.archive), options)
    return {"ok": True, "command": "load-congress-local", **summarize_load_result(result)}


def _handle_run_oracle_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("run-oracle-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-oracle-local", [limit_issue])

    runtime = build_runtime()
    configured_congress = args.congress if args.congress is not None else _current_congress()
    congress_source = "explicit-arg" if args.congress is not None else "current-date-default"
    congress_options = CongressOracleOptions(
        congress=configured_congress,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        congress_source=congress_source,
    )
    options = LocalOracleOptions(
        congress_options=congress_options,
        snapshot_date=args.snapshot_date,
        target_dir=Path(args.target_dir),
        snapshot_id=args.snapshot_id,
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
    )
    result = run_oracle_local_command(
        runtime.context,
        Path(args.congress_archive),
        load_disclosures_bundle(Path(args.disclosures_bundle)),
        options,
    )
    summary = summarize_local_oracle_run_result(result)
    return {"ok": _oracle_summary_ok(summary), "command": "run-oracle-local", **summary}


def _string_list_from_plan(
    plan: dict[str, Any],
    key: str,
    issues: list[str],
    *,
    optional: bool = False,
) -> list[str]:
    value = plan.get(key)
    if value is None and optional:
        return []
    if not isinstance(value, list):
        issues.append(f"{key} must be a list")
        return []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        issues.append(f"{key} must contain only non-empty strings")
        return []
    return sorted(value)


def _date_from_iso_string(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _is_present_invalid_iso_date(value: Any) -> bool:
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        return True
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return True
    return False


def _is_official_plan_source_anchor(anchor: Any) -> bool:
    if not isinstance(anchor, dict):
        return False
    source_type = anchor.get("source_type")
    if not isinstance(source_type, str):
        return False
    url = anchor.get("url")
    if url is not None and not isinstance(url, str):
        return False
    return is_official_source_url(source_type, url)


def _handle_verify_publish(args: Any) -> dict[str, Any]:
    result = verify_publish_local(Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-publish",
        **summarize_publish_verify_result(result),
    }


def _handle_verify_publish_roundtrip(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    result = verify_publish_roundtrip_local(runtime.context, Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-publish-roundtrip",
        "roundtrip": summarize_publish_roundtrip_result(result),
    }


def _option_value_from_command(command: str, option: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for index, part in enumerate(parts):
        if part == option and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _option_values_from_command(command: str, option: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    values: list[str] = []
    for index, part in enumerate(parts):
        if part == option and index + 1 < len(parts):
            values.append(parts[index + 1])
    return values


def _int_option_value_from_command(command: str, option: str) -> int | None:
    raw = _option_value_from_command(command, option)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_option_value_from_command(command: str, option: str) -> float | None:
    raw = _option_value_from_command(command, option)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _numeric_or_none_matches(actual: Any, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return (
        isinstance(actual, int | float)
        and not isinstance(actual, bool)
        and abs(actual - expected) <= 1e-9
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text_artifact(path: Path, text: str) -> str:
    return _write_bytes_artifact(path, text.encode("utf-8"))


def _top_learned_signal_coefficients(result: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    coefficients = list(getattr(result, "learned_signal_coefficients", []))
    coefficients.sort(key=lambda item: abs(float(getattr(item, "coefficient", 0.0))), reverse=True)
    return [
        cast(dict[str, Any], coefficient.model_dump(mode="json"))
        if hasattr(coefficient, "model_dump")
        else {
            "signal_name": coefficient.signal_name,
            "coefficient": coefficient.coefficient,
        }
        for coefficient in coefficients[:limit]
    ]


_PREDICTION_EVAL_INT_THRESHOLD_ARGS = (
    "min_training_examples",
    "min_evaluation_examples",
)


_PREDICTION_EVAL_RATE_THRESHOLD_ARGS = (
    "min_bill_semantic_coverage_rate",
    "min_bill_metadata_coverage_rate",
    "min_training_bill_semantic_coverage_rate",
    "min_evaluation_bill_semantic_coverage_rate",
    "min_training_feature_source_coverage_rate",
    "min_evaluation_feature_source_coverage_rate",
    "min_training_feature_source_url_coverage_rate",
    "min_training_feature_official_source_coverage_rate",
    "min_evaluation_feature_source_url_coverage_rate",
    "min_evaluation_feature_official_source_coverage_rate",
    "min_evaluation_source_url_coverage_rate",
)


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


def _ontology_signal_requires_source_anchor(signal_name: str, value: float | None) -> bool:
    if value is None:
        return False
    if signal_name == "sponsor_cosponsor_alignment":
        return abs(value - 0.5) > 1e-9
    return abs(value) > 1e-9


_PREDICTION_EVAL_MANIFEST_COVERAGE_THRESHOLDS = (
    ("min_bill_semantic_coverage_rate", "bill_semantic_coverage_rate"),
    ("min_bill_metadata_coverage_rate", "bill_metadata_coverage_rate"),
    (
        "min_training_feature_source_coverage_rate",
        "training_feature_source_coverage_rate",
    ),
    (
        "min_evaluation_feature_source_coverage_rate",
        "evaluation_feature_source_coverage_rate",
    ),
    (
        "min_training_feature_source_url_coverage_rate",
        "training_feature_source_url_coverage_rate",
    ),
    (
        "min_training_feature_official_source_coverage_rate",
        "training_feature_official_source_coverage_rate",
    ),
    (
        "min_evaluation_feature_source_url_coverage_rate",
        "evaluation_feature_source_url_coverage_rate",
    ),
    (
        "min_evaluation_feature_official_source_coverage_rate",
        "evaluation_feature_official_source_coverage_rate",
    ),
    ("min_evaluation_source_url_coverage_rate", "evaluation_source_url_coverage_rate"),
)


_PREDICTION_EVAL_MANIFEST_REQUIRED_BOOL_THRESHOLDS = (
    (
        "require_fail_on_unknown_bill_semantic_availability",
        "fail_on_unknown_bill_semantic_availability",
    ),
    (
        "require_fail_on_unknown_bill_signal_availability",
        "fail_on_unknown_bill_signal_availability",
    ),
    (
        "require_fail_on_unknown_ontology_edge_availability",
        "fail_on_unknown_ontology_edge_availability",
    ),
    (
        "require_fail_on_unknown_contribution_signal_availability",
        "fail_on_unknown_contribution_signal_availability",
    ),
    (
        "require_fail_on_unknown_statement_signal_availability",
        "fail_on_unknown_statement_signal_availability",
    ),
)


_PREDICTION_EVAL_MANIFEST_FAILURE_ANALYSIS_KEYS = (
    "failure_case_count",
    "failure_group_count",
    "backfill_recommendation_count",
    "top_failure_groups",
    "top_backfill_recommendations",
)


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item and item.strip() == item for item in value
    )


def _manifest_failure_analysis_int(value: Any, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    count = value.get(key)
    return count if _is_plain_int(count) else None


_PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS = {
    "min_training_examples",
    "min_evaluation_examples",
}

_PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS = {
    "min_bill_semantic_coverage_rate",
    "min_bill_metadata_coverage_rate",
    "min_training_bill_semantic_coverage_rate",
    "min_evaluation_bill_semantic_coverage_rate",
    "min_training_feature_source_coverage_rate",
    "min_evaluation_feature_source_coverage_rate",
    "min_training_feature_source_url_coverage_rate",
    "min_training_feature_official_source_coverage_rate",
    "min_evaluation_feature_source_url_coverage_rate",
    "min_evaluation_feature_official_source_coverage_rate",
    "min_evaluation_source_url_coverage_rate",
}

_PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS = {
    "fail_on_unknown_bill_semantic_availability",
    "fail_on_unknown_bill_signal_availability",
    "fail_on_unknown_ontology_edge_availability",
    "fail_on_unknown_contribution_signal_availability",
    "fail_on_unknown_statement_signal_availability",
    "fail_on_mixed_bill_semantics_models",
}

_PREDICTION_EVAL_MANIFEST_THRESHOLD_KEYS = (
    _PREDICTION_EVAL_MANIFEST_INT_THRESHOLDS
    | _PREDICTION_EVAL_MANIFEST_RATE_THRESHOLDS
    | _PREDICTION_EVAL_MANIFEST_BOOL_THRESHOLDS
)


_PREDICTION_EVAL_CUTOFF_AUDIT_KEYS = (
    "training_feature_row_count",
    "evaluation_feature_row_count",
    "training_label_row_count",
    "evaluation_label_row_count",
    "ontology_edge_count",
    "cutoff_ontology_edge_count",
    "unknown_availability_ontology_edge_count",
    "excluded_future_ontology_edge_count",
    "bill_signal_row_count",
    "cutoff_bill_signal_row_count",
    "unknown_availability_bill_signal_row_count",
    "excluded_future_bill_signal_row_count",
    "bill_semantic_count",
    "cutoff_training_bill_semantic_count",
    "excluded_future_training_bill_semantic_count",
    "cutoff_evaluation_bill_semantic_count",
    "excluded_future_evaluation_bill_semantic_count",
    "unknown_availability_bill_semantic_count",
    "training_contribution_signal_row_count",
    "cutoff_training_contribution_signal_row_count",
    "unknown_availability_training_contribution_signal_row_count",
    "excluded_future_training_contribution_signal_row_count",
    "evaluation_contribution_signal_row_count",
    "cutoff_evaluation_contribution_signal_row_count",
    "unknown_availability_evaluation_contribution_signal_row_count",
    "excluded_future_evaluation_contribution_signal_row_count",
    "training_statement_signal_row_count",
    "cutoff_training_statement_signal_row_count",
    "unknown_availability_training_statement_signal_row_count",
    "excluded_future_training_statement_signal_row_count",
    "evaluation_statement_signal_row_count",
    "cutoff_evaluation_statement_signal_row_count",
    "unknown_availability_evaluation_statement_signal_row_count",
    "excluded_future_evaluation_statement_signal_row_count",
)


def _source_family_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None for item in value
    )


def _mapping_mismatch_key_issues(
    *,
    label: str,
    actual: object,
    expected: dict[str, Any],
    ignored_keys: set[str] | None = None,
) -> list[str]:
    if not isinstance(actual, dict):
        return [f"{label}: not an object"]
    issues: list[str] = []
    ignored_keys = ignored_keys or set()
    for key in sorted(set(str(item) for item in actual) | set(expected)):
        if key in ignored_keys:
            continue
        actual_value = actual.get(key)
        expected_value = expected.get(key)
        if _json_scalar_type_mismatch(actual_value, expected_value):
            issues.append(f"{label}: {key}")
        elif actual_value != expected_value:
            issues.append(f"{label}: {key}")
    return issues


def _json_scalar_type_mismatch(actual: object, expected: object) -> bool:
    if type(expected) is int:
        return type(actual) is not int
    if type(expected) is float:
        return type(actual) not in (int, float) or isinstance(actual, bool)
    if isinstance(expected, str | bool) or expected is None:
        return type(actual) is not type(expected)
    return False


def _path_from_payload(payload: dict[str, Any], key: str) -> Path | None:
    value = payload.get(key)
    return Path(value) if isinstance(value, str) and value else None


_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS = (
    "backtest_feature_source_backed_prediction_count",
    "backtest_feature_source_missing_prediction_count",
    "backtest_official_feature_source_backed_prediction_count",
    "backtest_unofficial_feature_source_backed_prediction_count",
    "backtest_legislative_feature_source_anchor_count",
    "backtest_legislative_feature_source_anchor_context_count",
    "backtest_legislative_feature_source_anchor_missing_context_count",
)

_BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS = (
    "backtest_feature_source_coverage_rate",
    "backtest_official_feature_source_coverage_rate",
)

_BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS = _PREDICTION_EVAL_CUTOFF_AUDIT_KEYS

_BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS = tuple(
    f"eval_cutoff_{key}" for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_COUNT_KEYS
)

_OFFLINE_READINESS_BENCHMARK_EVAL_CUTOFF_AUDIT_KEYS = tuple(
    f"benchmark_{key}" for key in _BENCHMARK_EVAL_CUTOFF_AUDIT_SOURCE_STATE_KEYS
)

_PREDICTION_OFFLINE_READINESS_SOURCE_STATE_STRING_LIST_KEYS = (
    "benchmark_jurisdiction_ids",
    "benchmark_implemented_jurisdiction_ids",
    "benchmark_portable_jurisdiction_ids",
    "benchmark_legislative_body_ids",
    "benchmark_legislative_session_ids",
    "source_url_audit_sample_bill_keys",
    "source_url_audit_sample_member_bioguide_ids",
    "source_url_audit_sample_jurisdiction_ids",
    "source_url_audit_sample_legislative_body_ids",
    "source_url_audit_sample_legislative_session_ids",
    "source_url_audit_sample_source_family_ids",
    "backfill_plan_sample_bill_keys",
    "backfill_plan_sample_member_bioguide_ids",
    "backfill_plan_sample_jurisdiction_ids",
    "backfill_plan_sample_legislative_body_ids",
    "backfill_plan_sample_legislative_session_ids",
    "backfill_plan_sample_source_family_ids",
    "congress_load_source_family_ids",
)

_BENCHMARK_BACKTEST_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_BACKTEST_SOURCE_COVERAGE_RATE_KEYS,
)

_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_INVENTORY_FEATURE_SOURCE_COVERAGE_RATE_KEYS,
)

_BENCHMARK_INVENTORY_SOURCE_COVERAGE_KEYS = (
    *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_COUNT_KEYS,
    *_BENCHMARK_INVENTORY_SOURCE_COVERAGE_RATE_KEYS,
)


def _append_issue_once(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _string_list_has_duplicates(values: list[str]) -> bool:
    return len(set(values)) != len(values)


def _string_list_has_empty(values: list[str]) -> bool:
    return any(value.strip() == "" for value in values)


def _string_list_has_untrimmed(values: list[str]) -> bool:
    return any(value != value.strip() for value in values)


def _missing_required_source_family_ids(failures: list[str]) -> list[str]:
    prefix = "missing_required_source_family:"
    return sorted(
        {
            failure.removeprefix(prefix)
            for failure in failures
            if failure.startswith(prefix)
            and _SOURCE_FAMILY_ID_RE.fullmatch(failure.removeprefix(prefix))
        }
    )


def _run_metadata_source_state(result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    run_metadata = result.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return {}
    source_state = run_metadata.get("source_state")
    return source_state if isinstance(source_state, dict) else {}


def _strict_nonblank_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or value != value.strip():
        return None
    return value


def _strict_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item and item == item.strip()]


def _normalized_source_family_ids(value: Any) -> list[str]:
    return sorted(
        {
            item
            for item in _strict_string_list(value)
            if _SOURCE_FAMILY_ID_RE.fullmatch(item) is not None
        }
    )


def _strict_non_negative_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    items: list[int] = []
    for item in value:
        if type(item) is int and item >= 0 and item not in items:
            items.append(item)
    return items


def _strict_object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _object_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _strict_string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return _strict_string_list(value)
    if value is None:
        return []
    item = _strict_nonblank_string(value)
    return [item] if item is not None else []


def _append_unique_objects(existing: list[Any], additions: list[Any]) -> list[Any]:
    merged = list(existing)
    seen = {_stable_json_key(item) for item in merged}
    for addition in additions:
        key = _stable_json_key(addition)
        if key in seen:
            continue
        merged.append(addition)
        seen.add(key)
    return merged


def _stable_json_key(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _merge_int_count_dicts(target: object, source: object) -> dict[str, Any]:
    merged = _strict_int_count_dict(target)
    if not isinstance(source, dict):
        return merged
    for key, value in source.items():
        name = _strict_nonblank_string(key)
        if name is None or not _is_plain_int(value):
            continue
        merged[name] = merged.get(name, 0) + int(value)
    return merged


def _strict_int_count_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: int(count)
        for key, count in value.items()
        if _strict_nonblank_string(key) is not None and _is_plain_int(count)
    }


_PREDICTION_BACKFILL_DATA_ACTION_ORDER = {
    "load_missing_bill_metadata": 10,
    "materialize_missing_bill_semantics": 20,
    "backfill_feature_source_urls": 30,
    "backfill_cutoff_feature_members": 35,
    "load_source_backed_public_statement_signals": 40,
    "load_fec_donations_and_member_crosswalks": 50,
    "timestamp_bill_signal_availability": 55,
    "timestamp_bill_semantic_availability": 60,
    "timestamp_contribution_signal_availability": 65,
    "timestamp_ontology_edge_availability": 70,
    "timestamp_statement_signal_availability": 75,
}

_PREDICTION_BACKFILL_REFRESH_ACTION_ORDER = {
    "refresh_prediction_input_inventory": 90,
    "refresh_prediction_backtest": 95,
    "refresh_prediction_eval_report": 100,
}


def _append_unique_strings(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(merged)
    for addition in additions:
        if addition and addition not in seen:
            merged.append(addition)
            seen.add(addition)
    return merged


_JURISDICTION_SEMANTIC_ADAPTER_SOURCE_REQUIREMENT = (
    "Non-Congress bill keys require a jurisdiction-specific bill metadata and semantic "
    "materialization adapter before the Congress.gov bill-semantics command can satisfy them."
)


def _congresses_from_bill_keys(bill_keys: list[str]) -> list[int]:
    congresses: set[int] = set()
    for bill_key in bill_keys:
        congress_raw = bill_key.split("-", 1)[0]
        if congress_raw.isdigit():
            congresses.add(int(congress_raw))
    return sorted(congresses)


_PREDICTION_BACKFILL_SUPPORTED_COMMAND_ACTIONS = {
    "refresh_prediction_input_inventory",
    "refresh_prediction_backtest",
    "refresh_prediction_eval_report",
    "load_missing_bill_metadata",
    "materialize_missing_bill_semantics",
    "backfill_feature_source_urls",
    "load_source_backed_public_statement_signals",
    "load_fec_donations_and_member_crosswalks",
    "timestamp_bill_semantic_availability",
    "timestamp_bill_signal_availability",
    "timestamp_contribution_signal_availability",
    "timestamp_ontology_edge_availability",
    "timestamp_statement_signal_availability",
}

_PREDICTION_BACKFILL_SUGGESTED_COMMAND_PREFIXES = {
    "refresh_prediction_input_inventory": (
        "python3 -m src.runtime.main prediction-input-inventory ",
        "python3 -m src.runtime.main verify-prediction-input-inventory ",
    ),
    "refresh_prediction_backtest": (
        "python3 -m src.runtime.main prediction-backtest ",
        "python3 -m src.runtime.main verify-prediction-backtest ",
    ),
    "refresh_prediction_eval_report": (
        "python3 -m src.runtime.main prediction-eval-report ",
        "python3 -m src.runtime.main verify-prediction-eval-manifest ",
    ),
    "load_missing_bill_metadata": ("python3 -m src.runtime.main load-congress ",),
    "materialize_missing_bill_semantics": (
        "python3 -m src.runtime.main materialize-bill-semantics ",
        "python3 -m src.runtime.main verify-bill-semantics-plan ",
        "python3 -m src.runtime.main verify-bill-semantics ",
    ),
    "backfill_feature_source_urls": (
        "python3 -m src.runtime.main prediction-source-url-audit ",
        "python3 -m src.runtime.main verify-prediction-source-url-audit ",
    ),
    "load_source_backed_public_statement_signals": (
        "python3 -m src.runtime.main materialize-public-statement-rss ",
        "python3 -m src.runtime.main materialize-public-statement-rows ",
        "python3 -m src.runtime.main verify-public-statement-rows ",
        "python3 -m src.runtime.main recompute ",
    ),
    "load_fec_donations_and_member_crosswalks": (
        "python3 -m src.runtime.main materialize-fec-bulk-files ",
        "python3 -m src.runtime.main materialize-member-fec-crosswalk ",
        "python3 -m src.runtime.main verify-fec-inputs ",
        "python3 -m src.runtime.main load-member-fec-crosswalk-local ",
        "python3 -m src.runtime.main load-fec-local ",
    ),
    "timestamp_bill_semantic_availability": (
        "python3 -m src.runtime.main materialize-bill-semantics ",
        "python3 -m src.runtime.main verify-bill-semantics-plan ",
        "python3 -m src.runtime.main verify-bill-semantics ",
    ),
    "timestamp_bill_signal_availability": (
        "python3 -m src.runtime.main load-congress ",
        "python3 -m src.runtime.main recompute",
    ),
    "timestamp_contribution_signal_availability": (
        "python3 -m src.runtime.main verify-fec-inputs ",
        "python3 -m src.runtime.main recompute",
    ),
    "timestamp_ontology_edge_availability": ("python3 -m src.runtime.main recompute",),
    "timestamp_statement_signal_availability": (
        "python3 -m src.runtime.main verify-public-statement-rows ",
        "python3 -m src.runtime.main recompute ",
    ),
}


def _has_strict_source_url_audit_verify_command(commands: list[str]) -> bool:
    return any(
        command.startswith("python3 -m src.runtime.main verify-prediction-source-url-audit ")
        and "--require-run-metadata" in command
        and "--require-no-gaps" in command
        and "--require-no-official-source-gaps" in command
        for command in commands
    )


def _rate_meets_minimum(value: float | None, minimum: float) -> bool:
    return value is not None and value >= minimum
