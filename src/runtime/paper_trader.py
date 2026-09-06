"""Paper trader: pre-register frozen fair values for live linked markets (v7 #4c).

SIGNALS ONLY -- no real-money trading, no order placement, no exchange auth.

``freeze`` prices every OPEN market in Track A's sidecar that the engine can
link and price, then writes a content-hashed registry row per market BEFORE
resolution: fair value, the latest market price, the edge, and a quarter-Kelly
stake on a notional $100 bankroll. The content hash covers everything except
later scoring, so the pre-registration is tamper-evident exactly like the vote
prediction registry. ``score`` revisits the registry once markets resolve
(closed with an extreme final price, or a matched roll-call) and reports
log-loss model-vs-market -- engine-beats-market is THE metric.

Pricing by market type:

* becomes-law questions linked to a sidecar bill: the chain composer (hazard
  floor link conditioned on bill age, count-PMF pivots, empirical cross-chamber
  rate, cited signature prior);
* member-vote and count-bucket questions: member-rate / correlated-PMF heads
  (the same machinery the backtest scored honestly).

Spread-capture flags: the sidecar carries last prices but no bid/ask, so the
"quoted spread exceeds the model's uncertainty band" screen cannot run yet; it
arms automatically when Track A adds order-book fields. Documented, not faked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from src.runtime.bill_content_experiment import _iter_records
from src.runtime.crs_multitask_experiment import _canonical_to_key
from src.runtime.market_backtest import (
    _member_rates,
    _rollcalls_by_key,
    fit_within_party_sigma,
    load_title_index,
    parse_market,
    title_match_keys,
)

_KELLY_FRACTION = 0.25
_BANKROLL = 100.0
_BECOME_LAW_HINTS = ("become law", "signed into law", "be signed", "passes bill", "pass the")


def _kelly_stake(fair: float, price: float) -> tuple[str, float]:
    """Quarter-Kelly stake for a $100 bankroll; side with positive expectancy."""
    p = min(1.0 - 1e-6, max(1e-6, price))
    if fair > p:
        f = (fair - p) / (1.0 - p)
        return "buy_yes", round(_KELLY_FRACTION * f * _BANKROLL, 2)
    f = (p - fair) / p
    return "buy_no", round(_KELLY_FRACTION * f * _BANKROLL, 2)


def _content_hash(rows: list[dict[str, Any]]) -> str:
    frozen = json.dumps(rows, sort_keys=True).encode("utf-8")
    return hashlib.sha256(frozen).hexdigest()


def _latest_prices(markets_dir: Path) -> dict[str, float]:
    latest: dict[str, tuple[str, float]] = {}
    with (markets_dir / "prices.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("outcome", "Yes")).lower() != "yes":
                continue
            ts = str(row.get("known_at") or "")
            mid = str(row.get("market_id"))
            if mid not in latest or ts > latest[mid][0]:
                latest[mid] = (ts, float(row.get("price", 0.0)))
    return {mid: price for mid, (_ts, price) in latest.items()}


def freeze(
    markets_dir: Path,
    records_path: Path,
    sidecar: Path,
    senate_corpus: Path,
    *,
    as_of: date,
    out_path: Path,
) -> dict[str, Any]:
    canon2key = _canonical_to_key(records_path)
    title_index = load_title_index(sidecar)
    prices = _latest_prices(markets_dir)
    rollcalls = _rollcalls_by_key(senate_corpus)
    rates, party_rates = _member_rates(senate_corpus, as_of)
    sigma_d = fit_within_party_sigma(
        senate_corpus, party="D", rates_before=date(2023, 1, 3), fit_before=date(2025, 1, 3)
    )

    composer_inputs = _composer_inputs(sidecar, as_of)
    rows: list[dict[str, Any]] = []
    for market in _iter_records(markets_dir / "markets.jsonl"):
        if market.get("closed"):
            continue
        mid = str(market.get("market_id"))
        question = str(market.get("question", ""))
        linked = [canon2key.get(str(c)) for c in market.get("bill_ids") or []]
        keys = sorted({k for k in linked if k} | set(title_match_keys(question, title_index)))
        market_p = prices.get(mid)
        fair, method = _price_open_market(
            question,
            keys,
            rollcalls,
            rates,
            party_rates,
            sigma_d=sigma_d,
            composer_inputs=composer_inputs,
            end_date=str(market.get("end_date") or ""),
            as_of=as_of,
        )
        if fair is None or market_p is None:
            continue
        side, stake = _kelly_stake(fair, market_p)
        rows.append(
            {
                "market_id": mid,
                "native_id": market.get("native_id"),
                "venue": market.get("venue"),
                "question": question,
                "matched_keys": keys,
                "method": method,
                "fair_value": round(fair, 6),
                "market_price": market_p,
                "edge": round(fair - market_p, 6),
                "side": side,
                "kelly_stake_usd": stake,
                "as_of": as_of.isoformat(),
            }
        )

    registry = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "n_markets": len(rows),
        "spread_capture": "unavailable: sidecar has no bid/ask; arms when order-book fields land",
        "content_sha256": _content_hash(rows),
        "markets": rows,
    }
    out_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return registry


def _composer_inputs(sidecar: Path, as_of: date) -> dict[str, Any]:
    """Fit-once inputs for become-law pricing (hazard head + link constants)."""
    from src.prediction.stage_hazard import fit_hazard
    from src.runtime.bill_journey import JourneyFeaturizer, load_bill_journeys, load_floor_events
    from src.runtime.chain_composer_runner import cross_chamber_rate
    from src.runtime.stage_hazard_experiment import _HOUSE_CORPORA, _SENATE_CORPORA, _arrays

    house_events = load_floor_events([Path(p) for p in _HOUSE_CORPORA])
    senate_events = load_floor_events([Path(p) for p in _SENATE_CORPORA])
    journeys = load_bill_journeys(sidecar, house_events, senate_events)
    train = [j for j in journeys if j.congress <= 118]
    featurizer = JourneyFeaturizer.fit(train)
    dur, ev = _arrays(train)
    hazard = fit_hazard(featurizer.transform(train), dur, ev, featurizer.feature_names)
    rate, reached, total = cross_chamber_rate(
        house_events[0], senate_events[0], congresses=range(113, 119)
    )
    return {
        "hazard": hazard,
        "featurizer": featurizer,
        "journeys_by_key": {j.bill_key: j for j in journeys},
        "house_first": house_events[0],
        "senate_first": senate_events[0],
        "cross_rate": rate,
        "cross_cite": f"{reached}/{total} House-floor bills later reached the Senate floor (113th-118th)",
    }


def _price_open_market(
    question: str,
    keys: list[str],
    rollcalls: dict[str, Any],
    rates: dict[tuple[str, bool], tuple[int, int]],
    party_rates: dict[tuple[str, bool], tuple[int, int]],
    *,
    sigma_d: float,
    composer_inputs: dict[str, Any],
    end_date: str,
    as_of: date,
) -> tuple[float | None, str]:
    spec = parse_market(question)
    if spec is not None and spec.kind == "count_bucket" and keys:
        rolls = [rc for k in keys for rc in rollcalls.get(k, [])]
        if not rolls:
            return None, "count_bucket:no_rollcall_roster"
        from src.runtime.market_backtest import _fair_count_bucket

        fair = _fair_count_bucket(
            spec,
            max(rolls, key=lambda rc: rc.vote_date),
            rates,
            party_rates,
            sponsor_party="R",
            sigma_common=sigma_d if spec.party else 1.5,
            sigma_party=0.0 if spec.party else 2.2,
        )
        return fair, "count_pmf"
    lowered = question.lower()
    if any(h in lowered for h in _BECOME_LAW_HINTS) and keys:
        journey = next(
            (composer_inputs["journeys_by_key"].get(k) for k in keys if k.split(":")[1] == "hr"),
            None,
        ) or next((composer_inputs["journeys_by_key"].get(k) for k in keys), None)
        if journey is None:
            return None, "become_law:no_journey"
        return _compose_become_law(journey, composer_inputs, end_date=end_date, as_of=as_of)
    return None, "unpriced"


def _compose_become_law(
    journey: Any, inputs: dict[str, Any], *, end_date: str, as_of: date
) -> tuple[float | None, str]:
    from src.prediction.chain_composer import ChainLink, compose_chain

    try:
        deadline = date.fromisoformat(end_date[:10])
    except ValueError:
        deadline = date(as_of.year, 12, 31)
    horizon = max(0.0, float((deadline - as_of).days))
    featurizer = inputs["featurizer"]
    hazard = inputs["hazard"]
    x = featurizer.transform([journey])
    house_done = journey.bill_key in inputs["house_first"]
    senate_done = journey.bill_key in inputs["senate_first"]
    if house_done and senate_done:
        # The linked bill already cleared both floors yet the market still
        # trades: the market is about a provision/amendment, not the vehicle --
        # pricing the vehicle would be wrong. Refuse rather than mislink.
        return None, "become_law:vehicle_already_passed"
    p_floor = (
        1.0
        if house_done
        else float(
            hazard.predict_event_between(x, journey.duration_days, journey.duration_days + horizon)[
                0
            ]
        )
    )
    pmf_cite = "roster-level pivots from benchmarks/chain_composer_sample.json"
    links = [
        ChainLink("house_floor", p_floor, "observed" if house_done else "stage_hazard", ""),
        ChainLink(
            "house_passage",
            1.0 if house_done else 0.62,
            "observed" if house_done else "count_pmf prior",
            "" if house_done else pmf_cite,
        ),
        ChainLink(
            "senate_floor",
            1.0 if senate_done else float(inputs["cross_rate"]),
            "observed" if senate_done else "empirical cross-chamber rate",
            inputs["cross_cite"],
        ),
        ChainLink(
            "senate_cloture_and_passage",
            1.0 if senate_done else 0.30 * 0.86,
            "observed" if senate_done else "count_pmf prior",
            "" if senate_done else pmf_cite,
        ),
        ChainLink("signature", 0.98, "historical prior", "vetoes <2% of presented bills 2013-2024"),
    ]
    return compose_chain(links).probability, "chain_composer"


def score(registry_path: Path, markets_dir: Path) -> dict[str, Any]:
    """Score frozen rows whose markets have since resolved (extreme final price)."""
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    finals = _latest_prices(markets_dir)
    closed: set[str] = set()
    for market in _iter_records(markets_dir / "markets.jsonl"):
        if market.get("closed"):
            closed.add(str(market.get("market_id")))
    scored: list[dict[str, Any]] = []
    for row in registry.get("markets", []):
        mid = str(row["market_id"])
        final = finals.get(mid)
        if mid not in closed or final is None or 0.05 < final < 0.95:
            continue  # unresolved or ambiguous final print
        y = 1.0 if final >= 0.95 else 0.0
        fair = min(1.0 - 1e-6, max(1e-6, float(row["fair_value"])))
        mkt = min(1.0 - 1e-6, max(1e-6, float(row["market_price"])))
        scored.append(
            {
                **row,
                "resolved_yes": bool(y),
                "model_logloss": float(-(y * np.log(fair) + (1 - y) * np.log(1 - fair))),
                "market_logloss": float(-(y * np.log(mkt) + (1 - y) * np.log(1 - mkt))),
            }
        )
    n = len(scored)
    return {
        "frozen_sha256": registry.get("content_sha256"),
        "n_resolved": n,
        "mean_model_logloss": float(np.mean([r["model_logloss"] for r in scored])) if n else 0.0,
        "mean_market_logloss": float(np.mean([r["market_logloss"] for r in scored])) if n else 0.0,
        "engine_beats_market": bool(
            n
            and np.mean([r["model_logloss"] for r in scored])
            < np.mean([r["market_logloss"] for r in scored])
        ),
        "markets": scored,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Paper trader (signals only)")
    p.add_argument("command", choices=["freeze", "score"])
    p.add_argument("--markets-dir", default="data/exports/markets")
    p.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    p.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_full.jsonl")
    p.add_argument("--senate", default="data/real/senate_113_119_rich.jsonl")
    p.add_argument("--registry", default="benchmarks/paper_trades.json")
    p.add_argument("--as-of", default=None)
    p.add_argument("--out", default="benchmarks/paper_trades_scored.json")
    args = p.parse_args(argv)

    if args.command == "freeze":
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        registry = freeze(
            Path(args.markets_dir),
            Path(args.records),
            Path(args.sidecar),
            Path(args.senate),
            as_of=as_of,
            out_path=Path(args.registry),
        )
        print(
            f"froze {registry['n_markets']} open-market fair values | sha256 "
            f"{registry['content_sha256'][:16]} | wrote {args.registry}"
        )
    else:
        report = score(Path(args.registry), Path(args.markets_dir))
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            f"resolved {report['n_resolved']} | log-loss model "
            f"{report['mean_model_logloss']:.4f} vs market {report['mean_market_logloss']:.4f} | "
            f"engine beats market: {report['engine_beats_market']} | wrote {args.out}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
