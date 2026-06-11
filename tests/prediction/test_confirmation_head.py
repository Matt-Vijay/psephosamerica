"""Tests for the confirmation head (synthetic nominations)."""

from __future__ import annotations

from datetime import date

from src.prediction.confirmation_head import (
    ConfirmationVote,
    evaluate_confirmation_head,
    is_confirmation_question,
    train_confirmation_head,
)


def _vote(
    member: str, party: str, *, congress: int, is_yea: bool, day: int = 1
) -> ConfirmationVote:
    return ConfirmationVote(
        member=member,
        party=party,
        congress=congress,
        vote_date=date(2021 if congress == 117 else 2023, 2, day),
        is_yea=is_yea,
    )


def test_question_filter() -> None:
    assert is_confirmation_question("On the Nomination ")
    assert not is_confirmation_question("On Cloture on the Nomination")
    assert not is_confirmation_question("On Passage of the Bill")


def test_party_match_uses_president_party() -> None:
    # 117th: D president -> D senators match; 119th: R president -> R match.
    assert _vote("S1", "D", congress=117, is_yea=True).party_match
    assert not _vote("S1", "D", congress=119, is_yea=True).party_match


def test_head_learns_party_match_and_member_residual() -> None:
    train: list[ConfirmationVote] = []
    for day in range(1, 21):
        # same-party senators confirm, opposition mostly opposes...
        train.append(_vote("D1", "D", congress=117, is_yea=True, day=day))
        train.append(_vote("R1", "R", congress=117, is_yea=False, day=day))
        # ...except R2, an institutionalist who confirms across party lines
        train.append(_vote("R2", "R", congress=117, is_yea=True, day=day))
    head = train_confirmation_head(train)
    p_match = head.probability(_vote("D1", "D", congress=117, is_yea=True))
    p_opposed = head.probability(_vote("R1", "R", congress=117, is_yea=True))
    p_crosser = head.probability(_vote("R2", "R", congress=117, is_yea=True))
    assert p_match > 0.7
    assert p_opposed < 0.4
    assert p_crosser > p_opposed + 0.2  # the member residual separates R2 from R1


def test_evaluation_beats_party_line_when_crossers_exist() -> None:
    train = [
        _vote(m, p, congress=117, is_yea=y, day=d)
        for d in range(1, 16)
        for m, p, y in [("D1", "D", True), ("R1", "R", False), ("R2", "R", True)]
    ]
    head = train_confirmation_head(train)
    # 118th keeps the same president's party as the 117th train window, so the
    # member residual transfers: R2's cross-party record beats the party line.
    eval_votes = [
        _vote("D1", "D", congress=118, is_yea=True),
        _vote("R1", "R", congress=118, is_yea=False),
        _vote("R2", "R", congress=118, is_yea=True),
    ]
    metrics = evaluate_confirmation_head(head, eval_votes)
    assert metrics["eval_votes"] == 3.0
    assert metrics["auc"] == 1.0  # head ranks D1 > R2 > R1 perfectly
    assert metrics["auc"] > metrics["auc_party_line"]  # party line can't rank R2 over R1
    assert metrics["accuracy"] >= metrics["accuracy_party_line"]
