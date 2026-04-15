from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from src.runtime.app import build_runtime, open_runtime_connection
from src.runtime.bootstrap import bootstrap_database, load_initial_migration_sql, load_schema_sql
from src.runtime.cli import parse_args
from src.runtime.commands import publish_snapshot, recompute_snapshot
from src.runtime.congress_live import run_live_congress_load
from src.runtime.disclosures_artifacts import run_disclosure_artifact_ingest
from src.runtime.output import (
    as_json,
    summarize_disclosure_artifact_ingest_result,
    summarize_load_result,
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
        settings = runtime.context.settings
        if args.api_key:
            settings = settings.model_copy(update={"congress_api_key": args.api_key})
        result = run_live_congress_load(
            open_runtime_connection(runtime),
            settings,
            congress=args.congress or _current_congress(),
        )
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

    return {"ok": False, "error": f"unknown command: {args.command!r}"}
