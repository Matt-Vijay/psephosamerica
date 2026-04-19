"""Congress archive payload loader — explicit local file I/O, no network.

Each helper reads one or more JSON files from a CongressArchive and returns
the raw dict payloads that match the Congress.gov API response shape.
Normalization into typed records is left to congress_api.py callers.

Payload shapes mirror the live API:
  members_payload      → {"members": [...]}
  committees_payload   → {"committees": [...]}
  bills_payload        → {"bills": [...]}
  cosponsors map       → {(congress, bill_type, bill_number): {"cosponsors": [...]}}
  member_detail map    → {bioguide_id: {...}}          (inner "member" dict)
  bill_detail map      → {(congress, bill_type, bill_number): {...}}  (inner "bill" dict)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .archive import CongressArchive, parse_bill_stem


# ---------------------------------------------------------------------------
# List payloads
# ---------------------------------------------------------------------------


def load_members_payload(archive: CongressArchive) -> dict[str, Any]:
    """Return the raw ``{"members": [...]}`` payload from the archive."""
    return _read_json(archive.members_path())


def load_committees_payload(archive: CongressArchive) -> dict[str, Any]:
    """Return the raw ``{"committees": [...]}`` payload from the archive."""
    return _read_json(archive.committees_path())


def load_bills_payload(archive: CongressArchive) -> dict[str, Any]:
    """Return the raw ``{"bills": [...]}`` payload from the archive."""
    return _read_json(archive.bills_path())


# ---------------------------------------------------------------------------
# Map payloads (scan sub-directories, load every file found)
# ---------------------------------------------------------------------------


def load_member_detail_payload_map(
    archive: CongressArchive,
) -> dict[str, dict[str, Any]]:
    """Return ``{bioguide_id: member_dict}`` for every file in member_details/.

    Each file is expected to contain ``{"member": {...}}``; the inner dict is
    stored as the map value so callers receive the same shape as
    ``CongressAPIClient.get_member_detail_payload()``.
    """
    result: dict[str, dict[str, Any]] = {}
    detail_dir = archive.member_details_dir()
    if not detail_dir.is_dir():
        return result
    for path in sorted(detail_dir.glob("*.json")):
        bioguide_id = path.stem
        payload = _read_json(path)
        result[bioguide_id] = payload.get("member", payload)
    return result


def load_bill_detail_payload_map(
    archive: CongressArchive,
) -> dict[tuple[int, str, int], dict[str, Any]]:
    """Return ``{(congress, bill_type, bill_number): bill_dict}`` for every file
    in bill_details/.

    Each file is expected to contain ``{"bill": {...}}``; the inner dict is
    stored as the map value so callers receive the same shape as
    ``CongressAPIClient.get_bill_detail_payload()``.
    """
    result: dict[tuple[int, str, int], dict[str, Any]] = {}
    detail_dir = archive.bill_details_dir()
    if not detail_dir.is_dir():
        return result
    for path in sorted(detail_dir.glob("*.json")):
        try:
            key = parse_bill_stem(path.stem)
        except ValueError:
            continue
        payload = _read_json(path)
        result[key] = payload.get("bill", payload)
    return result


def load_cosponsors_payload_map(
    archive: CongressArchive,
) -> dict[tuple[int, str, int], dict[str, Any]]:
    """Return ``{(congress, bill_type, bill_number): {"cosponsors": [...]}}``
    for every file in cosponsors/.
    """
    result: dict[tuple[int, str, int], dict[str, Any]] = {}
    cosponsor_dir = archive.cosponsors_dir()
    if not cosponsor_dir.is_dir():
        return result
    for path in sorted(cosponsor_dir.glob("*.json")):
        try:
            key = parse_bill_stem(path.stem)
        except ValueError:
            continue
        result[key] = _read_json(path)
    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}, got {type(payload).__name__}")
    return payload
