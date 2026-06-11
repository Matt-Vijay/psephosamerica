from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.bills import BillRef
from src.graph.ingest.prediction_markets import (
    BillCitation,
    congress_for_date,
    epoch_to_utc,
    kalshi_candle_price,
    kalshi_market_record,
    market_entity_id,
    parse_bill_citations,
    polymarket_market_record,
    price_record,
)

_KNOWN = datetime(2026, 6, 11, tzinfo=UTC)

# Real-shape Gamma API market (the DEFIANCE Act series).
_GAMMA_MARKET = {
    "conditionId": "0xabc123",
    "question": "Will the DEFIANCE Act become law this year?",
    "slug": "will-the-defiance-act-become-law",
    "description": (
        "This market will resolve to 'Yes' if S. 1837 (119th) is signed into law "
        "before December 31, 2026."
    ),
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.73", "0.27"]',
    "volume": "152034.5",
    "liquidity": "8000",
    "endDateIso": "2026-12-31",
    "closed": False,
}

# Real-shape Kalshi trade-api v2 market (the SAVE America vote series).
_KALSHI_MARKET = {
    "ticker": "KXVOTESAVEAMERICA-26MAY-NOV03",
    "title": "Senate votes on the SAVE America Act before Nov 3?",
    "rules_primary": (
        "If Senate has conducted any recorded vote on H.R. 22 before Nov 3, 2026, "
        "then the market resolves to Yes."
    ),
    "last_price_dollars": "0.41",
    "volume_fp": "120.5",
    "liquidity_dollars": "950.00",
    "close_time": "2026-11-03T15:00:00Z",
    "status": "active",
}


def test_congress_for_date() -> None:
    assert congress_for_date(date(2025, 3, 1)) == 119
    assert congress_for_date(date(2026, 6, 11)) == 119
    assert congress_for_date(date(2013, 2, 1)) == 113


def test_parse_citations_with_explicit_congress() -> None:
    cites = parse_bill_citations("resolves on H.R. 6644 (119th)", default_congress=120)
    assert cites == [
        BillCitation(
            citation="hr-6644",
            congress=119,
            canonical_bill_id=BillRef.for_congress(119, "H.R. 6644").canonical_id,
        )
    ]


def test_parse_citations_defaults_congress() -> None:
    cites = parse_bill_citations("if S. 1837 passes", default_congress=119)
    assert cites[0].congress == 119
    assert cites[0].citation == "s-1837"


def test_parse_citations_all_resolution_types() -> None:
    text = "H.J.Res. 25, S. J. Res. 3, H. Con. Res. 5, S.Con.Res. 9, H. Res. 10, S. Res. 12"
    cites = parse_bill_citations(text, default_congress=119)
    assert [c.citation for c in cites] == [
        "hjres-25",
        "sjres-3",
        "hconres-5",
        "sconres-9",
        "hres-10",
        "sres-12",
    ]


def test_parse_citations_dedups_and_orders() -> None:
    cites = parse_bill_citations("H.R. 22 ... H.R. 22 ... S. 5", default_congress=119)
    assert [c.citation for c in cites] == ["hr-22", "s-5"]


def test_parse_citations_ignores_plain_prose() -> None:
    assert parse_bill_citations("the Senate will vote in 2026", default_congress=119) == []
    assert parse_bill_citations("", default_congress=119) == []


def test_parse_citations_skips_unnormalizable_match(monkeypatch) -> None:
    # Defensive: if the shared bill-citation normalizer rejects a regex match
    # (pattern drift), the citation is skipped rather than fabricated.
    import src.graph.ingest.prediction_markets as mod

    def boom(raw: str) -> str:
        raise ValueError("drift")

    monkeypatch.setattr(mod, "parse_congress_bill_identifier", boom)
    assert parse_bill_citations("H.R. 22", default_congress=119) == []


def test_polymarket_record_non_list_json_fields() -> None:
    record = polymarket_market_record(
        {"conditionId": "0xee", "outcomes": '{"a": 1}', "outcomePrices": "{}"},
        known_at=_KNOWN,
    )
    assert record is not None
    assert record["outcomes"] == [] and record["outcome_prices"] == []


def test_polymarket_record_bad_json_prices() -> None:
    record = polymarket_market_record(
        {"conditionId": "0xff", "outcomePrices": "not-json"}, known_at=_KNOWN
    )
    assert record is not None and record["outcome_prices"] == []


def test_polymarket_record_accepts_plain_lists() -> None:
    # Gamma sometimes returns the arrays un-encoded; both shapes must parse.
    record = polymarket_market_record(
        {"conditionId": "0x11", "outcomes": ["Yes", "No"], "outcomePrices": [0.5, 0.5]},
        known_at=_KNOWN,
    )
    assert record is not None
    assert record["outcomes"] == ["Yes", "No"] and record["outcome_prices"] == [0.5, 0.5]


def test_market_entity_id_is_stable_and_prefixed() -> None:
    a = market_entity_id("polymarket", "0xabc")
    assert a.startswith("cm-") and a == market_entity_id("polymarket", "0xabc")
    assert a != market_entity_id("kalshi", "0xabc")


def test_polymarket_record_links_bill() -> None:
    record = polymarket_market_record(_GAMMA_MARKET, known_at=_KNOWN)
    assert record is not None
    assert record["venue"] == "polymarket"
    assert record["market_id"].startswith("cm-")
    assert record["outcomes"] == ["Yes", "No"]
    assert record["outcome_prices"] == [0.73, 0.27]
    assert record["volume"] == 152034.5
    assert record["end_date"] == "2026-12-31"
    assert record["closed"] is False
    assert record["citations"] == [
        {
            "citation": "s-1837",
            "congress": 119,
            "canonical_bill_id": BillRef.for_congress(119, "S. 1837").canonical_id,
        }
    ]
    assert record["bill_ids"] == [BillRef.for_congress(119, "S. 1837").canonical_id]
    assert record["known_at"] == _KNOWN.isoformat()
    assert record["source_url"].endswith("/market/will-the-defiance-act-become-law")
    assert len(record["content_sha256"]) == 64


def test_polymarket_record_requires_condition_id() -> None:
    assert polymarket_market_record({"question": "x"}, known_at=_KNOWN) is None


def test_polymarket_record_tolerates_malformed_fields() -> None:
    record = polymarket_market_record(
        {
            "conditionId": "0xdef",
            "question": "Q",
            "outcomes": "not-json",
            "outcomePrices": '["abc"]',
            "volume": "not-a-number",
        },
        known_at=_KNOWN,
    )
    assert record is not None
    assert record["outcomes"] == [] and record["outcome_prices"] == []
    assert record["volume"] is None and record["end_date"] is None
    assert record["source_url"] == "https://polymarket.com"  # no slug


def test_kalshi_record_links_bill() -> None:
    record = kalshi_market_record(_KALSHI_MARKET, known_at=_KNOWN)
    assert record is not None
    assert record["venue"] == "kalshi"
    assert record["native_id"] == "KXVOTESAVEAMERICA-26MAY-NOV03"
    assert record["outcome_prices"] == [0.41, 0.59]
    assert record["closed"] is False
    assert record["citations"][0]["citation"] == "hr-22"
    assert record["end_date"] == "2026-11-03T15:00:00Z"


def test_kalshi_record_requires_ticker_and_handles_no_price() -> None:
    assert kalshi_market_record({"title": "x"}, known_at=_KNOWN) is None
    record = kalshi_market_record({"ticker": "T-1", "status": "settled"}, known_at=_KNOWN)
    assert record is not None
    assert record["outcome_prices"] == [] and record["closed"] is True


def test_kalshi_candle_price_prefers_trade_close() -> None:
    assert kalshi_candle_price({"price": {"close_dollars": "0.38"}}) == 0.38


def test_kalshi_candle_price_falls_back_to_mid() -> None:
    candle = {
        "price": {"previous_dollars": "0.41"},
        "yes_bid": {"close_dollars": "0.31"},
        "yes_ask": {"close_dollars": "0.38"},
    }
    assert kalshi_candle_price(candle) == round((0.31 + 0.38) / 2, 4)


def test_kalshi_candle_price_falls_back_to_previous() -> None:
    assert kalshi_candle_price({"price": {"previous_dollars": "0.41"}}) == 0.41


def test_kalshi_candle_price_none_when_empty() -> None:
    assert kalshi_candle_price({}) is None
    assert kalshi_candle_price({"yes_bid": {"close_dollars": "0.3"}}) is None  # no ask side


def test_price_record_carries_known_at() -> None:
    t = epoch_to_utc(1781155084)
    record = price_record(market_id="cm-x", observed_at=t, price=0.73, source="history")
    assert record["known_at"] == t.isoformat() == record["observed_at"]
    assert record["price"] == 0.73 and record["outcome"] == "Yes"


def test_epoch_to_utc() -> None:
    t = epoch_to_utc(0)
    assert t.year == 1970 and t.tzinfo is UTC


def test_closed_market_citation_defaults_to_its_own_congress() -> None:
    # A 2024-era market citing "H.R. 7890" must resolve to the 118th congress
    # (sitting at its end date), not the congress sitting at snapshot time.
    market = {
        "conditionId": "0xold",
        "question": "Will the bathroom bill pass in 2024?",
        "description": "Resolves Yes if H.R. 7890 passes before the end of 2024.",
        "endDateIso": "2024-12-31",
        "closed": True,
    }
    record = polymarket_market_record(market, known_at=_KNOWN)  # snapshot in 2026 (119th)
    assert record is not None
    assert record["citations"][0]["congress"] == 118
    assert record["citations"][0]["canonical_bill_id"] == (
        BillRef.for_congress(118, "H.R. 7890").canonical_id
    )


def test_unparseable_end_date_falls_back_to_snapshot_congress() -> None:
    market = {
        "conditionId": "0xnd",
        "description": "Resolves on S. 99.",
        "endDateIso": "soon",
    }
    record = polymarket_market_record(market, known_at=_KNOWN)
    assert record is not None and record["citations"][0]["congress"] == 119
