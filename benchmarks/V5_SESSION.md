# OpenPact Track B v5 — Session Report

**Goal:** semantic A/B + a forward track record, working a priority queue top-down,
never blocking. numpy only (torch lives in Track A's enrichment; we consume vectors).

## Priority-queue status

| # | Item | State |
|---|---|---|
| 1 | **Semantic A/B** (semantic vs hash vs concat on the 118th pin) | **harness built + watcher auto-armed**; Track A's semantic export was mid-write this session (churning 16k→… of 106k bills). Watcher gates on the semantic count *settling* then auto-fires; result re-pins if it beats 0.7707. |
| 2 | **Forward track record** | **DONE** — 400 content-hashed predictions frozen + scored, pages rendered (see below). |
| 3 | Senate joint + transfer | blocked — no Senate backfill in the contract yet. |
| 4 | State zero-shot | blocked — no state votes yet. |
| 5 | **Productize: weekly marginal-votes brief** | **DONE** — top-20 flippable with evidence + venue P(pass). (HTTP serving: next.) |
| 6 | Recalibrate on SOTA + re-pin gates | deferred while the contract is mid-export (bill-content SOTA inputs churning). |
| 7 | Multi-task on cosponsor/committee/sector edges | blocked — CRS sidecar landed but `dossier_json` still only carries summary/claims (no cosponsor/committee). |
| 8 | LLM-forecaster live/stub | stub + stacking already shipped (v4); no ANTHROPIC_API_KEY. |
| 9 | 10-seed Bayesian on SOTA + checkpoints | deferred with #6 (contract churning). |
| 10 | **Continuous-learning LIVE ≥60 min + semantic hot-swap** | **running** — launched against the actively-churning manifest so it captures the semantic hot-swap mid-run. |
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
