"""Adapt FEC bulk records into entity-resolution source records.

Federal campaign finance is one of the few public-record sources with strong,
stable identifiers, which makes it a backbone for resolution:

* **Committee master (``cm.txt``)** — every PAC, party committee, and campaign
  committee, keyed by its FEC committee ID. Parsed into ``org`` source records.
* **Member crosswalk** — the bioguide <-> FEC candidate ID mapping. Parsed into
  ``person`` source records carrying *both* strong keys, so a member observed in
  Congress (bioguide) and in FEC (candidate ID) resolves to one canonical
  person.

The committee's linked candidate ID is deliberately *not* used as the
committee's own external ID — it is a relationship (a later edge), and treating
it as identity would wrongly merge the committee with the candidate.
"""

from __future__ import annotations

from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

# Field positions in the FEC committee-master (cm.txt) pipe-delimited format.
_CM_COMMITTEE_ID = 0
_CM_NAME = 1
_CM_STATE = 6
_CM_MIN_FIELDS = 15


def parse_committee_master_line(line: str, *, provenance: ProvenanceEnvelope) -> SourceRecord:
    """Parse one FEC committee-master (``cm.txt``) line into an org source record."""
    fields = line.rstrip("\n").split("|")
    if len(fields) < _CM_MIN_FIELDS:
        raise ValueError(
            f"committee master line has {len(fields)} fields, expected >= {_CM_MIN_FIELDS}"
        )
    committee_id = fields[_CM_COMMITTEE_ID].strip()
    name = fields[_CM_NAME].strip()
    state = fields[_CM_STATE].strip() or None
    return SourceRecord(
        source_system="fec",
        source_record_id=committee_id,
        entity_type="org",
        display_name=name,
        external_ids=(ExternalId(system="fec_committee", value=committee_id),),
        jurisdiction="us",
        region=state,
        provenance=provenance,
    )


def member_crosswalk_source_record(
    *,
    bioguide_id: str,
    fec_candidate_id: str,
    display_name: str,
    provenance: ProvenanceEnvelope,
) -> SourceRecord:
    """A person record carrying both the bioguide and FEC candidate strong keys."""
    return SourceRecord(
        source_system="member_fec_crosswalk",
        source_record_id=bioguide_id,
        entity_type="person",
        display_name=display_name,
        external_ids=(
            ExternalId(system="bioguide", value=bioguide_id),
            ExternalId(system="fec_candidate", value=fec_candidate_id),
        ),
        jurisdiction="us-congress",
        provenance=provenance,
    )
