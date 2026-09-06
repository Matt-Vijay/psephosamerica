"""FEC bulk data and member-crosswalk commands."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.core.files import sha256_file as _sha256_file_path
from src.runtime.app import build_runtime
from src.runtime.commands._shared import (
    _attach_optional_verification_output,
    _command_issue_result,
    _is_non_negative_plain_int,
    _materialize_summary_run_metadata,
    _non_empty_line_count,
    _positive_finite_timeout,
    _write_json_artifact,
)
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.fec import FecBulkFilePaths, FecLocalLoadResult, run_fec_local_load_runtime
from src.runtime.fec_bulk_materialize import materialize_fec_bulk_files
from src.runtime.member_fec_crosswalk import (
    MemberFecCrosswalkLoadResult,
    load_member_fec_crosswalk_runtime,
)
from src.runtime.member_fec_crosswalk_materialize import materialize_member_fec_crosswalk
from src.runtime.output import summarize_fec_load_result, summarize_member_fec_crosswalk_load_result


def load_fec_local(
    ctx: RuntimeContext,
    files: FecBulkFilePaths,
) -> FecLocalLoadResult:
    """Open a connection and load local FEC bulk files."""
    conn = open_connection(ctx)
    return run_fec_local_load_runtime(conn, files)


def load_member_fec_crosswalk_local(
    ctx: RuntimeContext,
    crosswalk: Path,
    *,
    member_terms: Path | None = None,
    source_url: str | None = None,
) -> MemberFecCrosswalkLoadResult:
    """Open a connection and load a local member-FEC candidate crosswalk."""
    conn = open_connection(ctx)
    return load_member_fec_crosswalk_runtime(
        conn,
        crosswalk,
        member_terms_path=member_terms,
        source_url=source_url,
    )


def _handle_load_fec_local(args: Any) -> dict[str, Any]:
    contribution_chunk_size = getattr(args, "contribution_chunk_size", 50_000)
    if type(contribution_chunk_size) is not int or contribution_chunk_size <= 0:
        issues = ["contribution_chunk_size must be a positive integer"]
        return {
            "ok": False,
            "command": "load-fec-local",
            "issues": issues,
            "issue_count": len(issues),
        }

    runtime = build_runtime()
    files = FecBulkFilePaths(
        committee_master=Path(args.committee_master),
        candidate_committee_linkage=Path(args.candidate_committee_linkage),
        individual_contributions=Path(args.individual_contributions),
        committee_source_url=args.committee_source_url or FecBulkFilePaths.committee_source_url,
        linkage_source_url=args.linkage_source_url or FecBulkFilePaths.linkage_source_url,
        contribution_source_url=(
            args.contribution_source_url or FecBulkFilePaths.contribution_source_url
        ),
        contribution_chunk_size=contribution_chunk_size,
    )
    result = load_fec_local(runtime.context, files)
    summary = summarize_fec_load_result(result)
    return {"ok": summary.get("ok", True), "command": "load-fec-local", **summary}


def _handle_verify_fec_inputs(args: Any) -> dict[str, Any]:
    files = {
        "committee_master": Path(args.committee_master),
        "candidate_committee_linkage": Path(args.candidate_committee_linkage),
        "individual_contributions": Path(args.individual_contributions),
    }
    crosswalk_raw = getattr(args, "member_fec_crosswalk", None)
    crosswalk_path = Path(crosswalk_raw) if crosswalk_raw else None
    if crosswalk_path is not None:
        files["member_fec_crosswalk"] = crosswalk_path

    issues: list[str] = []
    quality_gate_failures: list[str] = []
    row_counts: dict[str, int | None] = {}
    artifact_sha256: dict[str, str | None] = {}
    for name, path in files.items():
        if not path.is_file():
            issues.append(f"{name}: file not found: {path}")
            row_counts[name] = None
            artifact_sha256[name] = None
            continue
        artifact_sha256[name] = _sha256_file_path(path)
        try:
            row_counts[name] = (
                _member_fec_crosswalk_row_count(path)
                if name == "member_fec_crosswalk"
                else _non_empty_line_count(path)
            )
        except Exception as exc:  # noqa: BLE001
            issues.append(f"{name}: failed to inspect: {exc}")
            row_counts[name] = None

    if bool(getattr(args, "require_member_fec_crosswalk", False)):
        if crosswalk_path is None or row_counts.get("member_fec_crosswalk") is None:
            quality_gate_failures.append("member_fec_crosswalk_missing")

    minimums = {
        "committee_master": getattr(args, "min_committee_rows", None),
        "candidate_committee_linkage": getattr(args, "min_linkage_rows", None),
        "individual_contributions": getattr(args, "min_contribution_rows", None),
        "member_fec_crosswalk": getattr(args, "min_member_fec_rows", None),
    }
    minimum_arg_names = {
        "committee_master": "min_committee_rows",
        "candidate_committee_linkage": "min_linkage_rows",
        "individual_contributions": "min_contribution_rows",
        "member_fec_crosswalk": "min_member_fec_rows",
    }
    for name, minimum in minimums.items():
        if minimum is None:
            continue
        if not _is_non_negative_plain_int(minimum):
            issues.append(f"{minimum_arg_names[name]} must be a non-negative integer")
            continue
        count = row_counts.get(name)
        if count is None or count < minimum:
            quality_gate_failures.append(f"{name}_rows_below_min")

    source_state = {
        "paths": {name: str(path) for name, path in files.items()},
        "row_counts": row_counts,
        "require_member_fec_crosswalk": bool(getattr(args, "require_member_fec_crosswalk", False)),
    }
    return _attach_optional_verification_output(
        args,
        {
            "ok": not issues and not quality_gate_failures,
            "command": "verify-fec-inputs",
            "checked": len(files),
            "issues": issues,
            "issue_count": len(issues),
            "quality_gate_failures": quality_gate_failures,
            "quality_gate_failure_count": len(quality_gate_failures),
            "row_counts": row_counts,
            "artifact_sha256": artifact_sha256,
            "run_metadata": {
                "command": "verify-fec-inputs",
                "verification_flags": {
                    "require_member_fec_crosswalk": bool(
                        getattr(args, "require_member_fec_crosswalk", False)
                    ),
                    "min_committee_rows": getattr(args, "min_committee_rows", None),
                    "min_linkage_rows": getattr(args, "min_linkage_rows", None),
                    "min_contribution_rows": getattr(
                        args,
                        "min_contribution_rows",
                        None,
                    ),
                    "min_member_fec_rows": getattr(args, "min_member_fec_rows", None),
                },
                "artifact_sha256": artifact_sha256,
                "source_state": source_state,
            },
        },
    )


def _handle_materialize_fec_bulk_files(args: Any) -> dict[str, Any]:
    cycle = getattr(args, "cycle", None)
    if type(cycle) is not int or cycle <= 0:
        return _command_issue_result(
            "materialize-fec-bulk-files",
            ["cycle must be a positive integer"],
        )

    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 60.0),
    )
    if timeout_issue is not None:
        return _command_issue_result("materialize-fec-bulk-files", [timeout_issue])

    result = materialize_fec_bulk_files(
        cycle=cycle,
        output_dir=Path(args.output_dir),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
        committee_url=getattr(args, "committee_url", None),
        linkage_url=getattr(args, "linkage_url", None),
        contribution_url=getattr(args, "contribution_url", None),
        filter_individual_contributions=bool(
            getattr(args, "filter_individual_contributions", True)
        ),
        member_fec_crosswalk=Path(args.member_fec_crosswalk)
        if getattr(args, "member_fec_crosswalk", None) is not None
        else None,
    )
    payload = asdict(result)
    summary = {
        "ok": True,
        "command": "materialize-fec-bulk-files",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-fec-bulk-files",
        payload,
    )
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _handle_materialize_member_fec_crosswalk(args: Any) -> dict[str, Any]:
    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 60.0),
    )
    if timeout_issue is not None:
        return _command_issue_result("materialize-member-fec-crosswalk", [timeout_issue])

    source_file = Path(args.source_file) if getattr(args, "source_file", None) is not None else None
    result = materialize_member_fec_crosswalk(
        output_path=Path(args.output),
        terms_output_path=Path(args.terms_output)
        if getattr(args, "terms_output", None) is not None
        else None,
        source_path=source_file,
        source_url=str(args.source_url),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
    )
    payload = asdict(result)
    summary = {
        "ok": True,
        "command": "materialize-member-fec-crosswalk",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-member-fec-crosswalk",
        payload,
    )
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _member_fec_crosswalk_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = set(reader.fieldnames or [])
        required = {"bioguide_id", "fec_candidate_id"}
        if not required <= fieldnames:
            missing = sorted(required - fieldnames)
            raise ValueError(f"missing required columns: {missing}")
        return sum(
            1
            for row in reader
            if str(row.get("bioguide_id") or "").strip()
            and str(row.get("fec_candidate_id") or "").strip()
        )


def _handle_load_member_fec_crosswalk_local(args: Any) -> dict[str, Any]:
    runtime = build_runtime()
    result = load_member_fec_crosswalk_local(
        runtime.context,
        Path(args.crosswalk),
        member_terms=Path(args.member_terms)
        if getattr(args, "member_terms", None) is not None
        else None,
        source_url=args.source_url,
    )
    summary = summarize_member_fec_crosswalk_load_result(result)
    return {
        "ok": summary.get("ok", True),
        "command": "load-member-fec-crosswalk-local",
        **summary,
    }


def _has_strict_fec_inputs_verify_command(commands: list[str]) -> bool:
    required_fragments = (
        "--committee-master data/fec/cm.txt",
        "--candidate-committee-linkage data/fec/ccl.txt",
        "--individual-contributions data/fec/itcont.txt",
        "--member-fec-crosswalk data/crosswalks/member_fec.csv",
        "--require-member-fec-crosswalk",
        "--min-committee-rows 1",
        "--min-linkage-rows 1",
        "--min-contribution-rows 1",
        "--min-member-fec-rows 1",
    )
    return any(
        command.startswith("python3 -m src.runtime.main verify-fec-inputs ")
        and all(fragment in command for fragment in required_fragments)
        for command in commands
    )
