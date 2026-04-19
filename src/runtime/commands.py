"""Operator command execution and CLI dispatch helpers."""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.parse.disclosures.transform import DisclosureTransformResult
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.runtime.app import build_runtime, open_runtime_connection
from src.runtime.bootstrap import bootstrap_database, describe_bootstrap_plan
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_archive import run_congress_archive_load
from src.runtime.congress_live_full import run_live_congress_load_full
from src.runtime.congress_options import CongressLoadOptions, current_congress_for_date
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures import DisclosuresLoadRuntimeResult, run_disclosures_load_runtime
from src.runtime.disclosures_artifacts import run_disclosure_artifact_ingest
from src.runtime.disclosures_bundle import DisclosuresBundle, load_disclosures_bundle
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_load_from_parse import run_disclosures_parse_load_runtime
from src.runtime.disclosures_parse import run_disclosure_parse_runtime
from src.runtime.oracle_contracts import CongressOracleOptions, LocalOracleOptions, LocalOracleRunResult
from src.runtime.oracle_local import run_oracle_local
from src.runtime.output import (
    summarize_disclosure_artifact_ingest_result,
    summarize_disclosures_bundle_process_result,
    summarize_load_result,
    summarize_local_oracle_run_result,
    summarize_parse_disclosures_result,
    summarize_process_disclosures_result,
    summarize_publish_result,
    summarize_publish_roundtrip_result,
    summarize_publish_verify_result,
    summarize_recompute_result,
)
from src.runtime.paths import local_artifact_root
from src.runtime.publish import PublishRuntimeResult, run_publish_runtime
from src.runtime.publish_roundtrip import verify_roundtrip as _verify_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify import verify_local_publish as _verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult, run_recompute_runtime
from src.runtime.status_command import get_runtime_status
from src.runtime.zip_bundle import load_zip_bundle


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


def load_disclosures(
    ctx: RuntimeContext,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult:
    """Open a connection and run the disclosures load."""
    conn = open_connection(ctx)
    return run_disclosures_load_runtime(conn, results)


def process_disclosures_local(
    ctx: RuntimeContext,
    bundle: DisclosuresBundle,
    *,
    local_root: Path | None = None,
) -> DisclosuresBundleProcessResult:
    """Open a connection and run the bundle-process disclosure pipeline."""
    conn = open_connection(ctx)
    return run_disclosures_bundle_process(conn, bundle, local_root=local_root)


def recompute_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
) -> RuntimeRecomputeResult:
    """Open a connection and run a full conflict-of-interest recompute."""
    conn = open_connection(ctx)
    return run_recompute_runtime(
        conn,
        snapshot_date,
        taxonomy=ctx.taxonomy,
        issuer_sector_resolver=ctx.issuer_sector_resolver,
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


def _publish_target_or_default(target_dir: str | Path | None, runtime: Any) -> Path:
    if target_dir is None:
        return runtime.publish_root
    return Path(target_dir)


def _artifact_root_or_default(local_root: str | Path | None) -> Path:
    if local_root is None:
        return local_artifact_root()
    return Path(local_root)


def _current_congress(today: dt.date | None = None) -> int:
    return current_congress_for_date(today)


def _disclosure_summary(result: Any) -> dict[str, Any]:
    summary = summarize_disclosure_artifact_ingest_result(result)
    summary.pop("local_root", None)
    return summary


def _load_house_disclosures(conn: Any, year: int, local_root: Path) -> dict[str, Any]:
    annual = run_disclosure_artifact_ingest(
        conn,
        chamber="house",
        year=year,
        filing_kind="annual",
        local_root=local_root,
    )
    ptr = run_disclosure_artifact_ingest(
        conn,
        chamber="house",
        year=year,
        filing_kind="ptr",
        local_root=local_root,
    )
    return {
        "annual": _disclosure_summary(annual),
        "ptr": _disclosure_summary(ptr),
        "discovered": annual.discovered_count + ptr.discovered_count,
        "stored": annual.stored_count + ptr.stored_count,
    }


def _load_senate_disclosures(conn: Any, year: int, local_root: Path) -> dict[str, Any]:
    return _disclosure_summary(
        run_disclosure_artifact_ingest(
            conn,
            chamber="senate",
            year=year,
            local_root=local_root,
        )
    )


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
    runtime = build_runtime()
    ctx = runtime.context
    if args.api_key:
        ctx = replace(ctx, settings=ctx.settings.model_copy(update={"congress_api_key": args.api_key}))

    include_votes = (
        args.include_votes
        or args.house_vote_year is not None
        or args.senate_session is not None
    )
    options = CongressLoadOptions(
        congress=args.congress or _current_congress(),
        include_votes=include_votes,
        house_vote_year=args.house_vote_year,
        senate_session=args.senate_session,
    )
    result = load_congress(ctx, options)
    return {"ok": True, "command": "load-congress", **summarize_load_result(result)}


def _handle_load_disclosures(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    year = args.year if args.year is not None else dt.date.today().year
    artifact_root = _artifact_root_or_default(args.local_root)

    if args.chamber == "both":
        return {
            "ok": True,
            "command": "load-disclosures",
            "year": year,
            "local_root": str(artifact_root),
            "house": _load_house_disclosures(conn, year, artifact_root),
            "senate": _load_senate_disclosures(conn, year, artifact_root),
        }

    summary = (
        _load_house_disclosures(conn, year, artifact_root)
        if args.chamber == "house"
        else _load_senate_disclosures(conn, year, artifact_root)
    )
    return {
        "ok": True,
        "command": "load-disclosures",
        "year": year,
        "local_root": str(artifact_root),
        args.chamber: summary,
    }


def _handle_parse_disclosures(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    chamber = None if args.chamber == "both" else args.chamber
    result = run_disclosure_parse_runtime(
        conn,
        local_root=_artifact_root_or_default(args.local_root),
        chamber=chamber,
        limit=args.limit,
    )
    return {
        "ok": True,
        "command": "parse-disclosures",
        **summarize_parse_disclosures_result(result),
    }


def _handle_process_disclosures(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
    chamber = None if args.chamber == "both" else args.chamber
    result = run_disclosures_parse_load_runtime(
        conn,
        local_root=_artifact_root_or_default(args.local_root),
        chamber=chamber,
        limit=args.limit,
    )
    return {
        "ok": True,
        "command": "process-disclosures",
        **summarize_process_disclosures_result(result),
    }


def _handle_recompute(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    result = recompute_snapshot(runtime.context, _snapshot_date_or_today(args.snapshot_date))
    return {"ok": True, "command": "recompute", **summarize_recompute_result(result)}


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
    runtime = build_runtime()
    options = CongressLoadOptions(
        congress=args.congress,
        include_votes=False,
        house_vote_year=None,
        senate_session=None,
    )
    result = load_congress_local(runtime.context, Path(args.archive), options)
    return {"ok": True, "command": "load-congress-local", **summarize_load_result(result)}


def _handle_process_disclosures_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    bundle = load_disclosures_bundle(Path(args.bundle))
    result = process_disclosures_local(runtime.context, bundle)
    return {
        "ok": True,
        "command": "process-disclosures-local",
        **summarize_disclosures_bundle_process_result(result),
    }


def _handle_run_oracle_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    congress_options = CongressOracleOptions(
        congress=_current_congress(),
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
    )
    options = LocalOracleOptions(
        congress_options=congress_options,
        snapshot_date=args.snapshot_date,
        target_dir=Path(args.target_dir),
        snapshot_id=args.snapshot_id,
    )
    result = run_oracle_local_command(
        runtime.context,
        Path(args.congress_archive),
        load_disclosures_bundle(Path(args.disclosures_bundle)),
        options,
    )
    summary = summarize_local_oracle_run_result(result)
    return {"ok": _oracle_summary_ok(summary), "command": "run-oracle-local", **summary}


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


COMMAND_REGISTRY: dict[str, Callable[[Any], dict[str, Any]]] = {
    "bootstrap-db": _handle_bootstrap_db,
    "status": _handle_status,
    "load-congress": _handle_load_congress,
    "load-disclosures": _handle_load_disclosures,
    "parse-disclosures": _handle_parse_disclosures,
    "process-disclosures": _handle_process_disclosures,
    "recompute": _handle_recompute,
    "publish": _handle_publish,
    "load-congress-local": _handle_load_congress_local,
    "process-disclosures-local": _handle_process_disclosures_local,
    "run-oracle-local": _handle_run_oracle_local,
    "verify-publish": _handle_verify_publish,
    "verify-publish-roundtrip": _handle_verify_publish_roundtrip,
}


def dispatch_command(args: Any) -> dict[str, Any]:
    handler = COMMAND_REGISTRY.get(args.command)
    if handler is None:
        return {"ok": False, "error": f"unknown command: {args.command!r}"}
    return handler(args)
