"""Local snapshot/export writer layer.

Pure helpers for:
- serialising payload models to deterministic JSON bytes
- computing output paths for member, ZIP, evidence, and manifest artefacts
- assembling a snapshot directory plan from existing export contracts

No I/O, no R2 client.  The :class:`PlannedFile` list returned by
:func:`plan_snapshot` can be handed to an upload layer or written to a
local staging directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .builders import build_manifest, sha256_hex
from .contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload


# ── Serialisation ──────────────────────────────────────────────────────────────


def serialize_payload(payload: BaseModel) -> bytes:
    """Serialise a Pydantic model to deterministic UTF-8 JSON bytes.

    Keys are sorted at every level to ensure byte-for-byte reproducibility
    across Python versions.  Dates and datetimes are emitted as ISO-8601
    strings via Pydantic's ``mode="json"`` round-trip.
    """
    raw: dict[str, Any] = payload.model_dump(mode="json")
    return json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")


# ── Output path helpers ────────────────────────────────────────────────────────


def member_path(slug: str) -> str:
    """Relative output path for a member profile artifact."""
    return f"members/{slug}.json"


def zip_path(zip_code: str) -> str:
    """Relative output path for a ZIP feed artifact."""
    return f"zip/{zip_code}.json"


def evidence_path(evidence_card_id: str) -> str:
    """Relative output path for an evidence card artifact."""
    return f"evidence/{evidence_card_id}.json"


def manifest_path(snapshot_id: str) -> str:
    """Relative output path for the snapshot manifest."""
    return f"snapshots/{snapshot_id}/manifest.json"


# ── PlannedFile ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedFile:
    """An artifact queued for writing.  Content is pre-computed and hashed.

    Use :meth:`from_bytes` rather than constructing directly so that
    ``sha256`` and ``size_bytes`` are always derived from ``content``.
    """

    path: str
    content: bytes
    sha256: str
    size_bytes: int

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> PlannedFile:
        """Create a :class:`PlannedFile` with hash and size computed from *content*."""
        return cls(
            path=path,
            content=content,
            sha256=sha256_hex(content),
            size_bytes=len(content),
        )


# ── Snapshot plan ──────────────────────────────────────────────────────────────


def plan_snapshot(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
) -> list[PlannedFile]:
    """Assemble the full set of files that make up one published snapshot.

    Returns a deterministic, ordered :class:`PlannedFile` list:

    1. Member profiles  (``members/<slug>.json``)
    2. ZIP feeds        (``zip/<zip_code>.json``)
    3. Evidence cards   (``evidence/<id>.json``)
    4. Manifest         (``snapshots/<snapshot_id>/manifest.json``)

    The manifest is always appended last so callers can stream data files
    first.  It covers only the data files, not itself.

    No I/O or network calls occur inside this function.
    """
    planned: list[PlannedFile] = []

    for profile in member_profiles:
        planned.append(PlannedFile.from_bytes(member_path(profile.slug), serialize_payload(profile)))

    for feed in zip_feeds:
        planned.append(PlannedFile.from_bytes(zip_path(feed.zip_code), serialize_payload(feed)))

    for card in evidence_cards:
        planned.append(
            PlannedFile.from_bytes(evidence_path(card.evidence_card_id), serialize_payload(card))
        )

    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes}
            for f in planned
        ],
    )
    planned.append(PlannedFile.from_bytes(manifest_path(snapshot_id), serialize_payload(manifest)))

    return planned
