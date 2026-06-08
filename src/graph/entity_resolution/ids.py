"""Deterministic, content-addressed ID minting for graph entities.

The same scheme the member-facing IDs in ``src/identity/public_ids.py`` use
(BLAKE2b digest -> unpadded lowercase base32, type-prefixed), kept here so every
entity-resolution artifact — source records, canonical entities — derives a
stable ID the same way and a later store can dedupe on it.
"""

from __future__ import annotations

import base64
import hashlib

_DIGEST_SIZE = 10  # 80 bits — ample for non-adversarial collision resistance


def stable_id(parts: list[str], prefix: str) -> str:
    """Return ``<prefix>-<base32(blake2b(parts joined by '|'))>``."""
    payload = "|".join(parts).encode("utf-8")
    raw = hashlib.blake2b(payload, digest_size=_DIGEST_SIZE).digest()
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"{prefix}-{encoded}"
