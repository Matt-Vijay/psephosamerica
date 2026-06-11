from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.ingest.prediction_markets import market_entity_id
from src.runtime.market_ingest import (
    fetch_kalshi_candles,
    fetch_kalshi_legislation_markets,
    fetch_polymarket_congress_markets,
    fetch_polymarket_history,
    snapshot_markets,
)

_NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)

_PM_MARKET = {
    "conditionId": "0xdef1",
    "question": "Will the DEFIANCE Act become law this year?",
    "slug": "defiance-act",
    "description": "Resolves Yes if S. 1837 is signed into law in 2026.",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.73", "0.27"]',
    "endDateIso": "2026-12-31",
    "closed": False,
    "clobTokenIds": '["7001", "7002"]',
}
_PM_UNLINKED = {
    "conditionId": "0xaaa2",
    "question": "Which party will win the Senate in 2026?",
    "description": "Party control market, no bill citation.",
    "outcomes": '["Rep", "Dem"]',
    "outcomePrices": '["0.6", "0.4"]',
    "closed": False,
}
_KALSHI_SERIES = [
    {"ticker": "KXVOTESAVEAMERICA", "title": "When will the Senate vote on the SAVE America Act?"},
    {"ticker": "KXWEATHER", "title": "Daily high temperature in NYC"},  # filtered out
    {"title": "A bill series with no ticker, skipped"},  # keyword match, no ticker
]
_KALSHI_MARKET = {
    "ticker": "KXVOTESAVEAMERICA-26MAY-NOV03",
    "title": "Senate votes on the SAVE America Act before Nov 3?",
    "rules_primary": "If Senate has voted on H.R. 22 before Nov 3, 2026, resolves Yes.",
    "last_price_dollars": "0.41",
    "status": "active",
    "close_time": "2026-11-03T15:00:00Z",
}
_CANDLE = {
    "end_period_ts": 1781150400,
    "price": {"previous_dollars": "0.4100"},
    "yes_bid": {"close_dollars": "0.3100"},
    "yes_ask": {"close_dollars": "0.3800"},
}
_CANDLE_NO_TS = {"price": {"close_dollars": "0.5"}}  # skipped: no end_period_ts


def _router(
    *,
    pm_events_pages: list[list[dict]] | None = None,
    history: list[dict] | None = None,
    history_status: int = 200,
    candles_status: int = 200,
) -> httpx.Client:
    events_open = (
        pm_events_pages
        if pm_events_pages is not None
        else [[{"title": "ev", "markets": [_PM_MARKET, _PM_UNLINKED]}]]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/events" in url:
            if request.url.params.get("closed") == "true":
                return httpx.Response(200, json=[])
            offset = int(request.url.params.get("offset", "0"))
            limit = int(request.url.params.get("limit", "100"))
            page_index = offset // limit
            page = events_open[page_index] if page_index < len(events_open) else []
            return httpx.Response(200, json=page)
        if "/prices-history" in url:
            if history_status != 200:
                return httpx.Response(history_status)
            return httpx.Response(
                200,
                json={"history": history if history is not None else [{"t": 1781000000, "p": 0.7}]},
            )
        if "/series" in url and "/candlesticks" in url:
            if candles_status != 200:
                return httpx.Response(candles_status)
            return httpx.Response(200, json={"candlesticks": [_CANDLE, _CANDLE_NO_TS]})
        if url.endswith("/series") or "/series?" in url:
            return httpx.Response(200, json={"series": _KALSHI_SERIES})
        if "/markets" in url:
            if request.url.params.get("series_ticker") == "KXVOTESAVEAMERICA":
                # the ticker-less market row is skipped by kalshi_market_record
                return httpx.Response(200, json={"markets": [_KALSHI_MARKET, {"title": "x"}]})
            return httpx.Response(200, json={"markets": []})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ fetchers


def test_fetch_polymarket_paginates() -> None:
    pages = [
        [{"markets": [_PM_MARKET]} for _ in range(100)],
        [{"markets": [_PM_UNLINKED]}],
    ]
    markets = fetch_polymarket_congress_markets(client=_router(pm_events_pages=pages))
    assert len(markets) == 101


def test_fetch_polymarket_open_only() -> None:
    markets = fetch_polymarket_congress_markets(client=_router(), include_closed=False)
    assert len(markets) == 2


def test_fetch_polymarket_history_parses() -> None:
    points = fetch_polymarket_history("7001", client=_router())
    assert points == [{"t": 1781000000, "p": 0.7}]


def test_fetch_polymarket_history_non_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"history": "oops"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_polymarket_history("7001", client=client) == []


def test_fetch_kalshi_filters_series_by_title() -> None:
    markets = fetch_kalshi_legislation_markets(client=_router())
    # the weather series is filtered out; the SAVE series' two raw markets come back
    assert [m.get("ticker") for m in markets] == ["KXVOTESAVEAMERICA-26MAY-NOV03", None]
    assert markets[0]["series_ticker"] == "KXVOTESAVEAMERICA"


def test_fetch_kalshi_respects_max_series() -> None:
    assert fetch_kalshi_legislation_markets(client=_router(), max_series=0) == []


def test_fetch_kalshi_candles() -> None:
    candles = fetch_kalshi_candles(
        "KXVOTESAVEAMERICA",
        "KXVOTESAVEAMERICA-26MAY-NOV03",
        client=_router(),
        start_ts=0,
        end_ts=10,
    )
    assert candles == [_CANDLE, _CANDLE_NO_TS]


# ------------------------------------------------------------------ snapshot


def test_snapshot_writes_sidecar_and_links(tmp_path: Path) -> None:
    report = snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    assert report.markets_total == 3  # 2 polymarket + 1 kalshi
    assert report.polymarket_markets == 2 and report.kalshi_markets == 1
    assert report.markets_linked == 2  # DEFIANCE (S. 1837) + SAVE (H.R. 22)
    assert report.linkage_rate == round(2 / 3, 4)
    assert report.fetch_errors == 0

    markets = [json.loads(line) for line in (tmp_path / "markets.jsonl").read_text().splitlines()]
    assert len(markets) == 3
    by_id = {m["market_id"]: m for m in markets}
    pm_id = market_entity_id("polymarket", "0xdef1")
    assert by_id[pm_id]["bill_ids"]  # linked

    prices = [json.loads(line) for line in (tmp_path / "prices.jsonl").read_text().splitlines()]
    sources = {p["source"] for p in prices}
    assert sources == {"history", "snapshot"}
    # every observation carries known_at
    assert all(p["known_at"] for p in prices)
    # kalshi candle became a history point with the candle's own timestamp
    kalshi_id = market_entity_id("kalshi", "KXVOTESAVEAMERICA-26MAY-NOV03")
    kalshi_history = [p for p in prices if p["market_id"] == kalshi_id and p["source"] == "history"]
    assert len(kalshi_history) == 1 and kalshi_history[0]["price"] == round((0.31 + 0.38) / 2, 4)

    deltas = [json.loads(line) for line in (tmp_path / "deltas.jsonl").read_text().splitlines()]
    assert len(deltas) == 3 and all(d["change_type"] == "created" for d in deltas)


def test_snapshot_is_idempotent_and_announces_updates(tmp_path: Path) -> None:
    first = snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    assert first.deltas_written == 3
    again = snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    assert again.deltas_written == 0  # nothing changed -> no deltas
    assert again.prices_written == 0  # same observations -> deduped

    # rules text changes -> one "updated" delta
    changed = dict(_PM_MARKET)
    changed["description"] = "Amended: resolves Yes if S. 1837 OR H.R. 99 is signed into law."
    pages = [[{"markets": [changed, _PM_UNLINKED]}]]
    third = snapshot_markets(
        out_directory=tmp_path, client=_router(pm_events_pages=pages), now=_NOW
    )
    assert third.deltas_written == 1
    deltas = [json.loads(line) for line in (tmp_path / "deltas.jsonl").read_text().splitlines()]
    assert deltas[-1]["change_type"] == "updated"


def test_snapshot_preserves_prior_markets(tmp_path: Path) -> None:
    snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    # second pass sees only the kalshi market -> polymarket rows are preserved
    pages: list[list[dict]] = [[]]
    snapshot_markets(out_directory=tmp_path, client=_router(pm_events_pages=pages), now=_NOW)
    markets = [json.loads(line) for line in (tmp_path / "markets.jsonl").read_text().splitlines()]
    assert len(markets) == 3  # 2 polymarket preserved + 1 kalshi refreshed


def test_snapshot_counts_history_fetch_errors(tmp_path: Path) -> None:
    report = snapshot_markets(
        out_directory=tmp_path,
        client=_router(history_status=500, candles_status=500),
        now=_NOW,
    )
    assert report.fetch_errors == 2  # one per linked market's history fetch
    assert report.markets_total == 3  # markets themselves still ingested


def test_snapshot_without_history(tmp_path: Path) -> None:
    report = snapshot_markets(
        out_directory=tmp_path, client=_router(), now=_NOW, with_history=False
    )
    prices = [json.loads(line) for line in (tmp_path / "prices.jsonl").read_text().splitlines()]
    assert {p["source"] for p in prices} == {"snapshot"}
    assert report.prices_written == 3


def test_snapshot_skips_malformed_history_points(tmp_path: Path) -> None:
    report = snapshot_markets(
        out_directory=tmp_path,
        client=_router(history=[{"t": "bad", "p": 0.5}, {"p": 0.5}, {"t": 1781000000, "p": 0.7}]),
        now=_NOW,
    )
    prices = [json.loads(line) for line in (tmp_path / "prices.jsonl").read_text().splitlines()]
    pm_id = market_entity_id("polymarket", "0xdef1")
    history = [p for p in prices if p["market_id"] == pm_id and p["source"] == "history"]
    assert len(history) == 1  # the two malformed points were skipped
    assert report.markets_total == 3


def test_snapshot_skips_idless_and_priceless_markets(tmp_path: Path) -> None:
    no_id = {"question": "market without a conditionId"}
    no_prices = {"conditionId": "0xnp", "question": "no prices yet", "closed": False}
    pages = [[{"markets": [no_id, no_prices, _PM_MARKET]}]]
    report = snapshot_markets(
        out_directory=tmp_path, client=_router(pm_events_pages=pages), now=_NOW
    )
    assert report.markets_total == 3  # no_prices + _PM_MARKET + kalshi; no_id dropped
    prices = [json.loads(line) for line in (tmp_path / "prices.jsonl").read_text().splitlines()]
    np_id = market_entity_id("polymarket", "0xnp")
    assert not any(p["market_id"] == np_id for p in prices)  # no snapshot row without a price


def test_linkage_rate_zero_when_empty(tmp_path: Path) -> None:
    from src.runtime.market_ingest import MarketSnapshotReport

    empty = MarketSnapshotReport(0, 0, 0, 0, 0, 0, 0, 0)
    assert empty.linkage_rate == 0.0


def test_read_jsonl_tolerates_blank_lines(tmp_path: Path) -> None:
    snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    prices_path = tmp_path / "prices.jsonl"
    prices_path.write_text("\n" + prices_path.read_text(), encoding="utf-8")
    again = snapshot_markets(out_directory=tmp_path, client=_router(), now=_NOW)
    assert again.prices_written == 0  # dedup still works across the blank line


def test_clob_token_bad_json_skips_history(tmp_path: Path) -> None:
    bad = dict(_PM_MARKET)
    bad["clobTokenIds"] = "not-json"
    pages = [[{"markets": [bad]}]]
    report = snapshot_markets(
        out_directory=tmp_path, client=_router(pm_events_pages=pages), now=_NOW
    )
    assert report.fetch_errors == 0  # no history attempted, not an error
    prices = [json.loads(line) for line in (tmp_path / "prices.jsonl").read_text().splitlines()]
    pm_id = market_entity_id("polymarket", "0xdef1")
    assert not any(p["market_id"] == pm_id and p["source"] == "history" for p in prices)


def test_snapshot_default_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.market_ingest as mod

    real = httpx.Client
    router = _router()
    monkeypatch.setattr(mod.httpx, "Client", lambda **_kw: router)
    # client_or_default builds via httpx.Client(...) -> our router; owns=True closes it at the end
    report = snapshot_markets(out_directory=tmp_path, now=_NOW)
    assert report.markets_total == 3
    monkeypatch.setattr(mod.httpx, "Client", real)
