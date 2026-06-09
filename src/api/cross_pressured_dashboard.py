"""Side-by-side overall-vs-cross-pressured dashboard.

Renders the headline contrast the experiment exposed: the model is strong overall
but fails on the cross-pressured slice. Given a multi-window report
(``window -> slice -> metrics``), this builds a table per congress with overall
and cross-pressured Brier/accuracy side by side, plus per-party and per-state
overall rows -- so the gap (the votes worth predicting) is visible at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any


@dataclass(frozen=True)
class SideBySideRow:
    label: str
    overall_brier: float | None
    overall_accuracy: float | None
    cross_brier: float | None
    cross_accuracy: float | None


def _metric(slices: dict[str, Any], name: str, field: str) -> float | None:
    entry = slices.get(name)
    return None if entry is None else float(entry[field])


def build_side_by_side(report: dict[str, Any]) -> list[SideBySideRow]:
    """One row per congress window: overall vs cross-pressured Brier/accuracy."""
    rows: list[SideBySideRow] = []
    for window, slices in report.get("windows", {}).items():
        rows.append(
            SideBySideRow(
                label=window,
                overall_brier=_metric(slices, "all", "brier_score"),
                overall_accuracy=_metric(slices, "all", "accuracy"),
                cross_brier=_metric(slices, "cross_pressured", "brier_score"),
                cross_accuracy=_metric(slices, "cross_pressured", "accuracy"),
            )
        )
    return rows


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_side_by_side_html(
    report: dict[str, Any], *, title: str = "Overall vs cross-pressured"
) -> str:
    """Render the side-by-side table (congress rows; overall vs cross-pressured columns)."""
    body_rows = "".join(
        f"<tr><td>{escape(row.label)}</td>"
        f"<td>{_fmt(row.overall_accuracy)}</td><td>{_fmt(row.overall_brier)}</td>"
        f"<td>{_fmt(row.cross_accuracy)}</td><td>{_fmt(row.cross_brier)}</td></tr>"
        for row in build_side_by_side(report)
    )
    style = (
        "body{font:14px system-ui,sans-serif;margin:2rem}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:.4rem .7rem;text-align:right}td:first-child,"
        "th:first-child{text-align:left}.cp{background:#fdecea}"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>{style}</style></head><body>"
        f"<h1>{escape(title)}</h1>"
        "<table><thead><tr><th>congress</th>"
        "<th>overall acc</th><th>overall Brier</th>"
        "<th class='cp'>cross-pressured acc</th><th class='cp'>cross-pressured Brier</th>"
        "</tr></thead><tbody>"
        f"{body_rows}</tbody></table>"
        "<p>The cross-pressured columns are the votes worth predicting; the gap to "
        "the overall columns is the open problem.</p>"
        "</body></html>"
    )
