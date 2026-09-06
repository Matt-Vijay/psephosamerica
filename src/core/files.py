"""Small filesystem primitives; callers own serialization and parent creation.

Atomic replacement prevents partial visible files. It is not a durability/fsync
guarantee or an immutable-object store: those source-specific policies stay with
their callers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the whole artifact into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _atomic_path(path: Path) -> Iterator[Path]:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        yield temporary
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_bytes_atomic(path: Path, content: bytes) -> None:
    with _atomic_path(path) as temporary:
        temporary.write_bytes(content)


def write_text_atomic(path: Path, text: str) -> None:
    with _atomic_path(path) as temporary:
        temporary.write_text(text, encoding="utf-8")
