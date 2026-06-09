"""Adapt GovInfo Congressional Record (CREC) floor speeches into the graph.

The Congressional Record's per-issue MODS metadata
(``govinfo.gov/metadata/pkg/CREC-<date>/mods.xml``, keyless) tags every speech
with the speaking member's **bioguide ID** — so floor speeches link straight to
``canonical_person_id`` with no NER required. :func:`parse_crec_mods` extracts
the speaking members from one issue; :func:`floor_speech_edge` builds a
``floor_speech`` edge from the member to that day's Record, ``known_at`` at the
issue date (the Record is public the day it is published). The speech becomes a
source-anchored fact in the member's dossier.

Parsed with a regex over the well-formed ``<congMember>`` elements (attribute
extraction, not XML evaluation — no XXE surface).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

_CONGMEMBER_RE = re.compile(r"<congMember\b([^>]*)>(.*?)</congMember>", re.S)
_PARSED_NAME_RE = re.compile(r'<name type="parsed">([^<]+)</name>')
_FNF_NAME_RE = re.compile(r'<name type="authority-fnf">([^<]+)</name>')


def crec_package_url(issue_date: date) -> str:
    """The keyless GovInfo MODS metadata URL for a Congressional Record issue."""
    return f"https://www.govinfo.gov/metadata/pkg/CREC-{issue_date.isoformat()}/mods.xml"


@dataclass(frozen=True)
class CrecSpeech:
    """One member's speaking appearance in a Congressional Record issue."""

    bioguide_id: str
    member_name: str
    chamber: str
    role: str
    speech_date: date


def _attr(attributes: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', attributes)
    return match.group(1).strip() if match else ""


def parse_crec_mods(xml: str, *, issue_date: date) -> list[CrecSpeech]:
    """Parse a CREC issue's MODS into one speech record per speaking member."""
    if not xml.strip():
        raise ValueError("empty CREC mods document")
    seen: set[str] = set()
    speeches: list[CrecSpeech] = []
    for match in _CONGMEMBER_RE.finditer(xml):
        attributes, body = match.group(1), match.group(2)
        bioguide = _attr(attributes, "bioGuideId")
        if not bioguide or bioguide in seen:
            continue
        seen.add(bioguide)
        fnf = _FNF_NAME_RE.search(body)
        parsed = _PARSED_NAME_RE.search(body)
        name = (fnf.group(1) if fnf else parsed.group(1) if parsed else bioguide).strip()
        speeches.append(
            CrecSpeech(
                bioguide_id=bioguide,
                member_name=name,
                chamber=_attr(attributes, "chamber"),
                role=_attr(attributes, "role"),
                speech_date=issue_date,
            )
        )
    return speeches


def floor_speech_provenance(
    *,
    source_url: str,
    content_sha256: str,
    speech_date: date,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a floor speech; ``known_at`` defaults to the issue day."""
    default_known = datetime(speech_date.year, speech_date.month, speech_date.day, tzinfo=UTC)
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=speech_date,
        known_at=known_at if known_at is not None else default_known,
    )


def floor_speech_edge(
    *,
    member_canonical_id: str,
    speech: CrecSpeech,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the member -> Congressional Record floor-speech edge."""
    iso = speech.speech_date.isoformat()
    attributes = {"role": speech.role.lower()} if speech.role else {}
    if speech.chamber:
        attributes["chamber"] = speech.chamber
    return GraphEdge(
        edge_type="floor_speech",
        src_id=member_canonical_id,
        dst_id=f"congressional_record:{iso}",
        attributes=attributes,
        external_key=f"{speech.bioguide_id}:{iso}",
        provenance=provenance,
    )
