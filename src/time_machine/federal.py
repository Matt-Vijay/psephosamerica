"""Normalize the already-local modern federal corpus into canonical facts."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.time_machine.inventory import InventoryEntry
from src.time_machine.model import (
    ParquetSink,
    federal_bill_id,
    federal_person_id,
    iso_date,
    provenance,
    stable_id,
)

Progress = Callable[[str], None]


@dataclass
class FederalMetrics:
    people: int = 0
    person_ids: int = 0
    terms: int = 0
    bills: int = 0
    sessions: int = 0
    roll_calls: int = 0
    member_votes: int = 0
    unmatched_people: int = 0
    unmatched_bills: int = 0
    duplicate_external_ids: int = 0
    unmatched_people_examples: list[str] = field(default_factory=list)
    unmatched_bill_examples: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _entry(entries: Mapping[Path, InventoryEntry], path: Path) -> InventoryEntry:
    try:
        return entries[path.resolve()]
    except KeyError as exc:
        raise KeyError(f"required federal input was not inventoried: {path}") from exc


def _source(entry: InventoryEntry, *, family: str | None = None) -> dict[str, Any]:
    return {
        "source_artifact_id": entry.source_artifact_id,
        "source_family": family or entry.source_family,
        "source_url": entry.source_url,
        "content_sha256": entry.content_sha256,
    }


def _fact_provenance(
    entry: InventoryEntry,
    *,
    event_at: str | date | datetime | None,
    available_at: str | date | datetime | None = None,
    availability_basis: str = "local_observation",
    observed_at: str | datetime | None = None,
    source_url: str | None = None,
    family: str | None = None,
    valid_from: str | date | datetime | None = None,
    valid_to: str | date | datetime | None = None,
) -> dict[str, Any]:
    observed = observed_at or entry.observed_at
    available = available_at if available_at is not None else observed
    return provenance(
        event_at=event_at,
        available_at=available,
        availability_basis=availability_basis,
        observed_at=observed,
        source_artifact_id=entry.source_artifact_id,
        source_family=family or entry.source_family,
        source_url=source_url if source_url is not None else entry.source_url,
        # URLs may identify records inside an aggregate. The hash identifies
        # the retained artifact named by source_artifact_id.
        content_sha256=entry.content_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _short_json_prefix(line: str, marker: str) -> dict[str, Any] | None:
    """Parse the small identity/provenance prefix without loading embedding tails."""
    prefix, found, _tail = line.partition(marker)
    if not found:
        return None
    try:
        value = json.loads(prefix + "}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def load_contract_crosswalks(
    path: Path,
) -> tuple[dict[str, str], dict[str, tuple[str | None, str | None]]]:
    """Return legacy person→Bioguide and bill→(URL, claimed source hash)."""
    people: dict[str, str] = {}
    bills: dict[str, tuple[str | None, str | None]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = _short_json_prefix(line, ',"known_at"')
            if row is None:
                continue
            canonical = str(row.get("canonical_id") or "")
            external = [str(value) for value in row.get("external_ids") or []]
            if row.get("entity_type") == "person":
                bioguide = next(
                    (value.split(":", 1)[1] for value in external if value.startswith("bioguide:")),
                    None,
                )
                if bioguide:
                    people[canonical] = bioguide.upper()
            elif row.get("entity_type") == "bill":
                anchors = row.get("source_anchors") or []
                anchor = anchors[0] if anchors else {}
                bills[canonical] = (anchor.get("source_url"), anchor.get("content_sha256"))
    return people, bills


def _name(person: dict[str, Any]) -> str:
    names = person.get("name") or {}
    official = names.get("official_full")
    if official:
        return str(official)
    parts = [names.get("first"), names.get("middle"), names.get("last"), names.get("suffix")]
    return " ".join(str(part) for part in parts if part)


def _iter_id_values(ids: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for scheme, raw in ids.items():
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if value is not None and str(value).strip():
                yield str(scheme), str(value)


def ingest_identity_spine(
    source_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sinks: Mapping[str, ParquetSink],
    legacy_people: Mapping[str, str],
    metrics: FederalMetrics,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Load congress-legislators people, durable IDs and exact term intervals."""
    roster = source_root / "data/raw/acquisitions/congress_legislators/repo"
    paths = [roster / "legislators-current.yaml", roster / "legislators-historical.yaml"]
    by_bioguide: dict[str, str] = {}
    by_lis: dict[str, str] = {}
    display_names: dict[str, str] = {}
    external_seen: dict[tuple[str, str], str] = {}
    for path in paths:
        entry = _entry(entries, path)
        raw_people = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for raw in raw_people:
            ids = raw.get("id") or {}
            bioguide = str(ids.get("bioguide") or "").upper()
            if not bioguide:
                continue
            person_id = federal_person_id(bioguide)
            if person_id in display_names:
                continue
            display = _name(raw)
            display_names[person_id] = display
            by_bioguide[bioguide] = person_id
            bio = raw.get("bio") or {}
            base = _fact_provenance(entry, event_at=None)
            sinks["people"].write(
                {
                    "person_id": person_id,
                    "display_name": display,
                    "jurisdiction_id": "us-congress",
                    "birth_date": iso_date(bio.get("birthday")),
                    "gender": bio.get("gender"),
                    **base,
                }
            )
            metrics.people += 1
            for scheme, value in _iter_id_values(ids):
                normalized = value.upper() if scheme in {"bioguide", "lis"} else value
                key = (scheme, normalized)
                owner = external_seen.get(key)
                if owner is not None and owner != person_id:
                    metrics.duplicate_external_ids += 1
                    continue
                external_seen[key] = person_id
                if scheme == "lis":
                    by_lis[normalized.upper()] = person_id
                sinks["person_ids"].write(
                    {
                        "person_id": person_id,
                        "id_scheme": scheme,
                        "id_value": normalized,
                        "is_primary": scheme == "bioguide",
                        **base,
                    }
                )
                metrics.person_ids += 1
            for term in raw.get("terms") or []:
                start = iso_date(term.get("start"))
                end = iso_date(term.get("end"))
                chamber = "house" if term.get("type") == "rep" else "senate"
                term_id = stable_id("term", bioguide, chamber, start, end)
                sinks["terms"].write(
                    {
                        "term_id": term_id,
                        "person_id": person_id,
                        "jurisdiction_id": "us-congress",
                        "chamber": chamber,
                        "state": term.get("state"),
                        "district": str(term["district"])
                        if term.get("district") is not None
                        else None,
                        "party": term.get("party"),
                        "start_date": start,
                        "end_date": end,
                        **_fact_provenance(
                            entry,
                            event_at=start,
                            valid_from=start,
                            valid_to=end,
                        ),
                    }
                )
                metrics.terms += 1

    contract_entry = _entry(entries, source_root / "data/exports/contract_records/records.jsonl")
    legacy_map: dict[str, str] = {}
    legacy_base = _fact_provenance(contract_entry, event_at=None)
    for legacy_id, bioguide in legacy_people.items():
        mapped_person_id = by_bioguide.get(bioguide.upper())
        if mapped_person_id is None:
            continue
        legacy_map[legacy_id] = mapped_person_id
        key = ("legacy_ce", legacy_id)
        if key in external_seen:
            continue
        external_seen[key] = mapped_person_id
        sinks["person_ids"].write(
            {
                "person_id": mapped_person_id,
                "id_scheme": "legacy_ce",
                "id_value": legacy_id,
                "is_primary": False,
                **legacy_base,
            }
        )
        metrics.person_ids += 1
    return legacy_map, display_names, by_lis


def _congress_dates(congress: int) -> tuple[date, date]:
    year = 1787 + congress * 2
    return date(year, 1, 3), date(year + 2, 1, 3)


def ingest_bills(
    source_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sinks: Mapping[str, ParquetSink],
    bill_anchors: Mapping[str, tuple[str | None, str | None]],
    metrics: FederalMetrics,
) -> dict[str, str]:
    path = source_root / "data/exports/govinfo_bills/bill_content_full.jsonl"
    entry = _entry(entries, path)
    legacy_to_bill: dict[str, str] = {}
    sessions_seen: set[int] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            congress = int(raw["congress"])
            bill_type = str(raw["bill_type"]).lower()
            number = int(raw["number"])
            legacy_id = str(raw["canonical_id"])
            bill_id = federal_bill_id(congress, bill_type, number)
            legacy_to_bill[legacy_id] = bill_id
            introduced = iso_date(raw.get("introduced_date"))
            source_url, _source_hash = bill_anchors.get(legacy_id, (None, None))
            if not source_url:
                source_url = (
                    f"https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{bill_type}/"
                    f"BILLSTATUS-{congress}{bill_type}{number}.xml"
                )
            sinks["bills"].write(
                {
                    "bill_id": bill_id,
                    "jurisdiction_id": "us-congress",
                    "session_id": f"session:us-congress:{congress}",
                    "source_bill_id": legacy_id,
                    "identifier": f"{bill_type.upper()} {number}",
                    "classification": bill_type,
                    "title": raw.get("title"),
                    "introduced_date": introduced,
                    "policy_area": raw.get("policy_area"),
                    "subjects": [str(value) for value in raw.get("subjects") or []],
                    "summary_text": raw.get("summary_text"),
                    **_fact_provenance(
                        entry,
                        event_at=introduced,
                        source_url=source_url,
                        family="govinfo_billstatus",
                        valid_from=introduced,
                    ),
                }
            )
            metrics.bills += 1
            if congress not in sessions_seen:
                sessions_seen.add(congress)
                start, end = _congress_dates(congress)
                sinks["sessions"].write(
                    {
                        "session_id": f"session:us-congress:{congress}",
                        "jurisdiction_id": "us-congress",
                        "identifier": str(congress),
                        "name": f"{congress}th Congress",
                        "start_date": start,
                        "end_date": end,
                        **_fact_provenance(
                            entry,
                            event_at=start,
                            available_at=start,
                            availability_basis="official_event_date",
                            valid_from=start,
                            valid_to=end,
                        ),
                    }
                )
                metrics.sessions += 1
    return legacy_to_bill


def _remember_example(values: list[str], value: str, *, limit: int = 20) -> None:
    if value not in values and len(values) < limit:
        values.append(value)


def ingest_house_vote_edges(
    source_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sinks: Mapping[str, ParquetSink],
    legacy_people: Mapping[str, str],
    display_names: Mapping[str, str],
    legacy_bills: Mapping[str, str],
    metrics: FederalMetrics,
    *,
    progress: Progress | None = None,
) -> None:
    paths = [("house", source_root / "data/exports/govinfo_bills/house_vote_edges.jsonl")]
    seen_rolls: set[str] = set()
    for chamber, path in paths:
        if progress:
            progress(f"federal: reading {chamber} vote feed")
        entry = _entry(entries, path)
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                roll_id = str(raw.get("external_key") or "")
                parts = roll_id.split("-")
                if len(parts) != 4:
                    continue
                congress = int(parts[1])
                session_number = int(parts[2])
                roll_number = int(parts[3])
                legacy_person = str(raw.get("src_id") or "")
                legacy_bill = str(raw.get("dst_id") or "")
                person_id = legacy_people.get(legacy_person)
                bill_id = legacy_bills.get(legacy_bill)
                if person_id is None:
                    metrics.unmatched_people += 1
                    _remember_example(metrics.unmatched_people_examples, legacy_person)
                if bill_id is None:
                    metrics.unmatched_bills += 1
                    _remember_example(metrics.unmatched_bill_examples, legacy_bill)
                source = raw.get("provenance") or {}
                event = source.get("valid_from") or source.get("known_at")
                observed = source.get("first_observed_at") or entry.observed_at
                source_url = source.get("source_url") or entry.source_url
                fact = _fact_provenance(
                    entry,
                    event_at=event,
                    available_at=event,
                    availability_basis="official_event_date",
                    observed_at=observed,
                    source_url=source_url,
                    family="house_clerk" if chamber == "house" else "senate_lis",
                )
                canonical_roll = (
                    f"rollcall:us-congress:{chamber}:{congress}:{session_number}:{roll_number}"
                )
                if roll_id not in seen_rolls:
                    seen_rolls.add(roll_id)
                    sinks["roll_calls"].write(
                        {
                            "roll_call_id": canonical_roll,
                            "jurisdiction_id": "us-congress",
                            "session_id": f"session:us-congress:{congress}",
                            "chamber": chamber,
                            "identifier": str(roll_number),
                            "source_roll_call_id": roll_id,
                            "bill_id": bill_id,
                            "source_bill_id": legacy_bill,
                            "motion": None,
                            "result": None,
                            "roll_call_date": iso_date(str(event) if event else None),
                            **fact,
                        }
                    )
                    metrics.roll_calls += 1
                choice = str((raw.get("attributes") or {}).get("choice") or "unknown")
                sinks["member_votes"].write(
                    {
                        "member_vote_id": f"member-vote:us-congress:{roll_id}:{legacy_person}",
                        "roll_call_id": canonical_roll,
                        "person_id": person_id,
                        "source_person_id": legacy_person,
                        "member_name": display_names.get(person_id or ""),
                        "choice": choice,
                        **fact,
                    }
                )
                metrics.member_votes += 1


def _rich_bill_id(raw_id: str) -> str | None:
    parts = raw_id.split(":")
    if len(parts) != 3 or parts[0] != "us_congress" or not parts[1].isdigit():
        return None
    identifier = parts[2]
    bill_type, separator, number = identifier.partition("-")
    if not separator or not number.isdigit() or not bill_type.isalpha():
        return None
    return federal_bill_id(int(parts[1]), bill_type, int(number))


def ingest_senate_rich(
    source_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sinks: Mapping[str, ParquetSink],
    lis_people: Mapping[str, str],
    display_names: Mapping[str, str],
    legacy_bills: Mapping[str, str],
    metrics: FederalMetrics,
    *,
    progress: Progress | None = None,
) -> None:
    """Load rich Senate rolls, retaining procedural votes with a null bill link."""
    paths = [
        source_root / "data/real/senate_113_119_rich_v2.jsonl",
        source_root / "data/real/senate_113_119_rich.jsonl",
    ]
    seen: set[str] = set()
    durable_bills = set(legacy_bills.values())
    for path in paths:
        entry = _entry(entries, path)
        if progress:
            progress(f"federal: reading rich Senate rolls from {path.name}")
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                source_roll = str(raw.get("vote_id") or "")
                if not source_roll or source_roll in seen:
                    continue
                seen.add(source_roll)
                parts = source_roll.split("-")
                if len(parts) != 4 or not all(part.isdigit() for part in parts[1:]):
                    continue
                congress, session_number, roll_number = map(int, parts[1:])
                event = str(raw.get("date") or "")
                source_bill = str(raw.get("bill_id") or "") or None
                resolved_bill = _rich_bill_id(source_bill or "")
                if resolved_bill not in durable_bills:
                    resolved_bill = None
                source_url = (
                    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
                    f"vote{congress}{session_number}/vote_{congress}_{session_number}_"
                    f"{roll_number:05d}.xml"
                )
                fact = _fact_provenance(
                    entry,
                    event_at=event,
                    available_at=event,
                    availability_basis="official_event_date",
                    source_url=source_url,
                    family="senate_lis",
                )
                canonical_roll = (
                    f"rollcall:us-congress:senate:{congress}:{session_number}:{roll_number}"
                )
                sinks["roll_calls"].write(
                    {
                        "roll_call_id": canonical_roll,
                        "jurisdiction_id": "us-congress",
                        "session_id": f"session:us-congress:{congress}",
                        "chamber": "senate",
                        "identifier": str(roll_number),
                        "source_roll_call_id": source_roll,
                        "bill_id": resolved_bill,
                        "source_bill_id": source_bill,
                        "motion": raw.get("question"),
                        "result": None,
                        "roll_call_date": iso_date(event),
                        **fact,
                    }
                )
                metrics.roll_calls += 1
                if source_bill and resolved_bill is None and not source_bill.endswith(":unknown"):
                    metrics.unmatched_bills += 1
                    _remember_example(metrics.unmatched_bill_examples, source_bill)
                for cast in raw.get("votes") or []:
                    if not isinstance(cast, list) or len(cast) < 4:
                        continue
                    lis_id, _party, _state, choice = cast[:4]
                    source_person = str(lis_id).upper()
                    person_id = lis_people.get(source_person)
                    if person_id is None:
                        metrics.unmatched_people += 1
                        _remember_example(metrics.unmatched_people_examples, source_person)
                    sinks["member_votes"].write(
                        {
                            "member_vote_id": f"member-vote:us-congress:{source_roll}:{source_person}",
                            "roll_call_id": canonical_roll,
                            "person_id": person_id,
                            "source_person_id": source_person,
                            "member_name": display_names.get(person_id or ""),
                            "choice": str(choice).strip().lower(),
                            **fact,
                        }
                    )
                    metrics.member_votes += 1


def ingest_federal(
    source_root: Path,
    entries: Mapping[Path, InventoryEntry],
    sinks: Mapping[str, ParquetSink],
    *,
    progress: Progress | None = None,
) -> FederalMetrics:
    """Normalize local federal identity, BILLSTATUS metadata and member votes."""
    metrics = FederalMetrics()
    contract = source_root / "data/exports/contract_records/records.jsonl"
    if progress:
        progress("federal: building legacy ID crosswalks")
    legacy_person_to_bioguide, contract_bill_anchors = load_contract_crosswalks(contract)

    # The dedicated GovInfo export is newer and supplies exact BILLSTATUS anchors.
    bill_record_path = source_root / "data/exports/govinfo_bills/records.jsonl"
    bill_anchors: dict[str, tuple[str | None, str | None]] = dict(contract_bill_anchors)
    with bill_record_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = _short_json_prefix(line, ',"dossier_json"')
            if row is None:
                continue
            anchors = row.get("source_anchors") or []
            anchor = anchors[0] if anchors else {}
            bill_anchors[str(row.get("canonical_id") or "")] = (
                anchor.get("source_url"),
                anchor.get("content_sha256"),
            )

    if progress:
        progress("federal: loading congress-legislators identity spine")
    legacy_people, display_names, lis_people = ingest_identity_spine(
        source_root, entries, sinks, legacy_person_to_bioguide, metrics
    )
    if progress:
        progress("federal: normalizing BILLSTATUS metadata (not bill text)")
    legacy_bills = ingest_bills(source_root, entries, sinks, bill_anchors, metrics)
    ingest_house_vote_edges(
        source_root,
        entries,
        sinks,
        legacy_people,
        display_names,
        legacy_bills,
        metrics,
        progress=progress,
    )
    ingest_senate_rich(
        source_root,
        entries,
        sinks,
        lis_people,
        display_names,
        legacy_bills,
        metrics,
        progress=progress,
    )
    return metrics


__all__ = ["FederalMetrics", "ingest_federal", "load_contract_crosswalks"]
