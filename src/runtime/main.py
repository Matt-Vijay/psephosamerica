from __future__ import annotations

import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.runtime.app import build_runtime, open_runtime_connection
from src.runtime.bootstrap import bootstrap_database, load_initial_migration_sql, load_schema_sql
from src.runtime.cli import parse_args
from src.runtime.commands import (
    load_congress,
    load_congress_local,
    process_disclosures_local,
    publish_snapshot,
    recompute_snapshot,
    run_oracle_local_command,
    verify_publish_local,
    verify_publish_roundtrip_local,
)
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.disclosures_artifacts import run_disclosure_artifact_ingest
from src.runtime.disclosures_bundle import load_disclosures_bundle
from src.runtime.disclosures_load_from_parse import run_disclosures_parse_load_runtime
from src.runtime.disclosures_parse import run_disclosure_parse_runtime
from src.runtime.oracle_contracts import CongressOracleOptions, LocalOracleOptions
from src.runtime.output import (
    as_json,
    summarize_disclosure_artifact_ingest_result,
    summarize_disclosures_bundle_process_result,
    summarize_load_result,
    summarize_local_oracle_run_result,
    summarize_parse_disclosures_result,
    summarize_publish_verify_result,
    summarize_publish_roundtrip_result,
    summarize_process_disclosures_result,
    summarize_publish_result,
    summarize_recompute_result,
)
from src.runtime.paths import local_artifact_root
from src.runtime.status_command import get_runtime_status_summary
from src.runtime.zip_bundle import load_zip_bundle


def run(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        result = _dispatch(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001
        print(as_json({"ok": False, "error": str(exc)}))
        return 1

    print(as_json(result))
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


def _snapshot_date_or_today(snapshot_date: dt.date | None) -> dt.date:
    return snapshot_date if snapshot_date is not None else dt.date.today()


def _publish_target_or_default(target_dir: str | Path | None, runtime) -> Path:
    if target_dir is None:
        return runtime.publish_root
    return Path(target_dir)


def _artifact_root_or_default(local_root: str | Path | None) -> Path:
    if local_root is None:
        return local_artifact_root()
    return Path(local_root)


def _current_congress(today: dt.date | None = None) -> int:
    current = today if today is not None else dt.date.today()
    return ((current.year - 1789) // 2) + 1


def _disclosure_summary(result) -> dict[str, Any]:
    summary = summarize_disclosure_artifact_ingest_result(result)
    summary.pop("local_root", None)
    return summary


def _load_house_disclosures(conn, year: int, local_root: Path) -> dict[str, Any]:
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


def _load_senate_disclosures(conn, year: int, local_root: Path) -> dict[str, Any]:
    return _disclosure_summary(
        run_disclosure_artifact_ingest(
            conn,
            chamber="senate",
            year=year,
            local_root=local_root,
        )
    )


def _dispatch(args) -> dict:
    if args.command == "bootstrap-db":
        runtime = build_runtime()
        conn = open_runtime_connection(runtime)
        if args.dry_run:
            return {
                "ok": True,
                "command": "bootstrap-db",
                "schema_sql_bytes": len(load_schema_sql().encode("utf-8")),
                "initial_migration_sql_bytes": len(load_initial_migration_sql().encode("utf-8")),
            }
        bootstrap_database(conn)
        return {"ok": True, "command": "bootstrap-db"}

    if args.command == "status":
        runtime = build_runtime()
        conn = open_runtime_connection(runtime)
        return {
            "ok": True,
            "command": "status",
            **get_runtime_status_summary(conn, limit=args.limit),
        }

    if args.command == "load-congress":
        runtime = build_runtime()
        ctx = runtime.context
        if args.api_key:
            ctx = replace(ctx, settings=ctx.settings.model_copy(update={"congress_api_key": args.api_key}))
        options = CongressLoadOptions(
            congress=args.congress or _current_congress(),
            include_votes=args.include_votes,
            house_vote_year=args.house_vote_year,
            senate_session=args.senate_session,
        )
        result = load_congress(ctx, options)
        return {"ok": True, "command": "load-congress", **summarize_load_result(result)}

    if args.command == "load-disclosures":
        runtime = build_runtime()
        conn = open_runtime_connection(runtime)
        year = args.year if args.year is not None else dt.date.today().year
        artifact_root = _artifact_root_or_default(args.local_root)
        chamber = args.chamber

        if chamber == "both":
            house_summary = _load_house_disclosures(conn, year, artifact_root)
            senate_summary = _load_senate_disclosures(conn, year, artifact_root)
            return {
                "ok": True,
                "command": "load-disclosures",
                "year": year,
                "local_root": str(artifact_root),
                "house": house_summary,
                "senate": senate_summary,
            }

        summary = (
            _load_house_disclosures(conn, year, artifact_root)
            if chamber == "house"
            else _load_senate_disclosures(conn, year, artifact_root)
        )
        return {
            "ok": True,
            "command": "load-disclosures",
            "year": year,
            "local_root": str(artifact_root),
            chamber: summary,
        }

    if args.command == "parse-disclosures":
        runtime = build_runtime()
        conn = open_runtime_connection(runtime)
        chamber = None if args.chamber == "both" else args.chamber
        local_root = _artifact_root_or_default(args.local_root)
        result = run_disclosure_parse_runtime(
            conn,
            local_root=local_root,
            chamber=chamber,
            limit=args.limit,
        )
        return {
            "ok": True,
            "command": "parse-disclosures",
            **summarize_parse_disclosures_result(result),
        }

    if args.command == "process-disclosures":
        runtime = build_runtime()
        conn = open_runtime_connection(runtime)
        chamber = None if args.chamber == "both" else args.chamber
        local_root = _artifact_root_or_default(args.local_root)
        result = run_disclosures_parse_load_runtime(
            conn,
            local_root=local_root,
            chamber=chamber,
            limit=args.limit,
        )
        return {
            "ok": True,
            "command": "process-disclosures",
            **summarize_process_disclosures_result(result),
        }

    if args.command == "recompute":
        runtime = build_runtime()
        result = recompute_snapshot(runtime.context, _snapshot_date_or_today(args.snapshot_date))
        return {"ok": True, "command": "recompute", **summarize_recompute_result(result)}

    if args.command == "publish":
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
        return {"ok": True, "command": "publish", **summarize_publish_result(result)}

    if args.command == "load-congress-local":
        runtime = build_runtime()
        options = CongressLoadOptions(
            congress=args.congress,
            include_votes=False,
            house_vote_year=None,
            senate_session=None,
        )
        result = load_congress_local(runtime.context, Path(args.archive), options)
        return {"ok": True, "command": "load-congress-local", **summarize_load_result(result)}

    if args.command == "process-disclosures-local":
        runtime = build_runtime()
        bundle = load_disclosures_bundle(Path(args.bundle))
        result = process_disclosures_local(runtime.context, bundle)
        return {
            "ok": True,
            "command": "process-disclosures-local",
            **summarize_disclosures_bundle_process_result(result),
        }

    if args.command == "run-oracle-local":
        runtime = build_runtime()
        congress_archive = Path(args.congress_archive)
        disclosures_bundle = load_disclosures_bundle(Path(args.disclosures_bundle))
        chamber = None if args.chamber == "both" else args.chamber
        congress_options = CongressOracleOptions(
            congress=_current_congress(),
            chamber=chamber,
            limit=args.limit,
        )
        options = LocalOracleOptions(
            congress_options=congress_options,
            snapshot_date=args.snapshot_date,
            target_dir=Path(args.target_dir),
            snapshot_id=args.snapshot_id,
        )
        result = run_oracle_local_command(runtime.context, congress_archive, disclosures_bundle, options)
        return {
            "ok": True,
            "command": "run-oracle-local",
            **summarize_local_oracle_run_result(result),
        }

    if args.command == "verify-publish":
        result = verify_publish_local(Path(args.publish_root))
        return {
            "ok": result.ok,
            "command": "verify-publish",
            **summarize_publish_verify_result(result),
        }

    if args.command == "verify-publish-roundtrip":
        runtime = build_runtime()
        result = verify_publish_roundtrip_local(runtime.context, Path(args.publish_root))
        return {
            "ok": result.ok,
            "command": "verify-publish-roundtrip",
            "roundtrip": summarize_publish_roundtrip_result(result),
        }

    return {"ok": False, "error": f"unknown command: {args.command!r}"}
