"""Tests for the forward registry HTML page."""

from __future__ import annotations

from src.api.registry_page import render_registry_html


def _registry(scored: bool) -> dict:
    return {
        "frozen_at_cutoff": "2025-09-30",
        "target_window_end": "2025-12-18",
        "model_name": "defection_head_logreg",
        "content_sha256": "b807e94ab80f2d5f" + "0" * 48,
        "scored": scored,
        "metrics": {
            "resolved": 200,
            "pending": 0,
            "brier": 0.21,
            "accuracy": 0.70,
            "auc": 0.71,
            "base_rate": 0.14,
        }
        if scored
        else {},
        "predictions": [
            {
                "member": "J000300",
                "party": "R",
                "state": "NJ",
                "bill_id": "us_congress:119:hr-1",
                "target_vote_date": "2025-10-15",
                "p_defect": 0.62,
                "predicted_defect": True,
                "counterfactual": "if loyalty were neutralised, P(defect) drops most",
                "citations": [
                    {"kind": "rollcall_bill", "ref": "us_congress:119:hr-1", "detail": "bill"}
                ],
                "actual_defect": True if scored else None,
            }
        ],
    }


def test_frozen_page_shows_pending_and_hash() -> None:
    html = render_registry_html(_registry(scored=False))
    assert "Forward Prediction Registry" in html
    assert "FROZEN (pending outcomes)" in html
    assert "b807e94ab80f2d5f" in html
    assert "pending" in html
    assert "J000300" in html and "62%" in html


def test_scored_page_shows_outcome_and_metrics() -> None:
    html = render_registry_html(_registry(scored=True))
    assert "SCORED" in html
    assert "AUC: 0.710" in html
    assert "defected" in html and "✓" in html
