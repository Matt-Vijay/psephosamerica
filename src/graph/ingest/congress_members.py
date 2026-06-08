"""Adapt federal Congress member records into entity-resolution inputs.

Maps the repo's already-parsed :class:`CurrentMemberLookupEntry` into a
:class:`~src.graph.entity_resolution.records.SourceRecord`, attaching the
bioguide ID as the strong external key (so the same official observed in FEC,
OpenStates, or a later snapshot resolves to one canonical Person ID) and a
:class:`~src.graph.provenance.ProvenanceEnvelope` pointing at the official
bioguide source.

This is the thin, on-spec adapter that puts real US public officials through the
Track A pipeline; richer attributes (party, committees, terms) attach as graph
edges in later slices.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime

from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope
from src.identity.current_member_lookup import (
    CurrentMemberLookupEntry,
    CurrentMemberLookupPayload,
)

BIOGUIDE_SOURCE_SYSTEM = "congress_bioguide"
CONGRESS_JURISDICTION = "us-congress"


def bioguide_source_url(bioguide_id: str) -> str:
    """The official bioguide biography URL for a member."""
    return f"https://bioguide.congress.gov/search/bio/{bioguide_id}"


def member_provenance(
    entry: CurrentMemberLookupEntry,
    *,
    snapshot_date: date,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Build the provenance envelope for a member observation.

    ``valid_from`` is the snapshot date the member was current as of; ``known_at``
    defaults to ``first_observed_at`` (a fact is knowable no later than when we
    observed it — the conservative, leakage-safe choice).
    """
    return ProvenanceEnvelope(
        source_url=bioguide_source_url(entry.bioguide_id),
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=snapshot_date,
        known_at=known_at if known_at is not None else first_observed_at,
    )


def source_record_from_member_entry(
    entry: CurrentMemberLookupEntry,
    provenance: ProvenanceEnvelope,
) -> SourceRecord:
    """Map one member lookup entry into a SourceRecord."""
    return SourceRecord(
        source_system=BIOGUIDE_SOURCE_SYSTEM,
        source_record_id=entry.bioguide_id,
        entity_type="person",
        display_name=entry.name,
        external_ids=(ExternalId(system="bioguide", value=entry.bioguide_id),),
        jurisdiction=CONGRESS_JURISDICTION,
        region=entry.state,
        provenance=provenance,
    )


def source_records_from_member_payload(
    payload: CurrentMemberLookupPayload,
    provenance_for: Callable[[CurrentMemberLookupEntry], ProvenanceEnvelope],
) -> list[SourceRecord]:
    """Map a whole member-lookup payload, building provenance per entry."""
    return _map_entries(payload.members, provenance_for)


def _map_entries(
    entries: Iterable[CurrentMemberLookupEntry],
    provenance_for: Callable[[CurrentMemberLookupEntry], ProvenanceEnvelope],
) -> list[SourceRecord]:
    return [source_record_from_member_entry(entry, provenance_for(entry)) for entry in entries]
