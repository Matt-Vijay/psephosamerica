# ADR 0001: Cutoff-safe, no-leakage legislative prediction

Status: accepted

## Context

The prediction subsystem backtests legislative votes: it learns from each
member's history *before* a feature cutoff and predicts votes inside a later
evaluation window. The product's credibility depends on this being honest — a
single leak of post-cutoff information would silently inflate accuracy and
invalidate every reported metric.

## Decision

Enforce a strict temporal contract and exclude anything that cannot be *proven*
to predate the feature cutoff.

1. **Strict window ordering.** `build_vote_baseline_backtest` and
   `build_vote_ontology_backtest` reject any window where
   `feature_cutoff >= label_start`, and require `label_start <= label_end`. A
   vote can therefore never be both a feature and a label.
2. **Feature/label leakage check.** `_validate_no_leakage` rejects feature rows
   whose latest vote date is after the cutoff and label rows that fall outside
   `[label_start, label_end]`.
3. **Conservative availability for signals.** Ontology edges, bill sponsors,
   contributions, and public statements are admitted as features only when they
   carry a parseable availability date at or before the cutoff. If the date is
   missing or unparseable the item is **excluded**, never assumed available
   (`_edge_has_known_availability`, `_signal_row_available_at_or_before`).
4. **Cutoff applied at the source.** DB queries that build feature rows filter
   `vote_date <= feature_cutoff`; the in-engine checks are defense in depth for
   directly-supplied rows and operator artifacts.

## Consequences

- Backtests under-count rather than over-count features when provenance dates
  are absent — the safe direction for an evidence-first product.
- The invariant is locked by tests (e.g. `filters_unknown_availability_ontology_edges`,
  `filters_undated_sponsor_rows`, `filters_undated_contribution_and_statement_rows`,
  `rejects_feature_leakage_after_cutoff`). Changes that weaken any of these
  should be treated as breaking.
- The same contract is jurisdiction-neutral: it is expressed in dates and window
  bounds, not in anything Congress-specific, so it holds unchanged for other
  legislatures.
