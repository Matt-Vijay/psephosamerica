from __future__ import annotations

from pathlib import Path

from src.graph.coverage import COVERAGE, render_markdown

_DOC = Path(__file__).resolve().parents[2] / "docs" / "coverage-snapshot.md"


def test_snapshot_doc_matches_registry() -> None:
    """The committed coverage snapshot must equal the registry render (pin)."""
    assert _DOC.read_text(encoding="utf-8") == render_markdown(), (
        "docs/coverage-snapshot.md is stale; regenerate with "
        '`python -c "from src.graph.coverage import render_markdown; '
        "open('docs/coverage-snapshot.md','w').write(render_markdown())\"`"
    )


def test_every_tier_is_represented() -> None:
    tiers = {entry.tier for entry in COVERAGE}
    assert tiers == {"federal", "state", "county", "city", "cross-cutting", "enrichment"}


def test_counts_are_nonblank() -> None:
    assert all(entry.demonstrated.strip() for entry in COVERAGE)
    assert all(entry.source.strip() and entry.produces.strip() for entry in COVERAGE)


def test_render_lists_every_source() -> None:
    rendered = render_markdown()
    for entry in COVERAGE:
        assert entry.source in rendered
    assert f"**{len(COVERAGE)} sources**" in rendered
