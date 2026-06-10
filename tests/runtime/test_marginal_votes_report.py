"""Tests for the weekly marginal-votes report generator."""

from __future__ import annotations

from datetime import date

from src.prediction.vote_record import VoteRecord
from src.runtime.marginal_votes_report import generate, render_html


def _vote(member: str, *, is_yea: bool, lean_yea: bool, day: int, bill: str) -> tuple[VoteRecord, str]:
    return (
        VoteRecord(
            member=member, party="R", state="TX",
            vote_date=date(2025, 1, 1) + (date(2025, 12, 31) - date(2025, 1, 1)) * day // 100,
            is_yea=is_yea, party_alignment=1.0 if lean_yea else -1.0, sectors=(),
            is_cross_pressured=is_yea != lean_yea,
        ),
        bill,
    )


def _votes() -> list[tuple[VoteRecord, str]]:
    out = []
    for day in range(100):
        out.append(_vote("D", is_yea=True, lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
        out.append(_vote("L1", is_yea=False, lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
        out.append(_vote("L2", is_yea=False, lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
    return out


def test_generate_ranks_top_n_with_evidence() -> None:
    report = generate(_votes(), cutoff=date(2025, 9, 30), target_end=date(2025, 12, 31), top_n=10)
    mv = report["marginal_votes"]
    assert len(mv) <= 10 and mv
    # ranked descending by P(defect)
    assert mv[0]["p_defect"] >= mv[-1]["p_defect"]
    # known defector D should top the list
    assert mv[0]["member"] == "D"
    # each row carries factors, a counterfactual, citations, venue P(pass)
    top = mv[0]
    assert top["factors"] and top["counterfactual"] and top["citations"]
    assert 0.0 <= top["venue_p_pass"] <= 1.0


def test_render_html_escapes_and_lists() -> None:
    report = generate(_votes(), cutoff=date(2025, 9, 30), target_end=date(2025, 12, 31), top_n=5)
    html = render_html(report)
    assert "Marginal-Votes Brief" in html
    assert "P(defect)" in html and "Venue P(pass)" in html
    assert "us_congress:119:hr-" in html
