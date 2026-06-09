"""Content-addressed model checkpoint pinning.

Serializes a fitted model (any pydantic ``BaseModel`` — e.g. ``PerMemberModel``)
to a sha256-addressed path under a checkpoint root, mirroring the repo's
immutable content-address discipline (``sha256/<aa>/<bb>/<full>.json``). Pinning
is reproducible: identical model parameters serialize to identical bytes and
therefore the same path, so a checkpoint is verifiable and a re-pin is a no-op.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel


def _canonical_bytes(model: BaseModel) -> bytes:
    # Stable, sorted serialization so the content hash is reproducible.
    import json

    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def content_address(model: BaseModel) -> str:
    """The sha256 hex of the model's canonical serialization."""
    return hashlib.sha256(_canonical_bytes(model)).hexdigest()


def checkpoint_path(root: Path, sha256: str) -> Path:
    """The sharded content-addressed path for a checkpoint."""
    return root / "sha256" / sha256[:2] / sha256[2:4] / f"{sha256}.json"


def pin_checkpoint(model: BaseModel, *, root: Path) -> Path:
    """Write the model to its content-addressed path; return the path.

    Idempotent: re-pinning identical parameters writes the same bytes to the
    same path.
    """
    payload = _canonical_bytes(model)
    sha256 = hashlib.sha256(payload).hexdigest()
    path = checkpoint_path(root, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def verify_checkpoint(path: Path) -> bool:
    """True if the file's content hash matches its content-addressed filename."""
    if not path.exists():
        return False
    expected = path.stem
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected
