"""Normalize prediction-market data (Polymarket, Kalshi) into market records.

Track B v7 prices P(bill becomes law by deadline) against live prediction
markets, so the market side of that comparison becomes part of the data layer:
every CONGRESS/legislation market is a graph entity (stable ``cm-…`` id) with
full provenance, and every price observation carries its own ``known_at``.

The pure layer lives here: record normalization for the two venues' public
read-only APIs, and the **market -> bill link**. Market rules text names the
bills it resolves on ("H.R. 6644 (119th)", "S. 1837"); :func:`parse_bill_citations`
extracts those citations and resolves each through the same
:class:`~src.graph.bills.BillRef` scheme the vote/bill corpus uses, so a linked
market points at the exact ``cb-…`` canonical bill id Track B already holds
embeddings for. A citation without an explicit congress qualifier defaults to
the congress sitting when the market was observed.

I/O (pagination, price-history fetch, the snapshot runner) lives in
:mod:`src.runtime.market_ingest`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.graph.bills import BillRef, parse_congress_bill_identifier
from src.graph.entity_resolution.ids import stable_id

POLYMARKET = "polymarket"
KALSHI = "kalshi"

# Longest alternatives first so "S. Res. 5" is not consumed as "S." + stray text.
_CITATION_RE = re.compile(
    r"\b(H\.\s?J\.\s?Res\.|S\.\s?J\.\s?Res\.|H\.\s?Con\.\s?Res\.|S\.\s?Con\.\s?Res\.|"
    r"H\.\s?Res\.|S\.\s?Res\.|H\.\s?R\.|S\.)\s*(\d{1,5})"
    r"(?:\s*\(\s*(\d{2,3})\s*(?:th|st|nd|rd)?\s*\))?",
    re.IGNORECASE,
)


def congress_for_date(when: date) -> int:
    """The congress sitting on ``when`` (2025 -> 119th)."""
    return (when.year - 1789) // 2 + 1


@dataclass(frozen=True)
class BillCitation:
    """One bill citation found in market text, resolved to the corpus id scheme."""

    citation: str  # normalized, e.g. "hr-6644"
    congress: int
    canonical_bill_id: str  # the cb-… id bills/votes already resolve to


def parse_bill_citations(text: str, *, default_congress: int) -> list[BillCitation]:
    """Extract bill citations from rules text (ordered, de-duplicated).

    "H.R. 6644 (119th)" pins its congress explicitly; a bare "S. 1837" defaults
    to ``default_congress`` (the congress sitting when the market was observed).
    """
    citations: list[BillCitation] = []
    seen: set[tuple[str, int]] = set()
    for match in _CITATION_RE.finditer(text or ""):
        raw = f"{match.group(1)} {match.group(2)}"
        try:
            identifier = parse_congress_bill_identifier(raw)
        except ValueError:
            continue
        congress = int(match.group(3)) if match.group(3) else default_congress
        key = (identifier, congress)
        if key in seen:
            continue
        seen.add(key)
        ref = BillRef.for_congress(congress, raw)
        citations.append(
            BillCitation(citation=identifier, congress=congress, canonical_bill_id=ref.canonical_id)
        )
    return citations


def market_entity_id(venue: str, native_id: str) -> str:
    """Stable graph-entity id for one market (``cm-…``)."""
    return stable_id([venue, native_id], "cm")


def _floats(values: object) -> list[float]:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            return []
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            return []
    return out


def _strings(values: object) -> list[str]:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            return []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]


def _market_era(end_date: str | None, *, observed: date) -> date:
    """The date that names a market's congress: min(its end date, today).

    A bare citation in a CLOSED market from a prior cycle ("...bill pass in
    2024?") must default to the congress sitting THEN, not at snapshot time —
    but a LIVE market whose deadline spills past the current term ("before
    Jan 4, 2027") still cites a bill that exists NOW, so the era never runs
    ahead of the observation date.
    """
    raw = (end_date or "")[:10]
    try:
        return min(date.fromisoformat(raw), observed)
    except ValueError:
        return observed


def _rules_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _market_record(
    *,
    venue: str,
    native_id: str,
    question: str,
    rules_text: str,
    outcomes: list[str],
    outcome_prices: list[float],
    volume: float | None,
    liquidity: float | None,
    end_date: str | None,
    closed: bool,
    source_url: str,
    known_at: datetime,
) -> dict[str, Any]:
    default_congress = congress_for_date(_market_era(end_date, observed=known_at.date()))
    citations = parse_bill_citations(f"{question}\n{rules_text}", default_congress=default_congress)
    return {
        "market_id": market_entity_id(venue, native_id),
        "venue": venue,
        "native_id": native_id,
        "question": question,
        "rules_text": rules_text,
        "outcomes": outcomes,
        "outcome_prices": outcome_prices,
        "volume": volume,
        "liquidity": liquidity,
        "end_date": end_date,
        "closed": closed,
        "bill_ids": [c.canonical_bill_id for c in citations],
        "citations": [
            {
                "citation": c.citation,
                "congress": c.congress,
                "canonical_bill_id": c.canonical_bill_id,
            }
            for c in citations
        ],
        "source_url": source_url,
        "content_sha256": _rules_sha256(f"{question}\n{rules_text}"),
        "known_at": known_at.isoformat(),
    }


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def polymarket_market_record(
    market: dict[str, Any], *, known_at: datetime
) -> dict[str, Any] | None:
    """A Gamma API market -> normalized market record (``None`` without an id)."""
    native_id = str(market.get("conditionId") or "").strip()
    if not native_id:
        return None
    slug = str(market.get("slug") or "")
    return _market_record(
        venue=POLYMARKET,
        native_id=native_id,
        question=str(market.get("question") or ""),
        rules_text=str(market.get("description") or ""),
        outcomes=_strings(market.get("outcomes")),
        outcome_prices=_floats(market.get("outcomePrices")),
        volume=_float_or_none(market.get("volume")),
        liquidity=_float_or_none(market.get("liquidity")),
        end_date=(str(market.get("endDateIso")) if market.get("endDateIso") else None),
        closed=bool(market.get("closed")),
        source_url=f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com",
        known_at=known_at,
    )


def kalshi_market_record(market: dict[str, Any], *, known_at: datetime) -> dict[str, Any] | None:
    """A Kalshi trade-api v2 market -> normalized market record."""
    ticker = str(market.get("ticker") or "").strip()
    if not ticker:
        return None
    last = _float_or_none(market.get("last_price_dollars"))
    return _market_record(
        venue=KALSHI,
        native_id=ticker,
        question=str(market.get("title") or ""),
        rules_text=str(market.get("rules_primary") or ""),
        outcomes=["Yes", "No"],
        outcome_prices=([last, round(1.0 - last, 4)] if last is not None else []),
        volume=_float_or_none(market.get("volume_fp") or market.get("volume")),
        liquidity=_float_or_none(market.get("liquidity_dollars")),
        end_date=(str(market.get("close_time")) if market.get("close_time") else None),
        closed=str(market.get("status") or "") not in ("active", "open", "initialized"),
        source_url=f"https://kalshi.com/markets/{ticker}",
        known_at=known_at,
    )


def kalshi_candle_price(candle: dict[str, Any]) -> float | None:
    """A candle's close price: trade close, else yes bid/ask close midpoint."""
    price = candle.get("price")
    if isinstance(price, dict):
        close = _float_or_none(price.get("close_dollars"))
        if close is not None:
            return close
    bid = candle.get("yes_bid")
    ask = candle.get("yes_ask")
    bid_close = _float_or_none(bid.get("close_dollars")) if isinstance(bid, dict) else None
    ask_close = _float_or_none(ask.get("close_dollars")) if isinstance(ask, dict) else None
    if bid_close is not None and ask_close is not None:
        return round((bid_close + ask_close) / 2, 4)
    if isinstance(price, dict):
        return _float_or_none(price.get("previous_dollars"))
    return None


def price_record(
    *,
    market_id: str,
    observed_at: datetime,
    price: float,
    outcome: str = "Yes",
    source: str = "snapshot",
) -> dict[str, Any]:
    """One price observation; ``known_at`` is when that price was public."""
    return {
        "market_id": market_id,
        "observed_at": observed_at.isoformat(),
        "price": price,
        "outcome": outcome,
        "source": source,
        "known_at": observed_at.isoformat(),
    }


def epoch_to_utc(seconds: float) -> datetime:
    """A unix timestamp as an aware UTC datetime."""
    return datetime.fromtimestamp(seconds, tz=UTC)
