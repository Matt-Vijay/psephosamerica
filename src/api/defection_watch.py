"""Defection-watch page + dashboard AUC tile (v4 #9).

Serves the product: for the live congress, the top-N members most likely to
break with their party on the bills in play, each with the ex-ante factors that
drove the ranking (cited, with their signed logit contribution) and a
counterfactual flip ("what would move this member off the watch-list"). Plus a
calibration-dashboard tile carrying the defection-ranking AUC per congress /
party / state, so the headline product metric is visible next to Brier/log-loss.

Dependency-free server-rendered HTML; every interpolated value is ``html.escape``d.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from src.prediction.defection_head import DefectionHead, RankedDefection

_STYLE = (
    "body{font:14px system-ui,sans-serif;margin:2rem;color:#111}"
    "h1{font-size:1.4rem}table{border-collapse:collapse;width:100%;margin:1rem 0}"
    "th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}"
    "th{background:#f6f6f6}.prob{font-weight:700;color:#c0392b}.muted{color:#666}"
    ".tile{display:inline-block;border:1px solid #ddd;border-radius:6px;"
    "padding:.5rem .75rem;margin:.25rem}.hot{border-color:#c0392b}"
)

_FACTOR_LABEL = {
    "loyalty_gap": "breaks with party overall",
    "sector_divergence": "breaks with party on this policy area",
    "rag_signal": "defected on similar past bills",
}


def _counterfactual(ranked: RankedDefection, head: DefectionHead) -> str:
    """The single ex-ante factor whose removal most lowers the defection score."""
    contributions = head.contributions(ranked.features)
    if not contributions:
        return "no ranked factors"
    top = max(contributions.items(), key=lambda item: item[1])
    name, value = top
    if value <= 0:
        return "already at the party line on every factor"
    label = _FACTOR_LABEL.get(name, name)
    return f"if this member no longer {label}, the defection score drops most"


def _factor_cells(ranked: RankedDefection, head: DefectionHead) -> str:
    contributions = head.contributions(ranked.features)
    ordered = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
    parts = [
        f"{_FACTOR_LABEL.get(name, name)} ({value:+.2f})"
        for name, value in ordered
        if abs(value) > 1e-9
    ]
    return escape("; ".join(parts)) if parts else '<span class="muted">none</span>'


def render_defection_watch_html(
    ranked: list[RankedDefection],
    head: DefectionHead,
    *,
    congress: str,
    top_n: int = 20,
) -> str:
    """Render the top-N most-likely defections for the live congress."""
    rows = []
    for rank, item in enumerate(ranked[:top_n], start=1):
        rows.append(
            "<tr>"
            f"<td>{rank}</td>"
            f"<td><strong>{escape(item.member)}</strong> "
            f'<span class="muted">({escape(item.party)}-{escape(item.state)})</span></td>'
            f'<td class="prob">{item.probability * 100:.1f}%</td>'
            f"<td>{_factor_cells(item, head)}</td>"
            f"<td>{escape(_counterfactual(item, head))}</td>"
            "</tr>"
        )
    body = (
        "".join(rows)
        or '<tr><td colspan="5" class="muted">No defection-prone members ranked.</td></tr>'
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OpenPact — Defection Watch</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"<h1>OpenPact — Defection Watch · Congress {escape(congress)}</h1>"
        '<p class="muted">Members most likely to break with their party, ranked by an '
        "ex-ante model (pre-cutoff loyalty + policy-area divergence). Factors show each "
        "driver's signed contribution; the counterfactual is what would move them off the "
        "list.</p>"
        "<table><thead><tr><th>#</th><th>Member</th><th>P(defect)</th>"
        "<th>Why (factor, contribution)</th><th>What would change it</th></tr></thead>"
        f"<tbody>{body}</tbody></table></body></html>"
    )


@dataclass(frozen=True)
class DefectionAucTile:
    """A defection-ranking AUC tile for one dashboard cell (congress/party/state)."""

    key: str
    auc: float
    sample_count: int
    positives: int


def render_defection_auc_tiles(tiles: list[DefectionAucTile], *, title: str) -> str:
    """Render AUC tiles as an HTML fragment for the calibration dashboard."""
    if not tiles:
        return ""
    cells = "".join(
        f'<span class="tile hot"><strong>{escape(tile.key)}</strong>: '
        f"AUC {tile.auc:.3f} "
        f'<span class="muted">(n={tile.sample_count}, '
        f"defections={tile.positives})</span></span>"
        for tile in tiles
    )
    return f"<h2>{escape(title)}</h2><div>{cells}</div>"
