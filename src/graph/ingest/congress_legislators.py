"""Adapt the keyless ``congress-legislators`` roster into person SourceRecords.

The CREC floor-speech backfill resolved only ~half its speakers because the
corpus carried a current-House subset of bioguides; historical members of
congresses 113-118 had no canonical Person ID to link to. The
unitedstates/congress-legislators dataset (``legislators-current.json`` +
``legislators-historical.json``, keyless GitHub-hosted, ~1.4 MB) lists **every**
member with their bioguide + LIS ids, name, and terms.

:func:`parse_legislators` maps each member whose terms overlap a target window
(default: the 113th Congress onward) into a
:class:`~src.graph.entity_resolution.records.SourceRecord` keyed by bioguide, so
they resolve to canonical Person IDs through the normal pipeline and the
speech/news/vote backfills can link to them. ``known_at`` sits at the member's
earliest relevant term start (a member is public when they take office).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.provenance import ProvenanceEnvelope

SOURCE_SYSTEM = "congress_legislators"
CONGRESS_JURISDICTION = "us-congress"
# Start of the 113th Congress -- the window the v5 backfills cover.
DEFAULT_SINCE = date(2013, 1, 3)
_ROSTER_URL = "https://unitedstates.github.io/congress-legislators/"


def _member_name(record: dict[str, Any]) -> str:
    name = record.get("name", {})
    official = (name.get("official_full") or "").strip()
    if official:
        return official
    first, last = (name.get("first") or "").strip(), (name.get("last") or "").strip()
    return f"{first} {last}".strip()


def _term_dates(term: dict[str, Any]) -> tuple[date | None, date | None]:
    def _parse(raw: object) -> date | None:
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None

    return _parse(term.get("start")), _parse(term.get("end"))


def legislator_source_record(
    record: dict[str, Any], *, since: date, first_observed_at: datetime
) -> SourceRecord | None:
    """Map one roster member to a SourceRecord, or ``None`` if outside the window.

    A member is in-window when any term ends on/after ``since``. ``known_at`` is
    that member's earliest relevant term start (else ``first_observed_at``).
    """
    bioguide = str(record.get("id", {}).get("bioguide") or "").strip()
    if not bioguide:
        return None
    terms = record.get("terms", []) or []
    relevant = [(s, e) for s, e in (_term_dates(t) for t in terms) if e is not None and e >= since]
    if not relevant:
        return None
    starts = [s for s, _ in relevant if s is not None]
    earliest = min(starts) if starts else first_observed_at.date()
    # State/region from the most recent term.
    region = ""
    for term in terms:
        state = (term.get("state") or "").strip()
        if state:
            region = state
    external_ids = [ExternalId(system="bioguide", value=bioguide)]
    lis = str(record.get("id", {}).get("lis") or "").strip()
    if lis:
        external_ids.append(ExternalId(system="lis", value=lis))

    provenance = ProvenanceEnvelope(
        source_url=_ROSTER_URL,
        content_sha256="0" * 64,  # roster file hash stamped by the runner
        first_observed_at=first_observed_at,
        valid_from=earliest,
        known_at=datetime(earliest.year, earliest.month, earliest.day, tzinfo=UTC),
    )
    return SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=bioguide,
        entity_type="person",
        display_name=_member_name(record) or bioguide,
        external_ids=tuple(external_ids),
        jurisdiction=CONGRESS_JURISDICTION,
        region=region or None,
        provenance=provenance,
    )


def parse_legislators(
    records: Iterable[dict[str, Any]],
    *,
    since: date = DEFAULT_SINCE,
    first_observed_at: datetime,
) -> list[SourceRecord]:
    """Map roster members whose terms overlap ``[since, ...)`` to SourceRecords."""
    out: list[SourceRecord] = []
    for record in records:
        source_record = legislator_source_record(
            record, since=since, first_observed_at=first_observed_at
        )
        if source_record is not None:
            out.append(source_record)
    return out
