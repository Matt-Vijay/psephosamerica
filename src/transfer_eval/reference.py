"""Trusted behavioral reference derived only from a staged pilot case."""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.transfer_eval.cases import audit_case, load_request
from src.transfer_eval.contract import canonical_output, semantic_key, validate_record

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class ReferenceSlice:
    case_id: str
    cutoff: str
    eligible: tuple[dict[str, Any], ...]
    universe: tuple[dict[str, Any], ...]

    def jsonl(self) -> str:
        return canonical_output(self.eligible) + "\n"


def derive_reference(case_dir: Path) -> ReferenceSlice:
    """Normalize the visible CSV rows using the exact public contract."""
    audit_case(case_dir)
    request = load_request(case_dir / "request.json")
    cutoff = _datetime(str(request["cutoff"]))
    universe: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for artifact in request["artifacts"]:
        artifact_dir = (case_dir / str(artifact["path"])).resolve()
        available = _datetime(str(artifact["available_at"]))
        rows = _derive_artifact(artifact_dir, artifact)
        universe.extend(rows)
        if available <= cutoff:
            for row in rows:
                event_date = row.get("event_date") or row.get("_event_date")
                if event_date is None or event_date <= cutoff.date().isoformat():
                    eligible.append(row)
    _assert_reference(eligible, universe)
    return ReferenceSlice(
        str(request["case_id"]),
        str(request["cutoff"]),
        tuple(_public(row) for row in eligible),
        tuple(_public(row) for row in universe),
    )


def validate_oracle(case_dir: Path, oracle_root: Path) -> dict[str, Any]:
    """Prove the case projection agrees with Time Machine V1 at its cutoff."""
    import duckdb

    reference = derive_reference(case_dir)
    request = load_request(case_dir / "request.json")
    source_ids = [str(row["source_id"]) for row in request["artifacts"]]
    expected = _group(reference.eligible)
    connection = duckdb.connect(str(oracle_root / "time_machine.duckdb"), read_only=True)
    try:
        connection.execute("SET TimeZone='UTC'")
        parameters = [reference.cutoff, source_ids]
        bills = connection.execute(
            """
            SELECT source_artifact_id, source_bill_id, identifier, title,
                   classification, subjects, jurisdiction_id, source_url
            FROM tm.bills_as_of(CAST(? AS TIMESTAMPTZ))
            WHERE source_artifact_id IN (SELECT unnest(?))
            """,
            parameters,
        ).fetchall()
        actions = connection.execute(
            """
            SELECT a.source_artifact_id, b.source_bill_id,
                   replace(a.action_id, 'action:openstates:', ''), a.organization_id,
                   a.description, a.classification, CAST(a.action_date AS VARCHAR), a.source_url
            FROM tm.actions_as_of(CAST(? AS TIMESTAMPTZ)) a
            JOIN tm.bills b USING (bill_id)
            WHERE a.source_artifact_id IN (SELECT unnest(?))
            """,
            parameters,
        ).fetchall()
        rolls = connection.execute(
            """
            SELECT source_artifact_id, source_roll_call_id, source_bill_id,
                   jurisdiction_id, session_id, chamber, identifier, motion, result,
                   CAST(roll_call_date AS VARCHAR), source_url, roll_call_id
            FROM tm.roll_calls_as_of(CAST(? AS TIMESTAMPTZ))
            WHERE source_artifact_id IN (SELECT unnest(?))
            """,
            parameters,
        ).fetchall()
        votes = connection.execute(
            """
            SELECT source_artifact_id, roll_call_id, member_vote_id, source_person_id,
                   member_name, choice, source_url
            FROM tm.member_votes_as_of(CAST(? AS TIMESTAMPTZ))
            WHERE source_artifact_id IN (SELECT unnest(?))
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()

    visible = _group(reference.universe)
    bill_keys = {(row["source_id"], row["bill_id"]) for row in visible["bill"]}
    action_keys = {
        (row["source_id"], row["bill_id"], row["action_id"]) for row in visible["action"]
    }
    roll_keys = {
        (row["source_id"], row["jurisdiction_id"], row["session"], row["roll_call_id"])
        for row in visible["roll_call"]
    }
    vote_keys = {
        (
            row["source_id"],
            row["jurisdiction_id"],
            row["session"],
            row["roll_call_id"],
            row["member_vote_id"],
        )
        for row in visible["member_vote"]
    }

    actual_bills: list[dict[str, Any]] = []
    for source_id, bill_id, identifier, title, classification, subjects, jurisdiction, url in bills:
        if (source_id, bill_id) not in bill_keys:
            continue
        actual_bills.append(
            {
                "source_id": source_id,
                "bill_id": bill_id,
                "identifier": identifier,
                "title": title,
                "classifications": [classification] if classification else [],
                "subjects": sorted(subjects or []),
                "jurisdiction_id": jurisdiction,
                "source_url": url,
            }
        )
    actual_actions: list[dict[str, Any]] = []
    for source_id, bill_id, action_id, org, description, classes, date, url in actions:
        if (source_id, bill_id, action_id) not in action_keys:
            continue
        actual_actions.append(
            {
                "source_id": source_id,
                "bill_id": bill_id,
                "action_id": action_id,
                "organization_id": org,
                "description": description,
                "classifications": sorted(classes.split(",") if classes else []),
                "event_date": date,
                "source_url": url,
            }
        )
    actual_rolls: list[dict[str, Any]] = []
    roll_context: dict[str, tuple[str, str, str, str]] = {}
    for row in rolls:
        source_id, roll_id, bill_id, jurisdiction, session_id = row[:5]
        session = str(session_id).rsplit(":", 1)[-1]
        key = (source_id, jurisdiction, session, roll_id)
        if key not in roll_keys:
            continue
        roll_context[row[11]] = key
        actual_rolls.append(
            {
                "source_id": source_id,
                "roll_call_id": roll_id,
                "bill_id": bill_id,
                "jurisdiction_id": jurisdiction,
                "session": session,
                "chamber": row[5],
                "identifier": row[6],
                "motion": row[7],
                "result": row[8],
                "event_date": row[9],
                "source_url": row[10],
            }
        )
    actual_votes: list[dict[str, Any]] = []
    for source_id, canonical_id, member_id, person_id, name, choice, url in votes:
        context = roll_context.get(canonical_id)
        if context is None:
            continue
        _, jurisdiction, session, roll_id = context
        source_member_id = str(member_id).rsplit(":", 1)[-1]
        vote_key = (source_id, jurisdiction, session, roll_id, source_member_id)
        if vote_key not in vote_keys:
            continue
        actual_votes.append(
            {
                "source_id": source_id,
                "roll_call_id": roll_id,
                "member_vote_id": source_member_id,
                "person_id": person_id,
                "member_name": name,
                "choice": choice,
                "source_url": url,
            }
        )

    comparisons = {
        "bill": _oracle_counter(expected["bill"], actual_bills, "bill"),
        "action": _oracle_counter(expected["action"], actual_actions, "action"),
        "roll_call": _oracle_counter(expected["roll_call"], actual_rolls, "roll_call"),
        "member_vote": _oracle_counter(expected["member_vote"], actual_votes, "member_vote"),
    }
    failures = [kind for kind, result in comparisons.items() if not result["match"]]
    if failures:
        raise ValueError(f"case differs from Time Machine oracle: {failures}")
    return {"case_id": reference.case_id, "status": "PASS", "tables": comparisons}


def _derive_artifact(directory: Path, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = str(artifact["source_id"])
    parent_url = str(artifact["source_url"])
    jurisdiction = str(artifact["jurisdiction_id"])
    session = str(artifact["session"])
    bill_sources = _url_index(directory / "bill_sources.csv", "bill_id")
    vote_sources = _url_index(directory / "vote_sources.csv", "vote_event_id")
    organizations = {
        _clean(row.get("id")): _clean(row.get("classification")) or None
        for row in _rows(directory / "organizations.csv")
        if _clean(row.get("id"))
    }
    bills = list(_rows(directory / "bills.csv"))
    bill_chambers = {
        _clean(row.get("id")): _clean(row.get("organization_classification")) or None
        for row in bills
    }
    result: list[dict[str, Any]] = [
        {
            "record_type": "source",
            "source_id": source_id,
            "source_url": parent_url,
            "content_sha256": artifact["content_sha256"],
            "available_at": artifact["available_at"],
        }
    ]
    for row in bills:
        bill_id = _clean(row.get("id"))
        result.append(
            {
                "record_type": "bill",
                "bill_id": bill_id,
                "jurisdiction_id": jurisdiction,
                "session": _clean(row.get("session_identifier")) or session,
                "identifier": _nullable(row.get("identifier")),
                "title": _nullable(row.get("title")),
                "classifications": _string_list(row.get("classification")),
                "subjects": _string_list(row.get("subject")),
                "chamber": _nullable(row.get("organization_classification")),
                "source_id": source_id,
                "source_urls": bill_sources.get(bill_id) or [parent_url],
            }
        )
    for row in _rows(directory / "bill_actions.csv"):
        bill_id = _clean(row.get("bill_id"))
        event_date = _date(row.get("date"))
        if not event_date:
            continue
        result.append(
            {
                "record_type": "action",
                "action_id": _clean(row.get("id")),
                "bill_id": bill_id,
                "organization_id": _nullable(row.get("organization_id")),
                "description": _nullable(row.get("description")),
                "classifications": _string_list(row.get("classification")),
                "event_date": event_date,
                "source_id": source_id,
                "source_urls": bill_sources.get(bill_id) or [parent_url],
            }
        )
    rolls: dict[str, dict[str, Any]] = {}
    for row in _rows(directory / "votes.csv"):
        roll_id = _clean(row.get("id"))
        roll_bill_id = _nullable(row.get("bill_id"))
        event_date = _date(row.get("start_date"))
        if not roll_id or not event_date:
            continue
        org_id = _nullable(row.get("organization_id"))
        urls = (
            vote_sources.get(roll_id)
            or (bill_sources.get(roll_bill_id) if roll_bill_id is not None else None)
            or [parent_url]
        )
        record = {
            "record_type": "roll_call",
            "roll_call_id": roll_id,
            "jurisdiction_id": jurisdiction,
            "session": _clean(row.get("session_identifier")) or session,
            "bill_id": roll_bill_id,
            "organization_id": org_id,
            "chamber": organizations.get(org_id or "") or bill_chambers.get(roll_bill_id or ""),
            "identifier": _nullable(row.get("identifier")),
            "motion": _nullable(row.get("motion_text")),
            "result": _nullable(row.get("result")),
            "event_date": event_date,
            "source_id": source_id,
            "source_urls": urls,
        }
        result.append(record)
        rolls[roll_id] = record
    for row in _rows(directory / "vote_people.csv"):
        roll_id = _clean(row.get("vote_event_id"))
        roll = rolls.get(roll_id)
        vote_id = _clean(row.get("id"))
        choice = _clean(row.get("option"))
        if roll is None or not vote_id or not choice:
            continue
        result.append(
            {
                "record_type": "member_vote",
                "member_vote_id": vote_id,
                "roll_call_id": roll_id,
                "jurisdiction_id": jurisdiction,
                "session": roll["session"],
                "person_id": _nullable(row.get("voter_id")),
                "member_name": _nullable(row.get("voter_name")),
                "choice": choice,
                "source_id": source_id,
                "source_urls": roll["source_urls"],
                "event_date": roll["event_date"],
            }
        )
    # event_date is inherited for cutoff but intentionally absent from the public vote schema.
    for record in result:
        if record["record_type"] == "member_vote":
            record["_event_date"] = record.pop("event_date")
    return result


def _assert_reference(eligible: list[dict[str, Any]], universe: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, ...]] = set()
    for record in universe:
        check = {key: value for key, value in record.items() if not key.startswith("_")}
        _, error = validate_record(check)
        if error:
            raise ValueError(f"trusted reference violates contract: {error}")
        key = semantic_key(check)
        if key in seen:
            raise ValueError(f"duplicate semantic key in trusted reference: {key}")
        seen.add(key)
    eligible_keys = {semantic_key(_public(row)) for row in eligible}
    if not eligible_keys.issubset({semantic_key(_public(row)) for row in universe}):
        raise AssertionError("eligible reference is not a subset of raw universe")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _group(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in ("source", "bill", "action", "roll_call", "member_vote")
    }
    for record in records:
        grouped[str(record["record_type"])].append(_public(record))
    return grouped


def _oracle_counter(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]], kind: str
) -> dict[str, Any]:
    def projection(row: dict[str, Any]) -> tuple[Any, ...]:
        fields: tuple[str, ...]
        if kind == "bill":
            fields = (
                "source_id",
                "bill_id",
                "identifier",
                "title",
                "classifications",
                "subjects",
                "jurisdiction_id",
            )
        elif kind == "action":
            fields = (
                "source_id",
                "bill_id",
                "action_id",
                "organization_id",
                "description",
                "classifications",
                "event_date",
            )
        elif kind == "roll_call":
            fields = (
                "source_id",
                "roll_call_id",
                "bill_id",
                "jurisdiction_id",
                "session",
                "chamber",
                "identifier",
                "motion",
                "result",
                "event_date",
            )
        else:
            fields = (
                "source_id",
                "roll_call_id",
                "member_vote_id",
                "person_id",
                "member_name",
                "choice",
            )
        values: list[Any] = []
        for field in fields:
            value = row.get(field)
            values.append(tuple(value) if isinstance(value, list) else value)
        return tuple(values)

    expected_counter = Counter(projection(row) for row in expected)
    actual_counter = Counter(projection(row) for row in actual)
    return {
        "expected": sum(expected_counter.values()),
        "oracle": sum(actual_counter.values()),
        "match": expected_counter == actual_counter,
    }


def _rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return ()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return tuple(csv.DictReader(handle))


def _url_index(path: Path, field: str) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for row in _rows(path):
        key, url = _clean(row.get(field)), _clean(row.get("url"))
        if key and url.startswith(("http://", "https://")):
            result.setdefault(key, set()).add(url)
    return {key: sorted(urls) for key, urls in result.items()}


def _string_list(value: Any) -> list[str]:
    raw = _clean(value)
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed = raw
    if isinstance(parsed, (list, tuple)):
        return sorted({_clean(item) for item in parsed if _clean(item)})
    return [str(parsed).strip()] if str(parsed).strip() else []


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _nullable(value: Any) -> str | None:
    return _clean(value) or None


def _date(value: Any) -> str | None:
    match = _DATE.match(_clean(value))
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed


__all__ = ["ReferenceSlice", "derive_reference", "validate_oracle"]
