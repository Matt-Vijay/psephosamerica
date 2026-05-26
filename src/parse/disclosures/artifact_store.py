"""Local filesystem store for raw disclosure artifacts.

Paths derive directly from ArtifactMeta.storage_key; no cloud clients.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from src.core.path_safety import safe_join_confined
from src.parse.disclosures.acquire import ArtifactMeta


def artifact_path(root: Path, meta: ArtifactMeta) -> Path:
    return safe_join_confined(root, meta.storage_key, label="storage_key")


def write_artifact(root: Path, meta: ArtifactMeta, data: bytes) -> Path:
    dest = artifact_path(root, meta)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(dest, data)
    return dest


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(data)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def read_artifact(root: Path, meta: ArtifactMeta) -> bytes:
    dest = artifact_path(root, meta)
    if not dest.exists():
        raise FileNotFoundError(f"Artifact not found: {dest}")
    return dest.read_bytes()


def verify_artifact(root: Path, meta: ArtifactMeta, expected_sha256: str) -> bool:
    data = read_artifact(root, meta)
    actual = hashlib.sha256(data).hexdigest()
    return actual == expected_sha256
