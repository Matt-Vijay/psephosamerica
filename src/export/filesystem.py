from __future__ import annotations

import json
from pathlib import Path

from .builders import sha256_hex
from .manifest import SnapshotManifest
from .writer import PlannedFile


def _ensure_parents(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)


def write_planned_files(files: list[PlannedFile], target_dir: Path) -> None:
    # Parent directories are created as needed.  Existing files are overwritten.
    for planned in files:
        dest = target_dir / planned.path
        _ensure_parents(dest)
        dest.write_bytes(planned.content)


def read_manifest(manifest_file: Path) -> SnapshotManifest:
    raw = manifest_file.read_bytes()
    data = json.loads(raw)
    return SnapshotManifest.model_validate(data)


def verify_written_files(
    files: list[PlannedFile],
    target_dir: Path,
) -> list[str]:
    # Returns relative paths of files whose on-disk hash mismatches or are missing.
    failures: list[str] = []
    for planned in files:
        dest = target_dir / planned.path
        if not dest.exists():
            failures.append(planned.path)
            continue
        actual_hash = sha256_hex(dest.read_bytes())
        if actual_hash != planned.sha256:
            failures.append(planned.path)
    return failures
