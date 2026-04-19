"""Snapshot writer: serialize payloads, compute paths, plan files.  No I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from .builders import build_manifest, sha256_hex
from .contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload


# ── Serialisation ──────────────────────────────────────────────────────────────


def serialize_payload(payload: BaseModel) -> bytes:
    # sort_keys ensures byte-for-byte reproducibility across Python versions
    raw: dict[str, Any] = payload.model_dump(mode="json")
    return json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")


# ── Output path helpers ────────────────────────────────────────────────────────


def member_path(slug: str) -> str:
    return f"members/{slug}.json"


def zip_path(zip_code: str) -> str:
    return f"zip/{zip_code}.json"


def evidence_path(evidence_card_id: str) -> str:
    return f"evidence/{evidence_card_id}.json"


def manifest_path(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/manifest.json"


def _is_manifest_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return len(pure.parts) == 3 and pure.parts[0] == "snapshots" and pure.parts[2] == "manifest.json"


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
        return cls(
            path=path,
            content=content,
            sha256=sha256_hex(content),
            size_bytes=len(content),
        )


def finalize_publish_plan(snapshot_id: str, files: list[PlannedFile]) -> list[PlannedFile]:
    manifest_file_path = manifest_path(snapshot_id)
    manifest_paths = {planned.path for planned in files if _is_manifest_path(planned.path)}

    if not manifest_paths:
        return list(files)

    if manifest_paths != {manifest_file_path}:
        unexpected = ", ".join(sorted(manifest_paths))
        raise ValueError(f"publish plan must contain exactly one canonical manifest path: {unexpected}")

    non_manifest_files = [planned for planned in files if planned.path != manifest_file_path]
    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": planned.path, "sha256": planned.sha256, "size_bytes": planned.size_bytes}
            for planned in non_manifest_files
        ],
    )
    finalized_manifest = PlannedFile.from_bytes(
        manifest_file_path,
        serialize_payload(manifest),
    )
    return non_manifest_files + [finalized_manifest]


# ── Snapshot plan ──────────────────────────────────────────────────────────────


def plan_snapshot(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
) -> list[PlannedFile]:
    # Manifest is always last so callers can stream data files first.
    # It covers only the data files, not itself.
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
