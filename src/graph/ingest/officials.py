"""Adapt public officials at any jurisdiction into source records.

The blueprint's homogeneity claim — federal, state, county, and city officials
are one model, not special cases — lives or dies on ingestion treating them
identically. :func:`official_record` is the generic adapter; the OpenStates
(state legislators) and municipal helpers are thin wrappers that fill in the
right :class:`~src.graph.jurisdictions.Jurisdiction` code and strong external ID.

Every official becomes the same :class:`~src.graph.entity_resolution.records.\
SourceRecord` shape that federal members do, so the entity-resolution pipeline,
the graph, and the output contract handle a city councilor exactly as they
handle a US Senator.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

MunicipalLevel = Literal["city", "county", "special_district"]


def official_record(
    *,
    source_system: str,
    source_record_id: str,
    name: str,
    jurisdiction_code: str,
    external_ids: Sequence[tuple[str, str]],
    provenance: ProvenanceEnvelope,
    region: str | None = None,
    party: str | None = None,
) -> SourceRecord:
    """Generic public-official adapter for any jurisdiction."""
    return SourceRecord(
        source_system=source_system,
        source_record_id=source_record_id,
        entity_type="person",
        display_name=name,
        external_ids=tuple(
            ExternalId(system=system, value=value) for system, value in external_ids
        ),
        jurisdiction=jurisdiction_code,
        region=region,
        party=party,
        provenance=provenance,
    )


def openstates_official_record(
    *,
    openstates_id: str,
    name: str,
    state_usps: str,
    provenance: ProvenanceEnvelope,
    party: str | None = None,
) -> SourceRecord:
    """A state legislator from OpenStates, keyed by its OCD person ID."""
    state = Jurisdiction.state(state_usps)
    return official_record(
        source_system="openstates",
        source_record_id=openstates_id,
        name=name,
        jurisdiction_code=state.code,
        external_ids=[("openstates", openstates_id)],
        region=state.name,
        party=party,
        provenance=provenance,
    )


def municipal_official_record(
    *,
    source_system: str,
    source_record_id: str,
    name: str,
    state_usps: str,
    place: str,
    provenance: ProvenanceEnvelope,
    level: MunicipalLevel = "city",
    external_ids: Sequence[tuple[str, str]] = (),
    party: str | None = None,
) -> SourceRecord:
    """A county / city / special-district official (Granicus, Legistar, …)."""
    builders = {
        "city": Jurisdiction.city,
        "county": Jurisdiction.county,
        "special_district": Jurisdiction.special_district,
    }
    builder = builders.get(level)
    if builder is None:
        raise ValueError(f"unknown municipal level: {level!r}")
    jurisdiction = builder(state_usps, place)
    return official_record(
        source_system=source_system,
        source_record_id=source_record_id,
        name=name,
        jurisdiction_code=jurisdiction.code,
        external_ids=external_ids,
        region=state_usps.strip().upper(),
        party=party,
        provenance=provenance,
    )
