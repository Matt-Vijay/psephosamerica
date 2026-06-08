"""Adapt Legistar (municipal) person records into source records.

Legistar (a Granicus product) runs the legislative systems of thousands of US
cities and counties and exposes a public OData API
(``webapi.legistar.com/v1/<client>/persons``) — within public-record bounds, no
login. :func:`parse_legistar_person` maps one council/board member into the same
:class:`~src.graph.entity_resolution.records.SourceRecord` shape federal and
state officials use, so a city councilor resolves to a canonical Person ID
identically — extending coverage to the municipal tier.

Per the hard constraints, only public-conduct fields are carried (name, the
per-city Legistar person ID, the jurisdiction). Personal contact fields the API
returns (email, home address, phone) are deliberately dropped.
"""

from __future__ import annotations

from typing import Any

from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.officials import official_record
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope


def is_active_person(record: dict[str, Any]) -> bool:
    """Whether a Legistar person is a currently-serving member."""
    return record.get("PersonActiveFlag") == 1


def parse_legistar_person(
    record: dict[str, Any],
    *,
    client: str,
    jurisdiction: Jurisdiction,
    region: str,
    provenance: ProvenanceEnvelope,
) -> SourceRecord:
    """Map one Legistar person record into a municipal-official source record."""
    person_id = record.get("PersonId")
    if person_id is None:
        raise ValueError("Legistar person is missing a PersonId")
    name = (record.get("PersonFullName") or "").strip()
    if not name:
        first = (record.get("PersonFirstName") or "").strip()
        last = (record.get("PersonLastName") or "").strip()
        name = " ".join(part for part in (first, last) if part)
    if not name:
        raise ValueError("Legistar person has no usable name")
    return official_record(
        source_system="legistar",
        source_record_id=f"{client}:{person_id}",
        name=name,
        jurisdiction_code=jurisdiction.code,
        external_ids=[("legistar", f"{client}:{person_id}")],
        region=region,
        provenance=provenance,
    )
