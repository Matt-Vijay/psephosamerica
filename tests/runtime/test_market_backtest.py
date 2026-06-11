"""Tests for the market backtest engine (parsing, costs, resolution)."""

from __future__ import annotations

from datetime import date

from src.runtime.market_backtest import (
    MarketSpec,
    MatchedRollcall,
    _resolve_count_bucket,
    entry_cost,
    parse_market,
    title_match_keys,
)


def test_parse_senate_count_bucket() -> None:
    spec = parse_market("between 60 to 100 members of the Senate vote for S.J.Res.49 - A joint")
    assert spec == MarketSpec("count_bucket", 60, 100, None, None)


def test_parse_democratic_buckets() -> None:
    assert parse_market("Will 8-11 Democratic senators vote in favor of the X?") == MarketSpec(
        "count_bucket", 8, 11, "D", None
    )
    assert parse_market("Will less than 8 Democratic senators vote in favor of X?") == MarketSpec(
        "count_bucket", 0, 7, "D", None
    )
    assert parse_market("Will 24 or more Democratic senators vote in favor of X?") == MarketSpec(
        "count_bucket", 24, 10_000, "D", None
    )


def test_parse_member_vote() -> None:
    spec = parse_market("Will Markwayne Mullin vote for a motion to invoke cloture?")
    assert spec is not None
    assert spec.kind == "member_vote"
    assert spec.member_name == "Markwayne Mullin"


def test_parse_unrelated_returns_none() -> None:
    assert parse_market("Will the DHS funding bill become law before May 1?") is None


def test_entry_cost_kalshi_includes_fee() -> None:
    assert entry_cost(0.5, "kalshi") > entry_cost(0.5, "polymarket") == 0.01


def test_resolve_count_bucket_party_filtered() -> None:
    rc = MatchedRollcall(
        vote_date=date(2025, 1, 20),
        votes=[
            ["S1", "D", "CA", "yea"],
            ["S2", "D", "NY", "yea"],
            ["S3", "D", "MA", "nay"],
            ["S4", "R", "TX", "yea"],
        ],
    )
    assert _resolve_count_bucket(MarketSpec("count_bucket", 2, 3, "D", None), rc)
    assert not _resolve_count_bucket(MarketSpec("count_bucket", 3, 5, "D", None), rc)
    assert _resolve_count_bucket(MarketSpec("count_bucket", 3, 3, None, None), rc)


def test_title_match_prefers_longest_title() -> None:
    index = [("laken riley act", "119:s:5"), ("riley act", "119:hr:999")]
    assert title_match_keys(
        "Will 8-11 Democratic senators vote in favor of the Laken Riley Act?", index
    ) == ["119:s:5"]
    assert title_match_keys("Will the Foo Act pass?", index) == []
