"""Tests for the forward pre-registration registry."""

from __future__ import annotations

from datetime import date

from src.prediction.prediction_registry import (
    content_hash,
    freeze_registry,
    score_registry,
)
from src.prediction.vote_record import VoteRecord


def _vote(member: str, *, is_yea: bool, party_lean_yea: bool, day: int, bill: str) -> tuple[VoteRecord, str]:
    return (
        VoteRecord(
            member=member,
            party="R",
            state="TX",
            vote_date=date(2025, 1, 1) + (date(2025, 12, 31) - date(2025, 1, 1)) * day // 100,
            is_yea=is_yea,
            party_alignment=1.0 if party_lean_yea else -1.0,
            sectors=(),
            is_cross_pressured=is_yea != party_lean_yea,
        ),
        bill,
    )


def _votes() -> list[tuple[VoteRecord, str]]:
    out = []
    for day in range(0, 100):
        # defector D breaks party; loyalists L1/L2 don't
        out.append(_vote("D", is_yea=True, party_lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
        out.append(_vote("L1", is_yea=False, party_lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
        out.append(_vote("L2", is_yea=False, party_lean_yea=False, day=day, bill=f"us_congress:119:hr-{day}"))
    return out


def test_freeze_is_strict_cutoff_and_hashed() -> None:
    votes = _votes()
    reg = freeze_registry(votes, cutoff=date(2025, 9, 30), target_end=date(2025, 12, 31), max_predictions=60)
    assert len(reg.predictions) >= 50
    # all targets are strictly after the cutoff
    assert all(p.target_vote_date > "2025-09-30" for p in reg.predictions)
    assert reg.scored is False
    # content hash is deterministic and matches a recompute
    assert reg.content_sha256 == content_hash(
        reg.predictions, cutoff=reg.frozen_at_cutoff, target_end=reg.target_window_end
    )
    # every prediction carries citations + a counterfactual
    assert all(p.citations and p.counterfactual for p in reg.predictions)


def test_score_fills_actuals_and_metrics() -> None:
    votes = _votes()
    reg = freeze_registry(votes, cutoff=date(2025, 9, 30), target_end=date(2025, 12, 31), max_predictions=60)
    scored = score_registry(reg, votes)
    assert scored.scored is True
    assert scored.metrics["resolved"] > 0
    assert "brier" in scored.metrics and "auc" in scored.metrics
    # the frozen hash is preserved through scoring (predictions not edited)
    assert scored.content_sha256 == reg.content_sha256
    assert any(p.actual_defect is not None for p in scored.predictions)


def test_hash_excludes_actuals() -> None:
    # filling actuals must not change the frozen content hash
    votes = _votes()
    reg = freeze_registry(votes, cutoff=date(2025, 9, 30), target_end=date(2025, 12, 31), max_predictions=55)
    scored = score_registry(reg, votes)
    assert content_hash(scored.predictions, cutoff=reg.frozen_at_cutoff, target_end=reg.target_window_end) == reg.content_sha256
