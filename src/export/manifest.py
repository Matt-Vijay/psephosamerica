"""SHA-256 snapshot manifest for published artifacts.

Each weekly recompute produces an immutable snapshot.  The manifest lists
every exported file and its content hash so consumers can verify integrity.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    """One file in the published snapshot."""

    path: str = Field(description="Key / relative path inside the snapshot")
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class SnapshotManifest(BaseModel):
    """Top-level manifest for a dated public snapshot."""

    snapshot_id: str = Field(description="e.g. '2026-04-13'")
    created_at: datetime
    entries: list[ManifestEntry]
    total_files: int
    total_bytes: int

    def verify_counts(self) -> bool:
        """Check that summary fields match entry list."""
        return (
            self.total_files == len(self.entries)
            and self.total_bytes == sum(e.size_bytes for e in self.entries)
        )
