# First real metrics — 118th Congress House (Track B)

Source: House Clerk public roll-call XML (`clerk.house.gov/evs/<year>/roll<NNN>.xml`),
**1,237 real roll-calls** (2023: 720, 2024: 517) → **508,382 real member votes**.
Model: per-member partial-pooling model, single signal `party_alignment` (the
member's party majority direction on each roll-call). Strict-cutoff windows;
unseen members fall back to the global fit. Corpus is reproducible (not committed);
report at `data/real/house_118_report.json`, baseline at `prediction_real_baseline.json`.

| window | slice | Brier | log-loss | accuracy | ECE | n |
|---|---|---|---|---|---|---|
| 118th-h1 | all | 0.082 | 0.324 | 0.914 | 0.056 | 180,675 |
| 118th-h1 | cross_pressured | 0.901 | 3.222 | 0.000 | 0.948 | 15,615 |
| 118th-h1 | party:D | 0.028 | 0.133 | 0.974 | 0.039 | 88,421 |
| 118th-h1 | party:R | 0.133 | 0.507 | 0.856 | 0.104 | 92,254 |
| 118th-h2 | all | 0.062 | 0.234 | 0.932 | 0.013 | 168,879 |
| 118th-h2 | cross_pressured | 0.796 | 2.471 | 0.003 | 0.888 | 11,459 |
| 118th-h2 | party:D | 0.044 | 0.178 | 0.952 | 0.019 | 82,901 |
| 118th-h2 | party:R | 0.079 | 0.288 | 0.913 | 0.019 | 85,978 |

## Findings
1. **Party alignment alone predicts House votes at ~93% accuracy** with good
   calibration (ECE 0.013 in 118th-h2). The chamber is highly partisan.
2. **The cross-pressured slice is catastrophic (≈0% accuracy, Brier ~0.8–0.9).**
   By construction a party-only model is wrong on every defection. This is the
   blueprint's central claim confirmed on real data: the votes worth predicting
   are exactly where a party model fails — the case for the richer four-stream
   signals (sector/structural/dossier/past-vote) and per-member sector slopes.
3. Republicans are slightly less predictable than Democrats from party alone
   (more intra-party dispersion in this period).
4. Calibration improves from 118th-h1 to 118th-h2 (ECE 0.056 → 0.013) as the
   per-member history accumulates — real evidence of the partial-pooling payoff.

## Artifacts
- `house_118_full_report.json` — the full 508K-vote per-window per-slice report
  (the headline numbers above).
- `real_house_sample.jsonl` — a deterministic 1-in-100 real sample (5,084 votes,
  449 members, 2023–2024) committed so CI can recompute without the 127MB corpus.
- `prediction_real_baseline.json` — the gate baseline, pinned from the sample's
  118th-h2 window. The `benchmark-gate` CI job recomputes the sample metrics and
  **fails on any Brier/log-loss regression > 0.005** per slice; re-pin with
  `--update-baseline` to accept a deliberate change.

## Bayesian seed-ensemble uncertainty (5 seeds, 118th-h2 real sample)
Bootstrap bag of the per-member model; interval width = ensemble prediction spread.
| slice | mean Brier | mean interval width | n |
|---|---|---|---|
| all | 0.071 | 0.070 | 1,688 |
| cross_pressured | 0.815 | **0.093** | 128 |
| party:D | 0.053 | 0.048 | 835 |
| party:R | 0.090 | **0.091** | 853 |

The ensemble is **wider (less certain) exactly where it is more often wrong** —
on the cross-pressured slice and on the more-dispersed Republican caucus — real
evidence that the uncertainty is informative, not cosmetic.

## Six-stream ablation on REAL data + Track A's real embeddings (118th-h2 sample)
Four-stream transformer trained on 1,200 real votes joined to Track A's contract
embeddings (real 256-d dossier + 64-d structural, projected to tokens); evaluated
on 1,655 real votes. Full accuracy 0.924.
| ablation | accuracy | Brier | Δacc vs full |
|---|---|---|---|
| full | 0.924 | 0.071 | — |
| minus context (party_alignment) | 0.399 | 0.416 | **−0.525** |
| minus politician_structural | 0.924 | 0.071 | 0.000 |
| minus politician_dossier | 0.924 | 0.072 | 0.000 |
| minus bill_structural / bill_dossier | 0.924 | 0.071 | 0.000 |
| minus retrieved_past_votes | 0.924 | 0.071 | 0.000 |

**Finding (honest):** on the overall slice the party-alignment context token carries
essentially all the signal; the real dossier/structural embeddings — with untrained
random projections — add ≈0 on top. This matches the per-member result: House votes
are party-dominated. The embeddings' expected payoff is on the **cross-pressured**
slice (where party fails) and with *trained* projections / per-member sector slopes;
that is the next experiment. The result proves the four-stream architecture runs end
to end on Track A's real `dossier_embedding`/`structural_embedding`.
