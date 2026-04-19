from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    path: str = Field(description="Key / relative path inside the snapshot")
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


def _entry_record(entry: ManifestEntry | dict[str, Any]) -> dict[str, Any]:
    if isinstance(entry, ManifestEntry):
        return {
            "path": entry.path,
            "sha256": entry.sha256,
            "size_bytes": entry.size_bytes,
        }
    return {
        "path": entry["path"],
        "sha256": entry["sha256"],
        "size_bytes": entry["size_bytes"],
    }


def manifest_root_sha256(entries: list[ManifestEntry] | list[dict[str, Any]]) -> str:
    canonical_entries = sorted(
        (_entry_record(entry) for entry in entries),
        key=lambda entry: entry["path"],
    )
    payload = json.dumps(
        canonical_entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SnapshotManifest(BaseModel):
    snapshot_id: str = Field(description="e.g. '2026-04-13'")
    created_at: datetime
    entries: list[ManifestEntry]
    total_files: int
    total_bytes: int
    root_sha256: str = Field(min_length=64, max_length=64)

    def verify_counts(self) -> bool:
        return (
            self.total_files == len(self.entries)
            and self.total_bytes == sum(e.size_bytes for e in self.entries)
        )
