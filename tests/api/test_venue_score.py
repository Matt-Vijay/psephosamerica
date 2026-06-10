"""Tests for the venue-score endpoint."""

from __future__ import annotations

from src.api.venue_score import Venue, build_venue_index


def _rollcalls() -> list[dict]:
    rolls = []
    # 118th passes energy bills, fails health bills
    for i in range(10):
        rolls.append({
            "bill_id": f"us_congress:118:energy{i}", "date": f"2023-03-{1+i:02d}",
            "congress": 118, "sectors": ["energy_utilities"],
            "votes": [["A", "R", "TX", "yea"], ["B", "R", "TX", "yea"], ["C", "D", "CA", "nay"]],
        })
        rolls.append({
            "bill_id": f"us_congress:118:health{i}", "date": f"2023-04-{1+i:02d}",
            "congress": 118, "sectors": ["health"],
            "votes": [["A", "R", "TX", "nay"], ["B", "R", "TX", "nay"], ["C", "D", "CA", "yea"]],
        })
    return rolls


def test_venue_index_scores_sector_pass_rates() -> None:
    index = build_venue_index(_rollcalls())
    energy = index.score(["energy_utilities"])
    health = index.score(["health"])
    assert energy[0].p_pass > 0.7  # energy passes
    assert health[0].p_pass < 0.5  # health fails
    # citations present and cite real bill ids
    assert energy[0].citations
    assert energy[0].citations[0].bill_id.startswith("us_congress:118:energy")


def test_score_includes_interval_and_marginal_members() -> None:
    index = build_venue_index(_rollcalls())
    venue_key = Venue(jurisdiction="us_congress:118", chamber="house").key()
    scores = index.score(
        ["energy_utilities"],
        marginal_members_by_venue={venue_key: ["A000001", "B000002"]},
    )
    top = scores[0]
    assert top.interval_lower <= top.p_pass <= top.interval_upper
    assert top.marginal_members == ["A000001", "B000002"]
    payload = top.as_dict()
    assert "p_pass" in payload and "citations" in payload and "marginal_members" in payload


def test_unknown_sector_falls_back_to_overall() -> None:
    index = build_venue_index(_rollcalls())
    scores = index.score(["nonexistent_sector"])
    # no sector history -> shrinks to the venue overall rate, still returns a venue
    assert scores
    assert 0.0 <= scores[0].p_pass <= 1.0
