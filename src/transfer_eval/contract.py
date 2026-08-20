"""Public stdin/files -> JSONL contract for the single V0 pilot task."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CONTRACT_VERSION = "psephos.openstates-slice.v0"
RECORD_TYPES = ("source", "bill", "action", "roll_call", "member_vote")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^artifact:sha256:[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)

_SCHEMAS: dict[str, dict[str, str]] = {
    "source": {
        "record_type": "str",
        "source_id": "artifact",
        "source_url": "url",
        "content_sha256": "sha256",
        "available_at": "datetime",
    },
    "bill": {
        "record_type": "str",
        "bill_id": "str",
        "jurisdiction_id": "str",
        "session": "str",
        "identifier": "nullable_str",
        "title": "nullable_str",
        "classifications": "str_list",
        "subjects": "str_list",
        "chamber": "nullable_str",
        "source_id": "artifact",
        "source_urls": "url_list",
    },
    "action": {
        "record_type": "str",
        "action_id": "str",
        "bill_id": "str",
        "organization_id": "nullable_str",
        "description": "nullable_str",
        "classifications": "str_list",
        "event_date": "date",
        "source_id": "artifact",
        "source_urls": "url_list",
    },
    "roll_call": {
        "record_type": "str",
        "roll_call_id": "str",
        "jurisdiction_id": "str",
        "session": "str",
        "bill_id": "nullable_str",
        "organization_id": "nullable_str",
        "chamber": "nullable_str",
        "identifier": "nullable_str",
        "motion": "nullable_str",
        "result": "nullable_str",
        "event_date": "date",
        "source_id": "artifact",
        "source_urls": "url_list",
    },
    "member_vote": {
        "record_type": "str",
        "member_vote_id": "str",
        "roll_call_id": "str",
        "jurisdiction_id": "str",
        "session": "str",
        "person_id": "nullable_str",
        "member_name": "nullable_str",
        "choice": "str",
        "source_id": "artifact",
        "source_urls": "url_list",
    },
}


@dataclass(frozen=True)
class ParsedOutput:
    records: tuple[dict[str, Any], ...]
    nonblank_lines: int
    valid_lines: int
    errors: tuple[str, ...]


def _valid_type(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str) and bool(value)
    if kind == "nullable_str":
        return value is None or isinstance(value, str)
    if kind == "artifact":
        return isinstance(value, str) and _ARTIFACT_ID.fullmatch(value) is not None
    if kind == "sha256":
        return isinstance(value, str) and _SHA256.fullmatch(value) is not None
    if kind == "url":
        return isinstance(value, str) and _HTTP.match(value) is not None
    if kind in {"str_list", "url_list"}:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
        return kind != "url_list" or (bool(value) and all(_HTTP.match(item) for item in value))
    if kind == "date":
        if not isinstance(value, str) or _DATE.fullmatch(value) is None:
            return False
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return False
        return True
    if kind == "datetime":
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    raise AssertionError(f"unknown contract kind: {kind}")


def validate_record(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "record must be a JSON object"
    record_type = value.get("record_type")
    if not isinstance(record_type, str):
        return None, "record_type must be a string"
    if record_type not in _SCHEMAS:
        return None, "unknown record_type"
    schema = _SCHEMAS[str(record_type)]
    missing = sorted(set(schema).difference(value))
    extra = sorted(set(value).difference(schema))
    if missing or extra:
        return None, f"{record_type}: missing={missing}, extra={extra}"
    for field, kind in schema.items():
        if not _valid_type(value[field], kind):
            return None, f"{record_type}.{field} does not satisfy {kind}"
    normalized = dict(value)
    for field in ("classifications", "subjects", "source_urls"):
        if field in normalized:
            normalized[field] = sorted(set(normalized[field]))
    return normalized, None


def parse_jsonl(payload: bytes, *, max_lines: int = 200_000) -> ParsedOutput:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    nonblank = 0
    for line_number, raw in enumerate(payload.decode("utf-8", errors="replace").splitlines(), 1):
        if not raw.strip():
            continue
        nonblank += 1
        if nonblank > max_lines:
            errors.append(f"line {line_number}: output line limit exceeded")
            break
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            detail = getattr(exc, "msg", str(exc) or type(exc).__name__)
            errors.append(f"line {line_number}: invalid JSON ({detail})")
            continue
        record, error = validate_record(value)
        if error is not None:
            errors.append(f"line {line_number}: {error}")
        elif record is not None:
            records.append(record)
    return ParsedOutput(tuple(records), nonblank, len(records), tuple(errors))


def semantic_key(record: dict[str, Any]) -> tuple[str, ...]:
    kind = str(record["record_type"])
    if kind == "source":
        return (kind, str(record["source_id"]))
    if kind == "bill":
        return (kind, str(record["source_id"]), str(record["bill_id"]))
    if kind == "action":
        return (
            kind,
            str(record["source_id"]),
            str(record["bill_id"]),
            str(record["action_id"]),
        )
    if kind == "roll_call":
        return (
            kind,
            str(record["source_id"]),
            str(record["jurisdiction_id"]),
            str(record["session"]),
            str(record["roll_call_id"]),
        )
    if kind == "member_vote":
        return (
            kind,
            str(record["source_id"]),
            str(record["jurisdiction_id"]),
            str(record["session"]),
            str(record["roll_call_id"]),
            str(record["member_vote_id"]),
        )
    raise ValueError(f"unsupported record type: {kind}")


def canonical_output(records: tuple[dict[str, Any], ...]) -> str:
    ordered = sorted(records, key=lambda row: (semantic_key(row), json.dumps(row, sort_keys=True)))
    return "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in ordered)


def task_schema() -> dict[str, Any]:
    """Return the exact public output fields without exposing grader logic."""
    return {"contract_version": CONTRACT_VERSION, "record_types": _SCHEMAS}


__all__ = [
    "CONTRACT_VERSION",
    "ParsedOutput",
    "RECORD_TYPES",
    "canonical_output",
    "parse_jsonl",
    "semantic_key",
    "task_schema",
    "validate_record",
]
