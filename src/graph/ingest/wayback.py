"""Adapt Wayback Machine CDX snapshots into time-resolved campaign-site edges.

The Internet Archive's CDX API lists every archived snapshot of a URL with its
capture timestamp — public, no login. That gives time-resolved versions of an
official's campaign site: what they said they stood for, *as of* each capture.
:func:`parse_cdx_row` parses one CDX row; :func:`campaign_snapshot_edge` builds a
``campaign_snapshot`` edge from the official to their campaign site keyed by the
capture timestamp; :func:`campaign_snapshot_provenance` points at the archived
copy and stamps ``known_at`` at the capture time (publicly observable from the
moment it was archived).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from src.graph.edges import GraphEdge
from src.graph.provenance import ProvenanceEnvelope

# CDX JSON columns: urlkey, timestamp, original, mimetype, statuscode, digest, length
_URLKEY = 0
_TIMESTAMP = 1
_ORIGINAL = 2
_MIMETYPE = 3
_STATUSCODE = 4
_DIGEST = 5
_MIN_FIELDS = 7


@dataclass(frozen=True)
class WaybackSnapshot:
    """One parsed Wayback CDX snapshot row."""

    urlkey: str
    original_url: str
    timestamp: datetime
    mimetype: str
    status_code: str
    digest: str


def _parse_timestamp(raw: str) -> datetime:
    digits = raw.strip()
    if len(digits) != 14 or not digits.isdigit():
        raise ValueError(f"unparseable Wayback timestamp: {raw!r}")
    try:
        return datetime(
            int(digits[0:4]),
            int(digits[4:6]),
            int(digits[6:8]),
            int(digits[8:10]),
            int(digits[10:12]),
            int(digits[12:14]),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise ValueError(f"unparseable Wayback timestamp: {raw!r}") from exc


def parse_cdx_row(row: list[str]) -> WaybackSnapshot:
    """Parse one Wayback CDX JSON row into a :class:`WaybackSnapshot`."""
    if len(row) < _MIN_FIELDS:
        raise ValueError(f"CDX row has {len(row)} fields, expected >= {_MIN_FIELDS}")
    return WaybackSnapshot(
        urlkey=row[_URLKEY].strip(),
        original_url=row[_ORIGINAL].strip(),
        timestamp=_parse_timestamp(row[_TIMESTAMP]),
        mimetype=row[_MIMETYPE].strip(),
        status_code=row[_STATUSCODE].strip(),
        digest=row[_DIGEST].strip(),
    )


def archived_url(snapshot: WaybackSnapshot) -> str:
    """The stable Wayback replay URL for a snapshot."""
    stamp = snapshot.timestamp.strftime("%Y%m%d%H%M%S")
    return f"http://web.archive.org/web/{stamp}/{snapshot.original_url}"


def campaign_snapshot_provenance(
    snapshot: WaybackSnapshot,
    *,
    content_sha256: str,
    first_observed_at: datetime,
    known_at: datetime | None = None,
) -> ProvenanceEnvelope:
    """Provenance for a campaign-site snapshot; ``known_at`` is the capture time."""
    return ProvenanceEnvelope(
        source_url=archived_url(snapshot),
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=date(snapshot.timestamp.year, snapshot.timestamp.month, snapshot.timestamp.day),
        known_at=known_at if known_at is not None else snapshot.timestamp,
    )


def campaign_snapshot_edge(
    *,
    official_canonical_id: str,
    snapshot: WaybackSnapshot,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the official -> campaign-site snapshot edge for one capture."""
    host = urlparse(snapshot.original_url).netloc or snapshot.urlkey
    attributes = {"original_url": snapshot.original_url, "status": snapshot.status_code}
    if snapshot.digest:
        attributes["wayback_digest"] = snapshot.digest
    return GraphEdge(
        edge_type="campaign_snapshot",
        src_id=official_canonical_id,
        dst_id=f"campaign_site:{host}",
        attributes=attributes,
        external_key=snapshot.timestamp.strftime("%Y%m%d%H%M%S"),
        provenance=provenance,
    )
