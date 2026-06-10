from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from src.graph.enrichment.semantic_embedder import (
    SEMANTIC_DIM,
    SEMANTIC_MODEL,
    SemanticEmbedder,
)


class _FakeEncoder:
    """A deterministic stand-in for SentenceTransformer.encode (no torch/model)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def encode(self, sentences: list[str], **kwargs: Any) -> Any:
        self.calls.append({"n": len(sentences), **kwargs})
        # Map each text to a deterministic unit vector by char-sum seeding.
        rows = []
        for text in sentences:
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            v = rng.standard_normal(SEMANTIC_DIM)
            rows.append(v / (np.linalg.norm(v) or 1.0))
        return np.array(rows)


def test_embed_batch_returns_plain_float_lists() -> None:
    emb = SemanticEmbedder(encoder=_FakeEncoder())
    out = emb.embed_batch(["a health bill", "an energy bill"])
    assert len(out) == 2
    assert all(len(row) == SEMANTIC_DIM for row in out)
    assert all(isinstance(x, float) for row in out for x in row)


def test_embed_single() -> None:
    emb = SemanticEmbedder(encoder=_FakeEncoder())
    vec = emb.embed("a bill")
    assert len(vec) == SEMANTIC_DIM


def test_embed_batch_empty_is_empty() -> None:
    assert SemanticEmbedder(encoder=_FakeEncoder()).embed_batch([]) == []


def test_distinct_texts_get_distinct_vectors() -> None:
    emb = SemanticEmbedder(encoder=_FakeEncoder())
    a, b = emb.embed_batch(["health care reform", "petroleum reserve drawdown"])
    assert a != b


def test_encode_called_with_normalize_and_no_progress() -> None:
    fake = _FakeEncoder()
    SemanticEmbedder(encoder=fake).embed_batch(["x"], batch_size=8)
    assert fake.calls[0]["normalize_embeddings"] is True
    assert fake.calls[0]["show_progress_bar"] is False
    assert fake.calls[0]["batch_size"] == 8


def test_fingerprint_pins_model_and_versions() -> None:
    fp = SemanticEmbedder(encoder=_FakeEncoder()).fingerprint()
    assert fp["model_name"] == SEMANTIC_MODEL
    assert fp["dim"] == SEMANTIC_DIM
    assert fp["device"] == "cpu"
    assert isinstance(fp["sentence_transformers"], str)
    assert isinstance(fp["torch"], str)


def test_lazy_loads_model_class_when_no_encoder_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cover the lazy real-model load path without downloading: patch the
    # SentenceTransformer constructor the embedder imports inside _ensure.
    import sentence_transformers

    captured: dict[str, Any] = {}

    def _fake_ctor(name: str, device: str | None = None) -> _FakeEncoder:
        captured["name"], captured["device"] = name, device
        return _FakeEncoder()

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _fake_ctor)
    emb = SemanticEmbedder()  # no encoder -> lazy-loads via the patched ctor
    out = emb.embed("an energy bill")
    assert len(out) == SEMANTIC_DIM
    assert captured["name"] == SEMANTIC_MODEL and captured["device"] == "cpu"
