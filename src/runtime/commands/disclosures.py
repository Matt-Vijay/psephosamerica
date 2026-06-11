"""Financial-disclosure load/parse/process commands."""

from __future__ import annotations

import datetime as dt

from pathlib import Path
from src.parse.disclosures.transform import DisclosureTransformResult
from src.runtime.app import build_runtime, open_runtime_connection
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures import DisclosuresLoadRuntimeResult, run_disclosures_load_runtime
from src.runtime.disclosures_artifacts import run_disclosure_artifact_ingest
from src.runtime.disclosures_bundle import DisclosuresBundle, load_disclosures_bundle
from src.runtime.disclosures_bundle_materialize import (
    MaterializedDisclosuresBundleResult,
    materialize_disclosures_bundle,
)
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_bundle_validate import validate_disclosures_bundle
from src.runtime.disclosures_load_from_parse import run_disclosures_parse_load_runtime
from src.runtime.disclosures_parse import run_disclosure_parse_runtime
from src.runtime.output import (
    summarize_disclosure_artifact_ingest_result,
    summarize_disclosures_bundle_process_result,
    summarize_parse_disclosures_result,
    summarize_process_disclosures_result,
)
from typing import Any

from src.runtime.commands._shared import (
    _artifact_root_or_default,
    _command_issue_result,
    _optional_limit_issue,
    _positive_int_sequence_issues,
)


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


def _materialized_disclosures_bundle_summary(
    result: MaterializedDisclosuresBundleResult,
    *,
    mode: str = "materialized",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "bundle_path": str(result.bundle_path),
        "artifact_root": str(result.artifact_root),
        "chamber": result.chamber,
        "years": list(result.years),
        "artifact_count": result.artifact_count,
        "house_count": result.house_count,
        "senate_count": result.senate_count,
    }


def _summarize_existing_disclosures_bundle(
    bundle_path: Path,
    *,
    artifact_root: Path,
    requested_years: list[int] | None,
    requested_chamber: str,
) -> tuple[DisclosuresBundle, dict[str, Any]]:
    bundle = load_disclosures_bundle(bundle_path)
    validate_disclosures_bundle(bundle)
    house_count = sum(entry.chamber == "house" for entry in bundle.artifacts)
    senate_count = sum(entry.chamber == "senate" for entry in bundle.artifacts)
    if house_count and senate_count:
        chamber = "both"
    elif house_count:
        chamber = "house"
    else:
        chamber = "senate"
    years = sorted({entry.filing_year for entry in bundle.artifacts})
    effective_requested_years = years if not requested_years else requested_years
    missing_years = sorted(set(effective_requested_years) - set(years))
    if missing_years:
        missing_years_text = ", ".join(str(year) for year in missing_years)
        raise ValueError(
            f"existing disclosures bundle is missing requested years: {missing_years_text}"
        )
    if requested_chamber == "house" and house_count == 0:
        raise ValueError("existing disclosures bundle is missing requested house filings")
    if requested_chamber == "senate" and senate_count == 0:
        raise ValueError("existing disclosures bundle is missing requested senate filings")
    if requested_chamber == "both" and (house_count == 0 or senate_count == 0):
        raise ValueError("existing disclosures bundle does not include both requested chambers")
    return (
        bundle,
        {
            "mode": "reused",
            "bundle_path": str(bundle_path),
            "artifact_root": str(artifact_root),
            "chamber": chamber,
            "years": effective_requested_years,
            "artifact_count": len(bundle.artifacts),
            "house_count": house_count,
            "senate_count": senate_count,
        },
    )


def _handle_load_disclosures(args: Any) -> dict[str, Any]:
    year = args.year if args.year is not None else dt.date.today().year
    if type(year) is not int or year <= 0:
        return _command_issue_result(
            "load-disclosures",
            ["year must be a positive integer"],
        )

    runtime = build_runtime()
    conn = open_runtime_connection(runtime)
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
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("parse-disclosures", [limit_issue])

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
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("process-disclosures", [limit_issue])

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


def _handle_process_disclosures_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    bundle = load_disclosures_bundle(Path(args.bundle))
    result = process_disclosures_local(runtime.context, bundle)
    return {
        "ok": True,
        "command": "process-disclosures-local",
        **summarize_disclosures_bundle_process_result(result),
    }


def _handle_materialize_disclosures_bundle(args: Any) -> dict[str, Any]:
    year_issues = _positive_int_sequence_issues("year", getattr(args, "year", None))
    if year_issues:
        return _command_issue_result("materialize-disclosures-bundle", year_issues)

    result = materialize_disclosures_bundle(
        years=args.year,
        chamber=args.chamber,
        bundle_path=Path(args.bundle_path),
        artifact_root=Path(args.artifact_root),
    )
    return {
        "ok": True,
        "command": "materialize-disclosures-bundle",
        "bundle_path": str(result.bundle_path),
        "artifact_root": str(result.artifact_root),
        "chamber": result.chamber,
        "years": list(result.years),
        "artifact_count": result.artifact_count,
        "house_count": result.house_count,
        "senate_count": result.senate_count,
    }
