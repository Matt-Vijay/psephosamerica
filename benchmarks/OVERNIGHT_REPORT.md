# Psephos America Track B — Overnight MAX: final report

First real metrics on real federal data, end to end, all numpy (no torch).
Data: **1.28M real House votes** from the House Clerk public roll-call feed
(115th–119th congresses) + Track A's contract corpus (**7,918 ready records,
440 federal members with real 256-d dossier + 64-d structural embeddings**).
~16 incrementally-committed-and-pushed slices; full prediction/runtime/api suite
green (4,647 tests), mypy-strict clean.

## Requirements status (all 8 satisfied with real numbers)

**(1) Real-data runner + first real metrics, per slice, per window.**
`real_data_eval` + `house_clerk_corpus` + `real_benchmark`. 118th-h2 (168,879 real
votes): Brier **0.062**, log-loss 0.234, accuracy **93.2%**, ECE 0.013. Sliced by
all / cross_pressured / per-party / per-state. (See REAL_METRICS.md.)

**(2) Six-stream ablation.** `nn/ablation` + `four_stream_real`. Four-stream model
trained on real votes joined to Track A's real embeddings (92.4% acc); ablation
shows the party-alignment context token carries ~all signal (−0.525 acc when
dropped) and the real embeddings (untrained projections) add ~0 on the overall
slice — honest, party-domination confirmed.

**(3) HTTP API + HTML explorer, serving real cited predictions.** `wsgi_app` +
`explorer` + `build_served_snapshot`. `GET /v1/prediction/<person>/<bill>`
(200/404/429), `GET /v1/dashboard(.html)`, `GET /v1/explorer`. Snapshot of **80
real cited predictions** (real clerk.house.gov URL + sha256 + timestamp evidence,
calibrated interval, counterfactual). serving-boundary no-leakage enforced.

**(4) Real-data baseline + CI gate.** `prediction_benchmark_gate --from-real-corpus`
recomputes per-slice metrics on a committed 1-in-100 real sample and **fails CI on
Brier/log-loss regression > 0.005** vs `prediction_real_baseline.json`. Live CI job.

**(5) Bayesian seed-ensemble uncertainty on real.** `per_member_ensemble`. 5-seed
bootstrap bag; on real 118th-h2 the ensemble is **wider where it is more often
wrong** — cross_pressured interval width 0.093 vs all 0.070; Republicans 0.091 vs
Democrats 0.048. Informative uncertainty.

**(6) Stated-position → vote-stance transfer on real statements.**
`stated_position_real` + `stated_position_transfer`. Trained on real public-statement
sector attention; **0.553 held-out accuracy vs 0.535 majority baseline** (+0.018,
36 members) with sensible learned coefficients. Weak-but-real, honestly reported.

**(7) Per-member multi-window calibration drift across congresses.** Real cold-start
→ warm → turnover → recovery: 115th (no history) acc 0.60 / Brier 0.25 → 116th–117th
acc 0.95–0.96 → 118th turnover dip 0.91 → recovery 0.93; ECE best (0.010) at highest
member coverage. The partial-pooling thin-record payoff, on real data.

**(8) Continuous-learning cron simulator.** `continuous_learning_sim`. Idempotent
plan generation across a schedule; cutoff advances once per tick, full retrain on
cadence, incremental on new votes only, never trains on future votes.

## Headline finding
Party alignment predicts House votes at ~93% accuracy, but a party model is
**catastrophically wrong (≈0%) on the cross-pressured slice** — the votes worth
predicting — in every warm window. That is the quantified, real-data case for the
richer four-stream signals and per-member sector slopes the architecture provides.

## Honest gaps
- The four-stream embedding streams use *untrained random projections*; their payoff
  on the cross-pressured slice needs trained projections + per-member sector slopes
  (next experiment).
- 5 congresses: 115th–119th all real (the 119th = 2025, acc 0.949).
- Bill embeddings are sparse in the corpus today (19 bills), so the bill streams are
  placeholders until bill enrichment is dense.
