from __future__ import annotations

import json
from pathlib import Path

from .contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from .manifest import SnapshotManifest
from .writer import evidence_path, manifest_path, member_path, zip_path


def _load_json(file: Path) -> object:
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    try:
        return json.loads(file.read_bytes())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file}: {exc}") from exc


def load_member_profile(snapshot_root: Path, slug: str) -> MemberProfilePayload:
    file = snapshot_root / member_path(slug)
    data = _load_json(file)
    return MemberProfilePayload.model_validate(data)


def load_evidence_card(snapshot_root: Path, evidence_card_id: str) -> EvidenceCardPayload:
    file = snapshot_root / evidence_path(evidence_card_id)
    data = _load_json(file)
    return EvidenceCardPayload.model_validate(data)


def load_zip_feed(snapshot_root: Path, zip_code: str) -> ZipFeedPayload:
    file = snapshot_root / zip_path(zip_code)
    data = _load_json(file)
    return ZipFeedPayload.model_validate(data)


def load_manifest(snapshot_root: Path, snapshot_id: str) -> SnapshotManifest:
    file = snapshot_root / manifest_path(snapshot_id)
    data = _load_json(file)
    return SnapshotManifest.model_validate(data)


def list_artifact_paths(manifest: SnapshotManifest) -> list[str]:
    return [entry.path for entry in manifest.entries]
