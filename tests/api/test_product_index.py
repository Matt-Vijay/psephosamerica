"""Tests for the marginal-vote product index."""

from __future__ import annotations

from src.api.product_index import ServedArtifact, render_product_index


def test_artifact_hashes_content() -> None:
    a = ServedArtifact.of(title="Defection Watch", href="defection_watch.html", description="who breaks ranks", content="<html>x</html>")
    b = ServedArtifact.of(title="Defection Watch", href="defection_watch.html", description="who breaks ranks", content="<html>x</html>")
    c = ServedArtifact.of(title="Defection Watch", href="defection_watch.html", description="who breaks ranks", content="<html>y</html>")
    assert a.content_sha256 == b.content_sha256
    assert a.content_sha256 != c.content_sha256


def test_index_lists_and_escapes() -> None:
    arts = [
        ServedArtifact.of(title="Defection Watch", href="defection_watch.html", description="who breaks ranks", content="a"),
        ServedArtifact.of(title="Forward Registry", href="prediction_registry.html", description="track record", content="b"),
    ]
    html = render_product_index(arts)
    assert "Marginal-Vote Product" in html
    assert "Defection Watch" in html and "Forward Registry" in html
    assert "defection_watch.html" in html
    assert "sha256" in html


def test_empty_index() -> None:
    assert "No artifacts" in render_product_index([])
