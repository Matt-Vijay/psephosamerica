from __future__ import annotations

from pathlib import Path

from src.export.contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from src.export.local_store import (
    load_evidence_card,
    load_manifest,
    load_member_profile,
    load_zip_feed,
)
from src.export.manifest import SnapshotManifest
from src.runtime.paths import local_publish_root


def load_local_manifest(
    snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
) -> SnapshotManifest:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_manifest(root, snapshot_id)


def load_local_member_profile(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberProfilePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_profile(root, slug)


def load_local_evidence_card(
    evidence_card_id: str,
    *,
    snapshot_root: Path | None = None,
) -> EvidenceCardPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_evidence_card(root, evidence_card_id)


def load_local_zip_feed(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
) -> ZipFeedPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_zip_feed(root, zip_code)
