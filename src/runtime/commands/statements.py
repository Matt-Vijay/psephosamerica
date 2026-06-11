"""Public-statement materialization and verification commands."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json

from dataclasses import asdict
from pathlib import Path
from src.evidence.source_anchor_policy import is_official_source_url
from src.runtime.public_statement_rows_materialize import materialize_public_statement_rows
from src.runtime.public_statement_rss_materialize import materialize_public_statement_rss
from typing import Any

from src.runtime.commands._shared import (
    _command_issue_result,
    _is_non_negative_plain_int,
    _is_optional_non_negative_plain_int,
    _materialize_summary_run_metadata,
    _positive_finite_timeout,
    _write_json_artifact,
)


def _handle_materialize_public_statement_rows(args: Any) -> dict[str, Any]:
    result = materialize_public_statement_rows(
        input_path=Path(args.input),
        output_path=Path(args.output),
        taxonomy_path=Path(args.taxonomy),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
    )
    payload = asdict(result)
    summary = {
        "ok": result.row_count > 0,
        "command": "materialize-public-statement-rows",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-public-statement-rows",
        payload,
    )
    if result.row_count == 0:
        summary["quality_gate_failures"] = ["no_statement_rows_materialized"]
    else:
        summary["quality_gate_failures"] = []
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _handle_materialize_public_statement_rss(args: Any) -> dict[str, Any]:
    timeout, timeout_issue = _positive_finite_timeout(
        getattr(args, "timeout", 30.0),
    )
    issues = [timeout_issue] if timeout_issue is not None else []
    max_feeds = getattr(args, "max_feeds", None)
    max_items_per_feed = getattr(args, "max_items_per_feed", None)
    for key, value in (
        ("max_feeds", max_feeds),
        ("max_items_per_feed", max_items_per_feed),
    ):
        if not _is_optional_non_negative_plain_int(value):
            issues.append(f"{key} must be a non-negative integer")
    if issues:
        return _command_issue_result("materialize-public-statement-rss", issues)

    source_file = Path(args.source_file) if getattr(args, "source_file", None) is not None else None
    result = materialize_public_statement_rss(
        output_path=Path(args.output),
        source_path=source_file,
        source_url=str(args.source_url),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=bool(getattr(args, "force", False)),
        timeout=timeout,
        max_feeds=max_feeds,
        max_items_per_feed=max_items_per_feed,
    )
    payload = asdict(result)
    summary = {
        "ok": result.feed_count > 0 if result.dry_run else result.row_count > 0,
        "command": "materialize-public-statement-rss",
        **payload,
    }
    summary["run_metadata"] = _materialize_summary_run_metadata(
        "materialize-public-statement-rss",
        payload,
    )
    if result.feed_count == 0:
        summary["quality_gate_failures"] = ["no_official_rss_feeds"]
    elif not result.dry_run and result.row_count == 0:
        summary["quality_gate_failures"] = ["no_statement_rows_materialized"]
    else:
        summary["quality_gate_failures"] = []
    summary_output = (
        Path(args.summary_output) if getattr(args, "summary_output", None) is not None else None
    )
    if summary_output is not None:
        summary["summary_output_sha256"] = _write_json_artifact(summary_output, summary)
        summary["summary_output"] = str(summary_output)
    return summary


def _load_statement_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"statement rows file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _load_statement_rows_jsonl(path)
    if suffix == ".json":
        return _load_statement_rows_json(path)
    if suffix == ".csv":
        return _load_statement_rows_csv(path)
    raise ValueError("statement rows must be a .json, .jsonl, or .csv file")


def _statement_rows_source_metadata(path: Path, row_count: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "row_count": row_count,
    }


def _handle_verify_public_statement_rows(args: Any) -> dict[str, Any]:
    path = Path(args.statement_rows)
    issues: list[str] = []
    quality_gate_failures: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        rows = _load_statement_rows(path)
    except Exception as exc:  # noqa: BLE001
        issues.append(str(exc))
    row_count = len(rows)
    min_rows = getattr(args, "min_rows", None)
    if min_rows is not None:
        if not _is_non_negative_plain_int(min_rows):
            issues.append("min_rows must be a non-negative integer")
        elif row_count < min_rows:
            quality_gate_failures.append("row_count_below_minimum")
    duplicate_source_ids = _duplicate_statement_source_ids(rows)
    if duplicate_source_ids:
        quality_gate_failures.append("duplicate_source_ids")
    member_count = len({str(row["member_bioguide_id"]) for row in rows})
    sector_count = len({str(row["sector"]) for row in rows})
    official_source_url_count = row_count if not issues else 0
    date_range = _statement_rows_date_range(rows)

    result: dict[str, Any] = {
        "ok": not issues and not quality_gate_failures,
        "command": "verify-public-statement-rows",
        "artifact": str(path),
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else None,
        "checked": 1 if path.is_file() else 0,
        "issues": issues,
        "issue_count": len(issues),
        "quality_gate_failures": quality_gate_failures,
        "quality_gate_failure_count": len(quality_gate_failures),
        "row_count": row_count,
        "member_count": member_count,
        "sector_count": sector_count,
        "official_source_url_count": official_source_url_count,
        "duplicate_source_ids": duplicate_source_ids,
        "date_range": date_range,
        "verification_flags": {
            "min_rows": min_rows,
        },
    }
    result["run_metadata"] = {
        "command": "verify-public-statement-rows",
        "artifact_sha256": result["artifact_sha256"],
        "verification_flags": result["verification_flags"],
        "source_state": {
            "row_count": row_count,
            "member_count": member_count,
            "sector_count": sector_count,
            "official_source_url_count": official_source_url_count,
            "duplicate_source_id_count": len(duplicate_source_ids),
            "date_range": date_range,
        },
    }
    output = Path(args.output) if getattr(args, "output", None) is not None else None
    if output is not None:
        result["output_sha256"] = _write_json_artifact(output, result)
        result["output"] = str(output)
    return result


def _statement_source_id(row: dict[str, Any]) -> str:
    for key in ("statement_id", "source_record_id", "source_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _duplicate_statement_source_ids(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        source_id = _statement_source_id(row)
        if source_id in seen:
            duplicates.add(source_id)
        seen.add(source_id)
    return sorted(duplicates)


def _statement_rows_date_range(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    dates = [
        row["statement_date"] for row in rows if isinstance(row.get("statement_date"), dt.date)
    ]
    return {
        "min": min(dates).isoformat() if dates else None,
        "max": max(dates).isoformat() if dates else None,
    }


def _load_statement_rows_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("statement_rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("statement rows JSON must be a list or object with statement_rows")
    return [_coerce_statement_row(row) for row in rows]


def _load_statement_rows_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"statement rows JSONL line {line_number} must be an object")
        rows.append(_coerce_statement_row(raw))
    return rows


def _load_statement_rows_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_coerce_statement_row(dict(row)) for row in csv.DictReader(handle)]


def _coerce_statement_row(row: object) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("statement row must be an object")
    coerced = dict(row)
    statement_date = coerced.get("statement_date")
    if isinstance(statement_date, str) and statement_date:
        coerced["statement_date"] = dt.date.fromisoformat(statement_date)
    _validate_statement_row(coerced)
    return coerced


def _validate_statement_row(row: dict[str, Any]) -> None:
    required_fields = (
        "member_bioguide_id",
        "sector",
        "statement_date",
        "statement_source_url",
    )
    missing = [field for field in required_fields if not row.get(field)]
    if not (row.get("statement_id") or row.get("source_record_id") or row.get("source_id")):
        missing.append("statement_id/source_record_id/source_id")
    if missing:
        raise ValueError(f"statement row missing required fields: {', '.join(missing)}")
    if not isinstance(row["statement_date"], dt.date):
        raise ValueError("statement row statement_date must be an ISO date")
    if not is_official_source_url("public_statement", str(row["statement_source_url"])):
        raise ValueError("statement row statement_source_url must be an official House/Senate URL")
