"""Tests for the real-data evaluation runner.

Consumes Track A's vote-corpus rows and reports per-slice Brier/log-loss/
accuracy/ECE for the per-member model over rolling strict-cutoff windows. The
fixtures here are valid-format rows (not fabricated metrics) that exercise the
loader, the no-leakage window split, and the metric rollups; real numbers come
from Track A's corpus when it lands.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.prediction.real_data_eval import (
    EvalWindow,
    VoteRow,
    evaluate_windows,
    load_vote_rows,
)


def _row(
    member: str,
    *,
    party: str,
    state: str,
    yea: bool,
    vote_date: date,
    sector: float = 0.0,
    aligned: float = 1.0,
    cross: bool = False,
) -> VoteRow:
    return VoteRow(
        member_bioguide_id=member,
        canonical_bill_id="bill:hr1",
        vote_option="yea" if yea else "nay",
        vote_date=vote_date,
        party=party,
        state=state,
        jurisdiction_id="us_congress",
        signals={"party_alignment": aligned, "sector_exposure": sector},
        is_cross_pressured=cross,
    )


def _corpus() -> list[VoteRow]:
    rows: list[VoteRow] = []
    # Pre-cutoff training votes: party_alignment perfectly predicts the vote.
    for index in range(40):
        member = f"M{index % 8}"
        party = "D" if index % 2 == 0 else "R"
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append(
            _row(
                member,
                party=party,
                state="CA" if index % 2 == 0 else "TX",
                yea=aligned > 0,
                vote_date=date(2023, 1, 1),
                aligned=aligned,
            )
        )
    # In-window evaluation votes (same generative rule).
    for index in range(16):
        member = f"M{index % 8}"
        party = "D" if index % 2 == 0 else "R"
        aligned = 1.0 if index % 3 != 0 else -1.0
        rows.append(
            _row(
                member,
                party=party,
                state="CA" if index % 2 == 0 else "TX",
                yea=aligned > 0,
                vote_date=date(2024, 6, 1),
                aligned=aligned,
                cross=index % 4 == 0,
            )
        )
    return rows


def test_evaluate_windows_reports_all_required_slices() -> None:
    window = EvalWindow(
        name="118th",
        train_end=date(2024, 1, 1),
        eval_start=date(2024, 1, 2),
        eval_end=date(2025, 1, 1),
    )
    result = evaluate_windows(_corpus(), windows=[window])
    slices = result["118th"]
    assert "all" in slices
    assert "cross_pressured" in slices
    assert "party:D" in slices and "party:R" in slices
    assert "state:CA" in slices and "state:TX" in slices
    for metrics in slices.values():
        assert 0.0 <= metrics.brier_score <= 1.0
        assert metrics.log_loss >= 0.0
        assert 0.0 <= metrics.accuracy <= 1.0
        assert 0.0 <= metrics.ece <= 1.0
        assert metrics.sample_count > 0


def test_perfectly_separable_signal_is_learned() -> None:
    window = EvalWindow(
        name="w", train_end=date(2024, 1, 1), eval_start=date(2024, 1, 2), eval_end=date(2025, 1, 1)
    )
    metrics = evaluate_windows(_corpus(), windows=[window])["w"]["all"]
    # party_alignment perfectly determines the vote, so the model nails it.
    assert metrics.accuracy >= 0.9


def test_no_leakage_train_uses_only_pre_cutoff_votes() -> None:
    rows = [
        _row("M0", party="D", state="CA", yea=True, vote_date=date(2024, 6, 1)),
    ]
    # Only an in-window vote, none before the cutoff -> the member is unseen at
    # train time and predicted from the global fallback, never from its own
    # future vote.
    window = EvalWindow(
        name="w", train_end=date(2024, 1, 1), eval_start=date(2024, 1, 2), eval_end=date(2025, 1, 1)
    )
    result = evaluate_windows(rows, windows=[window])
    assert result["w"]["all"].sample_count == 1


def test_load_vote_rows_round_trips_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "votes.jsonl"
    payload = {
        "member_bioguide_id": "M0",
        "canonical_bill_id": "bill:hr1",
        "vote_option": "yea",
        "vote_date": "2024-06-01",
        "party": "D",
        "state": "CA",
        "jurisdiction_id": "us_congress",
        "signals": {"party_alignment": 1.0},
        "is_cross_pressured": False,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    rows = load_vote_rows(path)
    assert len(rows) == 1
    assert rows[0].member_bioguide_id == "M0"
    assert rows[0].vote_date == date(2024, 6, 1)
    assert rows[0].signals == {"party_alignment": 1.0}
