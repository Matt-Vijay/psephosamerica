"""Normalize one existing OpenStates session ZIP into canonical table rows.

The raw session exports are already local and immutable, so this module does no
network I/O and never extracts a ZIP to disk.  Small per-session lookup indexes
(organizations, source URLs, version links, roll calls, and bill chambers) stay in memory;
the potentially multi-million-row ``*_vote_people.csv`` member is consumed one
row at a time and written directly to the supplied sinks.

OpenStates supplies durable OCD identifiers for people, bills, and vote events.
Those identifiers are the only identity joins made here.  A named voter without
an OCD person ID remains an unresolved member-vote row; no name match or state
term is invented.  Version URLs are metadata pointers, not downloaded text, so
every emitted OpenStates text-version row has ``is_full_text=False``.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from src.time_machine.inventory import InventoryEntry
from src.time_machine.model import (
    openstates_bill_id,
    openstates_person_id,
    provenance,
    stable_id,
    utc_datetime,
)

SOURCE_FAMILY = "openstates_bulk"

_OCD_BILL_RE = re.compile(r"^ocd-bill/[^\s]+$")
_OCD_PERSON_RE = re.compile(r"^ocd-person/[^\s]+$")
_OCD_VOTE_RE = re.compile(r"^ocd-vote/[^\s]+$")
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

_REQUIRED_SINKS = frozenset(
    {
        "source_artifacts",
        "sessions",
        "bills",
        "actions",
        "bill_text_versions",
        "roll_calls",
        "member_votes",
        "people",
        "person_ids",
    }
)


class RowSink(Protocol):
    """The one method shared by :class:`ParquetSink` and test collectors."""

    def write(self, row: Mapping[str, Any]) -> None: ...


@dataclass
class OpenStatesBuildState:
    """Cross-ZIP identity/session deduplication state for one canonical build."""

    seen_people: set[str] = field(default_factory=set)
    seen_person_ids: set[tuple[str, str]] = field(default_factory=set)
    seen_sessions: set[str] = field(default_factory=set)


@dataclass
class OpenStatesZipReport:
    """Bounded summary returned after one session ZIP has been streamed."""

    zip_path: str
    state: str
    session_identifier: str
    source_artifacts: int = 0
    sessions: int = 0
    bills: int = 0
    actions: int = 0
    bill_text_versions: int = 0
    roll_calls: int = 0
    member_votes: int = 0
    people: int = 0
    person_ids: int = 0
    unresolved_member_votes: int = 0
    skipped_member_votes: int = 0


@dataclass(frozen=True)
class _SessionContext:
    state: str
    identifier: str
    display_name: str
    generated_at: datetime | None
    jurisdiction_id: str


@dataclass(frozen=True)
class _RollCallContext:
    roll_call_id: str
    source_roll_call_id: str
    event_date: date | None
    source_url: str | None


def normalize_openstates_zip(
    zip_path: Path,
    artifact: InventoryEntry,
    sinks: Mapping[str, RowSink],
    *,
    build_state: OpenStatesBuildState | None = None,
) -> OpenStatesZipReport:
    """Stream one OpenStates bulk ZIP into the supplied canonical table sinks.

    Reuse one ``build_state`` across calls during a full build so people and
    sessions appearing in multiple ZIPs are emitted once.  Bills, actions,
    versions, roll calls, and member votes use upstream record IDs and therefore
    need no fuzzy or cross-session deduplication here.
    """
    missing = sorted(_REQUIRED_SINKS.difference(sinks))
    if missing:
        raise ValueError(f"missing OpenStates sinks: {', '.join(missing)}")
    resolved = zip_path.resolve()
    if resolved != artifact.path.resolve():
        raise ValueError("ZIP path does not match its inventory entry")
    if artifact.source_family != SOURCE_FAMILY:
        raise ValueError(
            f"expected {SOURCE_FAMILY!r} inventory entry, got {artifact.source_family!r}"
        )
    if not zipfile.is_zipfile(resolved):
        raise ValueError(f"not an OpenStates ZIP: {resolved}")

    shared_state = build_state if build_state is not None else OpenStatesBuildState()
    observed_at = utc_datetime(artifact.observed_at)
    if observed_at is None:  # InventoryEntry requires this, but keep the boundary explicit.
        raise ValueError("OpenStates inventory entry is missing observed_at")

    with zipfile.ZipFile(resolved) as archive:
        members = _member_index(archive)
        readme = _read_readme(archive, members.get("README"))
        organizations = _organization_index(archive, members.get("organizations"))
        session = _session_context(resolved, members, readme, organizations)
        report = OpenStatesZipReport(
            zip_path=str(resolved),
            state=session.state,
            session_identifier=session.identifier,
        )

        _write_source_artifact(sinks["source_artifacts"], artifact, session.generated_at)
        report.source_artifacts += 1

        bill_sources = _source_url_index(archive, members.get("bill_sources"), "bill_id")
        vote_sources = _source_url_index(archive, members.get("vote_sources"), "vote_event_id")
        version_links = _version_link_index(archive, members.get("bill_version_links"))
        bill_chambers: dict[str, str | None] = {}

        _ensure_session(
            session,
            session.identifier,
            artifact,
            sinks["sessions"],
            shared_state,
            report,
        )

        for row in _csv_rows(archive, members.get("bills")):
            source_bill_id = _clean(row.get("id"))
            if not _OCD_BILL_RE.fullmatch(source_bill_id):
                continue
            session_identifier = _clean(row.get("session_identifier")) or session.identifier
            canonical_session_id = _ensure_session(
                session,
                session_identifier,
                artifact,
                sinks["sessions"],
                shared_state,
                report,
            )
            source_url = bill_sources.get(source_bill_id) or artifact.source_url
            chamber = _clean(row.get("organization_classification")) or None
            bill_chambers[source_bill_id] = chamber
            sinks["bills"].write(
                {
                    "bill_id": openstates_bill_id(source_bill_id),
                    "jurisdiction_id": session.jurisdiction_id,
                    "session_id": canonical_session_id,
                    "source_bill_id": source_bill_id,
                    "identifier": _clean(row.get("identifier")) or None,
                    "classification": _first(_string_list(row.get("classification"))),
                    "title": _clean(row.get("title")) or None,
                    "introduced_date": None,
                    "policy_area": None,
                    "subjects": _string_list(row.get("subject")),
                    "summary_text": None,
                    **_identity_provenance(artifact, source_url),
                }
            )
            report.bills += 1

        for row in _csv_rows(archive, members.get("bill_actions")):
            source_bill_id = _clean(row.get("bill_id"))
            source_action_id = _clean(row.get("id"))
            if not source_action_id or not _OCD_BILL_RE.fullmatch(source_bill_id):
                continue
            action_date = _date(row.get("date"))
            source_url = bill_sources.get(source_bill_id) or artifact.source_url
            sinks["actions"].write(
                {
                    "action_id": f"action:openstates:{source_action_id}",
                    "bill_id": openstates_bill_id(source_bill_id),
                    "organization_id": _clean(row.get("organization_id")) or None,
                    "description": _clean(row.get("description")) or None,
                    "classification": _join_classifications(row.get("classification")),
                    "action_date": action_date,
                    **_event_or_observation_provenance(artifact, action_date, source_url),
                }
            )
            report.actions += 1

        for row in _csv_rows(archive, members.get("bill_versions")):
            source_version_id = _clean(row.get("id"))
            source_bill_id = _clean(row.get("bill_id"))
            if not source_version_id or not _OCD_BILL_RE.fullmatch(source_bill_id):
                continue
            issued_date = _date(row.get("date"))
            links = version_links.get(source_version_id) or [("metadata", None, None)]
            for source_link_id, media_type, source_url in links:
                resolved_url = source_url or bill_sources.get(source_bill_id) or artifact.source_url
                sinks["bill_text_versions"].write(
                    {
                        "text_version_id": (
                            f"text-version:openstates:{source_version_id}:{source_link_id}"
                        ),
                        "bill_id": openstates_bill_id(source_bill_id),
                        "version_code": _first(_string_list(row.get("classification"))),
                        "version_name": _clean(row.get("note")) or None,
                        "issued_date": issued_date,
                        "media_type": media_type,
                        "content_path": None,
                        "text_content": None,
                        "is_full_text": False,
                        **_event_or_observation_provenance(artifact, issued_date, resolved_url),
                    }
                )
                report.bill_text_versions += 1

        roll_calls: dict[str, _RollCallContext] = {}
        for row in _csv_rows(archive, members.get("votes")):
            source_roll_call_id = _clean(row.get("id"))
            if not _OCD_VOTE_RE.fullmatch(source_roll_call_id):
                continue
            source_bill_id = _clean(row.get("bill_id"))
            valid_bill = source_bill_id if _OCD_BILL_RE.fullmatch(source_bill_id) else None
            session_identifier = _clean(row.get("session_identifier")) or session.identifier
            canonical_session_id = _ensure_session(
                session,
                session_identifier,
                artifact,
                sinks["sessions"],
                shared_state,
                report,
            )
            event_date = _date(row.get("start_date"))
            source_url = (
                vote_sources.get(source_roll_call_id)
                or (bill_sources.get(valid_bill) if valid_bill is not None else None)
                or artifact.source_url
            )
            organization_id = _clean(row.get("organization_id"))
            chamber = organizations.get(organization_id, (None, None))[0]
            if chamber is None and valid_bill is not None:
                chamber = bill_chambers.get(valid_bill)
            # OpenStates has reused ocd-vote IDs for conflicting records in
            # different sessions (notably New Jersey).  The exact upstream ID
            # remains in source_roll_call_id; the canonical key needs session
            # context to avoid conflating distinct legislative events.
            roll_call_id = stable_id(
                "roll-call:openstates", canonical_session_id, source_roll_call_id
            )
            sinks["roll_calls"].write(
                {
                    "roll_call_id": roll_call_id,
                    "jurisdiction_id": session.jurisdiction_id,
                    "session_id": canonical_session_id,
                    "chamber": chamber,
                    "identifier": _clean(row.get("identifier")) or None,
                    "source_roll_call_id": source_roll_call_id,
                    "source_bill_id": valid_bill,
                    "bill_id": openstates_bill_id(valid_bill) if valid_bill is not None else None,
                    "motion": _clean(row.get("motion_text")) or None,
                    "result": _clean(row.get("result")) or None,
                    "roll_call_date": event_date,
                    **_event_or_observation_provenance(artifact, event_date, source_url),
                }
            )
            roll_calls[source_roll_call_id] = _RollCallContext(
                roll_call_id=roll_call_id,
                source_roll_call_id=source_roll_call_id,
                event_date=event_date,
                source_url=source_url,
            )
            report.roll_calls += 1

        # This is the large stream.  Do not convert it to a list or group it by
        # voter/vote event: each row is resolved and flushed to its sink now.
        for row in _csv_rows(archive, members.get("vote_people")):
            source_roll_call_id = _clean(row.get("vote_event_id"))
            roll_call = roll_calls.get(source_roll_call_id)
            choice = _clean(row.get("option"))
            if roll_call is None or not choice:
                report.skipped_member_votes += 1
                continue
            source_person_id = _clean(row.get("voter_id"))
            valid_person = source_person_id if _OCD_PERSON_RE.fullmatch(source_person_id) else None
            person_id = openstates_person_id(valid_person) if valid_person is not None else None
            member_name = _clean(row.get("voter_name")) or None

            if person_id is not None and person_id not in shared_state.seen_people:
                sinks["people"].write(
                    {
                        "person_id": person_id,
                        "display_name": member_name or valid_person,
                        "jurisdiction_id": session.jurisdiction_id,
                        "birth_date": None,
                        "gender": None,
                        **_identity_provenance(artifact, artifact.source_url),
                    }
                )
                shared_state.seen_people.add(person_id)
                report.people += 1
            id_key = ("openstates", valid_person) if valid_person is not None else None
            if (
                person_id is not None
                and id_key is not None
                and id_key not in shared_state.seen_person_ids
            ):
                sinks["person_ids"].write(
                    {
                        "person_id": person_id,
                        "id_scheme": "openstates",
                        "id_value": valid_person,
                        "is_primary": True,
                        **_identity_provenance(artifact, artifact.source_url),
                    }
                )
                shared_state.seen_person_ids.add(id_key)
                report.person_ids += 1

            source_member_vote_id = _clean(row.get("id"))
            member_vote_id = _member_vote_id(
                source_member_vote_id,
                roll_call.roll_call_id,
                valid_person,
                choice,
            )
            sinks["member_votes"].write(
                {
                    "member_vote_id": member_vote_id,
                    "roll_call_id": roll_call.roll_call_id,
                    "person_id": person_id,
                    "source_person_id": valid_person,
                    "member_name": member_name,
                    "choice": choice,
                    **_event_or_observation_provenance(
                        artifact, roll_call.event_date, roll_call.source_url
                    ),
                }
            )
            report.member_votes += 1
            if valid_person is None:
                report.unresolved_member_votes += 1

    return report


def _member_index(archive: zipfile.ZipFile) -> dict[str, str]:
    members: dict[str, str] = {}
    suffixes = {
        "bills": "_bills.csv",
        "bill_sources": "_bill_sources.csv",
        "bill_actions": "_bill_actions.csv",
        "bill_versions": "_bill_versions.csv",
        "bill_version_links": "_bill_version_links.csv",
        "votes": "_votes.csv",
        "vote_sources": "_vote_sources.csv",
        "vote_people": "_vote_people.csv",
        "organizations": "_organizations.csv",
    }
    for name in archive.namelist():
        if name.rsplit("/", 1)[-1].upper() == "README":
            members.setdefault("README", name)
        for key, suffix in suffixes.items():
            if name.endswith(suffix):
                members.setdefault(key, name)
    return members


def _csv_rows(archive: zipfile.ZipFile, member: str | None) -> Iterator[dict[str, str]]:
    if member is None:
        return
    with archive.open(member) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        yield from csv.DictReader(text)


def _read_readme(archive: zipfile.ZipFile, member: str | None) -> dict[str, str]:
    if member is None:
        return {}
    with archive.open(member) as raw:
        text = raw.read(64 * 1024).decode("utf-8", errors="replace")
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and value.strip():
            values[key.strip().lower()] = value.strip()
    return values


def _organization_index(
    archive: zipfile.ZipFile, member: str | None
) -> dict[str, tuple[str | None, str | None]]:
    organizations: dict[str, tuple[str | None, str | None]] = {}
    for row in _csv_rows(archive, member):
        organization_id = _clean(row.get("id"))
        if not organization_id:
            continue
        organizations[organization_id] = (
            _clean(row.get("classification")) or None,
            _clean(row.get("jurisdiction_id")) or None,
        )
    return organizations


def _source_url_index(
    archive: zipfile.ZipFile,
    member: str | None,
    foreign_key: str,
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for row in _csv_rows(archive, member):
        key = _clean(row.get(foreign_key))
        url = _clean(row.get("url"))
        if key and _HTTP_RE.match(url):
            urls.setdefault(key, url)
    return urls


def _version_link_index(
    archive: zipfile.ZipFile, member: str | None
) -> dict[str, list[tuple[str, str | None, str | None]]]:
    links: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for row in _csv_rows(archive, member):
        version_id = _clean(row.get("version_id"))
        url = _clean(row.get("url"))
        if not version_id:
            continue
        source_link_id = _clean(row.get("id")) or _short_digest(version_id, url)
        links.setdefault(version_id, []).append(
            (
                source_link_id,
                _clean(row.get("media_type")) or None,
                url if _HTTP_RE.match(url) else None,
            )
        )
    return links


def _session_context(
    zip_path: Path,
    members: Mapping[str, str],
    readme: Mapping[str, str],
    organizations: Mapping[str, tuple[str | None, str | None]],
) -> _SessionContext:
    state = _clean(readme.get("state")).lower()
    session_identifier = _clean(readme.get("session"))
    csv_member = next((name for key, name in members.items() if key != "README"), "")
    parts = csv_member.split("/")
    if not state and parts and re.fullmatch(r"[A-Za-z]{2}", parts[0]):
        state = parts[0].lower()
    if not session_identifier and len(parts) > 1:
        session_identifier = parts[1]
    if not state or not session_identifier:
        raise ValueError(f"cannot determine state/session from {zip_path}")
    jurisdictions = sorted(
        {
            jurisdiction
            for _, jurisdiction in organizations.values()
            if jurisdiction is not None and jurisdiction.strip()
        }
    )
    if jurisdictions:
        jurisdiction_id = jurisdictions[0]
    else:
        # Older exports omit organizations.  DC is an OCD district rather than
        # a state; all other represented OpenStates codes are the 50 states.
        division = "district" if state == "dc" else "state"
        jurisdiction_id = f"ocd-jurisdiction/country:us/{division}:{state}/government"
    generated_at = _datetime(readme.get("generated at"))
    return _SessionContext(
        state=state,
        identifier=session_identifier,
        display_name=f"{state.upper()} {session_identifier}",
        generated_at=generated_at,
        jurisdiction_id=jurisdiction_id,
    )


def _ensure_session(
    session: _SessionContext,
    identifier: str,
    artifact: InventoryEntry,
    sink: RowSink,
    build_state: OpenStatesBuildState,
    report: OpenStatesZipReport,
) -> str:
    session_id = f"session:openstates:{session.jurisdiction_id}:{identifier}"
    if session_id in build_state.seen_sessions:
        return session_id
    sink.write(
        {
            "session_id": session_id,
            "jurisdiction_id": session.jurisdiction_id,
            "identifier": identifier,
            "name": f"{session.state.upper()} {identifier}",
            "start_date": None,
            "end_date": None,
            **_identity_provenance(artifact, artifact.source_url),
        }
    )
    build_state.seen_sessions.add(session_id)
    report.sessions += 1
    return session_id


def _write_source_artifact(
    sink: RowSink, artifact: InventoryEntry, published_at: datetime | None
) -> None:
    observed_at = utc_datetime(artifact.observed_at)
    available_at = published_at if published_at is not None else observed_at
    sink.write(
        {
            "source_artifact_id": artifact.source_artifact_id,
            "source_family": artifact.source_family,
            "relative_path": artifact.relative_path,
            "source_url": artifact.source_url,
            "media_type": artifact.media_type,
            "content_sha256": artifact.content_sha256,
            "byte_count": artifact.byte_count,
            "retained": True,
            "modified_at": utc_datetime(artifact.modified_at),
            "published_at": published_at,
            "available_at": available_at,
            "availability_basis": (
                "source_published_at" if published_at is not None else "local_observation"
            ),
            "observed_at": observed_at,
            "inventoried_at": observed_at,
        }
    )


def _identity_provenance(artifact: InventoryEntry, source_url: str | None) -> dict[str, Any]:
    return provenance(
        event_at=None,
        available_at=artifact.observed_at,
        availability_basis="local_observation",
        observed_at=artifact.observed_at,
        source_artifact_id=artifact.source_artifact_id,
        source_family=artifact.source_family,
        source_url=source_url,
        content_sha256=artifact.content_sha256,
    )


def _event_provenance(
    artifact: InventoryEntry, event_date: date, source_url: str | None
) -> dict[str, Any]:
    event_at = utc_datetime(event_date)
    observed_at = utc_datetime(artifact.observed_at)
    if observed_at is None:  # InventoryEntry requires this; preserve a clear failure mode.
        raise ValueError("OpenStates inventory entry is missing observed_at")
    # Exports sometimes preannounce or misdate future events.  They were already
    # knowable when observed, but the event clock must stay future so as_of hides them.
    event_was_observable = event_at is not None and event_at <= observed_at
    return provenance(
        event_at=event_at,
        available_at=event_at if event_was_observable else observed_at,
        availability_basis=("official_event_date" if event_was_observable else "local_observation"),
        observed_at=observed_at,
        source_artifact_id=artifact.source_artifact_id,
        source_family=artifact.source_family,
        source_url=source_url,
        content_sha256=artifact.content_sha256,
    )


def _event_or_observation_provenance(
    artifact: InventoryEntry, event_date: date | None, source_url: str | None
) -> dict[str, Any]:
    if event_date is not None:
        return _event_provenance(artifact, event_date, source_url)
    return _identity_provenance(artifact, source_url)


def _member_vote_id(
    source_id: str,
    roll_call_id: str,
    source_person_id: str | None,
    choice: str,
) -> str:
    if source_id:
        # vote_people IDs are unique within an archive but 14k+ values are
        # reused across the full corpus.  Keep the exact source ID visible and
        # qualify it by the already session-safe canonical roll call.
        return f"member-vote:openstates:{roll_call_id}:{source_id}"
    digest = _short_digest(roll_call_id, source_person_id or "", choice)
    return f"member-vote:openstates:derived:{digest}"


def _short_digest(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value if _clean(item)]
    raw = _clean(value)
    if not raw or raw == "[]":
        return []
    parsed: object
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed = raw
    if isinstance(parsed, (list, tuple)):
        return [_clean(item) for item in parsed if _clean(item)]
    cleaned = _clean(parsed)
    return [cleaned] if cleaned else []


def _join_classifications(value: object) -> str | None:
    values = _string_list(value)
    return ",".join(values) if values else None


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _date(value: object) -> date | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _datetime(value: object) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    parsed = utc_datetime(raw)
    return parsed.astimezone(UTC) if parsed is not None else None


__all__ = [
    "OpenStatesBuildState",
    "OpenStatesZipReport",
    "RowSink",
    "normalize_openstates_zip",
]
