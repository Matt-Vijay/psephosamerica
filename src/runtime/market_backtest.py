"""Backtest engine fair values against real market prices (v7 #4a). SIGNALS ONLY.

Replays Track A's markets sidecar (``data/exports/markets/``: markets linked to
bill entities, price history with ``known_at``) against frozen-cutoff model
prices, fee+spread-aware, and reports the edge after costs honestly even if <= 0.

Scope (v1, real resolutions only): markets that resolve against roll-calls we
ingested ourselves --

* **count buckets** ("between 60 and 100 members of the Senate vote for
  S.J.Res.49", "Will 8-11 Democratic senators vote in favor of the Laken Riley
  Act?"): fair value = bucket mass of the correlated yes-count PMF (pinned
  sigmas) over the relevant member set; resolution = the actual party-filtered
  yea count on the matched roll-call.
* **member votes** ("Will Markwayne Mullin vote for ..."): fair value = the
  member's pre-cutoff yea rate conditioned on their party's lean; resolution =
  the member's actual vote.

Frozen-cutoff discipline: the decision price is the LAST observed price
strictly BEFORE the matched vote's date, and the fair value uses only
roll-calls strictly before that decision date. A market whose price history
begins only after the vote (snapshot-only captures) is skipped and counted
honestly as ``price_after_vote`` -- it cannot be backtested. Bill linkage is
Track A's ``bill_ids`` union a title match of the question against the govinfo
sidecar (Track A sometimes links the House companion while the Senate voted on
the S-bill). The matched roll-call is the LAST one on the linked bill at or
before the market's end (passage follows cloture); match metadata is reported
per market. Markets whose resolution is not in the ingested corpora
(becomes-law, signature counts) are out of scope here -- they belong to the
paper trader, scored at resolution.

Costs: Kalshi taker fee 0.07*p*(1-p) per contract plus a 1-cent half-spread;
Polymarket no fee plus the same 1-cent half-spread (documented constants).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.vote_count_pmf import correlated_count_pmf
from src.runtime.bill_content_experiment import _iter_records
from src.runtime.crs_multitask_experiment import _canonical_to_key

_HALF_SPREAD = 0.01
_KALSHI_FEE_RATE = 0.07
_EDGE_THRESHOLD = 0.05

_COUNT_SENATE = re.compile(r"between (\d+) to (\d+) members of the Senate vote for", re.I)
_DEM_RANGE = re.compile(r"Will (\d+)-(\d+) Democratic senators vote in favor", re.I)
_DEM_LESS = re.compile(r"Will less than (\d+) Democratic senators vote in favor", re.I)
_DEM_MORE = re.compile(r"Will (\d+) or more Democratic senators vote in favor", re.I)
_MEMBER_VOTE = re.compile(r"Will ([A-Z][a-zA-Z.'\- ]+?) vote for", re.I)


@dataclass(frozen=True)
class MarketSpec:
    kind: str  # "count_bucket" | "member_vote"
    lo: int
    hi: int
    party: str | None  # restrict the count to one party's members
    member_name: str | None


def parse_market(question: str) -> MarketSpec | None:
    m = _COUNT_SENATE.search(question)
    if m:
        return MarketSpec("count_bucket", int(m.group(1)), int(m.group(2)), None, None)
    m = _DEM_RANGE.search(question)
    if m:
        return MarketSpec("count_bucket", int(m.group(1)), int(m.group(2)), "D", None)
    m = _DEM_LESS.search(question)
    if m:
        return MarketSpec("count_bucket", 0, int(m.group(1)) - 1, "D", None)
    m = _DEM_MORE.search(question)
    if m:
        return MarketSpec("count_bucket", int(m.group(1)), 10_000, "D", None)
    m = _MEMBER_VOTE.search(question)
    if m:
        return MarketSpec("member_vote", 1, 1, None, m.group(1).strip())
    return None


def _kalshi_fee(price: float) -> float:
    return _KALSHI_FEE_RATE * price * (1.0 - price)


def entry_cost(price: float, venue: str) -> float:
    return _HALF_SPREAD + (_kalshi_fee(price) if venue == "kalshi" else 0.0)


def load_person_names(records_path: Path) -> dict[str, str]:
    """display_name (lower) -> LIS member id (upper), from contract person rows."""
    out: dict[str, str] = {}
    for r in _iter_records(records_path):
        if r.get("entity_type") != "person":
            continue
        name = str(r.get("display_name") or "").strip().lower()
        for ext in r.get("external_ids") or []:
            ext_s = str(ext)
            if ext_s.lower().startswith("lis:") and name:
                out[name] = ext_s.split(":", 1)[1].upper()
    return out


def _ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_title_index(sidecar: Path, *, congress: int = 119) -> list[tuple[str, str]]:
    """(lowercased short title, bill_key) for title-matching market questions.

    Track A's market->bill links sometimes point at the House companion while
    the Senate voted on the S-bill; matching the act's name in the question
    against sidecar titles recovers the votable keys.
    """
    out: list[tuple[str, str]] = []
    for r in _iter_records(sidecar):
        if int(r.get("congress") or 0) != congress:
            continue
        title = str(r.get("title") or "").strip()
        if not (6 <= len(title) <= 80) or " " not in title:
            continue
        key = f"{congress}:{r.get('bill_type')}:{int(r.get('number') or 0)}"
        out.append((title.lower(), key))
    return out


def title_match_keys(question: str, title_index: list[tuple[str, str]]) -> list[str]:
    q = question.lower()
    hits = [(len(title), key) for title, key in title_index if title in q]
    hits.sort(reverse=True)
    best_len = hits[0][0] if hits else 0
    return [key for length, key in hits if length == best_len]


@dataclass(frozen=True)
class MatchedRollcall:
    vote_date: date
    votes: list[list[str]]  # [member, party, state, choice]


def _rollcalls_by_key(corpus: Path) -> dict[str, list[MatchedRollcall]]:
    from src.runtime.bill_content_experiment import normalize_bill_key

    out: dict[str, list[MatchedRollcall]] = {}
    for r in _iter_records(corpus):
        key = normalize_bill_key(str(r.get("bill_id") or ""))
        if key is None or not r.get("date"):
            continue
        out.setdefault(key, []).append(
            MatchedRollcall(
                vote_date=date.fromisoformat(str(r["date"])), votes=list(r.get("votes") or [])
            )
        )
    for rolls in out.values():
        rolls.sort(key=lambda rc: rc.vote_date)
    return out


def _member_rates(
    corpus: Path, before: date
) -> tuple[dict[tuple[str, bool], tuple[int, int]], dict[tuple[str, bool], tuple[int, int]]]:
    from src.runtime.count_pmf_experiment import _accumulate_rates

    rolls = [
        r
        for r in _iter_records(corpus)
        if r.get("date") and date.fromisoformat(str(r["date"])) < before
    ]
    return _accumulate_rates(rolls)


def _fair_count_bucket(
    spec: MarketSpec,
    rollcall: MatchedRollcall,
    rates: dict[tuple[str, bool], tuple[int, int]],
    party_rates: dict[tuple[str, bool], tuple[int, int]],
    *,
    sponsor_party: str,
    sigma_common: float,
    sigma_party: float,
) -> float:
    """Bucket mass of the correlated count PMF over the roster that actually voted."""
    members = [v for v in rollcall.votes if v[3] in ("yea", "nay")]
    if spec.party:
        members = [v for v in members if v[1] == spec.party]
    if not members:
        return 0.0
    probs = np.empty(len(members))
    signs = np.empty(len(members))
    for i, (member, party, _state, _choice) in enumerate(members):
        lean = party == sponsor_party  # sponsor's party leans yea, others nay
        yes, n = rates.get((member, lean), (0, 0))
        if n == 0:
            yes, n = party_rates.get((party, lean), (0, 0))
        probs[i] = (yes + 1.0) / (n + 2.0)
        signs[i] = 1.0 if party == "D" else -1.0 if party == "R" else 0.0
    pmf = correlated_count_pmf(probs, signs, sigma_common=sigma_common, sigma_party=sigma_party)
    lo = max(0, spec.lo)
    hi = min(pmf.shape[0] - 1, spec.hi)
    return float(pmf[lo : hi + 1].sum()) if hi >= lo else 0.0


def fit_within_party_sigma(
    senate_corpus: Path, *, party: str, rates_before: date, fit_before: date
) -> float:
    """Effective shock scale for a SINGLE party's yes-count, fit ex-ante.

    The chamber-level (sigma_common, sigma_party) are fit on full-chamber counts
    where the party shocks partially cancel; applied to a one-party subset they
    both act as the same shared shift and wildly overspread the count. This fits
    the shift scale directly on historical one-party counts (rates strictly
    before ``rates_before``, counts in [rates_before, fit_before)) and returns
    sqrt(sc^2 + sp^2) -- the effective single-shift sigma.
    """
    from src.prediction.vote_count_pmf import RollCallCounts, fit_shock_scales

    rates, party_rates = _member_rates(senate_corpus, rates_before)
    counts: list[RollCallCounts] = []
    for r in _iter_records(senate_corpus):
        raw = str(r.get("date") or "")
        if not raw or not (rates_before <= date.fromisoformat(raw) < fit_before):
            continue
        members = [v for v in r.get("votes") or [] if v[3] in ("yea", "nay") and v[1] == party]
        if len(members) < 20:
            continue
        lean = _party_lean_yea(members)
        probs = np.empty(len(members))
        for i, (member, p_party, _state, _choice) in enumerate(members):
            yes, n = rates.get((member, lean), (0, 0))
            if n == 0:
                yes, n = party_rates.get((p_party, lean), (0, 0))
            probs[i] = (yes + 1.0) / (n + 2.0)
        counts.append(
            RollCallCounts(
                probabilities=probs,
                party_sign=np.ones(len(members)),
                observed_yes=sum(1 for v in members if v[3] == "yea"),
            )
        )
    if not counts:
        return 0.0
    sc, sp = fit_shock_scales(counts)
    return float(np.hypot(sc, sp))


def _party_lean_yea(members: list[list[str]]) -> bool:
    yes = sum(1 for v in members if v[3] == "yea")
    return yes * 2 >= len(members)


def _resolve_count_bucket(spec: MarketSpec, rollcall: MatchedRollcall) -> bool:
    yes = sum(
        1 for v in rollcall.votes if v[3] == "yea" and (spec.party is None or v[1] == spec.party)
    )
    return spec.lo <= yes <= spec.hi


def run(
    markets_dir: Path,
    records_path: Path,
    senate_corpus: Path,
    *,
    sigma_common: float,
    sigma_party: float,
    sponsor_party_by_key: dict[str, str] | None = None,
    sidecar: Path | None = None,
) -> dict[str, Any]:
    canon2key = _canonical_to_key(records_path)
    names = load_person_names(records_path)
    rollcalls = _rollcalls_by_key(senate_corpus)
    title_index = load_title_index(sidecar) if sidecar is not None else []

    price_series: dict[str, list[tuple[datetime, float]]] = {}
    with (markets_dir / "prices.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("outcome", "Yes")).lower() != "yes":
                continue
            ts = _ts(str(row.get("known_at") or row.get("observed_at") or ""))
            if ts is None:
                continue
            price_series.setdefault(str(row.get("market_id")), []).append(
                (ts, float(row.get("price", 0.0)))
            )
    for series in price_series.values():
        series.sort(key=lambda pair: pair[0])

    results: list[dict[str, Any]] = []
    within_sigma: dict[str, float] = {}
    skipped: dict[str, int] = {
        "unparsed": 0,
        "no_bill_link": 0,
        "no_rollcall": 0,
        "no_price": 0,
        "price_after_vote": 0,
    }
    for market in _iter_records(markets_dir / "markets.jsonl"):
        question = str(market.get("question", ""))
        spec = parse_market(question)
        if spec is None:
            skipped["unparsed"] += 1
            continue
        linked = [canon2key.get(str(c)) for c in market.get("bill_ids") or []]
        keys = sorted({k for k in linked if k} | set(title_match_keys(question, title_index)))
        if not keys:
            skipped["no_bill_link"] += 1
            continue
        mid = str(market.get("market_id"))
        series = price_series.get(mid, [])
        if not series:
            skipped["no_price"] += 1
            continue
        end = _ts(str(market.get("end_date") or ""))
        end_date = end.date() if end else date.max
        window_rolls = [rc for k in keys for rc in rollcalls.get(k, []) if rc.vote_date <= end_date]
        if not window_rolls:
            skipped["no_rollcall"] += 1
            continue
        matched = max(window_rolls, key=lambda rc: rc.vote_date)  # passage follows cloture
        # decision price: the LAST price strictly before the vote day (ex-ante)
        pre_vote = [(ts, p) for ts, p in series if ts.date() < matched.vote_date]
        if not pre_vote:
            skipped["price_after_vote"] += 1
            continue
        decision_ts, market_p = pre_vote[-1]
        rates, party_rates = _member_rates(senate_corpus, decision_ts.date())
        sponsor_party = (sponsor_party_by_key or {}).get(keys[0], "R")

        if spec.kind == "count_bucket":
            if spec.party and spec.party not in within_sigma:
                within_sigma[spec.party] = fit_within_party_sigma(
                    senate_corpus,
                    party=spec.party,
                    rates_before=date(2023, 1, 3),
                    fit_before=date(2025, 1, 3),
                )
            sc, sp = (within_sigma[spec.party], 0.0) if spec.party else (sigma_common, sigma_party)
            fair = _fair_count_bucket(
                spec,
                matched,
                rates,
                party_rates,
                sponsor_party=sponsor_party,
                sigma_common=sc,
                sigma_party=sp,
            )
            resolved_yes = _resolve_count_bucket(spec, matched)
        else:  # member_vote
            lis = names.get(str(spec.member_name).lower())
            vote = next((v for v in matched.votes if v[0] == lis), None) if lis else None
            if vote is None:
                skipped["no_rollcall"] += 1
                continue
            lean = vote[1] == sponsor_party
            yes, n = rates.get((vote[0], lean), (0, 0))
            if n == 0:
                yes, n = party_rates.get((vote[1], lean), (0, 0))
            fair = (yes + 1.0) / (n + 2.0)
            resolved_yes = vote[3] == "yea"

        y = 1.0 if resolved_yes else 0.0
        fair_c = min(1.0 - 1e-6, max(1e-6, fair))
        market_c = min(1.0 - 1e-6, max(1e-6, market_p))
        edge = fair - market_p
        venue = str(market.get("venue", ""))
        cost = entry_cost(market_p, venue)
        pnl = 0.0
        side = "none"
        if edge > _EDGE_THRESHOLD + cost:
            side = "buy_yes"
            pnl = y - (market_p + cost)
        elif edge < -(_EDGE_THRESHOLD + cost):
            side = "buy_no"
            pnl = (1.0 - y) - ((1.0 - market_p) + cost)
        results.append(
            {
                "market_id": mid,
                "venue": venue,
                "question": question[:100],
                "kind": spec.kind,
                "matched_vote_date": matched.vote_date.isoformat(),
                "matched_keys": keys,
                "decision_time": decision_ts.isoformat(),
                "market_price": market_p,
                "model_fair": fair,
                "edge": edge,
                "resolved_yes": resolved_yes,
                "model_logloss": float(-(y * np.log(fair_c) + (1 - y) * np.log(1 - fair_c))),
                "market_logloss": float(-(y * np.log(market_c) + (1 - y) * np.log(1 - market_c))),
                "side": side,
                "pnl_after_costs": pnl,
            }
        )

    n = len(results)
    traded = [r for r in results if r["side"] != "none"]
    return {
        "n_scored": n,
        "skipped": skipped,
        "mean_model_logloss": float(np.mean([r["model_logloss"] for r in results])) if n else 0.0,
        "mean_market_logloss": float(np.mean([r["market_logloss"] for r in results])) if n else 0.0,
        "model_beats_market": bool(
            n
            and np.mean([r["model_logloss"] for r in results])
            < np.mean([r["market_logloss"] for r in results])
        ),
        "n_traded": len(traded),
        "total_pnl_after_costs": float(sum(r["pnl_after_costs"] for r in traded)),
        "within_party_sigma": within_sigma,
        "cost_model": {
            "half_spread": _HALF_SPREAD,
            "kalshi_fee_rate": _KALSHI_FEE_RATE,
            "edge_threshold": _EDGE_THRESHOLD,
        },
        "markets": results,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Backtest model fair values vs market prices")
    p.add_argument("--markets-dir", default="data/exports/markets")
    p.add_argument("--records", default="data/exports/contract_records/records.jsonl")
    p.add_argument("--senate", default="data/real/senate_119_rich.jsonl")
    p.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_full.jsonl")
    p.add_argument("--pmf-pin", default="benchmarks/count_pmf_baseline.json")
    p.add_argument("--out", default="benchmarks/market_backtest.json")
    args = p.parse_args(argv)

    pin = json.loads(Path(args.pmf_pin).read_text(encoding="utf-8"))
    report = run(
        Path(args.markets_dir),
        Path(args.records),
        Path(args.senate),
        sigma_common=float(pin["sigma_common"]),
        sigma_party=float(pin["sigma_party"]),
        sidecar=Path(args.sidecar),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"scored {report['n_scored']} markets (skipped {report['skipped']}) | "
        f"log-loss model {report['mean_model_logloss']:.4f} vs market "
        f"{report['mean_market_logloss']:.4f} | beats market: {report['model_beats_market']} | "
        f"traded {report['n_traded']}, PnL after costs {report['total_pnl_after_costs']:+.3f}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
