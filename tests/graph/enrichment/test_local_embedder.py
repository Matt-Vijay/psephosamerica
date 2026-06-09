from __future__ import annotations

import math

import pytest

from src.graph.enrichment.local_embedder import LocalTextEmbedder, default_text_embedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def test_fixed_dimension() -> None:
    emb = LocalTextEmbedder(dim=128)
    vec = emb("Senator backs the energy bill")
    assert len(vec) == 128
    assert all(isinstance(x, float) for x in vec)


def test_default_embedder_dimension() -> None:
    vec = default_text_embedder()("hello world")
    assert len(vec) == default_text_embedder().dim


def test_deterministic_across_calls_and_instances() -> None:
    text = "An ordinance relating to land use and zoning"
    assert LocalTextEmbedder(dim=64)(text) == LocalTextEmbedder(dim=64)(text)


def test_unit_norm_for_nonempty_text() -> None:
    vec = LocalTextEmbedder(dim=256)("public official voting record")
    assert math.sqrt(sum(x * x for x in vec)) == pytest.approx(1.0, abs=1e-9)


def test_empty_text_is_zero_vector() -> None:
    vec = LocalTextEmbedder(dim=32)("   ")
    assert len(vec) == 32
    assert all(x == 0.0 for x in vec)


def test_distinct_texts_have_distinct_vectors() -> None:
    a = LocalTextEmbedder(dim=256)("climate and environmental policy")
    b = LocalTextEmbedder(dim=256)("defense appropriations and military")
    assert a != b
    assert _cosine(a, b) < 0.9


def test_shared_vocabulary_is_more_similar() -> None:
    emb = LocalTextEmbedder(dim=512)
    base = emb("the senator supports renewable energy and climate action")
    near = emb("the senator supports renewable energy investment")
    far = emb("a county zoning ordinance about parking permits")
    assert _cosine(base, near) > _cosine(base, far)


def test_custom_dimension() -> None:
    assert len(LocalTextEmbedder(dim=64)("x")) == 64


def test_invalid_dimension_rejected() -> None:
    with pytest.raises(ValueError, match="dim"):
        LocalTextEmbedder(dim=0)
    with pytest.raises(ValueError, match="dim"):
        LocalTextEmbedder(dim=-5)


def test_embedder_is_a_dossier_embedder_callable() -> None:
    # Must satisfy the DossierEmbedder signature: (str) -> list[float].
    emb = default_text_embedder()
    out = emb("text")
    assert isinstance(out, list)
    assert isinstance(out[0], float)
