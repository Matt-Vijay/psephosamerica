"""One deterministic feature space for indexed text and retrieval queries.

Word and character-trigram features use signed BLAKE2b buckets followed by L2
normalization. Changing this algorithm changes existing stored vectors.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

DEFAULT_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FeatureHashEmbedder:
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

    def __call__(self, text: str) -> list[float]:
        return self.embed(text)


def _features(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    features = list(tokens)
    for token in tokens:
        padded = f"#{token}#"
        features.extend(f"cg:{padded[i : i + 3]}" for i in range(len(padded) - 2))
    return features
