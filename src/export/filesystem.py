from __future__ import annotations

import json
from pathlib import Path
from pathlib import PurePosixPath

from .builders import sha256_hex
from .manifest import SnapshotManifest
from .writer import PlannedFile


def _ensure_parents(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)


def _is_confined(path: str) -> bool:
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def _validate_planned_files(files: list[PlannedFile]) -> None:
    seen_paths: set[str] = set()
    duplicate_paths: set[str] = set()

    for planned in files:
        if not _is_confined(planned.path):
            raise ValueError(f"planned file path must stay confined to target root: {planned.path!r}")
        if planned.path in seen_paths:
            duplicate_paths.add(planned.path)
        seen_paths.add(planned.path)

    if duplicate_paths:
        duplicates = ", ".join(sorted(duplicate_paths))
        raise ValueError(f"duplicate planned file paths are not allowed: {duplicates}")


def write_planned_files(files: list[PlannedFile], target_dir: Path) -> None:
    _validate_planned_files(files)
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
    seen_paths: set[str] = set()
    for planned in files:
        if not _is_confined(planned.path) or planned.path in seen_paths:
            failures.append(planned.path)
            continue
        seen_paths.add(planned.path)
        dest = target_dir / planned.path
        if not dest.exists():
            failures.append(planned.path)
            continue
        actual_hash = sha256_hex(dest.read_bytes())
        if actual_hash != planned.sha256:
            failures.append(planned.path)
    return failures
