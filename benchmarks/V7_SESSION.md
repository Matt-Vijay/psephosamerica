# OpenPact Track B v7 — price the journey, not just the vote

Mission: markets price compound events (committee → floor → House → Senate
cloture → signature, by a deadline); Track B previously priced one link. v7
builds the chain ADDITIVELY — new heads beside the defection head, nothing
replaced. All numpy; strict cutoffs; every number below is from a committed
artifact.

## Priority-queue status

| # | Item | State |
|---|---|---|
| 1 | **Hazard model** (stage transitions) | **DONE** — discrete-time floor-vote hazard, time-sliced (train ≤117th: 51,297 bills; eval 118th: 19,307): **C-index 0.7885** vs 0.5000 intercept-only, Brier@365 0.0343, **ECE@365 0.0188**, monotone reliability. Pinned (`stage_hazard_baseline.json`). |
| 2 | **Correlated count PMF** | **DONE** — latent common+party shocks over member logits → full yes-count PMF. 118th eval: independence's 90% interval covers **10.1%** of observed counts; correlated covers **90.4%** (σ=(1.5, 2.2) MLE), CRPS 22.49→21.23, log-lik up. **Beats independence.** Pinned (`count_pmf_baseline.json`). |
| 3 | **Composer** | **DONE** — `chain_composer` multiplies cited links: hazard floor arrival (age-conditioned via `predict_event_between`), count-PMF pivots (median / 60-vote cloture), **empirical** cross-chamber rate (98/1180 House-floor bills later reached the Senate floor, 113th–118th), historical signature prior. 8 live 119th bills priced with per-link attribution; binding link is floor arrival (1–3%); cloture prices the filibuster at 0.30 with 53 seats. `chain_composer_sample.json`. |
| 4a | **Market backtest** | **DONE — honest negative.** Track A's markets sidecar landed (1,275 markets, 5,322 prices, known_at). On the 6 resolvable real-resolution markets (Laken Riley Dem-count family; decision price = last pre-vote print; rates strictly ex-ante; fees+spread modeled): **model log-loss 0.877 vs market 0.435 — the market wins**; trading a 5¢ edge after costs loses −0.67. Ex-ante within-party shock fits at σ=3.22: one-party counts are hugely overdispersed vs rate-based means — member rates can't see whip counts/statements. Kalshi member-vote markets are snapshot-only (no pre-vote price history) → honestly unscoreable (`price_after_vote`). `market_backtest.json`. |
| 4b | Blend (shrink toward market) | **blocked, documented** — 6 resolved markets cannot fit a shrinkage weight; the 4a result already says the weight would be ≈1 (market). Re-fit when the paper-trade registry resolves. |
| 4c | **Paper trader** | **DONE** — froze **11 open-market fair values** (chain-composer priced become-law markets), content-hashed `94224fe4…`, quarter-Kelly stakes on a notional $100; `score` subcommand runs at resolution; engine-beats-market is the metric. Guard added: a market still trading on a bill that already cleared both floors is a provision/amendment market — refused rather than mispriced (caught the GENIUS-Act-vehicle / credit-card-routing case). Spread-capture screen documented as blocked: sidecar has no bid/ask. `paper_trades.json`. SIGNALS ONLY. |
| 5 | **Confirmation head** | **DONE** — the backfill had discarded question text, so the rich record now preserves it (extracted in the runtime layer; the graph parser is Track A's) and a full refetch landed 4,945 roll-calls with **1,383 "On the Nomination" votes**. Time-sliced (train ≤117th, eval 118th–119th, 40,933 senator-votes): **AUC 0.9525 vs party-line 0.9208** — the member residual ranks confirmation crossers the party line cannot. Honest caveat: the hard 0/1 party-line baseline wins Brier (0.0937 vs 0.1110) — at ~89% accuracy extreme predictions score sharper; the head's value is the ranking. Pinned (`confirmation_baseline.json`). |
| 6 | State zero-shot / LLM | unchanged: awaiting state votes via keyed ingest / ANTHROPIC_API_KEY. |
| 7 | ruff format + gates + seeds | **DONE** — 37 Track B files reformatted (zero behavior change); pin-guard tests extended to the new baselines; **10-seed bootstrap on the hazard pin: C-index 0.7874 ± 0.0075** (the 0.7885 pin is a property of the data, not a draw; checkpoint `5b1de619…`); count-PMF bootstrap running. |
| 8 | This report | done. |

## What the market module taught us (the honest read)

The defection-rate machinery that wins *within* our own benchmarks (0.7998
AUC) does **not** beat live markets on bill-specific count questions: markets
aggregate whip counts, statements, and amendment dynamics that historical
rates cannot see. The constructive consequences are already in the queue:
(a) the blend (#4b) will shrink hard toward price once enough markets resolve;
(b) the paper-trade registry (#4c) measures the composer against markets
out-of-sample, tamper-evidently; (c) the count head needs bill-content/
statement features (the same lever CRS policy-area provided for defection).

## Data notes

- `bill_content_full.jsonl` carries NO govinfo action history (no committee
  report / cloture / signature dates) — the hazard model's event is the first
  recorded floor roll-call joined from our own corpora; House-114 bills are
  skipped (no ingested floor data), never mislabelled; voice-vote-only
  advancement right-censors.
- Cosponsor features use only joiners within 30 days of introduction — the
  final tally leaks the bill's later success.
- Market→bill links sometimes point at the House companion (Laken Riley →
  HR 751) while the Senate voted S.5; a title match against the sidecar
  recovers votable keys, and the vehicle-already-passed guard refuses
  provision markets.

## Verification

ruff + ruff format + mypy --strict clean on all new modules; **4,968 tests**
green at session close (4,880 at open). Commits: hazard (9dbf01f), format
(0df32dd), count PMF (d4182b7), composer (c7b693a), backtest (ce092ca),
paper trader (this commit).
