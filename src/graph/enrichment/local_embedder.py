"""A deterministic, dependency-light local text embedder.

The output contract carries ``dossier_embedding`` and ``structural_embedding``;
populating them must not require a paid API or a heavyweight model in CI. This
module ships a **feature-hashing embedder** (the "hashing trick"): it hashes word
and character-trigram features of the text into a fixed-dimension vector with
signed buckets, then L2-normalizes. It is:

* **deterministic** — uses BLAKE2b (stable across processes), not Python's
  salted ``hash()``; the same text always yields the same vector;
* **local & free** — pure Python + numpy (already a dependency), no network,
  no model download, fast enough to run in CI;
* **repo-hostable** — the model *is* the code.

It is a real, swappable embedding behind the :data:`~src.graph.enrichment.\
enrich.DossierEmbedder` boundary: a production deployment can drop in a
sentence-transformers MiniLM (or any model) without changing the contract or
the emission path. Semantic quality is lower than a learned model, but the field
is genuinely populated, fixed-dimension, and cosine-comparable — enough to
unblock Track B's integration today.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

_DEFAULT_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class LocalTextEmbedder:
    """A deterministic feature-hashing text embedder (``(str) -> list[float]``)."""

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim must be a positive integer")
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
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


def default_text_embedder() -> LocalTextEmbedder:
    """The default local embedder used to populate the contract's embedding fields."""
    return LocalTextEmbedder(dim=_DEFAULT_DIM)


def _features(text: str) -> list[str]:
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    features = list(tokens)
    for token in tokens:
        padded = f"#{token}#"
        features.extend(f"cg:{padded[i : i + 3]}" for i in range(len(padded) - 2))
    return features
