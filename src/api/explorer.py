"""HTML renderers for the public prediction explorer and dashboard.

OVERALL_GOAL.md wants the predictions *visible*: a top-N explorer showing each
prediction with its calibrated probability, uncertainty, the cited evidence
that drove it (URL + sha256 + timestamp), the LLM explanation, and the
"what-would-change-this" counterfactual; and a calibration dashboard of Brier /
log-loss tiles per jurisdiction / party / faction with the cross-pressured slice
highlighted.

Dependency-free server-rendered HTML (no client framework). Every interpolated
value is ``html.escape``d, so source URLs, labels, and explanations cannot
inject markup.
"""

from __future__ import annotations

from html import escape

from src.prediction.calibration_dashboard import CalibrationDashboard, DashboardSlice
from src.prediction.served_prediction import PredictionEvidenceAnchor, ServedPrediction

_STYLE = (
    "body{font:14px system-ui,sans-serif;margin:2rem;color:#111}"
    "h1{font-size:1.4rem}.card{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}"
    ".prob{font-size:1.6rem;font-weight:700}.muted{color:#666}.tile{display:inline-block;"
    "border:1px solid #ddd;border-radius:6px;padding:.5rem .75rem;margin:.25rem}"
    ".hot{border-color:#c0392b}a{color:#1a5fb4}ul{margin:.4rem 0}"
)


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def _evidence_list(prediction: ServedPrediction) -> str:
    items = []
    for anchor in prediction.evidence_anchors:
        items.append(
            "<li>"
            f'<a href="{escape(anchor.source_url)}" rel="nofollow noopener">'
            f"{escape(anchor.label)}</a> "
            f'<span class="muted">(sha256 {escape(anchor.content_sha256[:12])}…, '
            f"{escape(anchor.retrieved_at.date().isoformat())}, "
            f"weight {anchor.contribution:+.3f})</span></li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def top_flip_factors(
    prediction: ServedPrediction, *, top_n: int = 3
) -> list[PredictionEvidenceAnchor]:
    """The top-N evidence anchors that, if flipped, would most change the prediction.

    Ranked by absolute contribution -- the model's signed influence of each cited
    source on the yea probability.
    """
    return sorted(
        prediction.evidence_anchors, key=lambda anchor: abs(anchor.contribution), reverse=True
    )[:top_n]


def _flip_factors_list(prediction: ServedPrediction) -> str:
    items = []
    for anchor in top_flip_factors(prediction, top_n=3):
        direction = "lowers" if anchor.contribution < 0 else "raises"
        items.append(
            f'<li><a href="{escape(anchor.source_url)}" rel="nofollow noopener">'
            f"{escape(anchor.label)}</a> — {direction} yea by "
            f"{abs(anchor.contribution):.3f}</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _prediction_card(prediction: ServedPrediction) -> str:
    uncertainty = prediction.uncertainty
    label_set = ", ".join(escape(option) for option in uncertainty.conformal_label_set)
    return (
        '<div class="card">'
        f"<div><strong>{escape(prediction.canonical_person_id)}</strong> on "
        f"<strong>{escape(prediction.canonical_bill_id)}</strong> "
        f'<span class="muted">({escape(prediction.model_name)}, '
        f"known at {escape(prediction.known_at.isoformat())})</span></div>"
        f'<div class="prob">{_percent(prediction.probability_yea)} yea</div>'
        f'<div class="muted">{int(uncertainty.confidence_level * 100)}% interval '
        f"[{_percent(uncertainty.interval_lower)}, {_percent(uncertainty.interval_upper)}]; "
        f"conformal set: {{{label_set}}}</div>"
        f"<p>{escape(prediction.llm_explanation)}</p>"
        f"<p><em>What would change this:</em> {escape(prediction.counterfactual)}</p>"
        "<div><strong>Top factors (flip to change)</strong>"
        f"{_flip_factors_list(prediction)}</div>"
        "<div><strong>Evidence</strong>"
        f"{_evidence_list(prediction)}</div>"
        "</div>"
    )


def render_explorer_html(predictions: list[ServedPrediction], *, top_n: int = 50) -> str:
    """Render the top-N predictions (most confident first) as a cited HTML page."""
    ordered = sorted(predictions, key=lambda item: abs(item.probability_yea - 0.5), reverse=True)
    cards = "".join(_prediction_card(prediction) for prediction in ordered[:top_n])
    body = cards or '<p class="muted">No predictions available yet.</p>'
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OpenPact — Prediction Explorer</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>OpenPact — Prediction Explorer</h1>"
        f'<p class="muted">Top {min(top_n, len(ordered))} of {len(predictions)} '
        "predictions, each with calibrated uncertainty and cited evidence.</p>"
        f"{body}</body></html>"
    )


def _tile(slice_: DashboardSlice) -> str:
    return (
        f'<span class="tile">{escape(slice_.key)}: '
        f"Brier {slice_.brier_score:.3f}, log-loss {slice_.log_loss:.3f} "
        f'<span class="muted">(n={slice_.sample_count})</span></span>'
    )


def _headline(name: str, slice_: DashboardSlice | None, *, hot: bool = False) -> str:
    if slice_ is None:
        return ""
    klass = "tile hot" if hot else "tile"
    return (
        f'<span class="{klass}"><strong>{escape(name)}</strong>: '
        f"Brier {slice_.brier_score:.3f}, log-loss {slice_.log_loss:.3f}</span>"
    )


def render_dashboard_html(dashboard: CalibrationDashboard) -> str:
    """Render the calibration dashboard as Brier/log-loss tiles per slice."""
    sections = []
    for dimension in ("party", "jurisdiction", "faction"):
        entries = dashboard.sections.get(dimension, [])
        if not entries:
            continue
        tiles = "".join(_tile(entry) for entry in entries)
        sections.append(f"<h2>{escape(dimension)}</h2><div>{tiles}</div>")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OpenPact — Calibration Dashboard</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"<h1>Calibration — {escape(dashboard.model_name)}</h1>"
        "<div>"
        f"{_headline('overall', dashboard.overall)}"
        f"{_headline('cross-pressured', dashboard.cross_pressured, hot=True)}"
        "</div>"
        f"{''.join(sections)}"
        "</body></html>"
    )
