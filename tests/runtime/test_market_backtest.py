"""Tests for the market backtest engine (parsing, costs, resolution)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.runtime.market_backtest import (
    MarketSpec,
    MatchedRollcall,
    _resolve_count_bucket,
    entry_cost,
    parse_market,
    run,
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


def test_backtest_uses_pre_vote_prices_and_receipts_every_skipped_market(tmp_path: Path) -> None:
    def write(name: str, rows: list[dict]) -> Path:
        path = tmp_path / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    records = write(
        "records.jsonl",
        [
            {
                "entity_type": "bill",
                "canonical_id": "bill",
                "external_ids": ["us_congress:119:s-1"],
            },
            {"entity_type": "person", "display_name": "Jane Doe", "external_ids": ["lis:s001"]},
        ],
    )
    votes = [["S001", "R", "MI", "yea"], ["S002", "D", "MI", "nay"]]
    corpus = write(
        "rolls.jsonl", [{"bill_id": "us_congress:119:s-1", "date": "2025-02-01", "votes": votes}]
    )
    default = {
        "question": "Will Jane Doe vote for Fixture Act?",
        "bill_ids": ["bill"],
        "venue": "kalshi",
    }
    write(
        "markets.jsonl",
        [
            {**default, "market_id": "member"},
            {
                **default,
                "market_id": "count",
                "question": "Will 0-1 Democratic senators vote in favor of Fixture Act?",
            },
            {**default, "market_id": "unparsed", "question": "Will it rain?"},
            {**default, "market_id": "unlinked", "bill_ids": []},
            {**default, "market_id": "no-price"},
            {**default, "market_id": "no-rollcall", "end_date": "2025-01-01"},
            {**default, "market_id": "late-only"},
            {
                **default,
                "market_id": "missing-member",
                "question": "Will Missing Person vote for Fixture Act?",
            },
        ],
    )
    prices = [
        {"market_id": mid, "known_at": day, "price": price}
        for mid, day, price in [
            ("member", "2025-01-29", 0.8),
            ("member", "2025-01-31", 0.1),
            ("member", "2025-02-01", 0.99),
            ("count", "2025-01-31", 0.9),
            ("no-rollcall", "2024-12-01", 0.5),
            ("late-only", "2025-02-01", 0.99),
            ("missing-member", "2025-01-31", 0.5),
        ]
    ]
    write("prices.jsonl", prices)
    report = run(tmp_path, records, corpus, sigma_common=0, sigma_party=0)
    assert report["n_scored"] == 2
    assert report["skipped"] == {
        "unparsed": 1,
        "no_bill_link": 1,
        "no_rollcall": 2,
        "no_price": 1,
        "price_after_vote": 1,
    }
    member = report["markets"][0]
    assert member["market_price"] == 0.1 and member["model_fair"] == 0.5
    assert member["resolved_yes"] is True and member["side"] == "buy_yes"
    assert member["pnl_after_costs"] == pytest.approx(1 - 0.1 - entry_cost(0.1, "kalshi"))
    # Post-vote price and unmatched future roll-calls cannot change the decision.
    prices[2]["price"] = 0.01
    write("prices.jsonl", prices)
    assert run(tmp_path, records, corpus, sigma_common=0, sigma_party=0) == report
