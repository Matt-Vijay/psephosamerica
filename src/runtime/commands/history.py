"""History backfill and congress-archive commands."""

from __future__ import annotations

import datetime as dt
import hashlib

from dataclasses import replace
from pathlib import Path
from src.ingest.congress.archive import CongressArchive, manifest_from_existing_archive
from src.ingest.congress.archive_manifest import (
    load_manifest as load_congress_archive_manifest,
    write_manifest,
)
from src.ingest.congress.archive_validate import validate_congress_archive_manifest
from src.runtime.app import build_runtime
from src.runtime.congress_archive_materialize import (
    MaterializedCongressArchiveResult,
    materialize_congress_archive,
)
from src.runtime.congress_options import resolve_congress_vote_coverage
from src.runtime.context import RuntimeContext
from src.runtime.disclosures_bundle import DisclosuresBundle, load_disclosures_bundle
from src.runtime.disclosures_bundle_materialize import materialize_disclosures_bundle
from src.runtime.history_backfill import (
    LocalHistoryBackfillResult,
    build_history_backfill_report,
    check_history_backfill_inputs,
    plan_congress_history_backfill,
    run_local_history_backfill,
)
from src.runtime.history_verify import (
    verify_history_aggregate_local as _verify_local_history_aggregate,
)
from src.runtime.history_verify_types import HistoryVerifyResult
from src.runtime.output import (
    summarize_history_verify_result,
    summarize_local_history_backfill_result,
)
from typing import Any

from src.runtime.commands._shared import (
    _command_issue_result,
    _count_json_items,
    _emit_verification_summary,
    _is_sha256_hex,
    _optional_limit_issue,
    _positive_int_arg_issues,
    _positive_int_sequence_issues,
    _single_value_or_none,
    _write_json_artifact,
)
from src.runtime.commands.disclosures import (
    _materialized_disclosures_bundle_summary,
    _summarize_existing_disclosures_bundle,
)


def verify_history_aggregate_local(publish_root: Path) -> HistoryVerifyResult:
    """Verify a local history aggregate root without a database connection."""
    result = _verify_local_history_aggregate(publish_root)
    _emit_verification_summary("verify-history-aggregate", publish_root, result)
    return result


def run_history_backfill_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    *,
    congress: int,
    target_root: Path,
    aggregate_root: Path | None = None,
    chamber: str | None = None,
    limit: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    overwrite: bool = False,
    continue_on_error: bool = False,
    artifact_root: Path | None = None,
    input_readiness: Any | None = None,
) -> LocalHistoryBackfillResult:
    """Run the local oracle across a planned weekly Congress history window."""
    result = run_local_history_backfill(
        ctx,
        congress_archive,
        disclosures_bundle,
        congress=congress,
        target_root=target_root,
        aggregate_root=aggregate_root,
        chamber=chamber,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        overwrite=overwrite,
        continue_on_error=continue_on_error,
        artifact_root=artifact_root,
    )
    if input_readiness is not None:
        result = replace(result, input_readiness=input_readiness)
        if result.report_path is not None:
            _write_json_artifact(
                result.report_path,
                build_history_backfill_report(result).model_dump(mode="json"),
            )
    return result


def _materialized_congress_archive_summary(
    result: MaterializedCongressArchiveResult,
    *,
    mode: str = "materialized",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "archive_root": str(result.archive_root),
        "manifest_path": str(result.manifest_path),
        "congress": result.congress,
        "include_votes": result.include_votes,
        "house_vote_year": result.house_vote_year,
        "senate_session": result.senate_session,
        "member_count": result.member_count,
        "committee_count": result.committee_count,
        "bill_count": result.bill_count,
        "member_detail_count": result.member_detail_count,
        "bill_detail_count": result.bill_detail_count,
        "cosponsor_file_count": result.cosponsor_file_count,
        "house_vote_count": result.house_vote_count,
        "senate_vote_count": result.senate_vote_count,
        "source_state": _congress_archive_source_state(
            congress=result.congress,
            include_votes=result.include_votes,
            member_count=result.member_count,
            committee_count=result.committee_count,
            bill_count=result.bill_count,
            member_detail_count=result.member_detail_count,
            bill_detail_count=result.bill_detail_count,
            cosponsor_file_count=result.cosponsor_file_count,
            house_vote_count=result.house_vote_count,
            senate_vote_count=result.senate_vote_count,
        ),
    }


def _reusable_congress_archive_path(archive_root: Path) -> Path:
    manifest_file = archive_root / "manifest.json"
    return manifest_file if manifest_file.is_file() else archive_root


def _should_reuse_existing_congress_archive(archive_root: Path) -> bool:
    if not archive_root.exists():
        return False
    if archive_root.is_dir():
        try:
            next(archive_root.iterdir())
        except StopIteration:
            return False
        return True
    return False


def _summarize_existing_congress_archive(
    archive_root: Path,
    *,
    congress: int,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> dict[str, Any]:
    manifest_file = archive_root / "manifest.json"
    manifest = (
        load_congress_archive_manifest(manifest_file)
        if manifest_file.is_file()
        else manifest_from_existing_archive(CongressArchive(archive_root, congress))
    )
    validation = validate_congress_archive_manifest(manifest)
    if not validation.valid:
        missing_labels = ", ".join(missing.label for missing in validation.missing)
        raise ValueError(f"congress archive references missing files: {missing_labels}")
    if manifest.congress != congress:
        raise ValueError(
            f"existing congress archive is for Congress {manifest.congress}, expected {congress}"
        )

    house_vote_years = {vote.year for vote in manifest.house_votes}
    senate_sessions = {vote.session_number for vote in manifest.senate_votes}
    if include_votes and house_vote_year is None and senate_session is None:
        coverage = resolve_congress_vote_coverage(congress)
        house_vote_year = coverage.house_vote_year
        senate_session = coverage.senate_session
    if house_vote_year is not None and house_vote_year not in house_vote_years:
        raise ValueError(
            f"existing congress archive is missing requested house vote year: {house_vote_year}"
        )
    if senate_session is not None and senate_session not in senate_sessions:
        raise ValueError(
            f"existing congress archive is missing requested senate session: {senate_session}"
        )
    member_count = _count_json_items(manifest.members.path, "members")
    committee_count = _count_json_items(manifest.committees.path, "committees")
    bill_count = _count_json_items(manifest.bills.path, "bills")
    return {
        "mode": "reused",
        "archive_root": str(archive_root),
        "manifest_path": str(manifest_file) if manifest_file.is_file() else None,
        "congress": manifest.congress,
        "include_votes": bool(manifest.house_votes or manifest.senate_votes),
        "house_vote_year": _single_value_or_none(house_vote_years),
        "senate_session": _single_value_or_none(senate_sessions),
        "member_count": member_count,
        "committee_count": committee_count,
        "bill_count": bill_count,
        "member_detail_count": len(manifest.member_details),
        "bill_detail_count": len(manifest.bill_details),
        "cosponsor_file_count": len(manifest.cosponsors),
        "house_vote_count": len(manifest.house_votes),
        "senate_vote_count": len(manifest.senate_votes),
        "source_state": _congress_archive_source_state(
            congress=manifest.congress,
            include_votes=bool(manifest.house_votes or manifest.senate_votes),
            member_count=member_count,
            committee_count=committee_count,
            bill_count=bill_count,
            member_detail_count=len(manifest.member_details),
            bill_detail_count=len(manifest.bill_details),
            cosponsor_file_count=len(manifest.cosponsors),
            house_vote_count=len(manifest.house_votes),
            senate_vote_count=len(manifest.senate_votes),
        ),
    }


def _congress_archive_source_state(
    *,
    congress: int,
    include_votes: bool,
    member_count: int,
    committee_count: int,
    bill_count: int,
    member_detail_count: int,
    bill_detail_count: int,
    cosponsor_file_count: int,
    house_vote_count: int,
    senate_vote_count: int,
) -> dict[str, Any]:
    vote_archive_file_count = house_vote_count + senate_vote_count
    source_family_ids = ["committee_membership", "congress_bill"]
    if vote_archive_file_count:
        source_family_ids.append("congress_vote")
    return {
        "congress": congress,
        "source_family_ids": source_family_ids,
        "source_family_count": len(source_family_ids),
        "member_count": member_count,
        "committee_count": committee_count,
        "bill_count": bill_count,
        "member_detail_count": member_detail_count,
        "bill_detail_count": bill_detail_count,
        "cosponsor_file_count": cosponsor_file_count,
        "house_vote_file_count": house_vote_count,
        "senate_vote_file_count": senate_vote_count,
        "vote_archive_file_count": vote_archive_file_count,
        "prediction_member_inputs_available": member_count > 0,
        "prediction_bill_inputs_available": bill_count > 0,
        "prediction_vote_inputs_requested": include_votes,
        "prediction_vote_inputs_available": vote_archive_file_count > 0,
    }


def _materialize_history_backfill_inputs(
    args: Any,
    *,
    load_bundle_for_replay: bool = False,
) -> tuple[
    RuntimeContext,
    Path,
    dict[str, Any],
    Path,
    dict[str, Any],
    DisclosuresBundle | None,
    Any,
]:
    runtime = build_runtime()
    congress_archive_root = Path(args.congress_archive_root)
    disclosures_bundle_path = Path(args.disclosures_bundle_path)
    disclosures_artifact_root = Path(args.disclosures_artifact_root)
    requested_years = list(args.disclosures_year) if args.disclosures_year is not None else None
    reuse_existing_inputs = bool(getattr(args, "reuse_existing_inputs", False))
    needs_congress_materialize = not (
        reuse_existing_inputs and _should_reuse_existing_congress_archive(congress_archive_root)
    )

    api_key = args.api_key or runtime.context.settings.congress_api_key
    if needs_congress_materialize and not api_key:
        raise ValueError("Congress.gov API key is required")

    if not needs_congress_materialize:
        congress_input_path = _reusable_congress_archive_path(congress_archive_root)
        congress_summary = _summarize_existing_congress_archive(
            congress_archive_root,
            congress=args.congress,
            include_votes=args.include_votes,
            house_vote_year=args.house_vote_year,
            senate_session=args.senate_session,
        )
    else:
        congress_result = materialize_congress_archive(
            api_key=api_key,
            archive_root=congress_archive_root,
            congress=args.congress,
            include_votes=args.include_votes,
            house_vote_year=args.house_vote_year,
            senate_session=args.senate_session,
            manifest_path=None,
        )
        congress_input_path = congress_result.manifest_path
        congress_summary = _materialized_congress_archive_summary(
            congress_result,
            mode="materialized",
        )

    if reuse_existing_inputs and disclosures_bundle_path.exists():
        reused_bundle, disclosures_summary = _summarize_existing_disclosures_bundle(
            disclosures_bundle_path,
            artifact_root=disclosures_artifact_root,
            requested_years=requested_years,
            requested_chamber=args.chamber,
        )
        disclosures_bundle = reused_bundle if load_bundle_for_replay else None
    else:
        if not requested_years:
            raise ValueError(
                "--disclosures-year is required unless --reuse-existing-inputs can infer years from an existing bundle"
            )
        disclosures_result = materialize_disclosures_bundle(
            years=requested_years,
            chamber=args.chamber,
            bundle_path=disclosures_bundle_path,
            artifact_root=disclosures_artifact_root,
        )
        disclosures_summary = _materialized_disclosures_bundle_summary(
            disclosures_result,
            mode="materialized",
        )
        disclosures_bundle = None

    readiness = check_history_backfill_inputs(
        congress_input_path,
        disclosures_bundle_path,
        artifact_root=disclosures_artifact_root,
        chamber=args.chamber,
        limit=args.limit,
    )
    if load_bundle_for_replay and readiness.ready_to_replay and disclosures_bundle is None:
        disclosures_bundle = load_disclosures_bundle(disclosures_bundle_path)
    return (
        runtime.context,
        congress_input_path,
        congress_summary,
        disclosures_bundle_path,
        disclosures_summary,
        disclosures_bundle,
        readiness,
    )


def _handle_run_history_backfill_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("run-history-backfill-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-history-backfill-local", [limit_issue])

    input_readiness = check_history_backfill_inputs(
        Path(args.congress_archive),
        Path(args.disclosures_bundle),
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        chamber=args.chamber,
        limit=args.limit,
    )
    if not input_readiness.ready_to_replay:
        plan = plan_congress_history_backfill(
            args.congress,
            target_root=Path(args.target_root),
            start_date=args.start_date,
            end_date=args.end_date,
        )
        return {
            "ok": False,
            "command": "run-history-backfill-local",
            "input_readiness": input_readiness.model_dump(mode="json"),
            "congress": plan.congress,
            "cadence": plan.cadence,
            "date_window": {
                "start_date": plan.date_window.start_date.isoformat(),
                "end_date": plan.date_window.end_date.isoformat(),
                "bounded_by_today": plan.date_window.bounded_by_today,
            },
            "planned_count": len(plan.targets),
            "attempted_count": 0,
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "remaining_count": len(plan.targets),
            "aggregate_source_count": 0,
            "attempts": [],
            "aggregate": None,
        }

    runtime = build_runtime()
    result = run_history_backfill_local_command(
        runtime.context,
        Path(args.congress_archive),
        load_disclosures_bundle(Path(args.disclosures_bundle)),
        congress=args.congress,
        target_root=Path(args.target_root),
        aggregate_root=Path(args.aggregate_root) if args.aggregate_root is not None else None,
        start_date=args.start_date,
        end_date=args.end_date,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        overwrite=args.overwrite,
        continue_on_error=args.continue_on_error,
        input_readiness=input_readiness,
    )
    summary = summarize_local_history_backfill_result(result)
    summary["input_readiness"] = input_readiness.model_dump(mode="json")
    return {"ok": result.ok, "command": "run-history-backfill-local", **summary}


def _handle_check_history_backfill_inputs(args: Any) -> dict[str, Any]:
    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("check-history-backfill-inputs", [limit_issue])

    payload = check_history_backfill_inputs(
        Path(args.congress_archive),
        Path(args.disclosures_bundle),
        artifact_root=Path(args.artifact_root) if args.artifact_root is not None else None,
        chamber=getattr(args, "chamber", "both"),
        limit=getattr(args, "limit", None),
    )
    return {
        "ok": payload.ready_to_replay,
        "command": "check-history-backfill-inputs",
        **payload.model_dump(mode="json"),
    }


def _handle_write_congress_archive_manifest(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("write-congress-archive-manifest", issues)

    archive_root = Path(args.archive_root)
    manifest_path = (
        Path(args.manifest_path)
        if args.manifest_path is not None
        else archive_root / "manifest.json"
    )
    manifest = manifest_from_existing_archive(CongressArchive(archive_root, args.congress))
    validation = validate_congress_archive_manifest(manifest)
    if not validation.valid:
        missing_labels = ", ".join(missing.label for missing in validation.missing)
        raise ValueError(f"congress archive references missing files: {missing_labels}")
    written = write_manifest(manifest_path, manifest)
    return {
        "ok": True,
        "command": "write-congress-archive-manifest",
        "archive_root": str(archive_root),
        "manifest_path": str(written),
        "congress": manifest.congress,
        "member_detail_count": len(manifest.member_details),
        "bill_detail_count": len(manifest.bill_details),
        "cosponsor_count": len(manifest.cosponsors),
        "house_vote_count": len(manifest.house_votes),
        "senate_vote_count": len(manifest.senate_votes),
    }


def _handle_materialize_congress_archive(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    if issues:
        return _command_issue_result("materialize-congress-archive", issues)

    runtime = build_runtime()
    api_key = args.api_key or runtime.context.settings.congress_api_key
    if not api_key:
        raise ValueError("Congress.gov API key is required")
    result = materialize_congress_archive(
        api_key=api_key,
        archive_root=Path(args.archive_root),
        congress=args.congress,
        include_votes=args.include_votes,
        house_vote_year=args.house_vote_year,
        senate_session=args.senate_session,
        manifest_path=Path(args.manifest_path) if args.manifest_path is not None else None,
    )
    return {
        "ok": True,
        "command": "materialize-congress-archive",
        "archive_root": str(result.archive_root),
        "manifest_path": str(result.manifest_path),
        "congress": result.congress,
        "include_votes": result.include_votes,
        "house_vote_year": result.house_vote_year,
        "senate_session": result.senate_session,
        "member_count": result.member_count,
        "committee_count": result.committee_count,
        "bill_count": result.bill_count,
        "member_detail_count": result.member_detail_count,
        "bill_detail_count": result.bill_detail_count,
        "cosponsor_file_count": result.cosponsor_file_count,
        "house_vote_count": result.house_vote_count,
        "senate_vote_count": result.senate_vote_count,
        "source_state": _congress_archive_source_state(
            congress=result.congress,
            include_votes=result.include_votes,
            member_count=result.member_count,
            committee_count=result.committee_count,
            bill_count=result.bill_count,
            member_detail_count=result.member_detail_count,
            bill_detail_count=result.bill_detail_count,
            cosponsor_file_count=result.cosponsor_file_count,
            house_vote_count=result.house_vote_count,
            senate_vote_count=result.senate_vote_count,
        ),
    }


def _handle_materialize_history_backfill_inputs(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    issues.extend(
        _positive_int_sequence_issues(
            "disclosures_year",
            getattr(args, "disclosures_year", None),
        )
    )
    if issues:
        return _command_issue_result("materialize-history-backfill-inputs", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("materialize-history-backfill-inputs", [limit_issue])

    _, _, congress_summary, _, disclosures_summary, _, readiness = (
        _materialize_history_backfill_inputs(args)
    )
    return {
        "ok": readiness.ready_to_replay,
        "command": "materialize-history-backfill-inputs",
        "congress_archive": congress_summary,
        "disclosures_bundle": disclosures_summary,
        "input_readiness": readiness.model_dump(mode="json"),
    }


def _handle_run_history_launch_local(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(
        args,
        ("congress", "house_vote_year", "senate_session"),
    )
    issues.extend(
        _positive_int_sequence_issues(
            "disclosures_year",
            getattr(args, "disclosures_year", None),
        )
    )
    if issues:
        return _command_issue_result("run-history-launch-local", issues)

    limit_issue = _optional_limit_issue(getattr(args, "limit", None))
    if limit_issue is not None:
        return _command_issue_result("run-history-launch-local", [limit_issue])

    (
        ctx,
        congress_input_path,
        congress_summary,
        _disclosures_bundle_path,
        disclosures_summary,
        disclosures_bundle,
        readiness,
    ) = _materialize_history_backfill_inputs(args, load_bundle_for_replay=True)
    base = {
        "command": "run-history-launch-local",
        "congress_archive": congress_summary,
        "disclosures_bundle": disclosures_summary,
        "input_readiness": readiness.model_dump(mode="json"),
    }
    if not readiness.ready_to_replay:
        return {
            "ok": False,
            **base,
            "history_backfill": None,
        }

    result = run_history_backfill_local_command(
        ctx,
        congress_input_path,
        disclosures_bundle
        if disclosures_bundle is not None
        else load_disclosures_bundle(Path(args.disclosures_bundle_path)),
        congress=args.congress,
        target_root=Path(args.target_root),
        aggregate_root=Path(args.aggregate_root) if args.aggregate_root is not None else None,
        start_date=args.start_date,
        end_date=args.end_date,
        chamber=None if args.chamber == "both" else args.chamber,
        limit=args.limit,
        artifact_root=Path(args.disclosures_artifact_root),
        overwrite=args.overwrite,
        continue_on_error=args.continue_on_error,
        input_readiness=readiness,
    )
    return {
        "ok": result.ok,
        **base,
        "history_backfill": summarize_local_history_backfill_result(result),
    }


def _handle_plan_history_backfill(args: Any) -> dict[str, Any]:
    issues = _positive_int_arg_issues(args, ("congress",))
    if issues:
        return _command_issue_result("plan-history-backfill", issues)

    plan = plan_congress_history_backfill(
        args.congress,
        target_root=Path(args.target_root),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    return {
        "ok": True,
        "command": "plan-history-backfill",
        "congress": plan.congress,
        "cadence": plan.cadence,
        "date_window": {
            "start_date": plan.date_window.start_date.isoformat(),
            "end_date": plan.date_window.end_date.isoformat(),
            "bounded_by_today": plan.date_window.bounded_by_today,
        },
        "snapshot_count": len(plan.targets),
        "targets": [
            {
                "snapshot_id": target.snapshot_id,
                "snapshot_date": target.snapshot_date.isoformat(),
                "publish_root": str(target.publish_root),
            }
            for target in plan.targets
        ],
    }


def _handle_aggregate_history(args: Any) -> dict[str, Any]:
    from src.pipeline.history_aggregate_run import write_history_aggregate

    result = write_history_aggregate(
        [Path(root) for root in args.source_root],
        Path(args.target_root),
    )
    verify_result = verify_history_aggregate_local(Path(args.target_root))
    return {
        "ok": verify_result.ok,
        "command": "aggregate-history",
        "latest_snapshot_id": result.latest_snapshot_id,
        "snapshot_count": len(result.snapshot_index.snapshots),
        "member_history_count": result.member_history_count,
        "target_root": str(result.target_root),
        "verify": summarize_history_verify_result(verify_result),
    }


def _handle_verify_history_aggregate(args: Any) -> dict[str, Any]:
    result = verify_history_aggregate_local(Path(args.publish_root))
    return {
        "ok": result.ok,
        "command": "verify-history-aggregate",
        **summarize_history_verify_result(result),
    }


def _validate_congress_archive_manifest_metadata(
    *,
    label: str,
    manifest: Any,
    issues: list[str],
    require_manifest: bool,
) -> None:
    if manifest is None:
        if require_manifest:
            issues.append(f"{label} missing")
        return
    if not isinstance(manifest, dict):
        issues.append(f"{label} must be an object")
        return
    manifest_path_raw = manifest.get("path")
    expected_sha = manifest.get("sha256")
    if not isinstance(manifest_path_raw, str) or not manifest_path_raw:
        issues.append(f"{label} path missing")
        return
    if not isinstance(expected_sha, str) or not _is_sha256_hex(expected_sha):
        issues.append(f"{label} sha256 invalid")
        return
    manifest_path = Path(manifest_path_raw)
    if not manifest_path.is_file():
        issues.append(f"{label} file not found: {manifest_path}")
        return
    actual_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        issues.append(f"{label} sha256 mismatch: expected {expected_sha}, got {actual_sha}")
