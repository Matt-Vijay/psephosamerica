"""Tests for the defection-watch page + dashboard AUC tiles."""

from __future__ import annotations

from src.api.defection_watch import (
    DefectionAucTile,
    render_defection_auc_tiles,
    render_defection_watch_html,
)
from src.prediction.defection_head import DefectionHead, RankedDefection


def _head() -> DefectionHead:
    return DefectionHead(
        intercept=-2.0,
        coefficients={"loyalty_gap": 0.27, "sector_divergence": 0.35},
    )


def _ranked() -> list[RankedDefection]:
    return [
        RankedDefection(
            member="J000300",
            party="R",
            state="NJ",
            probability=0.42,
            features={"loyalty_gap": 0.4, "sector_divergence": 0.6},
            actual_defected=True,
        ),
        RankedDefection(
            member="G000600",
            party="D",
            state="TX",
            probability=0.18,
            features={"loyalty_gap": 0.2, "sector_divergence": 0.1},
        ),
    ]


def test_watch_page_lists_members_and_escapes() -> None:
    html = render_defection_watch_html(_ranked(), _head(), congress="119", top_n=20)
    assert "Defection Watch" in html and "Congress 119" in html
    assert "J000300" in html and "42.0%" in html
    # factor labels + signed contributions present
    assert "breaks with party on this policy area" in html
    assert "(+0." in html
    # counterfactual present
    assert "would move them off the list" in html or "drops most" in html


def test_watch_page_handles_empty() -> None:
    html = render_defection_watch_html([], _head(), congress="119")
    assert "No defection-prone members ranked" in html


def test_auc_tiles_render_metric() -> None:
    tiles = [
        DefectionAucTile(key="118", auc=0.725, sample_count=148846, positives=8567),
        DefectionAucTile(key="R", auc=0.71, sample_count=70000, positives=4000),
    ]
    fragment = render_defection_auc_tiles(tiles, title="Defection AUC by congress")
    assert "AUC 0.725" in fragment and "n=148846" in fragment
    assert render_defection_auc_tiles([], title="x") == ""
