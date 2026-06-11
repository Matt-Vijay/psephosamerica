# Prediction-markets sidecar — the Track A → B contract (v7 #1)

`data/exports/markets/` is the market side of "price P(bill becomes law by
deadline) against live markets". Produced by `src/runtime/market_ingest.py`
(snapshot) and refreshed by the live cycle (`src/runtime/live_cycle.py`);
read-only keyless public APIs (Polymarket Gamma + CLOB, Kalshi trade-api v2) —
no auth, no trading.

## Files

### `markets.jsonl` — one current row per market (rewritten per snapshot)

| field | meaning |
|---|---|
| `market_id` | stable graph-entity id `cm-…` = `stable_id([venue, native_id])` |
| `venue` | `polymarket` \| `kalshi` |
| `native_id` | Polymarket `conditionId` / Kalshi ticker |
| `question`, `rules_text` | the market's title + full resolution rules |
| `outcomes`, `outcome_prices` | aligned lists; binary markets are `["Yes","No"]` |
| `volume`, `liquidity` | floats when the venue reports them, else null |
| `end_date` | resolution deadline (ISO; venue-native precision) |
| `closed` | true once the venue stops trading it |
| `bill_ids` | **the join key**: canonical `cb-…` ids cited in the rules text |
| `citations` | `[{citation, congress, canonical_bill_id}]` (e.g. `hr-22`, 119) |
| `source_url`, `content_sha256`, `known_at` | provenance; sha over question+rules |

Bill linkage: rules text names the bills ("H.R. 6644 (119th)", "S. 1837").
An explicit `(119th)` pins the congress; a bare citation defaults to the
congress sitting at the **market's own end date** (so a closed 2024 market
resolves to the 118th, not the snapshot-day congress). `bill_ids` are minted by
the same `BillRef` scheme as the bill corpus — join verified at **27/27 cited
bills present** in `contract_records`.

### `prices.jsonl` — append-only price observations

`{market_id, observed_at, price, outcome, source, known_at}` — `source` is
`history` (venue-exposed backfill: Polymarket CLOB `prices-history`, Kalshi
daily candles — close, else bid/ask-mid, else previous) or `snapshot` (the live
Yes price at snapshot time). **`known_at` = when that price was publicly
visible** (the history point's own timestamp), so backtests can replay
leakage-free. De-duplicated by `(market_id, observed_at, source)`.

### `deltas.jsonl` — append-only announcements

`{canonical_id, change_type: created|updated, known_at}` — `updated` fires when
the rules-text hash changes (rule amendments matter for resolution semantics).
Tail it like the corpus delta feed.

## Refresh cadence

- One-shot: `python -m src.runtime.market_ingest` (full, incl. closed markets
  + history backfill).
- Periodic: the live cycle's markets stage snapshots open markets per tick.
- A daily crontab entry is macOS-TCC-gated (needs one interactive approval);
  the runner is a single idempotent command when that's wanted.

## Demonstrated (2026-06-11)

1,275 markets (918 Polymarket + 357 Kalshi), 49 bill-linked (the
"become law"/"Senate votes on" series both venues run), 5,322 price
observations, 0 fetch errors.
