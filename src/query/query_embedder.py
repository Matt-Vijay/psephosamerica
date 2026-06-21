"""Deterministic feature-hashing text embedder for query-time retrieval.

The Track A contract populates ``dossier_embedding`` with a deterministic
feature-hashing embedder (the "hashing trick", BLAKE2b buckets, L2-normalized,
256-d). For GraphRAG cosine retrieval to be meaningful, the **question** must
be embedded into the *same* space with the *same* algorithm — otherwise the
query vector and the stored vectors aren't comparable.

This module is a self-contained, byte-compatible reimplementation living inside
the query domain (so the reasoning layer doesn't take a hard import dependency
on the ``src/graph`` enrichment package, which a parallel agent owns). It must
stay algorithmically identical to the contract's embedder; a parity test pins
that. Numpy-only, no network, no model download.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

DEFAULT_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _features(text: str) -> list[str]:
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    features = list(tokens)
    for token in tokens:
        padded = f"#{token}#"
        features.extend(f"cg:{padded[i : i + 3]}" for i in range(len(padded) - 2))
    return features


class QueryEmbedder:
    """Deterministic feature-hashing embedder (matches the contract's vectors)."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim must be a positive integer")
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = np.zeros(self.dim, dtype=np.float64)
        for feature in _features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dim
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector = vector / norm
        return [float(value) for value in vector]

    # Convenience alias so callers can use it as a plain callable too.
    def __call__(self, text: str) -> list[float]:
        return self.embed(text)


__all__ = ["QueryEmbedder", "DEFAULT_DIM"]
