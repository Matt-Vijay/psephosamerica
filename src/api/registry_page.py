"""Registry page: render the forward pre-registration track record (v5 #2).

Server-rendered, dependency-free HTML showing the frozen predictions, their
content hash (the tamper-evidence), and — once scored — the realized outcome and
metrics. Pending predictions render as open calls (no outcome yet); scored ones
show hit/miss. Every interpolated value is ``html.escape``d.
"""

from __future__ import annotations

from html import escape
from typing import Any


def _metric_row(metrics: dict[str, Any]) -> str:
    if not metrics:
        return ""
    parts = []
    for label, key in (("resolved", "resolved"), ("pending", "pending"), ("Brier", "brier"),
                        ("accuracy", "accuracy"), ("AUC", "auc"), ("base rate", "base_rate")):
        if key in metrics:
            val = metrics[key]
            parts.append(f'<span class="tile">{label}: {val:.3f}</span>'
                         if isinstance(val, float) and key in {"brier", "accuracy", "auc", "base_rate"}
                         else f'<span class="tile">{label}: {int(val)}</span>')
    return "<div>" + "".join(parts) + "</div>"


def _row(p: dict[str, Any]) -> str:
    actual = p.get("actual_defect")
    if actual is None:
        outcome = '<span class="muted">pending</span>'
    else:
        hit = (p["p_defect"] >= 0.5) == bool(actual)
        outcome = (
            f'<span style="color:{"#1a7f37" if hit else "#c0392b"}">'
            f'{"defected" if actual else "held"} · {"✓" if hit else "✗"}</span>'
        )
    cites = "; ".join(escape(f"{c['kind']}:{c['ref']}") for c in p.get("citations", [])[:3])
    return (
        "<tr>"
        f'<td><strong>{escape(p["member"])}</strong> '
        f'<span class="muted">({escape(p["party"])}-{escape(p["state"])})</span></td>'
        f'<td>{escape(p["bill_id"])}</td>'
        f'<td>{escape(p["target_vote_date"])}</td>'
        f'<td class="prob">{p["p_defect"] * 100:.0f}%</td>'
        f"<td>{outcome}</td>"
        f'<td class="muted">{cites}</td>'
        f'<td class="muted">{escape(p.get("counterfactual", ""))}</td>'
        "</tr>"
    )


def render_registry_html(registry: dict[str, Any], *, top_n: int = 100) -> str:
    """Render a (frozen or scored) registry dict as a track-record page."""
    preds = registry.get("predictions", [])
    rows = "".join(_row(p) for p in preds[:top_n])
    body = rows or '<tr><td colspan="7" class="muted">No predictions registered.</td></tr>'
    style = (
        "body{font:14px system-ui,sans-serif;margin:2rem;color:#111}h1{font-size:1.4rem}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #ddd;"
        "padding:.4rem .6rem;text-align:left}th{background:#f6f6f6}.prob{font-weight:700}"
        ".muted{color:#666}.tile{display:inline-block;border:1px solid #ddd;border-radius:6px;"
        "padding:.4rem .6rem;margin:.2rem}code{background:#f2f2f2;padding:.1rem .3rem}"
    )
    status = "SCORED" if registry.get("scored") else "FROZEN (pending outcomes)"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>OpenPact — Forward Prediction Registry</title>"
        f"<style>{style}</style></head><body>"
        "<h1>OpenPact — Forward Prediction Registry</h1>"
        f'<p class="muted">Defection predictions frozen at cutoff '
        f'<strong>{escape(str(registry.get("frozen_at_cutoff")))}</strong> for the window '
        f'through <strong>{escape(str(registry.get("target_window_end")))}</strong> · '
        f"status <strong>{status}</strong> · model "
        f'<code>{escape(str(registry.get("model_name")))}</code></p>'
        f'<p class="muted">Tamper-evidence — content sha256 '
        f'<code>{escape(str(registry.get("content_sha256", ""))[:32])}…</code> '
        "(frozen before outcomes; unchanged by scoring).</p>"
        f"{_metric_row(registry.get('metrics', {}))}"
        "<table><thead><tr><th>Member</th><th>Bill</th><th>Vote date</th>"
        "<th>P(defect)</th><th>Outcome</th><th>Citations</th><th>What would change it</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
        f'<p class="muted">Showing {min(top_n, len(preds))} of {len(preds)} predictions.</p>'
        "</body></html>"
    )
