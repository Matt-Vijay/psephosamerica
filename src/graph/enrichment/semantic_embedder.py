"""Dense semantic embedder (sentence-transformers all-MiniLM-L6-v2, CPU).

The in-repo :class:`~src.graph.enrichment.local_embedder.LocalTextEmbedder` is a
deterministic feature-hash -- great for reproducibility, blind to meaning (two
paraphrases land far apart). This wraps a real sentence-transformers bi-encoder
to emit a 384-d **semantic** embedding that is published *alongside* the hash one
(dual-emit), so Track B can A/B them. torch lives ONLY here, in the enrichment
layer; the output is a plain ``list[float]`` so the contract and Track B stay
numpy-only.

The model is loaded lazily (heavy import) and is injectable for tests. The model
name + library versions are captured by :meth:`SemanticEmbedder.fingerprint` so a
deterministic-replay manifest can pin exactly what produced a vector.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

SEMANTIC_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
SEMANTIC_DIM = 384


class _Encoder(Protocol):
    """The slice of the SentenceTransformer API we use (injectable for tests)."""

    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


class SemanticEmbedder:
    """Embed text to an L2-normalized 384-d semantic vector (CPU, batched)."""

    def __init__(
        self,
        *,
        model_name: str = SEMANTIC_MODEL,
        device: str = "cpu",
        encoder: _Encoder | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._encoder = encoder

    def _ensure(self) -> _Encoder:
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer

            self._encoder = SentenceTransformer(self._model_name, device=self._device)
        return self._encoder

    def embed_batch(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
        """Embed a batch of texts; empty input returns an empty list."""
        items = list(texts)
        if not items:
            return []
        array = self._ensure().encode(
            items,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in array]

    def embed(self, text: str) -> list[float]:
        """Embed a single text to a 384-d vector."""
        return self.embed_batch([text])[0]

    def fingerprint(self) -> dict[str, Any]:
        """Model + library versions, for pinning deterministic replay."""
        import sentence_transformers
        import torch

        return {
            "model_name": self._model_name,
            "device": self._device,
            "dim": SEMANTIC_DIM,
            "sentence_transformers": sentence_transformers.__version__,
            "torch": torch.__version__,
        }
