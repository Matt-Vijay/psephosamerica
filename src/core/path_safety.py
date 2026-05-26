"""Small helpers for keeping manifest paths confined to local roots."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def is_confined_relative_path(path: str) -> bool:
    """Return True when *path* is a non-empty relative POSIX path without traversal."""
    if not path.strip() or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts and path not in {"", "."}


def require_confined_relative_path(path: str, *, label: str) -> str:
    """Validate *path* for use inside a root directory and return it unchanged."""
    if not is_confined_relative_path(path):
        raise ValueError(f"{label} must be a confined relative path, got {path!r}")
    return path


def safe_join_confined(root: Path, path: str, *, label: str) -> Path:
    """Join a confined relative path to root and reject symlink escapes."""
    require_confined_relative_path(path, label=label)
    resolved_root = root.resolve(strict=False)
    candidate = (root / path).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay confined to target root, got {path!r}") from exc
    return candidate
