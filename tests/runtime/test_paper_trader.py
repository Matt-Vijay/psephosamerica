"""Tests for the paper trader (Kelly sizing, hashing, scoring)."""

from __future__ import annotations

import json
from pathlib import Path

from src.runtime.paper_trader import _content_hash, _kelly_stake, score


def test_kelly_side_and_size() -> None:
    side, stake = _kelly_stake(0.8, 0.5)
    assert side == "buy_yes"
    assert stake == round(0.25 * ((0.8 - 0.5) / 0.5) * 100.0, 2)
    side, stake = _kelly_stake(0.2, 0.5)
    assert side == "buy_no"
    assert stake > 0.0


def test_content_hash_is_order_insensitive_within_rows() -> None:
    a = [{"x": 1, "y": 2}]
    b = [{"y": 2, "x": 1}]
    assert _content_hash(a) == _content_hash(b)
    assert _content_hash(a) != _content_hash([{"x": 1, "y": 3}])


def test_score_only_counts_resolved_markets(tmp_path: Path) -> None:
    registry = {
        "content_sha256": "abc",
        "markets": [
            {"market_id": "m1", "fair_value": 0.9, "market_price": 0.5},
            {"market_id": "m2", "fair_value": 0.1, "market_price": 0.5},
            {"market_id": "m3", "fair_value": 0.5, "market_price": 0.5},
        ],
    }
    reg_path = tmp_path / "reg.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    markets_dir = tmp_path
    (markets_dir / "markets.jsonl").write_text(
        "\n".join(
            json.dumps(m)
            for m in [
                {"market_id": "m1", "closed": True},
                {"market_id": "m2", "closed": True},
                {"market_id": "m3", "closed": False},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (markets_dir / "prices.jsonl").write_text(
        "\n".join(
            json.dumps(p)
            for p in [
                {"market_id": "m1", "outcome": "Yes", "known_at": "2026-01-01", "price": 0.99},
                {"market_id": "m2", "outcome": "Yes", "known_at": "2026-01-01", "price": 0.5},
                {"market_id": "m3", "outcome": "Yes", "known_at": "2026-01-01", "price": 0.99},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = score(reg_path, markets_dir)
    # m1 resolved YES (final 0.99); m2 closed but ambiguous final (0.5); m3 open
    assert report["n_resolved"] == 1
    assert report["markets"][0]["market_id"] == "m1"
    assert report["markets"][0]["resolved_yes"] is True
    assert report["engine_beats_market"] is True  # fair 0.9 vs market 0.5 on a YES
