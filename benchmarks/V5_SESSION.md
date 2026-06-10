# OpenPact Track B v5 — Session Report

**Goal:** semantic A/B + a forward track record, working a priority queue top-down,
never blocking. numpy only (torch lives in Track A's enrichment; we consume vectors).

## Priority-queue status

| # | Item | State |
|---|---|---|
| 1 | **Semantic A/B** (semantic vs hash vs concat on the 118th pin) | **harness built + watcher armed; result PENDING Track A.** Track A's semantic export never stabilized this session — it ran repeated truncate-and-rewrite passes (the semantic count bounced 9k↔40k of 106k all session, never reaching full coverage), so the settle-gated watcher correctly never fired. The hash/semantic/concat A/B vs the 0.7707 pin (+ honest pooling/truncation/leakage diagnosis if semantic ≤ hash) runs the instant the export completes; `semantic_ab_experiment.py` + `corpus_watch --semantic-ab` are committed and armed. **Documented-blocked on the upstream data, not on Track B code.** |
| 2 | **Forward track record** | **DONE** — 400 content-hashed predictions frozen + scored, pages rendered (see below). |
| 3 | Senate joint + transfer | blocked — no Senate backfill in the contract yet. |
| 4 | State zero-shot | blocked — no state votes yet. |
| 5 | **Productize: weekly marginal-votes brief** | **DONE** — top-20 flippable with evidence + venue P(pass). (HTTP serving: next.) |
| 6 | Recalibrate per slice + re-pin gates | **DONE (vote-only)** — temperature ECE 0.0356→0.0001; Mondrian conformal 0.868 ≥0.85 on defection-prone. Bill-content recalibration reruns once the contract settles. |
| 7 | Multi-task on real CRS policy-area edges | **DONE — ΔAUC +0.071** on the 113th (base 0.674 → +CRS 0.745, 98% coverage). Was 0 in v4; the govinfo `bill_content.jsonl` sidecar (CRS policy_area + subjects, joined via the contract) made it real. |
| 8 | LLM-forecaster live/stub | stub + stacking already shipped (v4); no ANTHROPIC_API_KEY. |
| 9 | 10-seed Bayesian on SOTA + checkpoints | deferred with #6 (contract churning). |
| 10 | **Continuous-learning LIVE ≥60 min + semantic hot-swap** | **DONE** — 15 ticks / ~67 min; CDC delta count climbed 139,617→192,908; the **semantic hot-swap fired on 14 of 15 ticks** (manifest sha changed every tick during Track A's live semantic export, each forcing a full retrain). `cl_live_v5.jsonl`. |
| 11 | This report | done (updated as items land). |

## #2 — Forward prediction registry (the credibility milestone)

A content-hashed, tamper-evident track record: freeze predictions (probability +
citations + counterfactual flip) trained strictly on votes ≤ cutoff, score when
outcomes land, frozen sha256 preserved through scoring.

| registry | cutoff → window | n | Brier | acc | AUC | sha |
|---|---|---|---|---|---|---|
| 118th (rich, validation) | 2024-04-20 → 2024-12-31 | 200 | 0.134 | 0.850 | **0.634** | 2c8e1eff |
| 119th (flat, live forward) | 2025-09-30 → 2025-12-18 | 200 | 0.266 | 0.665 | 0.547 | a60eecde |

400 pre-registered predictions total (target ≥50). The 119th flat AUC is weak
because the flat corpus has no sectors (loyalty-only head); the **rich 119th
upgrade is blocked** — clerk.house.gov returned 403 (rate-limited after the bulk
fetches); it reruns on a later tick. The 118th-rich validation shows the framework
produces a credible, well-calibrated record when bill content is present.

Pages: `prediction_registry.html`, `prediction_registry_118.html`. Runner:
`registry_runner.py freeze|score` (`--rich`/`--flat`).

## #5 — Weekly marginal-votes brief

Composes defection-watch (who) + venue-score (where): the top-20 flippable
(member, bill) pairs by P(defect), each with cited ex-ante factors + signed
contribution, the counterfactual flip, and the bill's venue P(pass). Strict
cutoff. `marginal_votes_report.{json,html}`.

## #6 — Recalibration (vote-only defection head, 118th)

Temperature scaling cuts ECE **0.0356 → 0.0001** (isotonic 0.0079); best calibrator
= temperature. Mondrian split-conformal coverage on the defection-prone slice =
**0.868** (target ≥0.85). `defection_recalibration.json`. Also shipped the served
defection object (`served_defection.py`): calibrated P + Bayesian uncertainty band +
cited evidence + explanation + counterfactual — the Definition-of-Done served object.

## #7 — Multi-task on real CRS policy-area edges (113th)

| model (113th, train 2013 → eval 2014, 98% CRS coverage) | defection AUC |
|---|---|
| base (loyalty + keyword-sector) | 0.6739 |
| + CRS-policy-area defection signal | **0.7446** (ΔAUC **+0.0707**, coef +0.309) |

The CRS taxonomy (98% coverage, 31 policy areas) is a far stronger per-member
signal than the ~10%-coverage keyword sectors: a member who breaks on Immigration
bills breaks on new ones. Strict cutoff. `crs_multitask.json`. (118th reruns once
`bill_content.jsonl` — still mid-write at congress 113-115 — reaches the 118th.)

## Robustness shipped this session

- Contract readers skip malformed JSONL lines (Track A re-exports in place; reading
  mid-write no longer crashes — degrades to "read what's complete").
- Watcher fires only when its gating count **settles** across two polls, and in
  `--semantic-ab` mode gates on the **semantic** count (the hash count settles
  first and would fire early).
- **Label-corruption fix**: flat-corpus loaders grouped roll-calls by bill id
  alone, pooling distinct roll-calls across dates and corrupting party-majority /
  defection labels; now grouped by (bill_id, vote_date).

## Verification

`ruff` + `mypy --strict` clean on all new modules; **4789 tests pass**. Two
background workers are live: the semantic-AB watcher (fires on the settled semantic
export) and the ≥60-min continuous-learning run (capturing the semantic hot-swap).
Carried-over SOTA: bill-content defection AUC **0.7707** on the 118th (beats the
0.7247 vote-only pin by +0.046), pinned in `bill_content_baseline.json`.
