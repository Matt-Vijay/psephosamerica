"""Parse OpenStates people YAML into state-legislator source records.

OpenStates publishes a public roster of every state legislator (the
``openstates/people`` repo) as one YAML file per person — within public-record
bounds, no login. :func:`parse_openstates_person_yaml` extracts only
*public-conduct* fields — name, party, state, and the stable OCD person ID — and
deliberately drops the private-life fields the hard constraints forbid
(birth date, personal email, gender). The result is the same
:class:`~src.graph.entity_resolution.records.SourceRecord` shape federal members
use, so state legislators resolve to canonical Person IDs identically.
"""

from __future__ import annotations

import re
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.officials import openstates_official_record
from src.graph.provenance import ProvenanceEnvelope

_STATE_RE = re.compile(r"state:([a-z]{2})\b")
_FILENAME_UUID_RE = re.compile(
    r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.ya?ml$"
)


def parse_openstates_person_filename(filename: str) -> tuple[str, str] | None:
    """Extract ``(ocd_person_id, name)`` from an OpenStates people filename.

    Files are named ``<Name-With-Hyphens>-<ocd-uuid>.yml``. This lets a
    national-scale roster be built from directory *listings* alone (one request
    per state) — the OCD ID and display name come straight from the filename,
    no per-file fetch — for resolving every state legislator to a canonical ID.
    Returns ``None`` when the filename does not carry an OCD UUID.
    """
    match = _FILENAME_UUID_RE.search(filename.strip())
    if match is None:
        return None
    name = filename[: match.start()].replace("-", " ").strip()
    if not name:
        return None
    return f"ocd-person/{match.group(1)}", name


def _extract_state(roles: list[dict[str, Any]]) -> str | None:
    for role in roles:
        jurisdiction = role.get("jurisdiction")
        if isinstance(jurisdiction, str):
            match = _STATE_RE.search(jurisdiction)
            if match is not None:
                return match.group(1)
    return None


def parse_openstates_person_yaml(
    text: str,
    *,
    provenance: ProvenanceEnvelope,
) -> SourceRecord:
    """Parse one OpenStates person YAML into a state-legislator source record."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("OpenStates person YAML must be a mapping")

    person_id = data.get("id")
    if not isinstance(person_id, str) or not person_id.strip():
        raise ValueError("OpenStates person is missing an id")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("OpenStates person is missing a name")

    roles = data.get("roles") or []
    state = _extract_state(roles if isinstance(roles, list) else [])
    if state is None:
        raise ValueError("OpenStates person has no resolvable state jurisdiction")

    party_list = data.get("party") or []
    party: str | None = None
    if isinstance(party_list, list) and party_list:
        last = party_list[-1]
        if isinstance(last, dict):
            name_value = last.get("name")
            party = name_value if isinstance(name_value, str) else None

    return openstates_official_record(
        openstates_id=person_id.strip(),
        name=name.strip(),
        state_usps=state,
        party=party,
        provenance=provenance,
    )
