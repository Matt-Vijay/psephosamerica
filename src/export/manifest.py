from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    path: str = Field(description="Key / relative path inside the snapshot")
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class SnapshotManifest(BaseModel):
    snapshot_id: str = Field(description="e.g. '2026-04-13'")
    created_at: datetime
    entries: list[ManifestEntry]
    total_files: int
    total_bytes: int

    def verify_counts(self) -> bool:
        return (
            self.total_files == len(self.entries)
            and self.total_bytes == sum(e.size_bytes for e in self.entries)
        )
