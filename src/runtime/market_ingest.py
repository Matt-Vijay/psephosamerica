"""Snapshot runner: prediction markets -> the ``data/exports/markets`` sidecar.

Fetches every CONGRESS/legislation market from the two keyless public READ
APIs — Polymarket's Gamma API (congress-tagged events, open and closed) and
Kalshi's trade-api v2 (Politics series whose titles look legislative) — plus
price history where exposed (Polymarket CLOB ``prices-history`` for the full
series; Kalshi daily candlesticks), and writes the sidecar Track B's
backtester/paper-trader consumes:

* ``markets.jsonl``  — one current row per market (rewritten each snapshot,
  prior markets preserved by id; newest observation wins)
* ``prices.jsonl``   — append-only price observations, de-duplicated by
  ``(market_id, observed_at, source)``; ``known_at`` on every row
* ``deltas.jsonl``   — append-only ``created``/``updated`` announcements (by
  rules-text hash), the same tailable-delta pattern as the contract corpus

Read-only, no auth, no trading. Per-market fetch failures are skipped and
counted, never fabricated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.graph.export import DELTAS_FILENAME
from src.graph.ingest.prediction_markets import (
    epoch_to_utc,
    kalshi_candle_price,
    kalshi_market_record,
    polymarket_market_record,
    price_record,
)
from src.runtime.http_client import client_or_default

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

MARKETS_FILENAME = "markets.jsonl"
PRICES_FILENAME = "prices.jsonl"

# A Kalshi Politics series is in scope when its title looks legislative.
_KALSHI_KEYWORDS = ("bill", "act", "law", "congress", "senate", "house vote", "veto")


def fetch_polymarket_congress_markets(
    *, client: httpx.Client, include_closed: bool = True, page_size: int = 100
) -> list[dict[str, Any]]:
    """Every market under a congress-tagged Gamma event (open + closed)."""
    markets: list[dict[str, Any]] = []
    closed_values = ["false", "true"] if include_closed else ["false"]
    for closed in closed_values:
        offset = 0
        while True:
            response = client.get(
                f"{GAMMA_API}/events",
                params={
                    "limit": str(page_size),
                    "offset": str(offset),
                    "closed": closed,
                    "tag_slug": "congress",
                },
                follow_redirects=True,
            )
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list) or not page:
                break
            for event in page:
                for market in event.get("markets", []) or []:
                    markets.append(market)
            offset += page_size
            if len(page) < page_size:
                break
    return markets


def fetch_polymarket_history(
    clob_token_id: str, *, client: httpx.Client, fidelity_minutes: int = 720
) -> list[dict[str, Any]]:
    """Full price history for one CLOB token (``[{t, p}, ...]``)."""
    response = client.get(
        f"{CLOB_API}/prices-history",
        params={"market": clob_token_id, "interval": "max", "fidelity": str(fidelity_minutes)},
        follow_redirects=True,
    )
    response.raise_for_status()
    history = response.json().get("history", [])
    return list(history) if isinstance(history, list) else []


def fetch_kalshi_legislation_markets(
    *, client: httpx.Client, max_series: int | None = None
) -> list[dict[str, Any]]:
    """Markets of every Politics series whose title looks legislative."""
    response = client.get(
        f"{KALSHI_API}/series", params={"category": "Politics"}, follow_redirects=True
    )
    response.raise_for_status()
    series = response.json().get("series", [])
    in_scope = [
        s for s in series if any(k in str(s.get("title") or "").lower() for k in _KALSHI_KEYWORDS)
    ]
    if max_series is not None:
        in_scope = in_scope[:max_series]
    markets: list[dict[str, Any]] = []
    for entry in in_scope:
        ticker = str(entry.get("ticker") or "")
        if not ticker:
            continue
        page = client.get(
            f"{KALSHI_API}/markets",
            params={"series_ticker": ticker, "limit": "100"},
            follow_redirects=True,
        )
        page.raise_for_status()
        for market in page.json().get("markets", []) or []:
            market["series_ticker"] = ticker
            markets.append(market)
    return markets


def fetch_kalshi_candles(
    series_ticker: str,
    market_ticker: str,
    *,
    client: httpx.Client,
    start_ts: int,
    end_ts: int,
    period_minutes: int = 1440,
) -> list[dict[str, Any]]:
    """Daily candlesticks for one Kalshi market."""
    response = client.get(
        f"{KALSHI_API}/series/{series_ticker}/markets/{market_ticker}/candlesticks",
        params={
            "start_ts": str(start_ts),
            "end_ts": str(end_ts),
            "period_interval": str(period_minutes),
        },
        follow_redirects=True,
    )
    response.raise_for_status()
    candles = response.json().get("candlesticks", [])
    return list(candles) if isinstance(candles, list) else []


@dataclass(frozen=True)
class MarketSnapshotReport:
    """Counts from one market snapshot pass."""

    markets_total: int
    markets_linked: int
    polymarket_markets: int
    kalshi_markets: int
    prices_written: int
    prices_total: int
    deltas_written: int
    fetch_errors: int

    @property
    def linkage_rate(self) -> float:
        if self.markets_total == 0:
            return 0.0
        return round(self.markets_linked / self.markets_total, 4)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _existing_price_keys(path: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in _read_jsonl(path):
        keys.add((str(row["market_id"]), str(row["observed_at"]), str(row["source"])))
    return keys


def _clob_token(market: dict[str, Any]) -> str | None:
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return None


def snapshot_markets(
    *,
    out_directory: Path | str,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    include_closed: bool = True,
    with_history: bool = True,
    history_days: int = 365,
    max_kalshi_series: int | None = None,
) -> MarketSnapshotReport:
    """One full snapshot: fetch both venues, merge, append prices + deltas."""
    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = now if now is not None else datetime.now(UTC)
    http, owns = client_or_default(client)
    errors = 0

    records: list[dict[str, Any]] = []
    histories: list[tuple[str, list[dict[str, Any]]]] = []  # (market_id, [{t,p}])
    try:
        for raw in fetch_polymarket_congress_markets(client=http, include_closed=include_closed):
            record = polymarket_market_record(raw, known_at=observed)
            if record is None:
                continue
            records.append(record)
            token = _clob_token(raw)
            if with_history and token and record["bill_ids"]:
                try:
                    histories.append(
                        (record["market_id"], fetch_polymarket_history(token, client=http))
                    )
                except httpx.HTTPError:
                    errors += 1

        kalshi_count = 0
        for raw in fetch_kalshi_legislation_markets(client=http, max_series=max_kalshi_series):
            record = kalshi_market_record(raw, known_at=observed)
            if record is None:
                continue
            records.append(record)
            kalshi_count += 1
            if with_history and record["bill_ids"]:
                start = int(observed.timestamp()) - history_days * 86400
                try:
                    candles = fetch_kalshi_candles(
                        str(raw.get("series_ticker") or ""),
                        record["native_id"],
                        client=http,
                        start_ts=start,
                        end_ts=int(observed.timestamp()),
                    )
                except httpx.HTTPError:
                    errors += 1
                    candles = []
                points = []
                for candle in candles:
                    price = kalshi_candle_price(candle)
                    ts = candle.get("end_period_ts")
                    if price is not None and ts is not None:
                        points.append({"t": int(ts), "p": price})
                if points:
                    histories.append((record["market_id"], points))
    finally:
        if owns:
            http.close()

    # Merge with prior snapshot (prior markets not seen this pass are preserved).
    markets_path = out_dir / MARKETS_FILENAME
    prior = {str(row["market_id"]): row for row in _read_jsonl(markets_path)}
    current = {str(record["market_id"]): record for record in records}
    merged = {**prior, **current}
    with markets_path.open("w", encoding="utf-8") as handle:
        for market_id in sorted(merged):
            handle.write(json.dumps(merged[market_id], sort_keys=True) + "\n")

    # Deltas: created (new id) / updated (rules-text hash changed).
    deltas_path = out_dir / DELTAS_FILENAME
    deltas_written = 0
    with deltas_path.open("a", encoding="utf-8") as handle:
        for market_id, record in sorted(current.items()):
            before = prior.get(market_id)
            if before is None:
                change = "created"
            elif before.get("content_sha256") != record.get("content_sha256"):
                change = "updated"
            else:
                continue
            handle.write(
                json.dumps(
                    {
                        "canonical_id": market_id,
                        "change_type": change,
                        "known_at": record["known_at"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            deltas_written += 1

    # Prices: history points (known_at = the point's own time) + a snapshot
    # observation per market with a live Yes price; append-only, de-duplicated.
    prices_path = out_dir / PRICES_FILENAME
    seen = _existing_price_keys(prices_path)
    prices_written = 0
    with prices_path.open("a", encoding="utf-8") as handle:

        def _emit(row: dict[str, Any]) -> None:
            nonlocal prices_written
            key = (str(row["market_id"]), str(row["observed_at"]), str(row["source"]))
            if key in seen:
                return
            seen.add(key)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            prices_written += 1

        for market_id, points in histories:
            for point in points:
                try:
                    when = epoch_to_utc(float(point["t"]))
                    price = float(point["p"])
                except (KeyError, TypeError, ValueError):
                    continue
                _emit(
                    price_record(
                        market_id=market_id, observed_at=when, price=price, source="history"
                    )
                )
        for market_id, record in sorted(current.items()):
            prices = record.get("outcome_prices") or []
            if prices:
                _emit(
                    price_record(
                        market_id=market_id,
                        observed_at=observed,
                        price=float(prices[0]),
                        source="snapshot",
                    )
                )

    polymarket_count = sum(1 for r in records if r["venue"] == "polymarket")
    return MarketSnapshotReport(
        markets_total=len(records),
        markets_linked=sum(1 for r in records if r["bill_ids"]),
        polymarket_markets=polymarket_count,
        kalshi_markets=len(records) - polymarket_count,
        prices_written=prices_written,
        prices_total=len(seen),
        deltas_written=deltas_written,
        fetch_errors=errors,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Prediction-market snapshot (read-only)")
    parser.add_argument("--out", default="data/exports/markets")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--max-kalshi-series", type=int, default=None)
    args = parser.parse_args(argv)
    report = snapshot_markets(
        out_directory=args.out,
        with_history=not args.no_history,
        max_kalshi_series=args.max_kalshi_series,
    )
    print(
        f"markets={report.markets_total} (pm={report.polymarket_markets} "
        f"kalshi={report.kalshi_markets}) linked={report.markets_linked} "
        f"linkage={report.linkage_rate:.1%} prices+={report.prices_written} "
        f"deltas={report.deltas_written} errors={report.fetch_errors}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
