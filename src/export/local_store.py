from __future__ import annotations

import json
from pathlib import Path

from src.homepage.contracts import HomepageFeedPayload
from .contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from .manifest import SnapshotManifest
from .writer import evidence_path, manifest_path, member_path, zip_path


HOMEPAGE_FEED_PATH = "homepage/feed.json"


# ── Path safety ────────────────────────────────────────────────────


def _safe_subpath(root: Path, relative: str) -> Path:
    """Resolve *relative* under *root* and reject any path that escapes it.

    Raises ``ValueError`` on traversal attempts (``..``), null bytes, or
    absolute segments that would land outside the snapshot root.
    """
    if "\x00" in relative:
        raise ValueError(f"Path segment contains null byte: {relative!r}")
    resolved = (root / relative).resolve()
    root_resolved = root.resolve()
    # The resolved path must be equal to or a child of root.
    if not (resolved == root_resolved or str(resolved).startswith(str(root_resolved) + "/")):
        raise ValueError(
            f"Path escapes snapshot root: {relative!r} resolves to {resolved}"
        )
    return root / relative


# ── Internal loader ────────────────────────────────────────────────


def _load_json(file: Path) -> object:
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    try:
        return json.loads(file.read_bytes())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file}: {exc}") from exc


# ── Content artifact loaders ──────────────────────────────────────


def load_member_profile(snapshot_root: Path, slug: str) -> MemberProfilePayload:
    file = _safe_subpath(snapshot_root, member_path(slug))
    data = _load_json(file)
    return MemberProfilePayload.model_validate(data)


def load_evidence_card(snapshot_root: Path, evidence_card_id: str) -> EvidenceCardPayload:
    file = _safe_subpath(snapshot_root, evidence_path(evidence_card_id))
    data = _load_json(file)
    return EvidenceCardPayload.model_validate(data)


def load_zip_feed(snapshot_root: Path, zip_code: str) -> ZipFeedPayload:
    file = _safe_subpath(snapshot_root, zip_path(zip_code))
    data = _load_json(file)
    return ZipFeedPayload.model_validate(data)


def load_homepage_feed(root: Path) -> HomepageFeedPayload:
    """Load the pre-rendered homepage feed artifact from the publish tree."""
    file = _safe_subpath(root, HOMEPAGE_FEED_PATH)
    data = _load_json(file)
    return HomepageFeedPayload.model_validate(data)


# ── Manifest loaders ──────────────────────────────────────────────


def load_manifest(snapshot_root: Path, snapshot_id: str) -> SnapshotManifest:
    file = _safe_subpath(snapshot_root, manifest_path(snapshot_id))
    data = _load_json(file)
    return SnapshotManifest.model_validate(data)


def list_artifact_paths(manifest: SnapshotManifest) -> list[str]:
    return [entry.path for entry in manifest.entries]


# ── Snapshot resolution ───────────────────────────────────────────


def latest_snapshot_id(root: Path) -> str:
    """Return the snapshot_id of the most recent published snapshot.

    Snapshot directories live at ``<root>/snapshots/<snapshot_id>/``.
    ``YYYY-MM-DD`` lexicographic order equals chronological order.

    Raises ``FileNotFoundError`` when no snapshots exist yet.
    """
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.exists():
        raise FileNotFoundError(f"No snapshots directory found at: {snapshots_dir}")

    candidates = sorted(d.name for d in snapshots_dir.iterdir() if d.is_dir())
    if not candidates:
        raise FileNotFoundError(f"No snapshot directories found in: {snapshots_dir}")

    return candidates[-1]


def load_latest_manifest(root: Path) -> SnapshotManifest:
    """Load the manifest for the most recent published snapshot."""
    snapshot_id = latest_snapshot_id(root)
    return load_manifest(root, snapshot_id)
