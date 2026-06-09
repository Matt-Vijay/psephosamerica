"""Tests for the overall-vs-cross-pressured side-by-side dashboard."""

from __future__ import annotations

from src.api.cross_pressured_dashboard import build_side_by_side, render_side_by_side_html

_REPORT = {
    "windows": {
        "117th": {
            "all": {"brier_score": 0.040, "accuracy": 0.960},
            "cross_pressured": {"brier_score": 0.786, "accuracy": 0.000},
        },
        "118th-h2": {
            "all": {"brier_score": 0.065, "accuracy": 0.928},
            "cross_pressured": {"brier_score": 0.794, "accuracy": 0.003},
        },
    }
}


def test_build_side_by_side_pairs_overall_and_cross_pressured() -> None:
    rows = {row.label: row for row in build_side_by_side(_REPORT)}
    assert rows["117th"].overall_accuracy == 0.960
    assert rows["117th"].cross_accuracy == 0.000
    assert rows["118th-h2"].cross_brier == 0.794


def test_render_side_by_side_html_has_both_columns() -> None:
    html = render_side_by_side_html(_REPORT)
    assert "overall acc" in html
    assert "cross-pressured acc" in html
    assert "117th" in html and "118th-h2" in html
    assert "0.960" in html and "0.000" in html


def test_handles_missing_cross_pressured_slice() -> None:
    rows = build_side_by_side({"windows": {"x": {"all": {"brier_score": 0.1, "accuracy": 0.9}}}})
    assert rows[0].cross_accuracy is None
    assert "—" in render_side_by_side_html(
        {"windows": {"x": {"all": {"brier_score": 0.1, "accuracy": 0.9}}}}
    )
