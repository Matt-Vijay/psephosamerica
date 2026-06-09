# Cross-pressured session — summary (Track B, 2-3h MAX)

THE experiment and its support, all on real federal House data, numpy-only,
committed incrementally and pushed. Every requirement delivered with real
numbers or a tested path.

| # | requirement | outcome |
|---|---|---|
| 1 | **THE experiment**: train sector signal, cross-pressured before/after | Done. Hypothesis (lift to >50%) **refuted** with rigorous diagnosis: 10% sector coverage, the metric is partly tautological (`cross_pressured := is_yea != party_lean`), weak signal, minority-slice tension. Path: bill encoder + RAG. (CROSS_PRESSURED_EXPERIMENT.md) |
| 2 | HPO sweep on real validation | Done. **Config-insensitive** — every config 0.9239 acc; bottleneck is features, not capacity. (hpo_results.json) |
| 3 | multi-task pretraining on real | Done. Votes+statement-stance: 1.000 non-cp / 0.000 cp; auxiliary too sparse/non-bill-specific. (multitask_real.json) |
| 4 | LLM-forecaster ensemble | Path tested (no ANTHROPIC_API_KEY): AnthropicForecaster (httpx, no SDK dep) integrates into StackedForecaster; parse/prompt/protocol tested with a stub. |
| 5 | bill encoder on real bill embeddings | Exercised: four-stream consumes Track A's 19 real bill embeddings. Honest blocker: 0/19 bills carry external_ids linkable to votes → impact unmeasurable. |
| 6 | cross-pressured baseline + CI gate | Live: prediction_cross_pressured_baseline.json (Brier 0.810) + CI gate fails on regression > 0.005. |
| 7 | 10-seed Bayesian uncertainty on cross-pressured | Done: cp interval width 0.123 vs overall 0.090 — sharper "knows-what-it-doesn't-know". |
| 8 | dashboard side-by-side | Done: cross_pressured_dashboard.html — overall vs cross-pressured by congress. |
| 9 | counterfactual rendering | Done: top-3 flip factors (signed contribution) in the explorer + /v1/prediction JSON. |
| 10 | content-addressed checkpoints | Done: checkpoint.py + pinned real per-member model under checkpoints/sha256/... |
| 11 | continuous-learning LIVE ≥30 min | Ran 7 ticks at 5-min cadence against Track A's delta-CDC; cutoff advances, full/incremental retrains logged. (continuous_learning_live.jsonl) |

## The headline (real)
Across HPO, multi-task, and the sector experiment, **nothing moves the
cross-pressured slice off ~0% accuracy** on party-dominated real House votes —
because a party-aligned model is wrong on defections by construction, and the
only signal that can flip a specific defection is a per-(member, bill)
bill-content interaction (the bill encoder + RAG over similar past votes). That
needs dense, vote-linkable bill embeddings, which the corpus does not yet carry
(19 unlinked bills). This session quantifies, on real data, exactly where the
ceiling is and what removes it — the strongest possible spec for the next slice.
